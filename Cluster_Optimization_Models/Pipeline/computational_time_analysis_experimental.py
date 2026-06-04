"""
Pipeline/computational_time_analysis_experimental.py
──────────────────────────────────────────────────────
Timing analysis for the two experimental iterative variants:

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

Run:
  cd Pipeline/
  python computational_time_analysis_experimental.py
  python computational_time_analysis_experimental.py --skip-pa
  python computational_time_analysis_experimental.py --skip-rt
  python computational_time_analysis_experimental.py --graphs-only
  python computational_time_analysis_experimental.py --tenants 64 128 256
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
RT_SOLVER     = "CBC"   # backend for each sub-MILP
RT_TIME_MS    = 10_000  # total time budget per (J,N) call (split across batches)

# Batch configurations to compare: (batch_jobs, batch_nodes)
RT_BATCH_CONFIGS = [(8, 8), (16, 16), (32, 32), (64, 64)]

# PA iterative tenant counts
PA_TENANT_COUNTS = [8, 32, 64, 128, 256, 512, 1024]
PA_SEED          = 42


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


@dataclass
class PAIterResult:
    n_tenants:    int
    iterations:   int
    satisfaction: float   # 0–1
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

    t0 = time.perf_counter()
    try:
        result = iter_solve(
            jobs, nodes, W_t,
            time_limit_ms=RT_TIME_MS,
            batch_jobs=bj, batch_nodes=bn,
            solver_id=RT_SOLVER,
        )
    except Exception as exc:
        _pr(f"RT-iter J={n_jobs:<5} N={n_nodes:<5} [{bj}×{bn}] ERR: {exc}")
        return RTIterResult(n_jobs=n_jobs, n_nodes=n_nodes,
                            batch_jobs=bj, batch_nodes=bn, solver=RT_SOLVER,
                            elapsed_ms=float(RT_TIME_MS), placed=0.0,
                            n_vars=n_jobs * n_nodes)

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    placed     = sum(1 for v in result.values() if v is not None) / max(1, n_jobs)
    _pr(f"RT-iter J={n_jobs:<5} N={n_nodes:<5} [{bj}×{bn}] "
        f"→ {elapsed_ms:>9.1f} ms  placed={placed:.1%}")
    return RTIterResult(n_jobs=n_jobs, n_nodes=n_nodes,
                        batch_jobs=bj, batch_nodes=bn, solver=RT_SOLVER,
                        elapsed_ms=elapsed_ms, placed=placed,
                        n_vars=n_jobs * n_nodes)


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
    vals   = np.full((len(j_vals), len(n_vals)), np.nan)
    labels = [["" for _ in n_vals] for _ in j_vals]

    for ri, j in enumerate(j_vals):
        for ci, n in enumerate(n_vals):
            r = results.get((j, n, bj, bn))
            if r is None:
                labels[ri][ci] = "SKIP"
            else:
                vals[ri, ci] = r.elapsed_ms
                ms = r.elapsed_ms
                labels[ri][ci] = f"{ms/1000:.1f}s" if ms >= 1000 else f"{ms:.0f}ms"

    fig, ax = plt.subplots(figsize=(max(8, len(n_vals) * 1.4), max(4, len(j_vals) * 0.9)))

    valid = vals[~np.isnan(vals)]
    if valid.size > 0:
        from matplotlib import colors as mcolors
        norm = mcolors.LogNorm(vmin=max(1, valid.min()), vmax=valid.max())
        im = ax.imshow(vals, aspect="auto", cmap="RdYlGn_r", norm=norm)
        cbar = fig.colorbar(im, ax=ax, pad=0.02)
        cbar.set_label("ms (log scale)", fontsize=9)

    for ri in range(len(j_vals)):
        for ci in range(len(n_vals)):
            txt = labels[ri][ci]
            color = "white" if (not np.isnan(vals[ri, ci]) and valid.size > 0
                                and vals[ri, ci] > valid.mean()) else "black"
            if txt == "SKIP":
                color = "#555555"
            ax.text(ci, ri, txt, ha="center", va="center", fontsize=9, color=color)

    ax.set_xticks(range(len(n_vals)))
    ax.set_xticklabels([f"N={n}" for n in n_vals], fontsize=9)
    ax.set_yticks(range(len(j_vals)))
    ax.set_yticklabels([f"J={j}" for j in j_vals], fontsize=9)
    ax.set_xlabel("Machines (N)", fontsize=11)
    ax.set_ylabel("Pending Jobs (J)", fontsize=11)
    ax.set_title(f"RT Iterative — Batch {bj}×{bn}  |  Elapsed Time (J × N grid)",
                 fontsize=12, fontweight="bold")
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
        ax.scatter(xs, ys, color=c, label=f"Batch {bj}×{bn}", s=80, marker=m,
                   zorder=5, edgecolors="white", linewidths=0.6)
        if len(pts) >= 3:
            lx = np.log([p[0] for p in pts])
            ly = np.log([p[1] for p in pts])
            b, a = np.polyfit(lx, ly, 1)
            x_line = np.logspace(np.log10(min(xs)), np.log10(max(xs)), 60)
            ax.plot(x_line, np.exp(a) * x_line ** b, "--", color=c,
                    alpha=0.45, lw=1.4, label=f"Batch {bj}×{bn} trend (slope={b:.2f})")

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Variable count  J × N  (log scale)", fontsize=11)
    ax.set_ylabel("Elapsed time (ms, log scale)", fontsize=11)
    ax.set_title("RT Iterative — Elapsed Time vs Variable Count  (all batch configs)",
                 fontsize=12, fontweight="bold")
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
    ax1.set_title("PA Iterative — Wall Time vs Tenant Count", fontsize=12, fontweight="bold")
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
    ax2.set_title("PA Iterative — Iterations & Satisfaction", fontsize=12, fontweight="bold")
    ax2.legend(loc="upper left", fontsize=9)
    ax2b.legend(loc="upper right", fontsize=9)
    ax2.grid(axis="y", alpha=0.25)

    fig.tight_layout()
    _save_fig(fig, "exp_pa_iter_bar.png")


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
    n_threads = len(work)
    print(f"  Batch configs : {batch_configs}")
    print(f"  Grid          : J={j_vals}   N={n_vals}")
    print(f"  Filter        : skip if J > N×{RT_MAX_LOAD}  ({len(skip_cells)} skipped)")
    print(f"  Solver        : {RT_SOLVER}  (integer MILP per sub-batch)")
    print(f"  Threads       : {n_threads}  (one per J×N×batch triple)")
    print()

    results: dict[tuple, RTIterResult] = {}
    with ThreadPoolExecutor(max_workers=max(1, n_threads)) as pool:
        futures = {pool.submit(time_rt_iter, j, n, bj, bn): (j, n, bj, bn)
                   for j, n, bj, bn in work}
        for fut in as_completed(futures):
            key = futures[fut]
            results[key] = fut.result()

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
               "elapsed_ms", "placed_frac", "n_vars"],
              [[r.n_jobs, r.n_nodes, r.batch_jobs, r.batch_nodes, r.solver,
                round(r.elapsed_ms, 3), round(r.placed, 4), r.n_vars]
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
    args = ap.parse_args()

    j_vals = RT_JOBS_LIST
    n_vals = RT_NODES_LIST
    batch_configs = RT_BATCH_CONFIGS

    if args.graphs_only:
        print("  Loading saved CSVs ...")
        rt_results = _load_rt_iter_csv()
        pa_results = _load_pa_iter_csv()
        if not rt_results and not pa_results:
            print("  No saved experimental data found. Run without --graphs-only first.")
            return
        if rt_results:
            j_vals = sorted({r.n_jobs  for r in rt_results.values()})
            n_vals = sorted({r.n_nodes for r in rt_results.values()})
            batch_configs = sorted({(r.batch_jobs, r.batch_nodes)
                                     for r in rt_results.values()})
        generate_plots(rt_results, pa_results, j_vals, n_vals, batch_configs)
        print("  Done.")
        return

    rt_results: dict   = {}
    pa_results: list   = []

    if not args.skip_rt:
        rt_results = run_rt(j_vals, n_vals, batch_configs)

    if not args.skip_pa:
        pa_results = run_pa(args.tenants)

    wall = time.perf_counter() - _T0
    print(f"\n  Total wall time: {wall:.1f}s")
    generate_plots(rt_results, pa_results, j_vals, n_vals, batch_configs)
    print()


if __name__ == "__main__":
    main()
