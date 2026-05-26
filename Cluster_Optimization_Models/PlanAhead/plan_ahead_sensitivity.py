"""
plan_ahead_sensitivity.py
--------------------------
Parametric sensitivity analysis for the plan-ahead MISOCP.

Run directly (offline only -- each sweep re-solves many times):

    python plan_ahead_sensitivity.py

Each function rebuilds the synthetic dataset with one parameter varied while
holding all others at default values (seed=42).  Results are printed as a
table and returned as a list of dicts.
"""

from __future__ import annotations

import gurobipy as gp
from gurobipy import GRB

from plan_ahead_data import build_synthetic_data, make_gurobi_env
from plan_ahead_optimizer import build_model


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
    print("=== Sensitivity: Cantelli epsilon ===")
    rows_eps = sensitivity_epsilon()
    _print_table(rows_eps, "epsilon")

    print("\n=== Sensitivity: exclusive tenant count ===")
    rows_excl = sensitivity_exclusive_count()
    _print_table(rows_excl, "n_exclusive")

    print("\n=== Sensitivity: node capacity ===")
    rows_cap = sensitivity_node_capacity()
    _print_table(rows_cap, "capacity")

    print("\n=== Sensitivity: fairness weight lam[1] ===")
    rows_fair = sensitivity_fairness_weight()
    _print_table(rows_fair, "lam1")

    print("\n=== Sensitivity: mix-bonus weight lam[2] ===")
    rows_mix = sensitivity_mix_weight()
    _print_table(rows_mix, "lam2")
