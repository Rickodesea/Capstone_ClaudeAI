"""
Pipeline/generate_graphs.py
─────────────────────────────
Standalone graph generator. Reads the CSVs saved by computational_time_analysis.py
and regenerates all plots without re-running any solver.

Separate heatmap PNG files are saved per solver:
  plots/rt_heatmap_CBC.png
  plots/rt_heatmap_SCIP.png
  plots/rt_heatmap_GUROBI.png
  plots/rt_heatmap_HIGHS.png
  plots/rt_scaling_vars_vs_time.png
  plots/pa_heatmap_solve_time.png
  plots/pa_build_vs_solve_breakdown.png
  plots/pa_vars_vs_solve_time.png

Run:
  cd Pipeline/
  python generate_graphs.py
  python generate_graphs.py --rt-only
  python generate_graphs.py --pa-only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Pull in the analysis module (adds Realtime/ and PlanAhead/ to sys.path)
sys.path.insert(0, str(Path(__file__).parent))
from computational_time_analysis import (
    load_results_from_csv,
    _plot_rt_heatmap,
    _plot_rt_scaling,
    _plot_pa_heatmap,
    _plot_pa_build_vs_solve,
    _plot_pa_vars_vs_time,
    _MPL,
    DATA_DIR,
    PLOT_DIR,
)


def run(rt_only: bool = False, pa_only: bool = False) -> None:
    if not _MPL:
        print("  matplotlib is not installed — cannot generate graphs.")
        print("  Install with:  pip install matplotlib")
        return

    rt_by_solver, pa = load_results_from_csv()

    if not rt_by_solver and not pa:
        print("  No saved timing data found.")
        print("  Run  python computational_time_analysis.py  first to generate the CSVs.")
        return

    PLOT_DIR.mkdir(exist_ok=True)
    print(f"  Output directory: {PLOT_DIR}")
    print()

    if not pa_only and rt_by_solver:
        # Derive J and N from loaded data so grid matches what was actually run
        j_vals = sorted({j for rt in rt_by_solver.values() for j, _ in rt})
        n_vals = sorted({n for rt in rt_by_solver.values() for _, n in rt})
        print(f"  RT solvers found : {sorted(rt_by_solver)}")
        print(f"  RT grid          : J={j_vals}  N={n_vals}")
        _plot_rt_heatmap(rt_by_solver, j_vals=j_vals, n_vals=n_vals)
        _plot_rt_scaling(rt_by_solver)

    if not rt_only and pa:
        t_vals = sorted({t for t, _ in pa})
        n_vals = sorted({n for _, n in pa})
        print(f"  PA grid          : T={t_vals}  N={n_vals}")
        _plot_pa_heatmap(pa)
        _plot_pa_build_vs_solve(pa)
        _plot_pa_vars_vs_time(pa)

    print("\n  All graphs regenerated.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Regenerate timing graphs from saved CSVs")
    ap.add_argument("--rt-only", action="store_true", help="Only generate RT graphs")
    ap.add_argument("--pa-only", action="store_true", help="Only generate PA graphs")
    args = ap.parse_args()
    run(rt_only=args.rt_only, pa_only=args.pa_only)
