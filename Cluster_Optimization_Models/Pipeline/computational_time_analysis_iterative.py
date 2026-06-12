"""
Pipeline/computational_time_analysis_iterative.py
───────────────────────────────────────────────────
Timing analysis for the two iterative solver variants:

  TABLE 1 — RT Iterative   (Realtime/optimizer_iterative.py)
    Runs the batch placement loop across the same J×N grid as the main analysis.
    Compares elapsed time and placement rate vs single-shot MILP.
    Each (J, N, BATCH) triple runs in its own thread.

  TABLE 2 — PA Iterative   (PlanAhead/plan_ahead_iterative.py)
    Runs the greedy tenant-placement loop across a range of total-tenant counts.
    Reports iterations, satisfaction, node usage, and wall time.

Plots saved to  timing_data/plots/:
  exp_rt_iter_heatmap_<batch>.png  — one heatmap per batch config
  exp_rt_iter_scaling.png          — time vs variable count (all batches)
  exp_pa_iter_bar.png              — wall time vs tenant count
  exp_pa_iter_heatmap.png          — PA metrics (time/iters/satisfaction/nodes) vs tenant count
  pa_iter_heatmap_solve_time.png   — PA wall time on a tenants × machines grid
                                     (mirror of pa_heatmap_solve_time; same N < T skip)

Run:
  cd Pipeline/
  python computational_time_analysis_iterative.py
  python computational_time_analysis_iterative.py --skip-pa
  python computational_time_analysis_iterative.py --skip-rt
  python computational_time_analysis_iterative.py --graphs-only
  python computational_time_analysis_iterative.py --tenants 64 128 256
"""

from __future__ import annotations

import argparse
import csv
import io
import math
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
    _MPL = True
except ImportError:
    _MPL = False

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "Realtime"))
sys.path.insert(0, str(_ROOT / "PlanAhead"))

from simulation_data import Job, NodeState, K_WINDOW
from optimizer_iterative import solve as iter_solve, BATCH_JOBS, BATCH_NODES
from solver_backends import precompute, get_backend
from plan_ahead_iterative import run_iterative
import plan_ahead_iterative as _pai   # module handle to vary UNIT_NODES for the T×N sweep

DATA_DIR = Path(__file__).parent / "timing_data"
DATA_DIR.mkdir(exist_ok=True)
PLOT_DIR = DATA_DIR / "plots"

_PRINT_LOCK = threading.Lock()
_T0: float = 0.0


def _pr(*args, **kwargs) -> None:
    elapsed = time.perf_counter() - _T0
    with _PRINT_LOCK:
        print(f"  [{elapsed:7.1f}s]", *args, **kwargs)


# ═══════════════════════════════════════════════════════════════════════════════
# § CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

SEED = 42

# RT iterative grid — same as main analysis
RT_JOBS_LIST  = [16, 64, 256, 1024]
RT_NODES_LIST = [4, 16, 64, 256, 512, 1024]
RT_MAX_LOAD   = 4       # skip J > N × RT_MAX_LOAD
RT_SOLVER     = "GUROBI"   # backend for each sub-MILP (matches the production default)
# Each sub-MILP gets a FIXED solve budget (not total/n_batches). The old scheme
# divided one 10 s total across all batches, leaving ~500 ms per sub-MILP for every
# batch size — too little for a 32×32 (1 024-var) or 64×64 (4 096-var) sub-MILP to
# return any integer solution, so those batches placed nothing and tripped the
# stall guard (producing misleadingly fast "empty" cells). A fixed per-batch budget
# lets every batch size actually solve; bigger batches then show their true (larger)
# cost instead of quitting early.
RT_PER_BATCH_MS = 10_000   # solve budget PER sub-MILP
RT_ITER_CAP_S   = 5 * 60   # overall per-cell cap (matches the non-iterative grid);
                           # a cell whose work exceeds this is marked CAP in the heatmap

# Batch configurations to compare: (batch_jobs, batch_nodes)
RT_BATCH_CONFIGS = [(8, 8), (16, 16), (32, 32), (64, 64)]

# Chosen production default: a 16-job batch. Jobs (not nodes) drive solve time, so the
# node dimension is kept equal but does not matter much; 16 places even 1024 jobs in
# ~1.5 s under Gurobi while keeping each sub-MILP trivial.
RT_DEFAULT_BATCH = (16, 16)

# RT small-node study — job-bottleneck test with FEW nodes (J small, N down to 1).
# Run BOTH the iterative solver and the one-shot baseline on this grid.
RT_SMALL_JOBS  = [8, 16]
RT_SMALL_NODES = [1, 2, 4, 8, 16, 32]

# PA iterative tenant counts
PA_TENANT_COUNTS = [8, 32, 64, 128, 256, 512, 1024]
PA_SEED          = 42

# PA iterative T × N sweep — mirrors the non-iterative pa_heatmap_solve_time grid,
# INCLUDING the same intentional skip of N < T cells: fewer machines than tenants
# is not a realistic production regime, so those cells are deliberately excluded
# (same as the MISOCP grid). N is the active node-pool size (UNIT_NODES) per run.
# The story on the realistic cells (N ≥ T) is that the MISOCP hit the 5-min cap
# where the iterative method still completes quickly.
PA_GRID_TENANTS  = [2, 8, 128, 256, 512]
PA_GRID_NODES    = [4, 16, 64, 256, 512, 1024]
PA_MIN_NODES_PER_TENANT = 1   # skip (T, N) if N < T × this — same filter as MISOCP grid

# PA small grid — fine-grained small T/N to probe how the iterative method behaves
# with FEWER nodes (the original grid fixed the node window at 64). Same N < T skip.
PA_SMALL_TENANTS = [8, 16, 32, 64]
PA_SMALL_NODES   = [8, 16, 32, 64]


# ═══════════════════════════════════════════════════════════════════════════════
# § DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class RTIterResult:
    n_jobs:     int
    n_nodes:    int
    batch_jobs: int
    batch_nodes: int
    solver:     str
    elapsed_ms: float
    placed:     float   # fraction placed
    n_vars:     int     # J × N (same as single-shot)
    iterations: int = 0 # number of sub-MILP solves performed


@dataclass
class PAIterResult:
    n_tenants:    int
    iterations:   int
    satisfaction: float   # 0–1
    nodes_used:   int
    elapsed_s:    float


@dataclass
class PAIterGridResult:
    """One (tenants, machines) cell of the PA-iterative T × N wall-time sweep."""
    n_tenants:    int
    n_nodes:      int      # active node-pool size (UNIT_NODES) for this run
    iterations:   int
    satisfaction: float    # 0–1
    nodes_used:   int
    elapsed_s:    float


# ═══════════════════════════════════════════════════════════════════════════════
# § TEST DATA BUILDERS  (mirror computational_time_analysis.py)
# ═══════════════════════════════════════════════════════════════════════════════

