"""
Pipeline/computational_time_analysis.py
────────────────────────────────────────
Computational Time Analysis — Real-Time MILP and Plan-Ahead MISOCP.

Every valid combination launches its own thread immediately.
All threads race concurrently — total wall time ≈ slowest single combination.
Combinations that exceed SOLVER_CAP_S are annotated with a power-law time
estimate fitted from combinations that completed within the cap.

Terminology
───────────
  JOB_LIFETIME_S     How long a placed job occupies a node (1 s here).
                     Used when pre-loading nodes with background jobs to
                     simulate a realistically occupied cluster at test time.
                     This is NOT a solver budget — it is a workload property.

  SOLVER_CAP_S       Maximum wall-clock seconds the solver is allowed per
                     combination before it is cut off and marked ">cap".
                     Both models share the same cap (5 min = 900 s).

Infeasibility filters (combinations skipped before launch)
───────────────────────────────────────────────────────────
  Real-Time : skip if J > N × RT_MAX_LOAD_RATIO
              (e.g. 1024 jobs on 4 machines is unrealistic — the cluster
               would queue most of them; the interesting regime is J ≤ ~4N)

  Plan-Ahead: skip if N < T × PA_MIN_NODES_PER_TENANT
              (each tenant needs at least one machine to receive an assignment)

Real-world note on job queuing
───────────────────────────────
  Tenants can submit unlimited jobs. The cluster does not reject them — it
  queues whatever it cannot place immediately. Jobs that are not placed in
  an interval wait in the pending queue and are retried next interval.
  When the cluster is saturated (arrival rate > placement rate), the queue
  grows and wait times rise, triggering fairness adjustments (ω_delay) in
  the Real-Time model. The Plan-Ahead model is triggered at the start of
  each horizon to rebalance machine assignments and reduce congestion.

TABLE 1 — Real-Time MILP — Solver Comparison (CBC / SCIP / Gurobi)
  Rows: pending jobs J   |   Cols: machines N
  One solve call per valid (J, N, solver) triple.
  All solvers use integer variables x[j,n] ∈ {0,1} — no LP relaxation.

TABLE 2 — Plan-Ahead MISOCP (Gurobi)
  Rows: tenants T   |   Cols: machines N   |   Fixed planning periods P
  u[i,h] — tenant memory demand per period — drawn from
  [PA_USAGE_MIN, PA_USAGE_MAX] capacity units (configurable).
  node_capacity = PA_NODE_CAPACITY units per machine.

Outputs
───────
  Console : timestamped progress, tables, summary, insights
  CSV     : timing_data/rt_timing.csv
            timing_data/pa_timing_grid.csv

Run
───
  cd Pipeline/
  python computational_time_analysis.py
"""

from __future__ import annotations

import csv
import io
import math
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import numpy as np

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.colors as mcolors
    _MPL = True
except ImportError:
    _MPL = False

# ── Path setup ─────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "Realtime"))
sys.path.insert(0, str(_ROOT / "PlanAhead"))

from simulation_data import Job, NodeState, K_WINDOW
from solver_backends  import precompute, get_backend
from plan_ahead_data      import build_synthetic_data, make_gurobi_env
from plan_ahead_optimizer import build_model
from gurobipy import GRB

DATA_DIR = Path(__file__).parent / "timing_data"
DATA_DIR.mkdir(exist_ok=True)

_PRINT_LOCK = threading.Lock()
_T0: float = 0.0   # wall-clock start, set in main()


def _pr(*args, **kwargs) -> None:
    elapsed = time.perf_counter() - _T0
    with _PRINT_LOCK:
        print(f"  [{elapsed:7.1f}s]", *args, **kwargs)


# ═══════════════════════════════════════════════════════════════════════════════
# § CONFIGURATION  ← edit here
# ═══════════════════════════════════════════════════════════════════════════════

SEED = 42   # master seed — controls job data, PA workload samples

# ── Shared solver cap ──────────────────────────────────────────────────────────
SOLVER_CAP_S = 5 * 60 # 5 minutes — max wall-clock seconds per combination
                      # Applies to both RT (in ms: SOLVER_CAP_S × 1000)
                      # and PA (in seconds: SOLVER_CAP_S).

# ── Real-Time job properties ───────────────────────────────────────────────────
# Job lifetime: how long a placed job occupies a node before completing.
# Used to pre-load nodes with background running jobs (simulates a partially
# occupied cluster). Set min = max = 1 s for a fixed lifetime.
JOB_LIFETIME_MIN_S = 1    # seconds — minimum job lifetime
JOB_LIFETIME_MAX_S = 1    # seconds — maximum job lifetime (= min for fixed)

# ── Real-Time grid ─────────────────────────────────────────────────────────────
RT_JOBS_LIST       = [16, 64, 256, 1024]          # pending jobs (J) per solve call
RT_NODES_LIST      = [4, 16, 64, 256, 512, 1024]  # machines (N) per solve call

# Small-node study — one-shot baseline for the job-bottleneck test (few nodes).
# The iterative companion (same grid) lives in computational_time_analysis_iterative.py.
RT_SMALL_JOBS      = [8, 16]
RT_SMALL_NODES     = [1, 2, 4, 8, 16, 32]
RT_SMALL_SOLVER    = "GUROBI"

# Skip (J, N) if J > N × RT_MAX_LOAD_RATIO.
# In practice the scheduler sees J ≤ ~N (most intervals clear the queue).
# A ratio of 4 allows moderate overload stress tests while excluding extreme
# imbalances (e.g. 1024 jobs on 4 machines) that are operationally impossible.
RT_MAX_LOAD_RATIO  = 4

# Integer solvers to compare for Real-Time model.
# All produce certified integer x[j,n] ∈ {0,1} assignments.
# GLOP is excluded — it is an LP relaxation (professor's requirement).
RT_SOLVERS = ["CBC", "SCIP", "GUROBI", "HIGHS"]

# ── Plan-Ahead grid ────────────────────────────────────────────────────────────
PA_TENANTS_LIST    = [2, 8, 128, 256, 512]          # tenants T
PA_NODES_LIST      = [4, 16, 64, 256, 512, 1024]   # machines N
PA_FIXED_PERIODS   = 4                              # planning slots per horizon (fixed)

# Small grid — one-shot MISOCP baseline, no skips (every cell run).
PA_SMALL_TENANTS   = [8, 16, 32, 64]
PA_SMALL_NODES     = [8, 16, 32, 64]

