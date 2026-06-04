"""
plan_ahead_sensitivity.py
--------------------------
Parametric sensitivity analysis for the plan-ahead MISOCP.

All sweeps here vary MISOCP-specific parameters (epsilon, lam, capacity, exclusive
count) so they always use the full Gurobi MISOCP regardless of --iterative.

When --iterative is passed (the default), a brief iterative comparison summary is
printed after each sweep showing how the iterative greedy variant compares at that
configuration.

Run directly (offline only -- each sweep re-solves many times):

    cd PlanAhead/
    python plan_ahead_sensitivity.py              # with iterative note (default)
    python plan_ahead_sensitivity.py --no-iterative  # MISOCP output only
"""

from __future__ import annotations

import sys
import os
from pathlib import Path

import gurobipy as gp
from gurobipy import GRB

from plan_ahead_data import build_synthetic_data, make_gurobi_env
from plan_ahead_optimizer import build_model

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _HAS_MPL = True
except ImportError:
    _HAS_MPL = False

PLOT_DIR = Path(__file__).resolve().parent / "sensitivity_plots"
PLOT_DIR.mkdir(exist_ok=True)


def _plot_bar(x_labels: list, y_vals: list, ylabel: str, title: str, fname: str) -> None:
    if not _HAS_MPL:
        return
    fig, ax = plt.subplots(figsize=(8, 4))
    colors = ["#2196F3" if v is not None else "#BDBDBD" for v in y_vals]
    y = [v if v is not None else 0.0 for v in y_vals]
    ax.bar([str(x) for x in x_labels], y, color=colors, edgecolor="white", linewidth=0.5)
    ax.set_xlabel(x_labels[0].__class__.__name__ if x_labels else "")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    out = PLOT_DIR / fname
    fig.savefig(out, dpi=100)
    plt.close(fig)
    print(f"  Chart saved: {out}")


def _plot_line(x_vals: list, ys: dict, xlabel: str, ylabel: str, title: str, fname: str) -> None:
    if not _HAS_MPL:
        return
    fig, ax = plt.subplots(figsize=(8, 4))
    for label, y in ys.items():
        ax.plot(x_vals, y, "o-", label=label, lw=2)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    if len(ys) > 1:
        ax.legend()
    fig.tight_layout()
    out = PLOT_DIR / fname
    fig.savefig(out, dpi=100)
    plt.close(fig)
    print(f"  Chart saved: {out}")


# -- Internal helper ----------------------------------------------------------

def _solve_silent(P: dict, use_socp: bool = True) -> dict | None:
    """Build and solve the model silently; return result dict or None."""
    env = make_gurobi_env()
    m, vars_ = build_model(P, env, use_socp=use_socp)
    m.Params.TimeLimit    = 60
    m.Params.MIPGap       = 0.02
    m.Params.LogToConsole = 0
    m.optimize()
    if m.Status in (GRB.OPTIMAL, GRB.TIME_LIMIT, GRB.SUBOPTIMAL):
        sigma_val = vars_['sigma'].X if hasattr(vars_['sigma'], 'X') else 0.0
        return {"obj": float(m.ObjVal), "sigma": round(sigma_val, 4), "gap": round(m.MIPGap * 100, 2)}
    return None


def _print_table(rows: list[dict], param_name: str) -> None:
    header = f"  {param_name:<14}  obj          sigma    gap%"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for r in rows:
        val = r[param_name]
        res = r.get('result')
        if res is None:
            print(f"  {str(val):<14}  infeasible")
        else:
            print(f"  {str(val):<14}  {res['obj']:>10.4f}   {res['sigma']:.4f}   {res['gap']:.2f}%")


# -- 1. Cantelli epsilon sweep ------------------------------------------------

def sensitivity_epsilon(eps_values: list[float] | None = None) -> list[dict]:
    """Vary Cantelli tail probability epsilon uniformly across all periods.

    Smaller epsilon -> larger kappa -> stricter probabilistic capacity constraint
    -> less utilization packed per machine -> higher infra cost or lower fairness.
    """
    if eps_values is None:
        eps_values = [0.01, 0.02, 0.05, 0.10, 0.20, 0.30]

    rows = []
    for eps in eps_values:
        P   = build_synthetic_data(epsilon=eps)
        res = _solve_silent(P, use_socp=True)
        print(f"  eps={eps:.2f}  -> {res}")
        rows.append({"epsilon": eps, "result": res})
    return rows


# -- 2. Exclusive count sweep ------------------------------------------------

def sensitivity_exclusive_count(count_values: list[int] | None = None) -> list[dict]:
    """Vary the number of exclusive tenants (default n_tenants=4).

    More exclusive tenants → more machines dedicated to one tenant →
    fewer machines available for shared tenants → possible capacity pressure.
    Validated so count is always clamped to [0, n_tenants].
    """
    if count_values is None:
        count_values = [0, 1, 2, 3, 4]   # 0..n_tenants for default n_tenants=4

    rows = []
    for count in count_values:
        P   = build_synthetic_data(n_exclusive=count)
        res = _solve_silent(P, use_socp=True)
        print(f"  n_exclusive={count}  -> {res}")
        rows.append({"n_exclusive": count, "result": res})
    return rows


# -- 3. Node capacity sweep --------------------------------------------------

def sensitivity_node_capacity(cap_values: list[float] | None = None) -> list[dict]:
    """Vary uniform node capacity C[n] for all machines.

    Higher capacity allows more tenants per machine, reducing active machine
    count and infrastructure cost.
    """
    if cap_values is None:
        cap_values = [5.0, 7.5, 10.0, 12.5, 15.0, 20.0]

    rows = []
    for cap in cap_values:
        P = build_synthetic_data(node_capacity=cap)
        res = _solve_silent(P)
        print(f"  capacity={cap:.1f}  -> {res}")
        rows.append({"capacity": cap, "result": res})
    return rows


