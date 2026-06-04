"""
sensitivity_analysis.py
-----------------------
Parameter sweep for the multi-tenant cluster scheduling simulation.

Sweep dimensions
----------------
  K (violation rolling window)   — controls how aggressively SLA violations
                                   shrink node effective capacity
  jobs_per_round (arrival load)  — controls queue pressure

Solver modes
------------
  --iterative (default)   Use optimizer_iterative.solve() — batch MILP, faster at scale.
  --no-iterative          Use realtime_optimizer.solve()  — single-shot MILP baseline.

Usage
-----
    cd Realtime/
    python sensitivity_analysis.py                           # full sweep (iterative default)
    python sensitivity_analysis.py --no-iterative            # single-shot MILP
    python sensitivity_analysis.py --batches 20 --seed 99
    python sensitivity_analysis.py --plot-only               # replot existing CSV
    python sensitivity_analysis.py --output my_results.csv
    python sensitivity_analysis.py --rt-batch-jobs 16 --rt-batch-nodes 16
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import sys
import time
from itertools import product
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# ── Headless matplotlib (works without a display) ──────────────────────────────
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _MATPLOTLIB_AVAILABLE = True
except ImportError:
    _MATPLOTLIB_AVAILABLE = False

# Allow running directly from optimization/ or from the project root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cluster_manager import ClusterManager

# ═══════════════════════════════════════════════════════════════════════════════
# § SWEEP SPACE
# ═══════════════════════════════════════════════════════════════════════════════

K_WINDOW_VALUES:       list[int] = [5, 10, 20, 30]    # violation rolling window
JOBS_PER_ROUND_VALUES: list[int] = [10, 25, 50, 100]  # arrival load per batch

DEFAULT_NUM_BATCHES: int = 10
DEFAULT_SEED:        int = 42
DEFAULT_OUTPUT:      str = "sensitivity_results.csv"
PLOT_DIR:            str = "sensitivity_plots"

CSV_FIELDS = [
    "k_window", "jobs_per_round", "num_nodes",
    "placement_rate",
    "total_generated", "total_placed", "final_queue",
    "total_violations", "total_spikes", "total_overflows",
    "avg_eff_mem_pct", "avg_phys_mem_pct",
    "avg_wait_sec", "wait_spread_sec",
    "total_solver_calls", "run_time_sec",
]


# ═══════════════════════════════════════════════════════════════════════════════
# § SINGLE RUN
# ═══════════════════════════════════════════════════════════════════════════════

def run_one(
    k_window:       int,
    jobs_per_round: int,
    num_batches:    int,
    seed:           int,
    iterative:      bool = True,
    batch_jobs:     int  = 32,
    batch_nodes:    int  = 32,
) -> dict[str, Any]:
    """Run one ClusterManager configuration and return a CSV-ready row."""
    import cluster_manager as _cm

    orig_solve = _cm.solve
    if iterative:
        import optimizer_iterative as _oi
        _bj, _bn = batch_jobs, batch_nodes
        _cm.solve = lambda jobs, nodes, W_t, K, time_limit_ms=10_000: _oi.solve(
            jobs, nodes, W_t, K, time_limit_ms,
            batch_jobs=_bj, batch_nodes=_bn,
        )

    t0 = time.perf_counter()
    try:
        cm = ClusterManager(
            seed           = seed,
            verbose        = False,
            k_window       = k_window,
            jobs_per_round = jobs_per_round,
            log_file       = None,
        )
        r = cm.run(num_batches)
        num_nodes = len(cm.nodes)
    finally:
        _cm.solve = orig_solve

    elapsed = time.perf_counter() - t0

    waits    = list(r.final_W_t.values()) if r.final_W_t else [0.0]
    avg_wait = sum(waits) / len(waits)
    spread   = max(waits) - min(waits)
    n_b      = max(1, len(r.batch_results))

    return {
        "k_window":      k_window,
        "jobs_per_round": jobs_per_round,
        "num_nodes":     num_nodes,
        "placement_rate": round(r.placement_rate(), 4),
        "total_generated":    r.total_generated,
        "total_placed":       r.total_placed,
        "final_queue":        r.final_queue_size,
        "total_violations":   r.total_violations,
        "total_spikes":       r.total_spikes,
        "total_overflows":    r.total_overflows,
        "avg_eff_mem_pct":    round(sum(b.avg_eff_mem_pct  for b in r.batch_results) / n_b, 2),
        "avg_phys_mem_pct":   round(sum(b.avg_phys_mem_pct for b in r.batch_results) / n_b, 2),
        "avg_wait_sec":       round(avg_wait, 2),
        "wait_spread_sec":    round(spread,   2),
        "total_solver_calls": sum(b.solver_calls for b in r.batch_results),
        "run_time_sec":       round(elapsed, 3),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# § SWEEP
# ═══════════════════════════════════════════════════════════════════════════════

def run_sweep(
    num_batches: int  = DEFAULT_NUM_BATCHES,
    seed:        int  = DEFAULT_SEED,
    output:      str  = DEFAULT_OUTPUT,
    iterative:   bool = True,
    batch_jobs:  int  = 32,
    batch_nodes: int  = 32,
) -> list[dict[str, Any]]:
    """Run all (k_window × jobs_per_round) combinations."""
    mode = f"iterative (batch={batch_jobs}×{batch_nodes})" if iterative else "regular"
    configs = list(product(K_WINDOW_VALUES, JOBS_PER_ROUND_VALUES))
    total   = len(configs)
    results: list[dict[str, Any]] = []

    print(f"RT solver mode : {mode}")
    print(f"{'#':>4}  {'K':>4} {'jobs':>5} {'nodes':>5}  {'place%':>7}  {'viols':>5}  {'t(s)':>6}")
    print("─" * 52)

    with open(output, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()

        for i, (k, jobs) in enumerate(configs, 1):
            row = run_one(k, jobs, num_batches, seed,
                          iterative=iterative, batch_jobs=batch_jobs, batch_nodes=batch_nodes)
            writer.writerow(row)
            f.flush()
            results.append(row)

            print(
                f"{i:>4}  {k:>4} {jobs:>5} {row['num_nodes']:>5}  "
                f"{row['placement_rate']:>6.1%}  {row['total_violations']:>5}  "
                f"{row['run_time_sec']:>6.2f}"
            )

    print("─" * 52)
    print(f"\n{total} configurations written to {output}\n")

    # ── Inline insights ───────────────────────────────────────────────────────
    if results:
        best_place = max(results, key=lambda r: r["placement_rate"])
        worst_viols = max(results, key=lambda r: r["total_violations"])
        high_load   = [r for r in results if r["jobs_per_round"] >= 50]
        low_k       = [r for r in results if r["k_window"] <= 5]

        print("INSIGHTS")
        print("─" * 52)
        print(f"  Best placement : K={best_place['k_window']}, "
              f"jobs={best_place['jobs_per_round']}, "
              f"rate={best_place['placement_rate']:.1%}, "
              f"nodes={int(best_place['num_nodes'])}")
        print(f"  Most violations: K={worst_viols['k_window']}, "
              f"jobs={worst_viols['jobs_per_round']}, "
              f"viols={worst_viols['total_violations']}")

        if high_load:
            avg_place_high = sum(r["placement_rate"] for r in high_load) / len(high_load)
            print(f"  At high load (jobs≥50): avg placement {avg_place_high:.1%}  "
                  f"— pipeline is capacity-limited above this threshold.")
        if low_k:
            avg_viols_low_k = sum(r["total_violations"] for r in low_k) / len(low_k)
            print(f"  Small K (≤5): avg violations {avg_viols_low_k:.0f}  "
                  f"— short window reacts fast but over-penalises capacity.")
        print(f"  Node count is fixed at {int(results[0]['num_nodes'])} in this sweep. "
              f"Vary total_nodes in the sim config to test scaling.")
        print("─" * 52 + "\n")

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# § PLOTTING
# ═══════════════════════════════════════════════════════════════════════════════

def _load_csv(path: str) -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            rows.append({k: float(v) for k, v in row.items()})
    return rows


def _mean_by(
    rows:    list[dict[str, float]],
    key_col: str,
    val_col: str,
) -> tuple[list[float], list[float]]:
    """Average val_col grouped by key_col values."""
    from collections import defaultdict
    groups: dict[float, list[float]] = defaultdict(list)
    for r in rows:
        groups[r[key_col]].append(r[val_col])
    keys = sorted(groups)
    vals = [sum(groups[k]) / len(groups[k]) for k in keys]
    return keys, vals


def plot_results(csv_path: str) -> None:
    """Read CSV and generate a 2x3 summary grid plus CPU-weight comparison."""
    if not _MATPLOTLIB_AVAILABLE:
        print("matplotlib not installed (pip install matplotlib) -- skipping plots.")
        return
    rows = _load_csv(csv_path)
    os.makedirs(PLOT_DIR, exist_ok=True)

    # ── 2×3 summary (averaged over both cpu_util_weight values) ──────────
    fig, axes = plt.subplots(2, 3, figsize=(15, 9))
    fig.suptitle(
        "Sensitivity Analysis — K Window × Load × CPU Util Weight",
        fontsize=13, fontweight="bold",
    )

    # (0,0) Placement rate vs K
    ax = axes[0, 0]
    xs, ys = _mean_by(rows, "k_window", "placement_rate")
    ax.plot(xs, [y * 100 for y in ys], "o-", color="steelblue", lw=2)
    ax.set_xlabel("K  (violation rolling window)")
    ax.set_ylabel("Placement rate (%)")
    ax.set_title("Placement Rate vs K")
    ax.grid(True, alpha=0.3)

    # (0,1) Total violations vs K
    ax = axes[0, 1]
    xs, ys = _mean_by(rows, "k_window", "total_violations")
    ax.plot(xs, ys, "o-", color="crimson", lw=2)
    ax.set_xlabel("K  (violation rolling window)")
    ax.set_ylabel("Total SLA violations")
    ax.set_title("SLA Violations vs K")
    ax.grid(True, alpha=0.3)

    # (0,2) Placement rate vs load
    ax = axes[0, 2]
    xs, ys = _mean_by(rows, "jobs_per_round", "placement_rate")
    ax.plot(xs, [y * 100 for y in ys], "o-", color="teal", lw=2)
    ax.set_xlabel("Jobs per round  (arrival load)")
    ax.set_ylabel("Placement rate (%)")
    ax.set_title("Placement Rate vs Load")
    ax.grid(True, alpha=0.3)

    # (1,0) Avg effective mem % vs K
    ax = axes[1, 0]
    xs, ys = _mean_by(rows, "k_window", "avg_eff_mem_pct")
    ax.plot(xs, ys, "o-", color="mediumseagreen", lw=2)
    ax.set_xlabel("K  (violation rolling window)")
    ax.set_ylabel("Avg effective memory (%)")
    ax.set_title("Memory Utilization vs K")
    ax.grid(True, alpha=0.3)

    # (1,1) Wait-time spread vs K (fairness)
    ax = axes[1, 1]
    xs, ys = _mean_by(rows, "k_window", "wait_spread_sec")
    ax.plot(xs, ys, "o-", color="mediumpurple", lw=2)
    ax.set_xlabel("K  (violation rolling window)")
    ax.set_ylabel("Wait spread (s)  [max − min across tenants]")
    ax.set_title("Fairness — Wait Spread vs K")
    ax.grid(True, alpha=0.3)

    # (1,2) Wait spread vs load
    ax = axes[1, 2]
    xs, ys = _mean_by(rows, "jobs_per_round", "wait_spread_sec")
    ax.plot(xs, ys, "o-", color="darkorange", lw=2)
    ax.set_xlabel("Jobs per round  (arrival load)")
    ax.set_ylabel("Wait spread (s)  [max − min across tenants]")
    ax.set_title("Fairness — Wait Spread vs Load")
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    out_path = os.path.join(PLOT_DIR, "sensitivity_summary.png")
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Summary plot saved: {out_path}")

    _plot_k_detail(rows)
    _plot_kw_jobs_heatmap(rows)


def _plot_k_detail(rows: list[dict[str, float]]) -> None:
    """Placement rate and violations for each K value across jobs_per_round."""
    fig, axes = plt.subplots(1, 2, figsize=(12, 4))
    fig.suptitle("K Window Effect across Load Levels", fontsize=12, fontweight="bold")

    for k in sorted({r["k_window"] for r in rows}):
        sub = [r for r in rows if r["k_window"] == k]
        xs, ys_p = _mean_by(sub, "jobs_per_round", "placement_rate")
        xs, ys_v = _mean_by(sub, "jobs_per_round", "total_violations")
        lbl = f"K={int(k)}"
        axes[0].plot(xs, [y * 100 for y in ys_p], "o-", label=lbl, lw=2)
        axes[1].plot(xs, ys_v, "o-", label=lbl, lw=2)

    for ax, title, ylabel in [
        (axes[0], "Placement Rate vs Load  (by K)", "Placement rate (%)"),
        (axes[1], "SLA Violations vs Load  (by K)", "Total violations"),
    ]:
        ax.set_xlabel("Jobs per round")
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)

    plt.tight_layout()
    out = os.path.join(PLOT_DIR, "k_detail.png")
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"K detail plot saved: {out}")


def _plot_kw_jobs_heatmap(rows: list[dict[str, float]]) -> None:
    """Heatmap of avg violations for K × jobs_per_round."""
    from collections import defaultdict
    import numpy as np

    ks   = sorted({r["k_window"]       for r in rows})
    jobs = sorted({r["jobs_per_round"] for r in rows})

    grid: dict[tuple[float, float], list[float]] = defaultdict(list)
    for r in rows:
        grid[(r["k_window"], r["jobs_per_round"])].append(r["total_violations"])

    data = [
        [sum(grid[(k, j)]) / max(1, len(grid[(k, j)])) for j in jobs]
        for k in ks
    ]

    fig, ax = plt.subplots(figsize=(7, 5))
    im = ax.imshow(data, aspect="auto", cmap="YlOrRd", origin="lower")
    ax.set_xticks(range(len(jobs)))
    ax.set_xticklabels([str(int(j)) for j in jobs])
    ax.set_yticks(range(len(ks)))
    ax.set_yticklabels([str(int(k)) for k in ks])
    ax.set_xlabel("Jobs per round  (load)")
    ax.set_ylabel("K  (violation rolling window)")
    ax.set_title("Avg SLA Violations — K × Load heatmap")
    plt.colorbar(im, ax=ax, label="violations")
    plt.tight_layout()
    out = os.path.join(PLOT_DIR, "violations_heatmap.png")
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Heatmap saved: {out}")


# ═══════════════════════════════════════════════════════════════════════════════
# § ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sensitivity analysis for the multi-tenant cluster scheduler.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--batches",        type=int, default=DEFAULT_NUM_BATCHES,
                        help="Batches per configuration run")
    parser.add_argument("--seed",           type=int, default=DEFAULT_SEED,
                        help="RNG seed (same seed across all configs for comparability)")
    parser.add_argument("--output",         type=str, default=DEFAULT_OUTPUT,
                        help="CSV output file")
    parser.add_argument("--plot-only",      action="store_true",
                        help="Skip simulation; regenerate plots from existing CSV")
    parser.add_argument("--iterative", default=True, action=argparse.BooleanOptionalAction,
                        help="Use iterative RT solver (default: True)")
    parser.add_argument("--rt-batch-jobs",  type=int, default=32,
                        help="Jobs per sub-MILP — iterative RT only (default: 32)")
    parser.add_argument("--rt-batch-nodes", type=int, default=32,
                        help="Nodes per sub-MILP — iterative RT only (default: 32)")
    args = parser.parse_args()

    if not args.plot_only:
        n_configs = len(K_WINDOW_VALUES) * len(JOBS_PER_ROUND_VALUES)
        print(f"Sweep: {n_configs} configs × {args.batches} batches  |  seed={args.seed}")
        print(f"Output: {args.output}\n")
        run_sweep(args.batches, args.seed, args.output,
                  iterative=args.iterative,
                  batch_jobs=args.rt_batch_jobs,
                  batch_nodes=args.rt_batch_nodes)

    print("Generating plots…")
    plot_results(args.output)
    print("Done.")


if __name__ == "__main__":
    main()