# Tenant memory demand u[i,h]: drawn uniformly from [PA_USAGE_MIN, PA_USAGE_MAX]
# in abstract capacity units, where one machine holds PA_NODE_CAPACITY units.
# Example with PA_NODE_CAPACITY=10: u=0.5 → tenant uses 5% of a machine;
#                                   u=2.5 → tenant uses 25% of a machine.
# In production these come from predict_workload(tenant, hours) in MB,
# with PA_NODE_CAPACITY = node RAM in MB (e.g. 65 536 for a 64 GB machine).
PA_USAGE_MIN       = 0.5    # min memory demand per tenant per period
PA_USAGE_MAX       = 2.5    # max memory demand per tenant per period

# Skip (T, N) if N < T × PA_MIN_NODES_PER_TENANT.
PA_MIN_NODES_PER_TENANT = 1  # each tenant needs at least this many machines

# Plan-Ahead model settings
PA_NODE_CAPACITY   = 10.0   # capacity units per machine
PA_ALWAYS_FRAC     = 0.5    # fraction of nodes that are always-on (M_a)
PA_EXCL_FRAC       = 0.20   # fraction of tenants tagged as exclusive (T_e)
PA_MIP_GAP         = 0.01   # Gurobi relative optimality gap target


# ═══════════════════════════════════════════════════════════════════════════════
# § DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class RTResult:
    n_jobs:    int
    n_nodes:   int
    n_vars:    int
    solver:    str     # "CBC" | "SCIP" | "GUROBI"
    solve_ms:  float   # actual elapsed ms (= cap if cut off)
    placed:    float   # fraction of jobs placed (0–1)
    capped:    bool    # True if solve_ms hit SOLVER_CAP_S


@dataclass
class PAResult:
    n_tenants:  int
    n_nodes:    int
    n_periods:  int
    n_vars:     int
    n_constrs:  int
    build_s:    float
    solve_s:    float
    total_s:    float
    mip_gap:    float | None
    status:     str    # "OPT" | "TLim" | "INF" | "SKIP" | "ERR"


# ═══════════════════════════════════════════════════════════════════════════════
# § FEASIBILITY FILTERS
# ═══════════════════════════════════════════════════════════════════════════════

def _rt_is_valid(n_jobs: int, n_nodes: int) -> bool:
    """Skip combinations where J >> N (operationally impossible load)."""
    return n_jobs <= n_nodes * RT_MAX_LOAD_RATIO


def _pa_is_valid(n_tenants: int, n_nodes: int) -> bool:
    """Skip combinations where there are not enough machines for all tenants."""
    return n_nodes >= n_tenants * PA_MIN_NODES_PER_TENANT


# ═══════════════════════════════════════════════════════════════════════════════
# § REAL-TIME TIMING
# ═══════════════════════════════════════════════════════════════════════════════

def _build_rt_nodes(n_nodes: int, n_jobs: int) -> list[NodeState]:
    """
    Build N nodes pre-loaded with background running jobs.

    Background load estimate: if jobs arrive at ~n_jobs / interval and each
    lives JOB_LIFETIME_S intervals, then n_jobs × JOB_LIFETIME_S jobs are
    concurrently running across all nodes in steady state.  We distribute
    this load uniformly and convert to used_mb.
    """
    rng = np.random.default_rng(SEED)
    avg_job_mem_mb = 5_120.0   # ~5 GB average job size

    # Steady-state concurrent jobs across the whole cluster
    avg_lifetime = (JOB_LIFETIME_MIN_S + JOB_LIFETIME_MAX_S) / 2.0
    concurrent   = n_jobs * avg_lifetime
    per_node_mem = (concurrent * avg_job_mem_mb) / max(1, n_nodes)

    nodes = []
    for i in range(n_nodes):
        cap = 65_536.0
        tax = round(cap * 0.05 / 1024) * 1024
        # Add jitter so each node has slightly different load
        used = min(cap * 0.80, float(rng.normal(per_node_mem, per_node_mem * 0.15)))
        used = max(0.0, used)
        nodes.append(NodeState(
            node_id=i, capacity_mb=cap, os_tax_mb=tax, cpu_cores=8.0,
            used_mb=used, threshold_frac=0.10,
        ))
    return nodes


