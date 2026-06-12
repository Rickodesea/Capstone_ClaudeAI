"""
Simulation/sim_runner.py
─────────────────────────────
Solver-agnostic simulation runner. Plugs either the single-shot or the
iterative real-time solver into the existing ClusterManager scheduling loop
without modifying any existing file.

How it works
────────────
cluster_manager.py binds `solve` at import time via:
    from optimizer_google_or import solve

Python resolves that name at *call* time from the module dict, so replacing
`cluster_manager.solve` before calling cm.run() is enough to swap the solver.
The original reference is restored in a finally-block after every run.

RT modes
  iterative    — optimizer_iterative.solve()   batch MILP loop (default)
  no-iterative — realtime_optimizer.solve()    single-shot MILP baseline

PA modes
  none      — all tenants compete for all nodes (no grouping)  (default)
  mock      — deterministic round-robin groups, no Gurobi needed
  gurobi    — full MISOCP (falls back to mock if Gurobi unavailable)

Usage
──────
  cd Simulation/
  python sim_runner.py                              # iterative RT, mock PA, GUROBI, 20 batches
  python sim_runner.py --rt no-iterative            # single-shot MILP baseline
  python sim_runner.py --pa none                    # no PA grouping
  python sim_runner.py --compare                    # both RT modes, side-by-side table
  python sim_runner.py --compare --batches 30 --solver SCIP

All flags
  --rt {iterative,no-iterative}   RT solver mode (default: iterative)
  --pa {none,mock,gurobi}     PA grouping mode (default: none)
  --batches N                 scheduling intervals to run (default: 20)
  --seed N                    RNG seed (default: 42)
  --solver SOLVER             integer backend: CBC, SCIP, HIGHS, GUROBI (default: GUROBI)
  --rt-batch-jobs N           jobs per sub-MILP — iterative RT only (default: 16)
  --rt-batch-nodes N          nodes per sub-MILP — iterative RT only (default: 16)
  --time-limit MS             per-call solver wall-clock limit ms (default: 10000)
  --jobs-per-round N          jobs generated per interval (default: simulation_data default)
  --csv PATH                  save per-batch stats to CSV (compare mode: two files)
  --quiet                     suppress ClusterManager per-batch output
  --compare                   run both RT modes with same seed, print comparison table
  --use-borg-config           load Prediction/borg_configuration.BORG_CONFIG values
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# ── Windows console UTF-8 ──────────────────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# ── Path setup ─────────────────────────────────────────────────────────────────
_ROOT      = Path(__file__).resolve().parent.parent   # Cluster_Optimization_Models/
_REALTIME  = _ROOT / "Realtime"
_PLANAHEAD = _ROOT / "PlanAhead"

for _p in [str(_REALTIME), str(_PLANAHEAD)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── Core imports ───────────────────────────────────────────────────────────────
import numpy as np

import cluster_manager as _cm
from cluster_manager import SimulationResult
import simulation_data as _sd
from simulation_data import NUM_NODES, NUM_TENANTS

import realtime_optimizer as _rt_reg
import optimizer_iterative as _rt_iter   # imports ok; stdout reconfigure already done above


# ══════════════════════════════════════════════════════════════════════════════
# § SOLVER FACTORIES — return a callable with the same signature as
#   cluster_manager.solve(jobs, nodes, W_t, K, time_limit_ms)
# ══════════════════════════════════════════════════════════════════════════════

def _no_iterative_solver(solver_id: str, time_limit_ms: int):
    """Single-shot MILP via realtime_optimizer, using the specified backend."""
    def _fn(jobs, nodes, W_t, K, time_limit_ms=time_limit_ms):
        return _rt_reg.solve(jobs, nodes, W_t, K, time_limit_ms, solver_id=solver_id)
    return _fn


def _iterative_solver(solver_id: str, batch_jobs: int, batch_nodes: int, time_limit_ms: int):
    """Batch MILP loop via optimizer_iterative, using the specified backend."""
    def _fn(jobs, nodes, W_t, K, time_limit_ms=time_limit_ms):
        return _rt_iter.solve(
            jobs, nodes, W_t, K,
            time_limit_ms = time_limit_ms,
            batch_jobs    = batch_jobs,
            batch_nodes   = batch_nodes,
            solver_id     = solver_id,
        )
    return _fn


# ══════════════════════════════════════════════════════════════════════════════
# § PLAN-AHEAD HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _mock_plan_output(num_nodes: int, num_tenants: int,
                      num_exclusive: int = 1, n_periods: int = 4) -> dict:
    """
    Deterministic round-robin grouping. num_exclusive tenants each get their
    own isolated group; remaining tenants share one group. All groups see all
    machines — the RT solver handles actual placement.
    """
    all_ids = list(range(num_nodes))
    excl    = list(range(min(num_exclusive, num_tenants)))
    shared  = list(range(len(excl), num_tenants))

    def _groups() -> list:
        g = [
            {"tenant_ids": [i], "machine_ids": all_ids, "exclusive": True}
            for i in excl
        ]
        if shared:
            g.append({"tenant_ids": shared, "machine_ids": all_ids, "exclusive": False})
        return g

    return {
        "intervals": [
            {"interval": h, "groups": _groups()}
            for h in range(n_periods)
        ]
    }


def _gurobi_plan_output(num_nodes: int, num_tenants: int, n_periods: int = 4) -> dict:
    """
    Full Gurobi MISOCP plan-ahead.
    Raises on any failure so callers can fall back to mock.
    """
    from plan_ahead_data import build_synthetic_data, make_gurobi_env
    from plan_ahead_optimizer import build_model, extract_plan_output
    from gurobipy import GRB

    P = build_synthetic_data(
        seed=42,
        n_tenants=num_tenants,
        n_nodes=num_nodes,
        n_intervals=n_periods,
    )
    env          = make_gurobi_env()
    model, vars_ = build_model(P, env, use_socp=True)
    model.Params.TimeLimit    = 30
    model.Params.MIPGap       = 0.05
    model.Params.LogToConsole = 0
    model.optimize()

    if model.Status not in (GRB.OPTIMAL, GRB.TIME_LIMIT, GRB.SUBOPTIMAL):
        raise RuntimeError(f"Gurobi status {model.Status} — no feasible solution")

    return extract_plan_output(vars_, P)


def _build_plan_output(
    pa_mode:     str,
    num_nodes:   int,
    num_tenants: int,
) -> dict | None:
    """Return the plan_output dict for ClusterManager, or None for no grouping."""
    if pa_mode == "none":
        return None  # ClusterManager falls back to all-tenants-on-all-nodes

    if pa_mode == "mock":
        return _mock_plan_output(num_nodes, num_tenants)

    if pa_mode == "gurobi":
        try:
            po = _gurobi_plan_output(num_nodes, num_tenants)
            print("  [PA] Gurobi MISOCP succeeded.")
            return po
        except Exception as exc:
            print(f"  [PA] Gurobi unavailable ({exc}); using mock grouping.")
            return _mock_plan_output(num_nodes, num_tenants)

    return None   # unknown mode → no grouping


# ══════════════════════════════════════════════════════════════════════════════
# § BORG CONFIG  (load Prediction/borg_configuration.BORG_CONFIG into the run)
# ══════════════════════════════════════════════════════════════════════════════

def _load_borg_config() -> dict:
    """
    Import BORG_CONFIG and resolve every value the standalone runner needs,
    falling back to simulation_data defaults for keys BORG_CONFIG does not set
    (e.g. req_cpu range and mem_threshold_frac, which are intentionally left
    to the synthetic defaults).
    """
    _PRED = _ROOT / "Prediction"
    if str(_PRED) not in sys.path:
        sys.path.insert(0, str(_PRED))
    from borg_configuration import BORG_CONFIG as _BC
    g = _BC.get
    return {
        "total_nodes":        int(g("total_nodes", NUM_NODES)),
        "num_tenants":        int(g("num_tenants", NUM_TENANTS)),
        "node_mem_min_gb":    float(g("node_mem_min_gb", _sd.NODE_MEM_MIN_MB / 1024.0)),
        "node_mem_max_gb":    float(g("node_mem_max_gb", _sd.NODE_MEM_MAX_MB / 1024.0)),
        "node_cpu_min":       float(g("node_cpu_min", _sd.NODE_CPU_MIN)),
        "node_cpu_max":       float(g("node_cpu_max", _sd.NODE_CPU_MAX)),
        "mem_threshold_frac": float(g("mem_threshold_frac", _sd.MEM_THRESHOLD_FRAC)),
        "req_mem_min_mb":     float(g("req_mem_min_mb", _sd.REQUEST_MEM_MIN_MB)),
        "req_mem_max_mb":     float(g("req_mem_max_mb", _sd.REQUEST_MEM_MAX_MB)),
        "req_cpu_min":        float(g("req_cpu_min", _sd.REQ_CPU_MIN)),
        "req_cpu_max":        float(g("req_cpu_max", _sd.REQ_CPU_MAX)),
        "request_per":        float(g("request_per", _sd.REQUEST_PER)),
        "jobs_per_round":     int(g("jobs_per_round", _sd.JOBS_PER_ROUND)),
        "min_lifetime_sec":   float(g("min_lifetime_sec", _sd.MIN_LIFETIME_SEC)),
        "max_lifetime_sec":   float(g("max_lifetime_sec", _sd.MAX_LIFETIME_SEC)),
        "spike_prob_pct":     float(g("spike_prob_pct", _sd.SPIKE_PROB * 100.0)),
    }


def _make_borg_generators(cfg: dict):
    """
    Build node/job generators bound to the borg config, reusing the Realtime
    simulation_data building blocks so the produced objects are the exact same
    Job / NodeState dataclasses the rest of the pipeline expects.
    """
    n     = cfg["total_nodes"]
    mems  = _sd._make_node_mems(n, cfg["node_mem_min_gb"] * 1024.0, cfg["node_mem_max_gb"] * 1024.0)
    taxes = [round(m * _sd.OS_TAX_FRAC / 1024.0) * 1024.0 for m in mems]
    cores = _sd._make_node_cpu(n, cfg["node_cpu_min"], cfg["node_cpu_max"])
    tfrac = cfg["mem_threshold_frac"]

    def gen_nodes(rng=None):
        return [
            _sd.NodeState(node_id=i, capacity_mb=mems[i], os_tax_mb=taxes[i],
                          cpu_cores=cores[i], used_mb=0.0, threshold_frac=tfrac)
            for i in range(n)
        ]

    def _trunc(rng, lo, hi):
        mean = (lo + hi) / 2.0
        std  = (hi - lo) / 6.0
        return float(np.clip(rng.normal(mean, std), lo, hi))

    def gen_jobs(round_num, num_jobs=None, num_tenants=None, rng=None):
        rng = rng or np.random.default_rng()
        nj  = num_jobs if num_jobs is not None else cfg["jobs_per_round"]
        out = []
        for i in range(nj):
            t   = int(rng.integers(0, cfg["num_tenants"]))
            req = _trunc(rng, cfg["req_mem_min_mb"], cfg["req_mem_max_mb"])
            cpu = _trunc(rng, cfg["req_cpu_min"], cfg["req_cpu_max"])
            pm  = _sd.simulate_max_mem(req, lower_frac=cfg["request_per"], rng=rng)
            pc  = _sd.simulate_p95_cpu(cpu, lower_frac=cfg["request_per"], rng=rng)
            out.append(_sd.Job(
                job_id=f"r{round_num}_j{i}", tenant_id=t,
                req_mem_mb=round(req, 2), req_cpu=round(cpu, 3),
                pred_mem_mb=round(pm, 2), pred_cpu_p95=round(pc, 3),
                arrival_round=round_num,
            ))
        return out

    return gen_nodes, gen_jobs


# ══════════════════════════════════════════════════════════════════════════════
# § SIMULATION RUNNER
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class SimRun:
    """Result of one simulation run, including wall-clock timing."""
    label:       str
    result:      SimulationResult
    wall_time_s: float


def run_simulation(
    rt_mode:        str      = "iterative",
    pa_mode:        str      = "none",
    num_batches:    int      = 20,
    seed:           int      = 42,
    solver:         str      = "GUROBI",
    batch_jobs:     int      = 16,
    batch_nodes:    int      = 16,
    time_limit_ms:  int      = 10_000,
    jobs_per_round: int | None = None,
    verbose:        bool     = True,
    csv_path:       str | None = None,
    borg_config:    dict | None = None,
) -> SimRun:
    """
    Run one simulation sequence and return a SimRun.

    The chosen RT solver is injected into cluster_manager's module namespace
    for the duration of the run, then the original is restored.

    Parameters
    ----------
    rt_mode        : "iterative" | "no-iterative"
    pa_mode        : "none" | "mock" | "gurobi"
    num_batches    : number of scheduling intervals
    seed           : RNG seed (same seed → identical job arrival sequence)
    solver         : CBC, SCIP, HIGHS, or GUROBI (backend for the RT MILP)
    batch_jobs     : iterative mode only — max jobs per sub-MILP
    batch_nodes    : iterative mode only — max nodes per sub-MILP
    time_limit_ms  : per-call solver wall-clock limit in ms
    jobs_per_round : override JOBS_PER_ROUND from simulation_data (None = default)
    verbose        : print per-batch ClusterManager table
    csv_path       : if provided, save per-batch stats here
    """
    # ── Build RT solver callable ───────────────────────────────────────────────
    if rt_mode == "iterative":
        rt_fn = _iterative_solver(solver, batch_jobs, batch_nodes, time_limit_ms)
    else:
        rt_fn = _no_iterative_solver(solver, time_limit_ms)

    # ── Resolve topology (borg config overrides the synthetic defaults) ────────
    use_borg = borg_config is not None
    if use_borg:
        eff_nodes   = borg_config["total_nodes"]
        eff_tenants = borg_config["num_tenants"]
        if jobs_per_round is None:
            jobs_per_round = borg_config["jobs_per_round"]
    else:
        eff_nodes, eff_tenants = NUM_NODES, NUM_TENANTS

    # ── Build plan-ahead output ────────────────────────────────────────────────
    plan_output = _build_plan_output(pa_mode, eff_nodes, eff_tenants)

    # ── Inject solver (+ borg generators/constants), run, restore ──────────────
    orig_solve  = _cm.solve
    _cm.solve   = rt_fn
    borg_saved  = None
    if use_borg:
        gen_nodes, gen_jobs = _make_borg_generators(borg_config)
        borg_saved = (
            _cm.generate_nodes, _cm.generate_jobs,
            _cm.MIN_LIFETIME_SEC, _cm.MAX_LIFETIME_SEC,
            _cm.NUM_NODES, _cm.NUM_TENANTS, _cm.SPIKE_PROB,
            _sd.NUM_NODES, _sd.NUM_TENANTS, _sd.SPIKE_PROB,
        )
        spike = borg_config["spike_prob_pct"] / 100.0
        _cm.generate_nodes   = gen_nodes
        _cm.generate_jobs    = gen_jobs
        _cm.MIN_LIFETIME_SEC = borg_config["min_lifetime_sec"]
        _cm.MAX_LIFETIME_SEC = borg_config["max_lifetime_sec"]
        _cm.NUM_NODES        = eff_nodes
        _cm.NUM_TENANTS      = eff_tenants
        _cm.SPIKE_PROB       = spike
        _sd.NUM_NODES        = eff_nodes
        _sd.NUM_TENANTS      = eff_tenants
        _sd.SPIKE_PROB       = spike
    t0 = time.perf_counter()
    try:
        cm = _cm.ClusterManager(
            seed           = seed,
            verbose        = verbose,
            jobs_per_round = jobs_per_round,
            log_file       = None,   # suppress log file in runner context
        )
        result = cm.run(num_batches=num_batches, plan_output=plan_output)
    finally:
        _cm.solve = orig_solve   # always restore, even on exception
        if borg_saved is not None:
            (_cm.generate_nodes, _cm.generate_jobs,
             _cm.MIN_LIFETIME_SEC, _cm.MAX_LIFETIME_SEC,
             _cm.NUM_NODES, _cm.NUM_TENANTS, _cm.SPIKE_PROB,
             _sd.NUM_NODES, _sd.NUM_TENANTS, _sd.SPIKE_PROB) = borg_saved
    wall_time = time.perf_counter() - t0

    label = f"RT={rt_mode}  PA={pa_mode}  solver={solver}" + ("  [borg]" if use_borg else "")

    if csv_path:
        _save_csv(result, csv_path, rt_mode, pa_mode)

    return SimRun(label=label, result=result, wall_time_s=wall_time)


# ══════════════════════════════════════════════════════════════════════════════
# § CSV EXPORT
# ══════════════════════════════════════════════════════════════════════════════

def _save_csv(result: SimulationResult, path: str, rt_mode: str, pa_mode: str) -> None:
    """Write one row per batch interval to CSV."""
    if not result.batch_results:
        return
    rows = [
        {
            "batch_id":       br.batch_id,
            "rt_mode":        rt_mode,
            "pa_mode":        pa_mode,
            "jobs_generated": br.jobs_generated,
            "jobs_placed":    br.jobs_placed,
            "queue_size":     br.queue_size_after,
            "violations":     br.node_violations,
            "spikes":         br.spike_count,
            "overflows":      br.physical_overflow_count,
            "expired":        br.jobs_expired,
            "avg_eff_pct":    round(br.avg_eff_mem_pct, 2),
            "avg_phys_pct":   round(br.avg_phys_mem_pct, 2),
        }
        for br in result.batch_results
    ]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"  Saved {len(rows)} batch rows → {path}")


# ══════════════════════════════════════════════════════════════════════════════
# § DISPLAY
# ══════════════════════════════════════════════════════════════════════════════

_W = 72   # table width


def _print_separator(char: str = "─") -> None:
    print(char * _W)


def _print_comparison(no_iterative: SimRun, iterative: SimRun, seed: int = 42) -> None:
    """Side-by-side table comparing no-iterative vs iterative RT runs."""
    ra, ri = no_iterative.result, iterative.result
    COL = 20  # column width for values

    def _row(label: str, va, vi, delta_str: str = "") -> None:
        print(f"  {label:<28}{str(va):>{COL}}{str(vi):>{COL}}{delta_str:>12}")

    def _pct_delta(a: float, b: float) -> str:
        d = b - a
        return f"{'+' if d >= 0 else ''}{d:+.1f}pp"

    def _int_delta(a: int, b: int) -> str:
        return f"{b - a:+d}"

    def _time_delta(a: float, b: float) -> str:
        return f"{b - a:+.2f}s"

    n = max(1, len(ra.batch_results))
    avg_eff_r  = sum(r.avg_eff_mem_pct  for r in ra.batch_results) / n
    avg_phys_r = sum(r.avg_phys_mem_pct for r in ra.batch_results) / n
    avg_eff_i  = sum(r.avg_eff_mem_pct  for r in ri.batch_results) / n
    avg_phys_i = sum(r.avg_phys_mem_pct for r in ri.batch_results) / n

    print()
    _print_separator("═")
    print(f"  RT Solver Comparison — {n} batches   seed={seed}")
    _print_separator("═")
    print(f"  {'Metric':<28}{'no-iterative':>{COL}}{'iterative':>{COL}}{'Δ (iter−base)':>12}")
    _print_separator()

    _row("Generated",    ra.total_generated,  ri.total_generated,
         _int_delta(ra.total_generated, ri.total_generated))
    _row("Placed",       ra.total_placed,     ri.total_placed,
         _int_delta(ra.total_placed, ri.total_placed))

    rate_r = f"{ra.placement_rate():.1%}"
    rate_i = f"{ri.placement_rate():.1%}"
    d_rate = ri.placement_rate() - ra.placement_rate()
    print(f"  {'Placement rate':<28}{rate_r:>{COL}}{rate_i:>{COL}}{d_rate:>+11.1%}")

    print(f"  {'Avg eff mem %':<28}{avg_eff_r:>{COL-1}.1f}%{avg_eff_i:>{COL-1}.1f}%"
          f"{_pct_delta(avg_eff_r, avg_eff_i):>12}")
    print(f"  {'Avg phys mem %':<28}{avg_phys_r:>{COL-1}.1f}%{avg_phys_i:>{COL-1}.1f}%"
          f"{_pct_delta(avg_phys_r, avg_phys_i):>12}")

    _row("SLA violations", ra.total_violations, ri.total_violations,
         _int_delta(ra.total_violations, ri.total_violations))
    _row("Memory spikes",  ra.total_spikes,     ri.total_spikes,
         _int_delta(ra.total_spikes, ri.total_spikes))
    _row("Physical overflows", ra.total_overflows, ri.total_overflows,
         _int_delta(ra.total_overflows, ri.total_overflows))
    _row("Expired jobs",   ra.total_expired,    ri.total_expired,
         _int_delta(ra.total_expired, ri.total_expired))
    _row("Final queue",    ra.final_queue_size, ri.final_queue_size,
         _int_delta(ra.final_queue_size, ri.final_queue_size))

    _print_separator()
    print(f"  {'Wall-clock time':<28}{no_iterative.wall_time_s:>{COL-1}.2f}s"
          f"{iterative.wall_time_s:>{COL-1}.2f}s"
          f"{_time_delta(no_iterative.wall_time_s, iterative.wall_time_s):>12}")
    _print_separator("═")
    print()


def _print_header(args) -> None:
    """Print a short run-config banner."""
    print()
    _print_separator("═")
    if args.compare:
        print(f"  Simulation — Comparing RT solvers")
    else:
        print(f"  Simulation — RT={args.rt}  PA={args.pa}")
    print(f"  Batches={args.batches}  Seed={args.seed}  "
          f"Solver={args.solver}  TimeLimit={args.time_limit}ms")
    if args.rt != "no-iterative" or args.compare:
        print(f"  RT-BatchJobs={args.rt_batch_jobs}  RT-BatchNodes={args.rt_batch_nodes}")
    _print_separator("═")
    print()


# ══════════════════════════════════════════════════════════════════════════════
# § ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Solver-agnostic simulation runner — compare RT iterative vs no-iterative",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--rt",  choices=["iterative", "no-iterative"], default="iterative",
                    help="RT solver mode (default: iterative)")
    ap.add_argument("--pa",  choices=["none", "mock", "gurobi"],  default="mock",
                    help="Plan-ahead grouping mode (default: mock)")
    ap.add_argument("--batches",        type=int, default=20,
                    help="Scheduling intervals to run (default: 20)")
    ap.add_argument("--seed",           type=int, default=42,
                    help="RNG seed — same seed → identical job arrivals (default: 42)")
    ap.add_argument("--solver",         default="GUROBI",
                    help="Integer backend: CBC, SCIP, HIGHS, GUROBI (default: GUROBI)")
    ap.add_argument("--rt-batch-jobs",  type=int, default=16,
                    help="Jobs per sub-MILP — iterative RT only (default: 16)")
    ap.add_argument("--rt-batch-nodes", type=int, default=16,
                    help="Nodes per sub-MILP — iterative RT only (default: 16)")
    ap.add_argument("--time-limit",     type=int, default=10_000,
                    help="Per-call solver wall-clock limit ms (default: 10000)")
    ap.add_argument("--jobs-per-round", type=int, default=None,
                    help="Override JOBS_PER_ROUND from simulation_data (default: None)")
    ap.add_argument("--csv",            default=None,
                    help="Save per-batch stats to CSV (compare mode: two files)")
    ap.add_argument("--quiet",          action="store_true",
                    help="Suppress ClusterManager per-batch output")
    ap.add_argument("--compare",        action="store_true",
                    help="Run both RT modes with the same seed and print comparison table")
    ap.add_argument("--use-borg-config", action="store_true",
                    help="Load Prediction/borg_configuration.BORG_CONFIG (topology + workload)")
    args = ap.parse_args()

    verbose = not args.quiet

    _print_header(args)

    borg_cfg = None
    if args.use_borg_config:
        borg_cfg = _load_borg_config()
        print("  ── Borg config applied ──")
        print(f"  Nodes={borg_cfg['total_nodes']}  Tenants={borg_cfg['num_tenants']}  "
              f"Jobs/round={borg_cfg['jobs_per_round']}")
        print(f"  ReqMem={borg_cfg['req_mem_min_mb']:.0f}-{borg_cfg['req_mem_max_mb']:.0f}MB  "
              f"Lifetime={borg_cfg['min_lifetime_sec']:.0f}-{borg_cfg['max_lifetime_sec']:.0f}s  "
              f"Spike={borg_cfg['spike_prob_pct']:.2f}%  RequestPer={borg_cfg['request_per']:.3f}")
        print()

    common = dict(
        pa_mode        = args.pa,
        num_batches    = args.batches,
        seed           = args.seed,
        solver         = args.solver,
        batch_jobs     = args.rt_batch_jobs,
        batch_nodes    = args.rt_batch_nodes,
        time_limit_ms  = args.time_limit,
        jobs_per_round = args.jobs_per_round,
        verbose        = verbose,
        borg_config    = borg_cfg,
    )

    if args.compare:
        print("  ── No-Iterative RT (single-shot MILP) ──")
        run_base = run_simulation(rt_mode="no-iterative", **common)

        print("\n  ── Iterative RT (batch MILP loop) ──")
        run_itr  = run_simulation(rt_mode="iterative",    **common)

        _print_comparison(run_base, run_itr, seed=args.seed)

        if args.csv:
            base = args.csv.removesuffix(".csv")
            _save_csv(run_base.result, f"{base}_no_iterative.csv", "no-iterative", args.pa)
            _save_csv(run_itr.result,  f"{base}_iterative.csv",    "iterative",    args.pa)
    else:
        run = run_simulation(
            rt_mode  = args.rt,
            csv_path = args.csv,
            **common,
        )
        print()
        print(str(run.result))
        print()
        _print_separator()
        print(f"  Wall-clock time : {run.wall_time_s:.2f}s")
        _print_separator()
        print()