def _build_rt_nodes(n_nodes: int, n_jobs: int) -> list[NodeState]:
    rng = np.random.default_rng(SEED)
    avg_job_mem = 5_120.0
    concurrent  = n_jobs * 1.0
    per_node    = (concurrent * avg_job_mem) / max(1, n_nodes)
    nodes = []
    for i in range(n_nodes):
        cap  = 65_536.0
        tax  = round(cap * 0.05 / 1024) * 1024
        used = min(cap * 0.80, max(0.0, float(rng.normal(per_node, per_node * 0.15))))
        nodes.append(NodeState(
            node_id=i, capacity_mb=cap, os_tax_mb=tax,
            cpu_cores=8.0, used_mb=used, threshold_frac=0.10,
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


# ═══════════════════════════════════════════════════════════════════════════════
# § TIMING FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def time_rt_iter(n_jobs: int, n_nodes: int,
                 bj: int, bn: int) -> RTIterResult:
    nodes = _build_rt_nodes(n_nodes, n_jobs)
    jobs  = _build_rt_jobs(n_jobs)
    W_t   = {j.tenant_id: 0.0 for j in jobs}

    # Fixed per-sub-MILP budget: pass total = per_batch × n_batches so the optimizer's
    # internal split (max(500, total // n_batches)) lands on RT_PER_BATCH_MS for every
    # batch size. Bound the total at the overall cap so a cell cannot run unbounded.
    n_batches_est = -(-n_jobs // bj)   # ceil(n_jobs / bj)
    tlim_ms = min(RT_PER_BATCH_MS * n_batches_est, RT_ITER_CAP_S * 1000)

    stats: dict = {}
    t0 = time.perf_counter()
    try:
        result = iter_solve(
            jobs, nodes, W_t,
            time_limit_ms=tlim_ms,
            batch_jobs=bj, batch_nodes=bn,
            solver_id=RT_SOLVER,
            stats=stats,
        )
    except Exception as exc:
        _pr(f"RT-iter J={n_jobs:<5} N={n_nodes:<5} [{bj}×{bn}] ERR: {exc}")
        return RTIterResult(n_jobs=n_jobs, n_nodes=n_nodes,
                            batch_jobs=bj, batch_nodes=bn, solver=RT_SOLVER,
                            elapsed_ms=float(RT_ITER_CAP_S * 1000), placed=0.0,
                            n_vars=n_jobs * n_nodes, iterations=0)

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    placed     = sum(1 for v in result.values() if v is not None) / max(1, n_jobs)
    iters      = int(stats.get("iterations", 0))
    _pr(f"RT-iter J={n_jobs:<5} N={n_nodes:<5} [{bj}×{bn}] "
        f"→ {elapsed_ms:>9.1f} ms  placed={placed:.1%}  iters={iters}")
    return RTIterResult(n_jobs=n_jobs, n_nodes=n_nodes,
                        batch_jobs=bj, batch_nodes=bn, solver=RT_SOLVER,
                        elapsed_ms=elapsed_ms, placed=placed,
                        n_vars=n_jobs * n_nodes, iterations=iters)


def time_pa_iter(n_tenants: int) -> PAIterResult:
    t0 = time.perf_counter()
    try:
        stats, completed = run_iterative(
            total_tenants=n_tenants, seed=PA_SEED, verbose=False,
        )
    except Exception as exc:
        _pr(f"PA-iter T={n_tenants} ERR: {exc}")
        return PAIterResult(n_tenants=n_tenants, iterations=0,
                            satisfaction=0.0, nodes_used=0, elapsed_s=0.0)

    elapsed_s = time.perf_counter() - t0
    iters     = len(stats)
    nodes_used = stats[-1].active_nodes + sum(s.nodes_evicted for s in stats) if stats else 0
    satisfaction = (
        sum(c.satisfaction_frac() for c in completed) / max(1, len(completed))
        if completed else 0.0
    )
    _pr(f"PA-iter T={n_tenants:<5} → {elapsed_s:.4f}s  "
        f"iters={iters}  sat={satisfaction:.1%}  nodes={nodes_used}")
    return PAIterResult(n_tenants=n_tenants, iterations=iters,
                        satisfaction=satisfaction, nodes_used=nodes_used,
                        elapsed_s=elapsed_s)


def time_pa_iter_grid(n_tenants: int, n_nodes: int) -> PAIterGridResult:
    """
    Time one (tenants, machines) cell. The iterative runner reads its node-pool
    size from the module global UNIT_NODES, so we set it for the duration of this
    call and restore it afterwards. Runs are sequential (not threaded) because
    this global is shared.
    """
    old_units = _pai.UNIT_NODES
    _pai.UNIT_NODES = n_nodes
    try:
        t0 = time.perf_counter()
        stats, completed = _pai.run_iterative(
            total_tenants=n_tenants, seed=PA_SEED, verbose=False,
        )
        elapsed_s = time.perf_counter() - t0
    except Exception as exc:
        _pr(f"PA-grid T={n_tenants:<5} N={n_nodes:<5} ERR: {exc}")
        return PAIterGridResult(n_tenants=n_tenants, n_nodes=n_nodes,
                                iterations=0, satisfaction=0.0,
                                nodes_used=0, elapsed_s=0.0)
    finally:
        _pai.UNIT_NODES = old_units

    iters      = len(stats)
    nodes_used = (stats[-1].active_nodes + sum(s.nodes_evicted for s in stats)) if stats else 0
    satisfaction = (
        sum(c.satisfaction_frac() for c in completed) / max(1, len(completed))
        if completed else 0.0
    )
    _pr(f"PA-grid T={n_tenants:<5} N={n_nodes:<5} → {elapsed_s*1000:>9.1f} ms  "
        f"iters={iters}  sat={satisfaction:.1%}")
    return PAIterGridResult(n_tenants=n_tenants, n_nodes=n_nodes,
                            iterations=iters, satisfaction=satisfaction,
                            nodes_used=nodes_used, elapsed_s=elapsed_s)


# ═══════════════════════════════════════════════════════════════════════════════
# § PLOTS
# ═══════════════════════════════════════════════════════════════════════════════

def _save_fig(fig, name: str) -> None:
    PLOT_DIR.mkdir(exist_ok=True)
    out = PLOT_DIR / name
    fig.savefig(out, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"  Plot → {out}")


def _plot_rt_iter_heatmap(results: dict[tuple, RTIterResult],
                           bj: int, bn: int,
                           j_vals: list, n_vals: list) -> None:
    if not _MPL:
        return
    from matplotlib.patches import Rectangle

    cap_ms = RT_ITER_CAP_S * 1000.0
    vals   = np.full((len(j_vals), len(n_vals)), np.nan)   # only COMPLETED cells get a value
    labels = [["" for _ in n_vals] for _ in j_vals]
    state  = [["" for _ in n_vals] for _ in j_vals]        # "ok" | "skip" | "cap"

    for ri, j in enumerate(j_vals):
        for ci, n in enumerate(n_vals):
            r = results.get((j, n, bj, bn))
            if r is None:
                state[ri][ci]  = "skip"
                labels[ri][ci] = "SKIP"
                continue
            ms = r.elapsed_ms
            # A cell is CAPPED if it could not place every job, or its work ran to the
            # per-cell cap. Such a cell's elapsed time is meaningless (it quit early or
            # was cut off), so it is NOT coloured by time — it is flagged instead.
            if r.placed < 0.999 or ms >= cap_ms * 0.95:
                state[ri][ci]  = "cap"
                labels[ri][ci] = f"CAP\n{r.placed:.0%} placed\n({r.iterations} iter)"
            else:
                state[ri][ci]  = "ok"
                vals[ri, ci]   = ms
                t = f"{ms/1000:.1f}s" if ms >= 1000 else f"{ms:.0f}ms"
                labels[ri][ci] = f"{t}\n({r.iterations} iter)"

    fig, ax = plt.subplots(figsize=(max(8, len(n_vals) * 1.4), max(4, len(j_vals) * 0.9)))

    valid = vals[~np.isnan(vals)]
    if valid.size > 0:
        from matplotlib import colors as mcolors
        vmin = max(1, valid.min())
        vmax = max(vmin * 1.001, valid.max())
        norm = mcolors.LogNorm(vmin=vmin, vmax=vmax)
        im = ax.imshow(vals, aspect="auto", cmap="RdYlGn_r", norm=norm)
        cbar = fig.colorbar(im, ax=ax, pad=0.02)
        cbar.set_label("time to place 100% — ms (log scale)", fontsize=9)
        ax.set_xlim(-0.5, len(n_vals) - 0.5)
        ax.set_ylim(len(j_vals) - 0.5, -0.5)

    for ri in range(len(j_vals)):
        for ci in range(len(n_vals)):
            st = state[ri][ci]
            if st == "skip":
                ax.add_patch(Rectangle((ci - 0.5, ri - 0.5), 1, 1, facecolor="#dddddd", edgecolor="white"))
                txt_color = "#555555"
            elif st == "cap":
                ax.add_patch(Rectangle((ci - 0.5, ri - 0.5), 1, 1, facecolor="#7a0c0c", edgecolor="white"))
                txt_color = "white"
            else:
                txt_color = "white" if (vals[ri, ci] > valid.mean()) else "black"
            ax.text(ci, ri, labels[ri][ci], ha="center", va="center", fontsize=9, color=txt_color)

    ax.set_xticks(range(len(n_vals)))
    ax.set_xticklabels([f"N={n}" for n in n_vals], fontsize=9)
    ax.set_yticks(range(len(j_vals)))
    ax.set_yticklabels([f"J={j}" for j in j_vals], fontsize=9)
    ax.set_xlabel("Machines (N)", fontsize=11)
    ax.set_ylabel("Pending Jobs (J)", fontsize=11)
    # ax.set_title(f"RT Iterative — Batch size {bj}×{bn}  |  Elapsed Time",
    #              fontsize=12, fontweight="bold")
    fig.tight_layout()
    _save_fig(fig, f"exp_rt_iter_heatmap_{bj}x{bn}.png")


def _plot_rt_iter_scaling(results: dict, j_vals: list, n_vals: list) -> None:
    if not _MPL:
        return
    colors  = {(8,8): "#3b82f6", (16,16): "#22c55e",
                (32,32): "#ef4444", (64,64): "#a855f7"}
    markers = {(8,8): "o", (16,16): "s", (32,32): "^", (64,64): "D"}

    fig, ax = plt.subplots(figsize=(10, 6))

    for (bj, bn) in RT_BATCH_CONFIGS:
        pts = [
            (r.n_vars, r.elapsed_ms, r.n_jobs, r.n_nodes)
            for r in results.values()
            if r.batch_jobs == bj and r.batch_nodes == bn and r.elapsed_ms > 0
        ]
        if not pts:
            continue
        c = colors.get((bj, bn), "#888888")
        m = markers.get((bj, bn), "o")
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        ax.scatter(xs, ys, color=c, label=f"Batch size {bj}×{bn}", s=80, marker=m,
                   zorder=5, edgecolors="white", linewidths=0.6)
        if len(pts) >= 3:
            lx = np.log([p[0] for p in pts])
            ly = np.log([p[1] for p in pts])
            b, a = np.polyfit(lx, ly, 1)
            x_line = np.logspace(np.log10(min(xs)), np.log10(max(xs)), 60)
            ax.plot(x_line, np.exp(a) * x_line ** b, "--", color=c,
                    alpha=0.45, lw=1.4, label=f"Batch size {bj}×{bn} trend (slope={b:.2f})")

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Variable count  J × N  (log scale)", fontsize=11)
    ax.set_ylabel("Elapsed time (ms, log scale)", fontsize=11)
    # ax.set_title("RT Iterative — Elapsed Time vs Variable Count  (all batch sizes)",
    #              fontsize=12, fontweight="bold")
    ax.legend(fontsize=9)
    ax.grid(True, which="both", alpha=0.2)
    fig.tight_layout()
    _save_fig(fig, "exp_rt_iter_scaling.png")


def _plot_pa_iter_bar(pa_results: list[PAIterResult]) -> None:
    if not _MPL or not pa_results:
        return
    pa_results = sorted(pa_results, key=lambda r: r.n_tenants)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

    # Left: wall time
    xs    = [r.n_tenants for r in pa_results]
    times = [r.elapsed_s * 1000 for r in pa_results]
    ax1.bar(range(len(xs)), times, color="#3b82f6", alpha=0.85)
    for i, (t, r) in enumerate(zip(times, pa_results)):
        ax1.text(i, t * 1.03, f"{t:.1f}ms" if t < 1000 else f"{t/1000:.2f}s",
                 ha="center", fontsize=8, fontweight="bold")
    ax1.set_xticks(range(len(xs)))
    ax1.set_xticklabels([f"T={n}" for n in xs], fontsize=9)
    ax1.set_ylabel("Wall time (ms)", fontsize=11)
    # ax1.set_title("PA Iterative — Wall Time vs Tenant Count", fontsize=12, fontweight="bold")
    ax1.grid(axis="y", alpha=0.25)

    # Right: iterations and satisfaction
    iters = [r.iterations for r in pa_results]
    sats  = [r.satisfaction * 100 for r in pa_results]
    ax2b  = ax2.twinx()
    bars  = ax2.bar(range(len(xs)), iters, color="#22c55e", alpha=0.7, label="Iterations")
    line, = ax2b.plot(range(len(xs)), sats, "o--", color="#ef4444",
                       lw=2, markersize=7, label="Satisfaction %")
    ax2.set_xticks(range(len(xs)))
    ax2.set_xticklabels([f"T={n}" for n in xs], fontsize=9)
    ax2.set_ylabel("Iterations", fontsize=11)
    ax2b.set_ylabel("Satisfaction (%)", fontsize=11)
    ax2b.set_ylim(0, 110)
    # ax2.set_title("PA Iterative — Iterations & Satisfaction", fontsize=12, fontweight="bold")
    ax2.legend(loc="upper left", fontsize=9)
    ax2b.legend(loc="upper right", fontsize=9)
    ax2.grid(axis="y", alpha=0.25)

    fig.tight_layout()
    _save_fig(fig, "exp_pa_iter_bar.png")


def _plot_pa_iter_heatmap(pa_results: list[PAIterResult]) -> None:
    """
    Heatmap of PA-iterative metrics across tenant counts.

    PA iterative sweeps a single axis (tenant count), so the heatmap lays the
    four reported metrics as rows and tenant counts as columns. Each row is
    min-max normalised independently so the colour shows the trend within that
    metric: cost metrics (wall time, iterations, nodes used) use a red=high
    scale, satisfaction uses a green=high scale. The actual value is annotated
    in every cell (text colour auto-picked for contrast).
    """
    if not _MPL or not pa_results:
        return

    pa_results = sorted(pa_results, key=lambda r: r.n_tenants)
    tenants = [r.n_tenants for r in pa_results]

    # (label, values, formatter, higher_is_better)
    rows_spec = [
        ("Wall time",    [r.elapsed_s * 1000 for r in pa_results],
         lambda v: f"{v:.0f}ms" if v < 1000 else f"{v/1000:.2f}s", False),
        ("Iterations",   [float(r.iterations) for r in pa_results],
         lambda v: f"{v:.0f}", False),
        ("Satisfaction", [r.satisfaction * 100 for r in pa_results],
         lambda v: f"{v:.0f}%", True),
        ("Nodes used",   [float(r.nodes_used) for r in pa_results],
         lambda v: f"{v:.0f}", False),
    ]

    n_rows, n_cols = len(rows_spec), len(tenants)
    rgba = np.ones((n_rows, n_cols, 4))
    cmap_cost = plt.get_cmap("RdYlGn_r")   # red = high cost
    cmap_good = plt.get_cmap("RdYlGn")     # green = high satisfaction

    for ri, (_, vals, _, higher_better) in enumerate(rows_spec):
        arr = np.array(vals, dtype=float)
        lo, hi = float(arr.min()), float(arr.max())
        cmap = cmap_good if higher_better else cmap_cost
        for ci, v in enumerate(arr):
            t = 0.5 if hi == lo else (v - lo) / (hi - lo)
            rgba[ri, ci] = cmap(t)

    fig, ax = plt.subplots(figsize=(max(8, n_cols * 1.3), max(3.2, n_rows * 0.85)))
    ax.imshow(rgba, aspect="auto")

    for ri, (_, vals, fmt, _) in enumerate(rows_spec):
        for ci, v in enumerate(vals):
            r, g, b, _a = rgba[ri, ci]
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            ax.text(ci, ri, fmt(v), ha="center", va="center", fontsize=9,
                    color="white" if lum < 0.5 else "black")

    ax.set_xticks(range(n_cols))
    ax.set_xticklabels([f"T={t}" for t in tenants], fontsize=9)
    ax.set_yticks(range(n_rows))
    ax.set_yticklabels([s[0] for s in rows_spec], fontsize=10)
    ax.set_xlabel("Tenant count (T)", fontsize=11)
    # ax.set_title(f"PA Iterative — Metrics across Tenant Counts  "
    #              f"(window {_pai.UNIT_TENANTS} tenants × {_pai.UNIT_NODES} nodes)\n"
    #              f"(each row min-max normalised; red = high cost, green = high satisfaction)",
    #              fontsize=12, fontweight="bold")

    # White gridlines between cells
    ax.set_xticks(np.arange(-.5, n_cols, 1), minor=True)
    ax.set_yticks(np.arange(-.5, n_rows, 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=2)
    ax.tick_params(which="minor", length=0)

    fig.tight_layout()
    _save_fig(fig, "exp_pa_iter_heatmap.png")


def _plot_pa_iter_grid_heatmap(grid: dict, t_axis=None, n_axis=None,
                               fname: str = "pa_iter_heatmap_solve_time.png",
                               title: str | None = None) -> None:
    """
    Tenants × Machines wall-time heatmap for the PA-iterative method — the direct
    analogue of the non-iterative pa_heatmap_solve_time (rows = tenants T, cols =
    node-pool size N, colour = wall time on a log scale). Uses the same intentional
    N < T skip as the MISOCP grid (those cells are unrealistic in production). The
    comparison point: on the realistic cells (N ≥ T) the MISOCP hit the 5-min cap,
    whereas the iterative method completes here in milliseconds.
    """
    if not _MPL or not grid:
        return
    from matplotlib import colors as mcolors

    t_axis = t_axis if t_axis is not None else PA_GRID_TENANTS
    n_axis = n_axis if n_axis is not None else PA_GRID_NODES

    # Axes from the configured grid so skipped cells still render.
    T = [t for t in t_axis if any(k[0] == t for k in grid)] or \
        sorted({t for t, _ in grid})
    N = [n for n in n_axis if any(k[1] == n for k in grid)] or \
        sorted({n for _, n in grid})

    vals   = np.full((len(T), len(N)), np.nan)
    labels = [["" for _ in N] for _ in T]

    for ri, t in enumerate(T):
        for ci, n in enumerate(N):
            r = grid.get((t, n))
            if r is None:                       # intentional N < T skip
                labels[ri][ci] = "SKIP"
            else:
                s = r.elapsed_s
                vals[ri, ci] = s
                lbl = f"{s*1000:.0f}ms" if s < 1 else f"{s:.2f}s"
                labels[ri][ci] = f"{lbl}\n({r.iterations} iter)"

    fig, ax = plt.subplots(figsize=(max(8, len(N) * 1.3), max(4, len(T) * 0.9)))
    valid = vals[~np.isnan(vals)]
    if valid.size > 0:
        vmin = max(1e-4, float(valid.min()))
        vmax = max(float(valid.max()), vmin * 1.0001)
        norm = mcolors.LogNorm(vmin=vmin, vmax=vmax)
        im = ax.imshow(vals, aspect="auto", cmap="RdYlGn_r", norm=norm)
        cbar = fig.colorbar(im, ax=ax, pad=0.02)
        cbar.set_label("Wall time (s, log scale)", fontsize=9)

    for ri in range(len(T)):
        for ci in range(len(N)):
            txt = labels[ri][ci]
            if txt == "SKIP":
                color = "#555555"
            else:
                is_dark = (not np.isnan(vals[ri, ci]) and valid.size > 0
                           and vals[ri, ci] > valid.mean())
                color = "white" if is_dark else "black"
            ax.text(ci, ri, txt, ha="center", va="center", fontsize=8,
                    fontweight="bold" if txt != "SKIP" else "normal", color=color)

    ax.set_xticks(range(len(N)))
    ax.set_xticklabels([f"N={n}" for n in N], fontsize=9)
    ax.set_yticks(range(len(T)))
    ax.set_yticklabels([f"T={t}" for t in T], fontsize=9)
    ax.set_xlabel("Machines (N)", fontsize=11)
    ax.set_ylabel("Tenants (T)", fontsize=11)
    # ax.set_title(title or (f"Plan-Ahead Iterative — Total Wall Time  "
    #                        f"(P={_pai.N_PERIODS} periods, tenant window {_pai.UNIT_TENANTS})"),
    #              fontsize=12, fontweight="bold")
    fig.tight_layout()
    _save_fig(fig, fname)


# ═══════════════════════════════════════════════════════════════════════════════
# § CSV HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _save_csv(filename: str, headers: list, rows: list) -> None:
    path = DATA_DIR / filename
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows([headers] + rows)
    print(f"\n  CSV → {path}")


def _load_rt_iter_csv() -> dict:
    path = DATA_DIR / "rt_iter_timing.csv"
    results: dict[tuple, RTIterResult] = {}
    if path.exists():
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                j   = int(row["n_jobs"])
                n   = int(row["n_nodes"])
                bj  = int(row["batch_jobs"])
                bn  = int(row["batch_nodes"])
                results[(j, n, bj, bn)] = RTIterResult(
                    n_jobs=j, n_nodes=n, batch_jobs=bj, batch_nodes=bn,
                    solver=row["solver"], elapsed_ms=float(row["elapsed_ms"]),
                    placed=float(row["placed_frac"]), n_vars=int(row["n_vars"]),
                    iterations=int(row.get("iterations", 0) or 0),
                )
    return results


def _load_pa_iter_csv() -> list[PAIterResult]:
    path = DATA_DIR / "pa_iter_timing.csv"
    if not path.exists():
        return []
    results = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            results.append(PAIterResult(
                n_tenants=int(row["n_tenants"]),
                iterations=int(row["iterations"]),
                satisfaction=float(row["satisfaction"]),
                nodes_used=int(row["nodes_used"]),
                elapsed_s=float(row["elapsed_s"]),
            ))
    return results


def _load_pa_iter_grid_csv(fname: str = "pa_iter_grid.csv") -> dict:
    path = DATA_DIR / fname
    grid: dict[tuple, PAIterGridResult] = {}
    if path.exists():
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                t, n = int(row["n_tenants"]), int(row["n_nodes"])
                grid[(t, n)] = PAIterGridResult(
                    n_tenants=t, n_nodes=n,
                    iterations=int(row["iterations"]),
                    satisfaction=float(row["satisfaction"]),
                    nodes_used=int(row["nodes_used"]),
                    elapsed_s=float(row["elapsed_s"]),
                )
    return grid


# ═══════════════════════════════════════════════════════════════════════════════
# § MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def _hdr(title: str) -> None:
    print(f"\n{'═' * 72}")
    print(f"  {title}")
    print(f"{'═' * 72}")


def run_rt(j_vals: list[int], n_vals: list[int],
           batch_configs: list[tuple[int, int]]) -> dict:
    _hdr("TABLE 1 — RT Iterative  (Realtime/optimizer_iterative.py)")
    valid_cells = [(j, n) for j in j_vals for n in n_vals if j <= n * RT_MAX_LOAD]
    skip_cells  = [(j, n) for j in j_vals for n in n_vals if j > n * RT_MAX_LOAD]

    work = [(j, n, bj, bn) for j, n in valid_cells for bj, bn in batch_configs]
    print(f"  Batch configs : {batch_configs}")
    print(f"  Grid          : J={j_vals}   N={n_vals}")
    print(f"  Filter        : skip if J > N×{RT_MAX_LOAD}  ({len(skip_cells)} skipped)")
    print(f"  Solver        : {RT_SOLVER}  (integer MILP per sub-batch)")
    print(f"  Budget        : {RT_PER_BATCH_MS} ms per sub-MILP, cap {RT_ITER_CAP_S}s per cell")
    print(f"  Execution     : sequential (timing accuracy — larger sub-MILPs now take seconds)")
    print()

    # Sequential: with a fixed per-sub-MILP budget the larger batches take tens of
    # seconds, so running cells concurrently would let them contend for cores and
    # corrupt the measured wall times. One cell at a time keeps timings clean.
    results: dict[tuple, RTIterResult] = {}
    for j, n, bj, bn in work:
        results[(j, n, bj, bn)] = time_rt_iter(j, n, bj, bn)

    # Print one table per batch config
    for bj, bn in batch_configs:
        print(f"\n  Batch {bj}×{bn}  — elapsed time:")
        hdr = f"  {'J \\ N':<9}" + "".join(f"{f'N={n}':>14}" for n in n_vals)
        print(hdr)
        print("  " + "─" * (9 + 14 * len(n_vals)))
        for j in j_vals:
            row = f"  {f'J={j}':<9}"
            for n in n_vals:
                r = results.get((j, n, bj, bn))
                if r is None:
                    row += f"{'SKIP':>14}"
                else:
                    ms = r.elapsed_ms
                    s  = f"{ms/1000:.2f}s" if ms >= 1000 else f"{ms:.0f}ms"
                    row += f"{s:>14}"
            print(row)

    # Save CSV
    _save_csv("rt_iter_timing.csv",
              ["n_jobs", "n_nodes", "batch_jobs", "batch_nodes", "solver",
               "elapsed_ms", "placed_frac", "n_vars", "iterations"],
              [[r.n_jobs, r.n_nodes, r.batch_jobs, r.batch_nodes, r.solver,
                round(r.elapsed_ms, 3), round(r.placed, 4), r.n_vars, r.iterations]
               for r in results.values()])

    return results


def run_pa(tenant_counts: list[int]) -> list[PAIterResult]:
    _hdr("TABLE 2 — PA Iterative  (PlanAhead/plan_ahead_iterative.py)")
    print(f"  Tenant counts : {tenant_counts}")
    print(f"  Seed          : {PA_SEED}")
    print(f"  Unit          : 8 tenants × 64 nodes per iteration")
    print()

    pa_results: list[PAIterResult] = []
    for n_t in tenant_counts:
        pa_results.append(time_pa_iter(n_t))

    # Print summary table
    print(f"\n  {'Tenants':>8}  {'Iters':>6}  {'Satisfaction':>13}  {'Nodes used':>11}  {'Time':>10}")
    print("  " + "─" * 56)
    for r in pa_results:
        print(f"  {r.n_tenants:>8}  {r.iterations:>6}  "
              f"{r.satisfaction:>12.1%}  {r.nodes_used:>11}  "
              f"{r.elapsed_s*1000:>8.1f}ms" if r.elapsed_s < 1 else
              f"  {r.n_tenants:>8}  {r.iterations:>6}  "
              f"{r.satisfaction:>12.1%}  {r.nodes_used:>11}  "
              f"{r.elapsed_s:>9.3f}s")

    _save_csv("pa_iter_timing.csv",
              ["n_tenants", "iterations", "satisfaction", "nodes_used", "elapsed_s"],
              [[r.n_tenants, r.iterations, round(r.satisfaction, 6),
                r.nodes_used, round(r.elapsed_s, 6)]
               for r in pa_results])

    return pa_results


def run_pa_grid(tenants_list: list[int], nodes_list: list[int],
                csv_name: str = "pa_iter_grid.csv", apply_skip: bool = True) -> dict:
    _hdr("TABLE 3 — PA Iterative  ·  Tenants × Machines wall-time grid")
    if apply_skip:
        valid = [(t, n) for t in tenants_list for n in nodes_list
                 if n >= t * PA_MIN_NODES_PER_TENANT]
        skipped = [(t, n) for t in tenants_list for n in nodes_list
                   if n < t * PA_MIN_NODES_PER_TENANT]
    else:
        valid   = [(t, n) for t in tenants_list for n in nodes_list]
        skipped = []
    print(f"  Tenants : {tenants_list}")
    print(f"  Nodes   : {nodes_list}   (UNIT_NODES node-pool per run)")
    print(f"  Filter  : {'skip if N < T' if apply_skip else 'none (all cells run)'}  "
          f"({len(skipped)} skipped, {len(valid)} active)")
    print(f"  Runs    : sequential (UNIT_NODES is a shared module global)")
    print()

    grid: dict[tuple, PAIterGridResult | None] = {(t, n): None for t, n in skipped}
    for t, n in valid:
        grid[(t, n)] = time_pa_iter_grid(t, n)

    # Print table (rows = tenants, cols = nodes)
    print(f"\n  Wall time  (T rows × N cols)   SKIP = N < T:")
    hdr = f"  {'T \\ N':<9}" + "".join(f"{f'N={n}':>13}" for n in nodes_list)
    print(hdr)
    print("  " + "─" * (9 + 13 * len(nodes_list)))
    for t in tenants_list:
        row = f"  {f'T={t}':<9}"
        for n in nodes_list:
            r = grid.get((t, n))
            if r is None:
                cell = "SKIP"
            else:
                s = r.elapsed_s
                cell = f"{s*1000:.0f}ms" if s < 1 else f"{s:.2f}s"
            row += f"{cell:>13}"
        print(row)

    _save_csv(csv_name,
              ["n_tenants", "n_nodes", "iterations", "satisfaction",
               "nodes_used", "elapsed_s"],
              [[r.n_tenants, r.n_nodes, r.iterations, round(r.satisfaction, 6),
                r.nodes_used, round(r.elapsed_s, 6)]
               for r in grid.values() if r is not None])

    return grid


# ── RT small-node grid (job-bottleneck study) ───────────────────────────────────

RT_HEADTOHEAD_CELLS = [(16, 16), (64, 64), (256, 64), (256, 256),
                       (1024, 256), (1024, 512), (1024, 1024)]
PA_HEADTOHEAD_CELLS = [(8, 64), (8, 1024), (128, 256), (128, 512),
                       (256, 256), (256, 1024), (512, 512), (512, 1024)]


def run_rt_small_iter(jobs_list=None, nodes_list=None, batch=None) -> dict:
    """Iterative timing on the small-node grid (few nodes) at the default batch."""
    jobs_list  = jobs_list  or RT_SMALL_JOBS
    nodes_list = nodes_list or RT_SMALL_NODES
    bj, bn     = batch or RT_DEFAULT_BATCH
    _hdr(f"RT SMALL-NODE GRID — Iterative (batch {bj}×{bn}, {RT_SOLVER})")
    print(f"  Grid : J={jobs_list}  N={nodes_list}")
    rows, results = [], {}
    for j in jobs_list:
        for n in nodes_list:
            r = time_rt_iter(j, n, bj, bn)
            results[(j, n)] = r
            rows.append([r.n_jobs, r.n_nodes, r.batch_jobs, r.batch_nodes, r.solver,
                         round(r.elapsed_ms, 3), round(r.placed, 4), r.iterations])
    _save_csv("rt_small_iter.csv",
              ["n_jobs", "n_nodes", "batch_jobs", "batch_nodes", "solver",
               "elapsed_ms", "placed_frac", "iterations"], rows)
    return results


def _load_rt_small_iter_csv() -> dict:
    path = DATA_DIR / "rt_small_iter.csv"
    out: dict = {}
    if path.exists():
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                out[(int(row["n_jobs"]), int(row["n_nodes"]))] = {
                    "ms": float(row["elapsed_ms"]),
                    "placed": float(row["placed_frac"]),
                    "iters": int(row.get("iterations", 0) or 0),
                }
    return out


def _load_rt_small_oneshot_csv() -> dict:
    path = DATA_DIR / "rt_small_oneshot.csv"
    out: dict = {}
    if path.exists():
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                out[(int(row["n_jobs"]), int(row["n_nodes"]))] = {
                    "ms": float(row["solve_ms"]),
                    "placed": float(row["placed_frac"]),
                    "capped": row.get("capped", "N") == "Y",
                }
    return out


def _grouped_bar(cells, series, labels_x, title, fname, ylabel="Solve time (ms, log scale)",
                 annot=None, show_title=False, bigger_fonts=False) -> None:
    """Generic grouped bar chart. series = list of (label, values, color)."""
    if not _MPL or not cells:
        return
    # Slightly larger fonts for the report's head-to-head figures.
    fs_lab, fs_tick, fs_y, fs_title, fs_leg = (
        (9, 10, 12, 15, 11) if bigger_fonts else (7, 8, 10, 12, 9)
    )
    x = np.arange(len(cells))
    k = len(series)
    w = 0.8 / max(1, k)
    fig, ax = plt.subplots(figsize=(max(9, len(cells) * 0.95), 5.2))
    for i, (lab, vals, color) in enumerate(series):
        off = (i - (k - 1) / 2) * w
        bars = ax.bar(x + off, vals, w, label=lab, color=color)
        for xi, v in zip(x + off, vals):
            if v is not None and not (isinstance(v, float) and np.isnan(v)):
                ax.text(xi, v, _fmt_ms(v), ha="center", va="bottom", fontsize=fs_lab)
    ax.set_yscale("log")
    ax.set_xticks(x)
    ax.set_xticklabels(labels_x, fontsize=fs_tick)
    ax.set_ylabel(ylabel, fontsize=fs_y)
    if show_title:
        ax.set_title(title, fontsize=fs_title, fontweight="bold")
    ax.legend(fontsize=fs_leg)
    ax.grid(axis="y", which="both", alpha=0.25)
    if annot:
        annot(ax, x)
    fig.tight_layout()
    _save_fig(fig, fname)


def _fmt_ms(ms: float) -> str:
    if ms >= 1000:
        return f"{ms/1000:.1f}s"
    if ms >= 1:
        return f"{ms:.0f}ms"
    return f"{ms*1000:.0f}µs"


def _plot_rt_small_grid() -> None:
    """Bar chart: one-shot vs iterative on the RT small-node grid (visual-first)."""
    it  = _load_rt_small_iter_csv()
    osh = _load_rt_small_oneshot_csv()
    if not it and not osh:
        return
    cells   = sorted(set(it) | set(osh))
    labels  = [f"J={j}\nN={n}" for j, n in cells]
    series  = []
    if osh:
        series.append(("One-shot MILP", [osh.get(c, {}).get("ms", np.nan) for c in cells], "#c0504d"))
    series.append((f"Iterative {RT_DEFAULT_BATCH[0]}×{RT_DEFAULT_BATCH[1]}",
                   [it.get(c, {}).get("ms", np.nan) for c in cells], "#4f81bd"))
    _grouped_bar(cells, series, labels,
                 "RT Small-Node Grid — One-Shot vs Iterative  (J=8,16 · N=1..32)",
                 "rt_small_grid_bar.png")


def _plot_rt_headtohead_bar() -> None:
    """Bar chart mirroring the RT head-to-head table (one-shot Gurobi vs iterative)."""
    one = {}
    p = DATA_DIR / "rt_timing.csv"
    if p.exists():
        with open(p, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row["solver"].upper() == "GUROBI":
                    one[(int(row["n_jobs"]), int(row["n_nodes"]))] = float(row["solve_ms"])
    it = _load_rt_iter_csv()
    bj, bn = RT_DEFAULT_BATCH
    cells  = [c for c in RT_HEADTOHEAD_CELLS
              if c in one or (c[0], c[1], bj, bn) in it]
    if not cells:
        return
    labels = [f"J={j}\nN={n}" for j, n in cells]
    series = [
        ("One-shot", [one.get(c, np.nan) for c in cells], "#c0504d"),
        (f"Iterative ({bj}x{bn})",
         [it[(j, n, bj, bn)].elapsed_ms if (j, n, bj, bn) in it else np.nan
          for j, n in cells], "#4f81bd"),
    ]
    _grouped_bar(cells, series, labels,
                 "Real Time Solve Time for One-Shot vs Iterative using Gurobi solver",
                 "rt_headtohead_bar.png", show_title=True, bigger_fonts=True)


def _plot_pa_headtohead_bar() -> None:
    """Bar chart mirroring the PA head-to-head table (one-shot MISOCP vs greedy FFD)."""
    one = {}
    p = DATA_DIR / "pa_timing_grid.csv"
    if p.exists():
        with open(p, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row.get("status") != "SKIP" and row.get("total_s"):
                    one[(int(row["n_tenants"]), int(row["n_nodes"]))] = float(row["total_s"]) * 1000.0
    grid = _load_pa_iter_grid_csv()
    cells = [c for c in PA_HEADTOHEAD_CELLS if c in one or c in grid]
    if not cells:
        return
    labels = [f"T={t}\nN={n}" for t, n in cells]
    series = [
        ("One-shot", [one.get(c, np.nan) for c in cells], "#c0504d"),
        ("Iterative (using FFD)",
         [grid[c].elapsed_s * 1000.0 if c in grid else np.nan for c in cells], "#4f81bd"),
    ]
    _grouped_bar(cells, series, labels,
                 "Plan Ahead Solve Time for One-Shot vs Iterative",
                 "pa_headtohead_bar.png", show_title=True, bigger_fonts=True)


def _plot_rt_solver_performance() -> None:
    """Horizontal grouped bar: one-shot RT solve time by solver across problem sizes.

    Reads the one-shot benchmark (rt_timing.csv). Runs that hit the 5-minute wall
    limit are drawn as a uniform bar extending past the timed bars and labelled
    '(capped 5 min)'; completed runs are labelled with their time."""
    if not _MPL:
        return
    p = DATA_DIR / "rt_timing.csv"
    if not p.exists():
        return
    solvers = [("GUROBI", "Gurobi"), ("CBC", "CBC"), ("HIGHS", "HiGHS")]
    sizes   = [((16, 16),   "16×16",   "#4f81bd"),
               ((64, 64),   "64×64",   "#c0504d"),
               ((256, 256), "256×256", "#9bbb59")]
    data: dict = {}                       # (j, n, solver) -> (seconds, capped)
    with open(p, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            data[(int(row["n_jobs"]), int(row["n_nodes"]), row["solver"].upper())] = (
                float(row["solve_ms"]) / 1000.0, row.get("capped", "N") == "Y")

    timed = [data[(j, n, s)][0]
             for (j, n), _, _ in sizes for s, _ in solvers
             if (j, n, s) in data and not data[(j, n, s)][1]]
    max_timed = max(timed) if timed else 300.0
    cap_len   = max_timed * 1.7           # capped bars all extend to here

    n_grp = len(sizes)
    bh    = 0.8 / n_grp
    y     = np.arange(len(solvers))
    fig, ax = plt.subplots(figsize=(10, 5.6))
    ax.set_xscale("log")
    xmin, xmax = 0.8, cap_len * 1.2
    ax.set_xlim(xmin, xmax)
    lo, hi = np.log10(xmin), np.log10(xmax)

    def _label(x_end, yp, txt, capped):
        """Place label inside the bar (right edge) when it is long enough, else outside."""
        frac = (np.log10(max(x_end, xmin)) - lo) / (hi - lo)
        if capped or frac > 0.18:
            ax.text(x_end / 1.04, yp, txt, ha="right", va="center",
                    fontsize=9, fontweight="bold", color="white")
        else:
            ax.text(x_end * 1.08, yp, txt, ha="left", va="center",
                    fontsize=9, fontweight="bold", color="#222222")

    for i, ((j, n), _slab, color) in enumerate(sizes):
        off = ((n_grp - 1) / 2 - i) * bh          # first size drawn on top
        for gi, (skey, _sname) in enumerate(solvers):
            rec = data.get((j, n, skey))
            if rec is None:
                continue
            secs, capped = rec
            yp = y[gi] + off
            if capped:
                ax.barh(yp, cap_len, bh, color=color, alpha=0.5, hatch="///",
                        edgecolor=color, linewidth=1.0)
                _label(cap_len, yp, "(capped 5 min)", True)
            else:
                ax.barh(yp, secs, bh, color=color)
                _label(secs, yp, f"{secs:.1f}s", False)

    from matplotlib.patches import Patch
    handles = [Patch(facecolor=c, label=l) for _, l, c in sizes]
    ax.set_yticks(y)
    ax.set_yticklabels([name for _, name in solvers], fontsize=11)
    ax.invert_yaxis()                              # first solver on top
    ax.set_xlabel("Solve time (seconds, log scale)", fontsize=11)
    ax.set_ylabel("Solver", fontsize=11)
    ax.set_title("Real Time Solver Performance for One-shot optimization",
                 fontsize=15, fontweight="bold")
    ax.legend(handles=handles, fontsize=10, loc="upper right",
              bbox_to_anchor=(0.995, 0.86),
              title="Problem size (jobs × nodes)", title_fontsize=9)
    ax.grid(axis="x", which="both", alpha=0.25)
    fig.tight_layout()
    _save_fig(fig, "rt_solver_performance.png")


def _plot_rt_small_heatmap(data: dict, title: str, fname: str, kind: str = "iter") -> None:
    """RT small-node grid heatmap (J rows × N cols). No skips. kind: 'iter' | 'oneshot'."""
    if not _MPL or not data:
        return
    from matplotlib import colors as mcolors
    J, N = RT_SMALL_JOBS, RT_SMALL_NODES
    vals   = np.full((len(J), len(N)), np.nan)
    labels = [["" for _ in N] for _ in J]
    for ri, j in enumerate(J):
        for ci, n in enumerate(N):
            d = data.get((j, n))
            if d is None:
                labels[ri][ci] = "—"
                continue
            ms = d["ms"]
            vals[ri, ci] = ms
            t = f"{ms/1000:.1f}s" if ms >= 1000 else f"{ms:.0f}ms"
            lab = f"{t}\n{d['placed']:.0%} placed"
            if kind == "iter":
                lab += f"\n({d.get('iters', 0)} iter)"
            labels[ri][ci] = lab
    fig, ax = plt.subplots(figsize=(max(8, len(N) * 1.4), max(3, len(J) * 1.4)))
    valid = vals[~np.isnan(vals)]
    if valid.size:
        vmin = max(1, float(valid.min()))
        vmax = max(vmin * 1.001, float(valid.max()))
        im = ax.imshow(vals, aspect="auto", cmap="RdYlGn_r",
                       norm=mcolors.LogNorm(vmin=vmin, vmax=vmax))
        cbar = fig.colorbar(im, ax=ax, pad=0.02)
        cbar.set_label("solve time (ms, log scale)", fontsize=9)
    for ri in range(len(J)):
        for ci in range(len(N)):
            v = vals[ri, ci]
            color = "white" if (not np.isnan(v) and valid.size and v > valid.mean()) else "black"
            ax.text(ci, ri, labels[ri][ci], ha="center", va="center", fontsize=8, color=color)
    ax.set_xticks(range(len(N))); ax.set_xticklabels([f"N={n}" for n in N], fontsize=9)
    ax.set_yticks(range(len(J))); ax.set_yticklabels([f"J={j}" for j in J], fontsize=9)
    ax.set_xlabel("Machines (N)", fontsize=11)
    ax.set_ylabel("Pending Jobs (J)", fontsize=11)
    # ax.set_title(title, fontsize=12, fontweight="bold")
    fig.tight_layout()
    _save_fig(fig, fname)


def _load_pa_small_oneshot_csv() -> dict:
    path = DATA_DIR / "pa_small_oneshot.csv"
    out: dict = {}
    if path.exists():
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                out[(int(row["n_tenants"]), int(row["n_nodes"]))] = {
                    "total_s": float(row["total_s"]),
                    "status": row.get("status", ""),
                }
    return out


def _plot_pa_small_oneshot_heatmap() -> None:
    """PA small grid one-shot MISOCP wall-time heatmap (T rows × N cols). No skips."""
    data = _load_pa_small_oneshot_csv()
    if not _MPL or not data:
        return
    from matplotlib import colors as mcolors
    T, N = PA_SMALL_TENANTS, PA_SMALL_NODES
    vals   = np.full((len(T), len(N)), np.nan)
    labels = [["" for _ in N] for _ in T]
    for ri, t in enumerate(T):
        for ci, n in enumerate(N):
            d = data.get((t, n))
            if d is None:
                labels[ri][ci] = "—"
                continue
            s = d["total_s"]
            vals[ri, ci] = s
            tt = f"{s*1000:.0f}ms" if s < 1 else f"{s:.1f}s"
            labels[ri][ci] = f"{tt}\n{d['status']}"
    fig, ax = plt.subplots(figsize=(max(8, len(N) * 1.4), max(3, len(T) * 1.2)))
    valid = vals[~np.isnan(vals)]
    if valid.size:
        vmin = max(1e-3, float(valid.min()))
        vmax = max(vmin * 1.001, float(valid.max()))
        im = ax.imshow(vals, aspect="auto", cmap="RdYlGn_r",
                       norm=mcolors.LogNorm(vmin=vmin, vmax=vmax))
        cbar = fig.colorbar(im, ax=ax, pad=0.02)
        cbar.set_label("total wall time (s, log scale)", fontsize=9)
    for ri in range(len(T)):
        for ci in range(len(N)):
            v = vals[ri, ci]
            color = "white" if (not np.isnan(v) and valid.size and v > valid.mean()) else "black"
            ax.text(ci, ri, labels[ri][ci], ha="center", va="center", fontsize=8, color=color)
    ax.set_xticks(range(len(N))); ax.set_xticklabels([f"N={n}" for n in N], fontsize=9)
    ax.set_yticks(range(len(T))); ax.set_yticklabels([f"T={t}" for t in T], fontsize=9)
    ax.set_xlabel("Machines (N)", fontsize=11)
    ax.set_ylabel("Tenants (T)", fontsize=11)
    # ax.set_title("PA Small Grid — One-Shot MISOCP  |  Total Wall Time",
    #              fontsize=12, fontweight="bold")
    fig.tight_layout()
    _save_fig(fig, "pa_small_oneshot_heatmap.png")


def generate_plots(rt_results: dict, pa_results: list[PAIterResult],
                   j_vals: list, n_vals: list,
                   batch_configs: list[tuple]) -> None:
    if not _MPL:
        print("\n  matplotlib not available — skipping plots")
        return
    print()
    for bj, bn in batch_configs:
        _plot_rt_iter_heatmap(rt_results, bj, bn, j_vals, n_vals)
    _plot_rt_iter_scaling(rt_results, j_vals, n_vals)
    _plot_pa_iter_bar(pa_results)
    _plot_pa_iter_heatmap(pa_results)
    # Visual-first comparison bars + small-node grid (read their own CSVs; modular).
    _plot_rt_small_grid()
    _plot_rt_headtohead_bar()
    _plot_pa_headtohead_bar()
    _plot_rt_solver_performance()
    # RT small-node heatmaps (iterative + one-shot) and PA small one-shot heatmap.
    _plot_rt_small_heatmap(
        _load_rt_small_iter_csv(),
        f"RT Small-Node Grid — Iterative ({RT_DEFAULT_BATCH[0]}×{RT_DEFAULT_BATCH[1]})  |  Solve Time",
        "rt_small_iter_heatmap.png", kind="iter")
    _plot_rt_small_heatmap(
        _load_rt_small_oneshot_csv(),
        "RT Small-Node Grid — One-Shot MILP  |  Solve Time",
        "rt_small_oneshot_heatmap.png", kind="oneshot")
    _plot_pa_small_oneshot_heatmap()


def main() -> None:
    global _T0
    _T0 = time.perf_counter()

    ap = argparse.ArgumentParser(
        description="Experimental timing analysis — RT Iterative + PA Iterative")
    ap.add_argument("--skip-rt",     action="store_true", help="Skip RT iterative timing")
    ap.add_argument("--skip-pa",     action="store_true", help="Skip PA iterative timing")
    ap.add_argument("--graphs-only", action="store_true",
                    help="Load saved CSVs and regenerate plots only")
    ap.add_argument("--tenants",     type=int, nargs="+", default=PA_TENANT_COUNTS,
                    help=f"PA tenant counts (default: {PA_TENANT_COUNTS})")
    ap.add_argument("--rt-solver",   default=RT_SOLVER,
                    help=f"RT sub-MILP backend (default {RT_SOLVER})")
    ap.add_argument("--with-oneshot", action="store_true",
                    help="Also run the one-shot (non-iterative) baseline on the RT "
                         "small-node grid (imported from computational_time_analysis.py)")
    args = ap.parse_args()

    j_vals = RT_JOBS_LIST
    n_vals = RT_NODES_LIST
    batch_configs = RT_BATCH_CONFIGS

    if args.graphs_only:
        print("  Loading saved CSVs ...")
        rt_results   = _load_rt_iter_csv()
        pa_results   = _load_pa_iter_csv()
        pa_grid      = _load_pa_iter_grid_csv()
        pa_small     = _load_pa_iter_grid_csv("pa_small_grid.csv")
        if not rt_results and not pa_results and not pa_grid:
            print("  No saved experimental data found. Run without --graphs-only first.")
            return
        if rt_results:
            j_vals = sorted({r.n_jobs  for r in rt_results.values()})
            n_vals = sorted({r.n_nodes for r in rt_results.values()})
            batch_configs = sorted({(r.batch_jobs, r.batch_nodes)
                                     for r in rt_results.values()})
        generate_plots(rt_results, pa_results, j_vals, n_vals, batch_configs)
        _plot_pa_iter_grid_heatmap(pa_grid)
        _plot_pa_iter_grid_heatmap(
            pa_small, PA_SMALL_TENANTS, PA_SMALL_NODES, "pa_small_grid_heatmap.png",
            title=f"Plan-Ahead Iterative — Small Grid Wall Time  "
                  f"(P={_pai.N_PERIODS} periods, tenant window {_pai.UNIT_TENANTS})")
        print("  Done.")
        return

    rt_results: dict   = {}
    pa_results: list   = []
    pa_grid:    dict   = {}
    pa_small:   dict   = {}

    if not args.skip_rt:
        rt_results = run_rt(j_vals, n_vals, batch_configs)
        run_rt_small_iter()
        if args.with_oneshot:
            import computational_time_analysis as _cta
            _cta.run_rt_small_oneshot()

    if not args.skip_pa:
        pa_results = run_pa(args.tenants)
        pa_grid    = run_pa_grid(PA_GRID_TENANTS, PA_GRID_NODES)
        pa_small   = run_pa_grid(PA_SMALL_TENANTS, PA_SMALL_NODES, "pa_small_grid.csv",
                                 apply_skip=False)
        if args.with_oneshot:
            import computational_time_analysis as _cta
            _cta.run_pa_small_oneshot()

    wall = time.perf_counter() - _T0
    print(f"\n  Total wall time: {wall:.1f}s")
    generate_plots(rt_results, pa_results, j_vals, n_vals, batch_configs)
    _plot_pa_iter_grid_heatmap(pa_grid)
    _plot_pa_iter_grid_heatmap(
        pa_small, PA_SMALL_TENANTS, PA_SMALL_NODES, "pa_small_grid_heatmap.png",
        title=f"Plan-Ahead Iterative — Small Grid Wall Time  "
              f"(P={_pai.N_PERIODS} periods, tenant window {_pai.UNIT_TENANTS})")
    print()


if __name__ == "__main__":
    main()
