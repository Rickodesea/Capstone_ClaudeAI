"""
sensitivity_analysis.py
-----------------------
Parameter sweep for the multi-tenant cluster scheduling simulation.

The model has no free tuning coefficients (gamma/alpha/delta removed).
The only structural parameter is K (violation rolling window length).
This script sweeps K × jobs_per_round to characterise how the scheduler
behaves under different load levels and SLA memory windows.

Usage
-----
    cd optimization/
    python sensitivity_analysis.py                           # full sweep
    python sensitivity_analysis.py --batches 20 --seed 99
    python sensitivity_analysis.py --plot-only               # replot existing CSV
    python sensitivity_analysis.py --output my_results.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from itertools import product
from typing import Any

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

K_WINDOW_VALUES:       list[int] = [5, 10, 20, 30]   # violation rolling window
JOBS_PER_ROUND_VALUES: list[int] = [10, 25, 50, 100]  # arrival load per batch

DEFAULT_NUM_BATCHES: int = 10
DEFAULT_SEED:        int = 42
DEFAULT_OUTPUT:      str = "sensitivity_results.csv"
PLOT_DIR:            str = "sensitivity_plots"

CSV_FIELDS = [
    "k_window", "jobs_per_round",
    "placement_rate",
    "total_generated", "total_placed", "final_queue",
    "total_violations", "total_spikes", "total_overflows",
    "avg_eff_mem_pct", "avg_phys_mem_pct", "avg_phys_cpu_pct",
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
) -> dict[str, Any]:
    """Run one ClusterManager configuration and return a CSV-ready row."""
    t0 = time.perf_counter()

    cm = ClusterManager(
        seed           = seed,
        verbose        = False,
        k_window       = k_window,
        jobs_per_round = jobs_per_round,
    )
    r = cm.run(num_batches)

    elapsed = time.perf_counter() - t0

    waits    = list(r.final_W_t.values()) if r.final_W_t else [0.0]
    avg_wait = sum(waits) / len(waits)
    spread   = max(waits) - min(waits)
    n_b      = max(1, len(r.batch_results))

    return {
        "k_window":           k_window,
        "jobs_per_round":     jobs_per_round,
        "placement_rate":     round(r.placement_rate(), 4),
        "total_generated":    r.total_generated,
        "total_placed":       r.total_placed,
        "final_queue":        r.final_queue_size,
        "total_violations":   r.total_violations,
        "total_spikes":       r.total_spikes,
        "total_overflows":    r.total_overflows,
        "avg_eff_mem_pct":    round(sum(b.avg_eff_mem_pct  for b in r.batch_results) / n_b, 2),
        "avg_phys_mem_pct":   round(sum(b.avg_phys_mem_pct for b in r.batch_results) / n_b, 2),
        "avg_phys_cpu_pct":   round(sum(b.avg_phys_cpu_pct for b in r.batch_results) / n_b, 2),
        "avg_wait_sec":       round(avg_wait, 2),
        "wait_spread_sec":    round(spread,   2),
        "total_solver_calls": sum(b.solver_calls for b in r.batch_results),
        "run_time_sec":       round(elapsed, 3),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# § SWEEP
# ═══════════════════════════════════════════════════════════════════════════════

def run_sweep(
    num_batches: int = DEFAULT_NUM_BATCHES,
    seed:        int = DEFAULT_SEED,
    output:      str = DEFAULT_OUTPUT,
) -> list[dict[str, Any]]:
    """Run all (k_window × jobs_per_round) combinations."""
    configs = list(product(K_WINDOW_VALUES, JOBS_PER_ROUND_VALUES))
    total   = len(configs)
    results: list[dict[str, Any]] = []

    print(f"{'#':>4}  {'K':>4} {'jobs':>5}  {'place%':>7}  {'viols':>5}  {'t(s)':>6}")
    print("─" * 46)

    with open(output, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()

        for i, (k, jobs) in enumerate(configs, 1):
            row = run_one(k, jobs, num_batches, seed)
            writer.writerow(row)
            f.flush()
            results.append(row)

            print(
                f"{i:>4}  {k:>4} {jobs:>5}  "
                f"{row['placement_rate']:>6.1%}  {row['total_violations']:>5}  "
                f"{row['run_time_sec']:>6.2f}"
            )

    print("─" * 46)
    print(f"\n{total} configurations written to {output}\n")
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
    """Read CSV and generate a 2x3 grid of summary plots."""
    if not _MATPLOTLIB_AVAILABLE:
        print("matplotlib not installed (pip install matplotlib) -- skipping plots.")
        return
    rows = _load_csv(csv_path)
    os.makedirs(PLOT_DIR, exist_ok=True)

    fig, axes = plt.subplots(2, 3, figsize=(15, 9))
    fig.suptitle(
        "Sensitivity Analysis — K Window × Load Sweep",
        fontsize=13, fontweight="bold",
    )

    # ── (0,0) Placement rate vs K ─────────────────────────────────────────
    ax = axes[0, 0]
    xs, ys = _mean_by(rows, "k_window", "placement_rate")
    ax.plot(xs, [y * 100 for y in ys], "o-", color="steelblue", lw=2)
    ax.set_xlabel("K  (violation rolling window)")
    ax.set_ylabel("Placement rate (%)")
    ax.set_title("Placement Rate vs K")
    ax.grid(True, alpha=0.3)

    # ── (0,1) Total violations vs K ───────────────────────────────────────
    ax = axes[0, 1]
    xs, ys = _mean_by(rows, "k_window", "total_violations")
    ax.plot(xs, ys, "o-", color="crimson", lw=2)
    ax.set_xlabel("K  (violation rolling window)")
    ax.set_ylabel("Total SLA violations")
    ax.set_title("SLA Violations vs K")
    ax.grid(True, alpha=0.3)

    # ── (0,2) Placement rate vs jobs_per_round (load) ─────────────────────
    ax = axes[0, 2]
    xs, ys = _mean_by(rows, "jobs_per_round", "placement_rate")
    ax.plot(xs, [y * 100 for y in ys], "o-", color="teal", lw=2)
    ax.set_xlabel("Jobs per round  (arrival load)")
    ax.set_ylabel("Placement rate (%)")
    ax.set_title("Placement Rate vs Load")
    ax.grid(True, alpha=0.3)

    # ── (1,0) Avg effective mem % vs K ────────────────────────────────────
    ax = axes[1, 0]
    xs, ys = _mean_by(rows, "k_window", "avg_eff_mem_pct")
    ax.plot(xs, ys, "o-", color="mediumseagreen", lw=2)
    ax.set_xlabel("K  (violation rolling window)")
    ax.set_ylabel("Avg effective memory (%)")
    ax.set_title("Memory Utilization vs K")
    ax.grid(True, alpha=0.3)

    # ── (1,1) Wait-time spread vs K (fairness) ────────────────────────────
    ax = axes[1, 1]
    xs, ys = _mean_by(rows, "k_window", "wait_spread_sec")
    ax.plot(xs, ys, "o-", color="mediumpurple", lw=2)
    ax.set_xlabel("K  (violation rolling window)")
    ax.set_ylabel("Wait spread (s)  [max − min across tenants]")
    ax.set_title("Fairness — Wait Spread vs K")
    ax.grid(True, alpha=0.3)

    # ── (1,2) Avg phys CPU % vs jobs_per_round ────────────────────────────
    ax = axes[1, 2]
    xs, ys = _mean_by(rows, "jobs_per_round", "avg_phys_cpu_pct")
    ax.plot(xs, [y for y in ys], "o-", color="darkorange", lw=2)
    ax.set_xlabel("Jobs per round  (arrival load)")
    ax.set_ylabel("Avg physical CPU (%)")
    ax.set_title("CPU Utilization vs Load")
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
    parser.add_argument("--batches",   type=int, default=DEFAULT_NUM_BATCHES,
                        help="Batches per configuration run")
    parser.add_argument("--seed",      type=int, default=DEFAULT_SEED,
                        help="RNG seed (same seed across all configs for comparability)")
    parser.add_argument("--output",    type=str, default=DEFAULT_OUTPUT,
                        help="CSV output file")
    parser.add_argument("--plot-only", action="store_true",
                        help="Skip simulation; regenerate plots from existing CSV")
    args = parser.parse_args()

    if not args.plot_only:
        n_configs = len(K_WINDOW_VALUES) * len(JOBS_PER_ROUND_VALUES)
        print(f"Sweep: {n_configs} configs × {args.batches} batches  |  seed={args.seed}")
        print(f"Output: {args.output}\n")
        run_sweep(args.batches, args.seed, args.output)

    print("Generating plots…")
    plot_results(args.output)
    print("Done.")


if __name__ == "__main__":
    main()