def _build_rt_jobs(n_jobs: int) -> list[Job]:
    rng = np.random.default_rng(SEED + 1)
    now = datetime.now(timezone.utc)
    n_tenants = max(2, n_jobs // 6)
    jobs = []
    for i in range(n_jobs):
        mem = float(np.clip(rng.normal(5_120, 2_048), 512, 32_768))
        cpu = float(rng.uniform(0.5, 4.0))
        jobs.append(Job(
            job_id=f"j{i}", tenant_id=int(rng.integers(0, n_tenants)),
            req_mem_mb=round(mem, 1), req_cpu=round(cpu, 3),
            pred_mem_mb=round(mem * float(rng.uniform(0.85, 1.0)), 1),
            pred_cpu_p95=round(cpu * float(rng.uniform(0.85, 1.0)), 3),
            arrival_round=0, arrival_timestamp=now,
        ))
    return jobs


def time_rt(n_jobs: int, n_nodes: int, solver_name: str) -> RTResult:
    """
    Time one (J, N, solver) combination using an integer MILP backend.

    Each call is independent — safe to run concurrently across all (J, N, solver)
    triples.  solver_name must be one of RT_SOLVERS (CBC, SCIP, GUROBI).
    """
    nodes  = _build_rt_nodes(n_nodes, n_jobs)
    jobs   = _build_rt_jobs(n_jobs)
    W_t    = {j.tenant_id: 0.0 for j in jobs}
    cap_ms = int(SOLVER_CAP_S * 1000)
    n_vars = n_jobs * n_nodes

    try:
        backend = get_backend(solver_name)
        data    = precompute(jobs, nodes, W_t, K_WINDOW)
        t0 = time.perf_counter()
        result = backend.solve(data, cap_ms)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
    except Exception as exc:
        _pr(f"RT  J={n_jobs:<5} N={n_nodes:<5} [{solver_name:<6}] ERR: {exc}")
        return RTResult(
            n_jobs=n_jobs, n_nodes=n_nodes, n_vars=n_vars,
            solver=solver_name, solve_ms=float(cap_ms), placed=0.0, capped=True,
        )

    placed = sum(1 for v in result.values() if v is not None)
    capped = elapsed_ms >= cap_ms * 0.95

    tag = "CAPPED" if capped else "done"
    _pr(f"RT  J={n_jobs:<5} N={n_nodes:<5} [{solver_name:<6}] → "
        f"{elapsed_ms:>10.1f} ms   placed={placed}/{n_jobs}  [{tag}]")

    return RTResult(
        n_jobs=n_jobs, n_nodes=n_nodes, n_vars=n_vars,
        solver=solver_name, solve_ms=elapsed_ms,
        placed=placed / max(1, n_jobs), capped=capped,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# § PLAN-AHEAD TIMING
# ═══════════════════════════════════════════════════════════════════════════════

def time_pa(n_tenants: int, n_nodes: int, n_periods: int) -> PAResult:
    n_always = max(1, int(n_nodes * PA_ALWAYS_FRAC))
    n_excl   = min(max(1, int(n_tenants * PA_EXCL_FRAC)), n_tenants - 1)

    # Scale node capacity so total supply ≥ total demand (feasibility guarantee).
    # Total demand = T × PA_USAGE_MAX; total supply = N × cap
    # Require: N × cap ≥ T × PA_USAGE_MAX × 1.3  (30% safety margin)
    min_cap = (n_tenants * PA_USAGE_MAX) / max(1, n_nodes) * 1.3
    cap     = max(PA_NODE_CAPACITY, min_cap)
    cap     = min(cap, 500.0)

    # Scale usage bounds so a single tenant's demand fits within one node
    usage_max = min(PA_USAGE_MAX, cap * 0.70)
    usage_min = min(PA_USAGE_MIN, usage_max * 0.50)

    P = build_synthetic_data(
        seed=SEED, n_tenants=n_tenants, n_nodes=n_nodes, n_intervals=n_periods,
        node_capacity=cap, n_always_available=n_always, n_exclusive=n_excl,
        tenant_usage_min=usage_min, tenant_usage_max=usage_max,
        sigma_frac=0.20, epsilon=0.10, min_machines_per_tenant=1,
    )

    env = make_gurobi_env()
    try:
        t_build = time.perf_counter()
        model, _ = build_model(P, env, use_socp=True)
        build_s = time.perf_counter() - t_build

        model.Params.LogToConsole = 0
        model.Params.OutputFlag   = 0
        model.Params.TimeLimit    = SOLVER_CAP_S
        model.Params.MIPGap       = PA_MIP_GAP
        model.Params.Threads      = max(1, (os.cpu_count() or 4) // 4)

        n_vars    = model.NumVars
        n_constrs = model.NumConstrs + model.NumQConstrs

        model.optimize()
        solve_s = model.Runtime

        sc = model.Status
        if sc == GRB.OPTIMAL:
            status, gap = "OPT", model.MIPGap
        elif sc == GRB.TIME_LIMIT:
            status = "TLim"
            try:    gap = model.MIPGap
            except: gap = None
        elif sc in (GRB.INFEASIBLE, GRB.INF_OR_UNBD):
            status, gap = "INF", None
        else:
            status, gap = f"S{sc}", None

        model.dispose()
        env.dispose()

    except Exception as exc:
        _pr(f"PA  T={n_tenants:<5} N={n_nodes:<5} ERR: {exc}")
        try: env.dispose()
        except: pass
        n_excl_est = min(max(1, int(n_tenants * PA_EXCL_FRAC)), n_tenants - 1)
        n_v = _pa_var_estimate(n_tenants, n_nodes, n_periods, n_excl_est)
        return PAResult(
            n_tenants=n_tenants, n_nodes=n_nodes, n_periods=n_periods,
            n_vars=n_v, n_constrs=0,
            build_s=0.0, solve_s=0.0, total_s=0.0,
            mip_gap=None, status="ERR",
        )

    total_s = build_s + solve_s
    labels  = {"OPT": "OPTIMAL", "TLim": "CAPPED", "INF": "INFEASIBLE"}
    _pr(f"PA  T={n_tenants:<5} N={n_nodes:<5} → "
        f"{total_s:>10.2f} s    vars={n_vars:,}  [{labels.get(status, status)}]")

    return PAResult(
        n_tenants=n_tenants, n_nodes=n_nodes, n_periods=n_periods,
        n_vars=n_vars, n_constrs=n_constrs,
        build_s=build_s, solve_s=solve_s, total_s=total_s,
        mip_gap=gap, status=status,
    )


def _pa_var_estimate(n_tenants: int, n_nodes: int, n_periods: int, n_excl: int) -> int:
    n_shared = max(0, n_tenants - n_excl)
    return (
        n_excl   * n_nodes * n_periods +
        n_shared * n_nodes * n_periods +
        n_tenants* n_nodes * n_periods +
        n_nodes  * n_periods           +
        n_nodes                        +
        n_nodes  * n_periods           +
        1                              +
        3 * n_nodes * n_periods
    )


# ═══════════════════════════════════════════════════════════════════════════════
# § POWER-LAW ESTIMATION FOR CAPPED RESULTS
# ═══════════════════════════════════════════════════════════════════════════════

def _fit_2d(pts: list[tuple[float, float, float]]) -> tuple | None:
    """Fit log(time) = a + b·log(x1) + c·log(x2).  Returns (a,b,c) or None."""
    if len(pts) < 3:
        return None
    X = np.array([[1.0, math.log(x1), math.log(x2)] for x1, x2, _ in pts])
    y = np.array([math.log(t) for _, _, t in pts])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    return float(coef[0]), float(coef[1]), float(coef[2])


def _extrapolate(x1: float, x2: float, model: tuple) -> float:
    a, b, c = model
    return math.exp(a + b * math.log(x1) + c * math.log(x2))


def _human(seconds: float) -> str:
    if seconds < 60:    return f"~{seconds:.0f}s"
    if seconds < 3600:  return f"~{seconds/60:.0f}m"
    return f"~{seconds/3600:.1f}h"


# ═══════════════════════════════════════════════════════════════════════════════
# § TABLE FORMATTING
# ═══════════════════════════════════════════════════════════════════════════════

def _hdr(title: str) -> None:
    print(f"\n{'═' * 84}")
    print(f"  {title}")
    print(f"{'═' * 84}")


def _subhdr(title: str) -> None:
    print(f"\n  {'─' * 68}")
    print(f"  {title}")
    print(f"  {'─' * 68}")


def _grid(corner: str, col_label: str,
          row_vals: list, col_vals: list, cells: list[list[str]],
          rlw: int = 9, cw: int = 16) -> None:
    hdr = f"  {(corner + ' \\ ' + col_label):<{rlw}}" + "".join(
        f"{str(c):>{cw}}" for c in col_vals
    )
    print(hdr)
    print("  " + "─" * (rlw + cw * len(col_vals)))
    for rv, row in zip(row_vals, cells):
        print(f"  {str(rv):<{rlw}}" + "".join(f"{str(v):>{cw}}" for v in row))


def _fmt_ms(ms: float) -> str:
    if ms < 1:       return f"{ms:.3f}ms"
    if ms < 1_000:   return f"{ms:.1f}ms"
    return f"{ms/1000:.2f}s"


def _fmt_s(s: float) -> str:
    if s < 0.001:  return f"{s:.4f}s"
    if s < 1:      return f"{s:.3f}s"
    if s < 10:     return f"{s:.2f}s"
    if s < 100:    return f"{s:.1f}s"
    return f"{s:.0f}s"


def _fmt_vars(n: int) -> str:
    if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
    if n >= 1_000:     return f"{n/1_000:.0f}k"
    return str(n)


def _cell_rt(r, rt_model) -> str:
    if r is None: return "SKIP"
    if not r.capped:
        return _fmt_ms(r.solve_ms)
    est = ""
    if rt_model:
        try:
            e_s = _extrapolate(r.n_jobs, r.n_nodes, rt_model) / 1000.0
            est = f" ({_human(e_s)})"
        except Exception:
            pass
    return f">cap{est}"


def _cell_pa(r, pa_model) -> str:
    if r is None:          return "SKIP"
    if r.status == "SKIP": return "SKIP"
    if r.status == "ERR":  return "ERR"
    if r.status == "INF":  return "INF"
    if r.status == "OPT":  return _fmt_s(r.total_s)
    est = ""
    if pa_model:
        try:
            e = _extrapolate(r.n_tenants, r.n_nodes, pa_model)
            est = f" ({_human(e)})"
        except Exception:
            pass
    return f">cap{est}"


def _fmt_gap(r) -> str:
    if r is None or r.status in ("SKIP", "ERR", "INF", "TLim"): return "—"
    if r.mip_gap is None: return "—"
    if r.mip_gap < 0.0001: return "<0.01%"
    return f"{r.mip_gap*100:.2f}%"


def _save_csv(filename: str, headers: list, rows: list) -> None:
    path = DATA_DIR / filename
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows([headers] + rows)
    print(f"\n  CSV → {path}")


# ═══════════════════════════════════════════════════════════════════════════════
# § INSIGHTS
# ═══════════════════════════════════════════════════════════════════════════════

def _insights_rt(rt_by_solver: dict[str, dict]) -> None:
    print(f"\n  Real-Time MILP — Solver Comparison Insights")
    print(f"  {'─'*60}")
    print(f"  All solvers produce certified integer x[j,n] ∈ {{0,1}} assignments.")

    for solver_name, rt in rt_by_solver.items():
        solved  = [r for r in rt.values() if r is not None and not r.capped]
        capped  = [r for r in rt.values() if r is not None and r.capped]
        print(f"\n  [{solver_name}]")
        print(f"    Solved within cap  : {len(solved)}")
        print(f"    Hit solver cap     : {len(capped)}")
        if solved:
            f = min(solved, key=lambda r: r.solve_ms)
            s = max(solved, key=lambda r: r.solve_ms)
            print(f"    Fastest : J={f.n_jobs}, N={f.n_nodes} → {_fmt_ms(f.solve_ms)}")
            print(f"    Slowest : J={s.n_jobs}, N={s.n_nodes} → {_fmt_ms(s.solve_ms)}")

    # Winner comparison — which solver is fastest per (J,N)?
    all_cells = set()
    for rt in rt_by_solver.values():
        all_cells |= {k for k, v in rt.items() if v is not None and not v.capped}

    if all_cells and len(rt_by_solver) > 1:
        wins: dict[str, int] = {s: 0 for s in rt_by_solver}
        for cell in all_cells:
            times = {
                s: rt_by_solver[s][cell].solve_ms
                for s in rt_by_solver
                if cell in rt_by_solver[s] and rt_by_solver[s][cell] is not None
                   and not rt_by_solver[s][cell].capped
            }
            if times:
                winner = min(times, key=times.get)
                wins[winner] += 1
        print(f"\n  Fastest solver per cell (of {len(all_cells)} cells where all solved):")
        for s, w in sorted(wins.items(), key=lambda x: -x[1]):
            print(f"    {s:<8}: {w} cells")

    print(f"\n  Note: In production each RT call handles one tenant group "
          f"(J=5–20, N=5–50).\n"
          f"  Those combinations are typically in the <500 ms range for all solvers.\n"
          f"  Capped cells still return a valid feasible placement — only the\n"
          f"  optimality certificate is cut short.")


def _insights_pa(pa: dict) -> None:
    all_r  = [r for r in pa.values() if r is not None and r.status != "SKIP"]
    opt    = [r for r in all_r if r.status == "OPT"]
    tlim   = [r for r in all_r if r.status == "TLim"]
    inf_   = [r for r in all_r if r.status == "INF"]
    skip   = sum(1 for r in pa.values() if r is None or r.status == "SKIP")

    print(f"\n  Plan-Ahead MISOCP — Insights")
    print(f"  {'─'*52}")
    print(f"  Optimal (within cap) : {len(opt)}")
    print(f"  Hit solver cap       : {len(tlim)}")
    print(f"  Infeasible           : {len(inf_)}")
    print(f"  Skipped (N < T)      : {skip}")

    if opt:
        f = min(opt, key=lambda r: r.total_s)
        s = max(opt, key=lambda r: r.total_s)
        print(f"\n  Fastest OPT : T={f.n_tenants}, N={f.n_nodes} "
              f"→ {_fmt_s(f.total_s)}  (vars={f.n_vars:,})")
        print(f"  Slowest OPT : T={s.n_tenants}, N={s.n_nodes} "
              f"→ {_fmt_s(s.total_s)}  (vars={s.n_vars:,})")

    if opt and tlim:
        L = max(opt,  key=lambda r: r.n_vars)
        S = min(tlim, key=lambda r: r.n_vars)
        print(f"\n  Practical feasibility boundary:")
        print(f"    Largest solved to OPT : vars={L.n_vars:,}  "
              f"(T={L.n_tenants}, N={L.n_nodes}, P={L.n_periods})")
        print(f"    Smallest at cap       : vars={S.n_vars:,}  "
              f"(T={S.n_tenants}, N={S.n_nodes}, P={S.n_periods})")
        print(f"    → Decomposition or warm-starting needed beyond this point.")

    print(f"\n  MISOCP variable count grows as O(T × N × P).")
    print(f"  u[i,h] — tenant memory demand — ranges "
          f"[{PA_USAGE_MIN}, {PA_USAGE_MAX}] capacity units per period")
    print(f"  (node capacity = {PA_NODE_CAPACITY} units; "
          f"in production units are MB and node capacity = node RAM).")


# ═══════════════════════════════════════════════════════════════════════════════
# § PLOTS
# ═══════════════════════════════════════════════════════════════════════════════

PLOT_DIR = DATA_DIR / "plots"


def _save_fig(fig, name: str) -> None:
    PLOT_DIR.mkdir(exist_ok=True)
    out = PLOT_DIR / name
    fig.savefig(out, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"  Plot → {out}")


# ── RT Plot 1: Per-solver heatmap — one PNG file per solver ───────────────────

def _plot_rt_heatmap_one(solver_name: str, rt: dict,
                          j_vals: list | None = None,
                          n_vals: list | None = None) -> None:
    """Save one heatmap PNG for a single RT solver."""
    J = j_vals if j_vals is not None else RT_JOBS_LIST
    N = n_vals if n_vals is not None else RT_NODES_LIST

    vals   = np.full((len(J), len(N)), np.nan)
    labels = [["" for _ in N] for _ in J]

    for ri, j in enumerate(J):
        for ci, n in enumerate(N):
            r = rt.get((j, n))
            if r is None:
                labels[ri][ci] = "SKIP"
            elif r.capped:
                labels[ri][ci] = ">cap"
            else:
                vals[ri, ci] = r.solve_ms
                ms = r.solve_ms
                labels[ri][ci] = f"{ms/1000:.1f}s" if ms >= 1000 else f"{ms:.0f}ms"

    fig, ax = plt.subplots(figsize=(max(8, len(N) * 1.4), max(4, len(J) * 0.9)))

    valid = vals[~np.isnan(vals)]
    if valid.size > 0:
        norm = mcolors.LogNorm(vmin=max(1, valid.min()), vmax=valid.max())
        im = ax.imshow(vals, aspect="auto", cmap="RdYlGn_r", norm=norm)
        cbar = fig.colorbar(im, ax=ax, pad=0.02)
        cbar.set_label("ms (log scale)", fontsize=9)

    for ri in range(len(J)):
        for ci in range(len(N)):
            txt = labels[ri][ci]
            color = "white" if (not np.isnan(vals[ri, ci]) and valid.size > 0
                                and vals[ri, ci] > valid.mean()) else "black"
            if "SKIP" in txt or ">cap" in txt:
                color = "#555555"
            ax.text(ci, ri, txt, ha="center", va="center", fontsize=9, color=color)

    ax.set_xticks(range(len(N)))
    ax.set_xticklabels([f"N={n}" for n in N], fontsize=9)
    ax.set_yticks(range(len(J)))
    ax.set_yticklabels([f"J={j}" for j in J], fontsize=9)
    ax.set_xlabel("Machines (N)", fontsize=11)
    ax.set_ylabel("Pending Jobs (J)", fontsize=11)
    # ax.set_title(f"Real-Time MILP — {solver_name}  |  Solve Time",
    #              fontsize=12, fontweight="bold")
    fig.tight_layout()
    _save_fig(fig, f"rt_heatmap_{solver_name}.png")


def _plot_rt_heatmap(rt_by_solver: dict[str, dict],
                     j_vals: list | None = None,
                     n_vals: list | None = None) -> None:
    if not _MPL:
        return
    for solver_name in [s for s in RT_SOLVERS if s in rt_by_solver]:
        _plot_rt_heatmap_one(solver_name, rt_by_solver[solver_name], j_vals, n_vals)
    # Also save unknown solvers not in RT_SOLVERS (e.g. from older CSV)
    for solver_name in rt_by_solver:
        if solver_name not in RT_SOLVERS:
            _plot_rt_heatmap_one(solver_name, rt_by_solver[solver_name], j_vals, n_vals)


# ── RT Plot 2: Log-log scaling — solve time vs variable count (all solvers) ────

def _plot_rt_scaling(rt_by_solver: dict[str, dict]) -> None:
    if not _MPL:
        return

    colors  = {"CBC": "#3b82f6", "SCIP": "#22c55e", "GUROBI": "#ef4444", "HIGHS": "#a855f7"}
    markers = {"CBC": "o",       "SCIP": "s",        "GUROBI": "^",       "HIGHS": "D"}

    fig, ax = plt.subplots(figsize=(10, 6))

    for solver_name, rt in rt_by_solver.items():
        pts = [(r.n_vars, r.solve_ms, r.n_jobs, r.n_nodes)
               for r in rt.values() if r and not r.capped and r.solve_ms > 0]
        if not pts:
            continue
        c = colors.get(solver_name, "#888888")
        m = markers.get(solver_name, "o")
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        ax.scatter(xs, ys, color=c, label=solver_name, s=90, marker=m,
                   zorder=5, edgecolors="white", linewidths=0.6)
        if len(pts) >= 3:
            log_x = np.log([p[0] for p in pts])
            log_y = np.log([p[1] for p in pts])
            b, a = np.polyfit(log_x, log_y, 1)
            x_line = np.logspace(np.log10(min(xs)), np.log10(max(xs)), 60)
            ax.plot(x_line, np.exp(a) * x_line ** b, "--", color=c,
                    alpha=0.45, lw=1.4, label=f"{solver_name} trend (slope={b:.2f})")
        for v, ms, j, n in pts:
            ax.annotate(f"J={j}\nN={n}", (v, ms),
                        textcoords="offset points", xytext=(5, 3),
                        fontsize=6.5, color=c, alpha=0.85)

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Variable count  J × N  (log scale)", fontsize=11)
    ax.set_ylabel("Solve time (ms, log scale)", fontsize=11)
    # ax.set_title("Real-Time MILP — Solve Time vs Variable Count  (integer solvers only)",
    #              fontsize=12, fontweight="bold")
    ax.legend(fontsize=9)
    ax.grid(True, which="both", alpha=0.2)
    fig.tight_layout()
    _save_fig(fig, "rt_scaling_vars_vs_time.png")


# ── PA Plot 3: Heatmap of solve time (T × N) ───────────────────────────────────

def _plot_pa_heatmap(pa: dict) -> None:
    if not _MPL:
        return

    T = PA_TENANTS_LIST
    N = PA_NODES_LIST
    vals   = np.full((len(T), len(N)), np.nan)
    labels = [["" for _ in N] for _ in T]

    for ri, t in enumerate(T):
        for ci, n in enumerate(N):
            r = pa.get((t, n))
            if r is None or r.status == "SKIP":
                labels[ri][ci] = "SKIP"
            elif r.status == "ERR":
                labels[ri][ci] = "OOM"
            elif r.status == "TLim":
                labels[ri][ci] = f">cap\n({_human(r.total_s)})"
            elif r.status == "OPT":
                vals[ri, ci] = r.total_s
                labels[ri][ci] = _fmt_s(r.total_s)
            else:
                labels[ri][ci] = r.status

    fig, ax = plt.subplots(figsize=(10, 5))
    valid = vals[~np.isnan(vals)]
    if valid.size > 0:
        norm = mcolors.LogNorm(vmin=max(0.001, valid.min()), vmax=valid.max())
        im = ax.imshow(vals, aspect="auto", cmap="RdYlGn_r", norm=norm)
        cbar = fig.colorbar(im, ax=ax, pad=0.02)
        cbar.set_label("Solve time (s, log scale)", fontsize=9)

    for ri in range(len(T)):
        for ci in range(len(N)):
            txt = labels[ri][ci]
            is_dark = not np.isnan(vals[ri, ci]) and valid.size > 0 and vals[ri, ci] > valid.mean()
            color = "white" if is_dark else "black"
            if txt in ("SKIP", "OOM") or ">cap" in txt:
                color = "#555555"
            ax.text(ci, ri, txt, ha="center", va="center", fontsize=8,
                    color=color, fontweight="bold" if txt not in ("SKIP", "OOM") else "normal")

    ax.set_xticks(range(len(N)))
    ax.set_xticklabels([f"N={n}" for n in N], fontsize=9)
    ax.set_yticks(range(len(T)))
    ax.set_yticklabels([f"T={t}" for t in T], fontsize=9)
    ax.set_xlabel("Machines (N)", fontsize=11)
    ax.set_ylabel("Tenants (T)", fontsize=11)
    # ax.set_title(f"Plan-Ahead MISOCP — Total Solve Time  (P={PA_FIXED_PERIODS} periods)",
    #              fontsize=12, fontweight="bold")
    fig.tight_layout()
    _save_fig(fig, "pa_heatmap_solve_time.png")


# ── PA Plot 4: Build time vs solve time breakdown (stacked bar) ────────────────

def _plot_pa_build_vs_solve(pa: dict) -> None:
    if not _MPL:
        return

    opt = sorted(
        [r for r in pa.values() if r and r.status == "OPT"],
        key=lambda r: r.total_s,
    )
    if not opt:
        return

    labels_bar = [f"T={r.n_tenants}\nN={r.n_nodes}" for r in opt]
    build_s    = [r.build_s for r in opt]
    solve_s    = [r.solve_s for r in opt]
    xs = range(len(opt))

    fig, ax = plt.subplots(figsize=(max(8, len(opt) * 0.9), 5))
    ax.bar(xs, build_s, label="Model build time", color="#3b82f6", alpha=0.85)
    ax.bar(xs, solve_s, bottom=build_s, label="Gurobi solve time", color="#ef4444", alpha=0.85)

    # Annotate total on each bar
    for i, r in enumerate(opt):
        ax.text(i, r.total_s + r.total_s * 0.03,
                _fmt_s(r.total_s), ha="center", va="bottom", fontsize=7.5, fontweight="bold")

    ax.set_xticks(list(xs))
    ax.set_xticklabels(labels_bar, fontsize=8)
    ax.set_ylabel("Time (s)", fontsize=11)
    # ax.set_title(f"Plan-Ahead MISOCP — Build vs Solve Time Breakdown  (P={PA_FIXED_PERIODS})",
    #              fontsize=12, fontweight="bold")
    ax.legend(fontsize=9)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    _save_fig(fig, "pa_build_vs_solve_breakdown.png")


# ── PA Plot 5: Variable count vs total solve time (log-log scatter) ────────────

def _plot_pa_vars_vs_time(pa: dict) -> None:
    if not _MPL:
        return

    groups = {
        "OPT":  ("#22c55e", "o", "Solved to OPT"),
        "TLim": ("#f59e0b", "^", "Hit time cap"),
        "ERR":  ("#ef4444", "x", "Out of memory"),
    }

    fig, ax = plt.subplots(figsize=(9, 5))

    for status, (color, marker, label) in groups.items():
        pts = [r for r in pa.values()
               if r and r.status == status and r.n_vars > 0]
        if not pts:
            continue
        xs = [r.n_vars for r in pts]
        # For ERR use n_vars as x, place them at the cap line for y
        ys = [r.total_s if r.total_s > 0 else SOLVER_CAP_S for r in pts]
        ec = "white" if marker != "x" else "none"
        ax.scatter(xs, ys, color=color, label=label, s=90, marker=marker,
                   zorder=5, edgecolors=ec, linewidths=0.8)
        for r, y in zip(pts, ys):
            ax.annotate(f"T={r.n_tenants}\nN={r.n_nodes}", (r.n_vars, y),
                        textcoords="offset points", xytext=(5, 3),
                        fontsize=6.5, color=color, alpha=0.85)

    # Cap reference line
    ax.axhline(SOLVER_CAP_S, color="#94a3b8", linestyle="--", lw=1.2,
               label=f"5-min cap ({SOLVER_CAP_S}s)")

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Variable count  (log scale)", fontsize=11)
    ax.set_ylabel("Total solve time (s, log scale)", fontsize=11)
    # ax.set_title(f"Plan-Ahead MISOCP — Variable Count vs Solve Time  (P={PA_FIXED_PERIODS})",
    #              fontsize=12, fontweight="bold")
    ax.legend(fontsize=9)
    ax.grid(True, which="both", alpha=0.2)
    fig.tight_layout()
    _save_fig(fig, "pa_vars_vs_solve_time.png")


def _generate_plots(rt_by_solver: dict[str, dict], pa: dict) -> None:
    if not _MPL:
        print("\n  [INFO] matplotlib not available — skipping plots")
        return
    print()
    _subhdr("Plots")
    _plot_rt_heatmap(rt_by_solver)
    _plot_rt_scaling(rt_by_solver)
    _plot_pa_heatmap(pa)
    _plot_pa_build_vs_solve(pa)
    _plot_pa_vars_vs_time(pa)


# ═══════════════════════════════════════════════════════════════════════════════
# § CSV LOADER  (for --graphs-only and generate_graphs.py)
# ═══════════════════════════════════════════════════════════════════════════════

def load_results_from_csv() -> tuple[dict, dict]:
    """
    Read rt_timing.csv and pa_timing_grid.csv and reconstruct result dicts.

    Returns
    -------
    (rt_by_solver, pa)
      rt_by_solver : dict[solver_name, dict[(j,n), RTResult]]
      pa           : dict[(t,n), PAResult]
    """
    rt_by_solver: dict[str, dict] = {}
    rt_path = DATA_DIR / "rt_timing.csv"
    if rt_path.exists():
        with open(rt_path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                s = row["solver"]
                j, n = int(row["n_jobs"]), int(row["n_nodes"])
                rt_by_solver.setdefault(s, {})[(j, n)] = RTResult(
                    n_jobs=j, n_nodes=n, n_vars=int(row["n_vars"]),
                    solver=s, solve_ms=float(row["solve_ms"]),
                    placed=float(row["placed_frac"]),
                    capped=row["capped"] == "Y",
                )
    else:
        print(f"  [WARN] {rt_path} not found — skipping RT graphs")

    pa: dict = {}
    pa_path = DATA_DIR / "pa_timing_grid.csv"
    if pa_path.exists():
        with open(pa_path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                t, n = int(row["n_tenants"]), int(row["n_nodes"])
                pa[(t, n)] = PAResult(
                    n_tenants=t, n_nodes=n, n_periods=int(row["n_periods"]),
                    n_vars=int(row["n_vars"]), n_constrs=int(row["n_constrs"]),
                    build_s=float(row["build_s"]), solve_s=float(row["solve_s"]),
                    total_s=float(row["total_s"]),
                    mip_gap=float(row["mip_gap"]) if row.get("mip_gap") else None,
                    status=row["status"],
                )
    else:
        print(f"  [WARN] {pa_path} not found — skipping PA graphs")

    return rt_by_solver, pa


# ═══════════════════════════════════════════════════════════════════════════════
# § MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def run_rt_small_oneshot(jobs_list=None, nodes_list=None, solver=None) -> None:
    """
    One-shot (single-shot MILP) timing on the small-node grid (J in RT_SMALL_JOBS,
    N in RT_SMALL_NODES). This is the non-iterative baseline for the job-bottleneck
    study; the iterative companion lives in computational_time_analysis_iterative.py.
    Runs sequentially for clean timings and writes rt_small_oneshot.csv.
    """
    jobs_list  = jobs_list  or RT_SMALL_JOBS
    nodes_list = nodes_list or RT_SMALL_NODES
    solver     = solver     or RT_SMALL_SOLVER
    _hdr(f"RT SMALL-NODE GRID — One-Shot Baseline ({solver})")
    print(f"  Grid : J={jobs_list}  N={nodes_list}")
    rows = []
    for j in jobs_list:
        for n in nodes_list:
            r = time_rt(j, n, solver)
            rows.append([r.n_jobs, r.n_nodes, r.solver, round(r.solve_ms, 3),
                         round(r.placed, 4), "Y" if r.capped else "N"])
    _save_csv("rt_small_oneshot.csv",
              ["n_jobs", "n_nodes", "solver", "solve_ms", "placed_frac", "capped"],
              rows)


def run_pa_small_oneshot(tenants_list=None, nodes_list=None, periods=None) -> None:
    """
    One-shot MISOCP timing on the small PA grid (T,N in PA_SMALL_*), NO skips — every
    cell is run. time_pa() scales node capacity to keep each cell feasible, so even
    N < T cells solve. Companion to the iterative small grid in the iterative file.
    """
    tenants_list = tenants_list or PA_SMALL_TENANTS
    nodes_list   = nodes_list   or PA_SMALL_NODES
    periods      = periods      or PA_FIXED_PERIODS
    _hdr("PA SMALL GRID — One-Shot MISOCP Baseline (no skips)")
    print(f"  Grid : T={tenants_list}  N={nodes_list}  P={periods}")
    rows = []
    for t in tenants_list:
        for n in nodes_list:
            r = time_pa(t, n, periods)
            rows.append([r.n_tenants, r.n_nodes, r.n_periods, r.n_vars,
                         round(r.build_s, 4), round(r.solve_s, 4), round(r.total_s, 4),
                         round(r.mip_gap, 6) if r.mip_gap is not None else "", r.status])
    _save_csv("pa_small_oneshot.csv",
              ["n_tenants", "n_nodes", "n_periods", "n_vars",
               "build_s", "solve_s", "total_s", "mip_gap", "status"], rows)


def main() -> None:
    global _T0
    _T0 = time.perf_counter()

    # ── Real-Time ──────────────────────────────────────────────────────────────

    rt_all_cells   = [(j, n) for j in RT_JOBS_LIST for n in RT_NODES_LIST]
    rt_valid_cells = [(j, n) for j, n in rt_all_cells if _rt_is_valid(j, n)]
    rt_skip_cells  = [(j, n) for j, n in rt_all_cells if not _rt_is_valid(j, n)]
    n_rt_threads   = len(rt_valid_cells) * len(RT_SOLVERS)

    _hdr("TABLE 1 — Real-Time MILP  (Integer Solver Comparison)")
    print(f"  Solvers : {RT_SOLVERS}  (all produce integer x[j,n] ∈ {{0,1}})")
    print(f"  Grid    : J = {RT_JOBS_LIST}")
    print(f"            N = {RT_NODES_LIST}")
    print(f"  Filter  : skip if J > N × {RT_MAX_LOAD_RATIO}  "
          f"({len(rt_skip_cells)} skipped, {len(rt_valid_cells)} valid cells)")
    print(f"  Cap     : {SOLVER_CAP_S} s = {SOLVER_CAP_S//60} min per combination")
    print(f"  Threads : {n_rt_threads}  (one per (J, N, solver) triple, all concurrent)")
    print(f"  Job lifetime : fixed {JOB_LIFETIME_MIN_S} s  "
          f"(used to estimate cluster background load)")
    print()
    print("  Progress (elapsed from start):")

    # Build all (J, N, solver) work items
    rt_work = [(j, n, s) for j, n in rt_valid_cells for s in RT_SOLVERS]

    # rt_by_solver[solver][(j,n)] = RTResult | None(SKIP)
    rt_by_solver: dict[str, dict] = {}
    for s in RT_SOLVERS:
        rt_by_solver[s] = {(j, n): None for j, n in rt_skip_cells}

    with ThreadPoolExecutor(max_workers=max(1, n_rt_threads)) as pool:
        futures = {pool.submit(time_rt, j, n, s): (j, n, s) for j, n, s in rt_work}
        for fut in as_completed(futures):
            j, n, s = futures[fut]
            rt_by_solver[s][(j, n)] = fut.result()

    # One solve-time table per solver
    for solver_name in RT_SOLVERS:
        rt = rt_by_solver[solver_name]
        rt_pts   = [(r.n_jobs, r.n_nodes, r.solve_ms)
                    for r in rt.values() if r is not None and not r.capped and r.solve_ms > 0]
        rt_model = _fit_2d(rt_pts)
        _subhdr(f"[{solver_name}]  Solve time per call  "
                f"(>cap = hit 5-min limit  |  SKIP = filtered out)")
        _grid(
            corner="J \\ N", col_label="Nodes",
            row_vals=RT_JOBS_LIST, col_vals=RT_NODES_LIST,
            cells=[[_cell_rt(rt.get((j, n)), rt_model) for n in RT_NODES_LIST]
                   for j in RT_JOBS_LIST],
        )

    # Variable count (same for all solvers — show once)
    rt_first = rt_by_solver[RT_SOLVERS[0]]
    _subhdr("Variable count  J × N  (binary x[j,n])  — same for all solvers")
    _grid(
        corner="J \\ N", col_label="Nodes",
        row_vals=RT_JOBS_LIST, col_vals=RT_NODES_LIST,
        cells=[[_fmt_vars(rt_first[(j,n)].n_vars) if rt_first.get((j,n)) else "—"
                for n in RT_NODES_LIST]
               for j in RT_JOBS_LIST],
    )

    # Fastest solver per cell
    _subhdr("Fastest solver per (J, N) cell")
    def _winner_cell(j, n):
        times = {
            s: rt_by_solver[s][(j,n)].solve_ms
            for s in RT_SOLVERS
            if rt_by_solver[s].get((j,n)) is not None
               and not rt_by_solver[s][(j,n)].capped
        }
        if not times:
            return "—"
        w = min(times, key=times.get)
        return f"{w}  {_fmt_ms(times[w])}"
    _grid(
        corner="J \\ N", col_label="Nodes",
        row_vals=RT_JOBS_LIST, col_vals=RT_NODES_LIST,
        cells=[[_winner_cell(j, n) for n in RT_NODES_LIST] for j in RT_JOBS_LIST],
        cw=22,
    )

    # Save CSV — one row per (J, N, solver)
    all_rt_rows = []
    for s, rt in rt_by_solver.items():
        for r in rt.values():
            if r is not None:
                all_rt_rows.append([
                    r.n_jobs, r.n_nodes, r.n_vars, r.solver,
                    round(r.solve_ms, 3), round(r.placed, 4), "Y" if r.capped else "N",
                ])
    _save_csv("rt_timing.csv",
              ["n_jobs", "n_nodes", "n_vars", "solver", "solve_ms", "placed_frac", "capped"],
              all_rt_rows)

    # ── Plan-Ahead ─────────────────────────────────────────────────────────────

    pa_all_cells   = [(t, n) for t in PA_TENANTS_LIST for n in PA_NODES_LIST]
    pa_valid_cells = [(t, n) for t, n in pa_all_cells if _pa_is_valid(t, n)]
    pa_skip_cells  = [(t, n) for t, n in pa_all_cells if not _pa_is_valid(t, n)]

    _hdr(f"TABLE 2 — Plan-Ahead MISOCP  (Gurobi)  ·  T × N  "
         f"(P = {PA_FIXED_PERIODS} periods)")
    print(f"  Grid    : T = {PA_TENANTS_LIST}")
    print(f"            N = {PA_NODES_LIST}")
    print(f"  Filter  : skip if N < T  "
          f"({len(pa_skip_cells)} skipped, {len(pa_valid_cells)} active)")
    print(f"  Cap     : {SOLVER_CAP_S} s = {SOLVER_CAP_S//60} min per combination")
    print(f"  Threads : {len(pa_valid_cells)}  (one per combination, all concurrent)")
    print(f"  u[i,h]  : tenant memory demand drawn from "
          f"[{PA_USAGE_MIN}, {PA_USAGE_MAX}] capacity units per period")
    print(f"            node capacity = {PA_NODE_CAPACITY} units  "
          f"(in production: node RAM in MB)")
    print()
    print("  Progress (elapsed from start):")

    pa: dict[tuple[int, int], PAResult | None] = {
        (t, n): PAResult(
            n_tenants=t, n_nodes=n, n_periods=PA_FIXED_PERIODS,
            n_vars=0, n_constrs=0, build_s=0.0, solve_s=0.0, total_s=0.0,
            mip_gap=None, status="SKIP",
        )
        for t, n in pa_skip_cells
    }

    with ThreadPoolExecutor(max_workers=max(1, len(pa_valid_cells))) as pool:
        futures = {
            pool.submit(time_pa, t, n, PA_FIXED_PERIODS): (t, n)
            for t, n in pa_valid_cells
        }
        for fut in as_completed(futures):
            t, n = futures[fut]
            pa[(t, n)] = fut.result()

    pa_pts   = [(r.n_tenants, r.n_nodes, r.total_s)
                for r in pa.values() if r is not None and r.status == "OPT" and r.total_s > 0]
    pa_model = _fit_2d(pa_pts)

    _subhdr(f"Total solve time  (P={PA_FIXED_PERIODS})    >cap = hit 5-min limit    SKIP = N < T")
    _grid(
        corner="T \\ N", col_label="Nodes",
        row_vals=PA_TENANTS_LIST, col_vals=PA_NODES_LIST,
        cells=[[_cell_pa(pa.get((t, n)), pa_model) for n in PA_NODES_LIST]
               for t in PA_TENANTS_LIST],
        rlw=9,
    )

    _subhdr(f"Variable count  (P={PA_FIXED_PERIODS})")
    _grid(
        corner="T \\ N", col_label="Nodes",
        row_vals=PA_TENANTS_LIST, col_vals=PA_NODES_LIST,
        cells=[[_fmt_vars(pa[(t,n)].n_vars) if pa.get((t,n)) and pa[(t,n)].status != "SKIP" else "—"
                for n in PA_NODES_LIST]
               for t in PA_TENANTS_LIST],
        rlw=9,
    )

    _subhdr(f"MIP gap at termination  (P={PA_FIXED_PERIODS})")
    _grid(
        corner="T \\ N", col_label="Nodes",
        row_vals=PA_TENANTS_LIST, col_vals=PA_NODES_LIST,
        cells=[[_fmt_gap(pa.get((t, n))) for n in PA_NODES_LIST]
               for t in PA_TENANTS_LIST],
        rlw=9,
    )

    _save_csv("pa_timing_grid.csv",
              ["n_tenants", "n_nodes", "n_periods", "n_vars", "n_constrs",
               "build_s", "solve_s", "total_s", "mip_gap", "status"],
              [[r.n_tenants, r.n_nodes, r.n_periods, r.n_vars, r.n_constrs,
                round(r.build_s, 4), round(r.solve_s, 4), round(r.total_s, 4),
                round(r.mip_gap, 6) if r.mip_gap is not None else "",
                r.status]
               for r in pa.values() if r is not None])

    # ── Small-grid one-shot baselines (job-bottleneck + small PA study) ─────────
    run_rt_small_oneshot()
    run_pa_small_oneshot()

    # ── Summary & Insights ─────────────────────────────────────────────────────

    wall = time.perf_counter() - _T0
    _hdr(f"SUMMARY  (total wall time: {wall:.1f} s  =  {wall/60:.1f} min)")
    _insights_rt(rt_by_solver)
    _insights_pa(pa)
    print(f"\n  CSV output: {DATA_DIR}")
    _generate_plots(rt_by_solver, pa)
    print()


if __name__ == "__main__":
    import argparse as _ap
    _parser = _ap.ArgumentParser(description="Computational time analysis")
    _parser.add_argument(
        "--graphs-only", action="store_true",
        help="Skip timing; regenerate all plots from saved rt_timing.csv / pa_timing_grid.csv",
    )
    _args = _parser.parse_args()

    if _args.graphs_only:
        print("  Regenerating plots from saved CSVs ...")
        _rt, _pa = load_results_from_csv()
        if not _rt and not _pa:
            print("  No saved data found. Run without --graphs-only first.")
        else:
            _generate_plots(_rt, _pa)
            print("  Done.")
    else:
        main()