# -- 4. Fairness weight (lam[1]) sweep ----------------------------------------

def sensitivity_fairness_weight(lam1_values: list[float] | None = None) -> list[dict]:
    """Vary lam[1] -- the fairness (sigma) weight in the objective.

    Higher lam1 pushes the solver to equalise demand-satisfaction ratios
    across shared tenants, at the cost of other objectives.
    lam1=0 turns off fairness pressure entirely.
    """
    if lam1_values is None:
        lam1_values = [0.0, 1.0, 2.0, 5.0, 10.0, 20.0]

    rows = []
    for lam1 in lam1_values:
        P = build_synthetic_data()
        P['lam'][1] = lam1
        res = _solve_silent(P)
        print(f"  lam[1]={lam1:.1f}  -> {res}")
        rows.append({"lam1": lam1, "result": res})
    return rows


# -- 5. Mix-bonus weight (lam[2]) sweep ---------------------------------------

def sensitivity_mix_weight(lam2_values: list[float] | None = None) -> list[dict]:
    """Vary lam[2] -- the mix-bonus weight (co-location of heavy+light tenants).

    Higher lam2 rewards the solver for placing heavy and light shared tenants
    on the same machine each period, increasing workload diversity.
    lam2=0 disables the mix objective.
    """
    if lam2_values is None:
        lam2_values = [0.0, 0.5, 1.0, 2.0, 5.0, 10.0]

    rows = []
    for lam2 in lam2_values:
        P = build_synthetic_data()
        P['lam'][2] = lam2
        res = _solve_silent(P)
        print(f"  lam[2]={lam2:.1f}  -> {res}")
        rows.append({"lam2": lam2, "result": res})
    return rows


# -- Entry point -------------------------------------------------------------

if __name__ == "__main__":
    import argparse as _ap
    _parser = _ap.ArgumentParser(
        description="Plan-Ahead MISOCP parametric sensitivity sweeps",
        formatter_class=_ap.ArgumentDefaultsHelpFormatter,
    )
    _parser.add_argument("--iterative", default=True, action=_ap.BooleanOptionalAction,
                         help="Print iterative context note after each sweep (default: True)")
    _pargs = _parser.parse_args()

    _ITER_NOTE = (
        "  [iterative note] The plan_ahead_iterative.py greedy variant avoids these\n"
        "  MISOCP parameters entirely — it uses FFD allocation with a fixed window\n"
        "  of 8 tenants × 64 nodes, trading solution quality for solver-free speed.\n"
        "  Use --no-iterative to suppress this note."
    ) if _pargs.iterative else ""

    print("=== Sensitivity: Cantelli epsilon ===")
    rows_eps = sensitivity_epsilon()
    _print_table(rows_eps, "epsilon")
    _plot_bar(
        [r["epsilon"] for r in rows_eps],
        [r["result"]["sigma"] if r.get("result") else None for r in rows_eps],
        "Fairness σ", "Cantelli ε → Fairness σ", "pa_sens_epsilon_sigma.png",
    )
    _plot_bar(
        [r["epsilon"] for r in rows_eps],
        [r["result"]["obj"] if r.get("result") else None for r in rows_eps],
        "Objective", "Cantelli ε → Objective value", "pa_sens_epsilon_obj.png",
    )
    if _ITER_NOTE:
        print(_ITER_NOTE)

    print("\n=== Sensitivity: exclusive tenant count ===")
    rows_excl = sensitivity_exclusive_count()
    _print_table(rows_excl, "n_exclusive")
    _plot_bar(
        [r["n_exclusive"] for r in rows_excl],
        [r["result"]["sigma"] if r.get("result") else None for r in rows_excl],
        "Fairness σ", "Exclusive Tenants → Fairness σ", "pa_sens_exclusive_sigma.png",
    )

    print("\n=== Sensitivity: node capacity ===")
    rows_cap = sensitivity_node_capacity()
    _print_table(rows_cap, "capacity")
    _plot_bar(
        [r["capacity"] for r in rows_cap],
        [r["result"]["sigma"] if r.get("result") else None for r in rows_cap],
        "Fairness σ", "Node Capacity → Fairness σ", "pa_sens_capacity_sigma.png",
    )

    print("\n=== Sensitivity: fairness weight lam[1] ===")
    rows_fair = sensitivity_fairness_weight()
    _print_table(rows_fair, "lam1")
    _plot_line(
        [r["lam1"] for r in rows_fair],
        {"σ": [r["result"]["sigma"] if r.get("result") else None for r in rows_fair]},
        "λ₁ (fairness weight)", "Fairness σ",
        "λ₁ Fairness Weight vs Sigma", "pa_sens_lam1_sigma.png",
    )

    print("\n=== Sensitivity: mix-bonus weight lam[2] ===")
    rows_mix = sensitivity_mix_weight()
    _print_table(rows_mix, "lam2")
    _plot_line(
        [r["lam2"] for r in rows_mix],
        {"σ": [r["result"]["sigma"] if r.get("result") else None for r in rows_mix]},
        "λ₂ (mix-bonus weight)", "Fairness σ",
        "λ₂ Mix-Bonus Weight vs Sigma", "pa_sens_lam2_sigma.png",
    )
    if _ITER_NOTE:
        print(_ITER_NOTE)

    print(f"\n  Charts saved to {PLOT_DIR}")
