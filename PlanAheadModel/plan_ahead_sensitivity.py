"""
plan_ahead_sensitivity.py
──────────────────────────
Parametric / sensitivity analysis for the plan-ahead MISOCP.

DO NOT import this module at runtime — each analysis re-solves the model
many times and is intended only for offline research use.  Run directly:

    python plan_ahead_sensitivity.py

Each function sweeps one parameter while holding all others at their default
synthetic values (seed=42).  Results are returned as a list of dicts and
printed to stdout.
"""

from __future__ import annotations

import gurobipy as gp
from gurobipy import GRB

from plan_ahead_data import build_synthetic_data, make_gurobi_env
from plan_ahead_optimizer import build_model


# ── Internal helper ───────────────────────────────────────────────────────

def _solve_silent(P: dict) -> float | None:
    """Build and solve the model silently; return objective or None."""
    env = make_gurobi_env()
    m, _ = build_model(P, env)
    m.Params.TimeLimit    = 60
    m.Params.MIPGap       = 0.02
    m.Params.LogToConsole = 0
    m.optimize()
    if m.Status in (GRB.OPTIMAL, GRB.TIME_LIMIT, GRB.SUBOPTIMAL):
        return float(m.ObjVal)
    return None


def _print_table(rows: list[dict], param_name: str) -> None:
    header = f"  {param_name:<12}  obj"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for r in rows:
        val = r[param_name]
        obj = r['obj']
        obj_str = f"{obj:.4f}" if obj is not None else "infeasible"
        print(f"  {val:<12}  {obj_str}")


# ── 1. SLA risk tolerance (eps_i) sweep ───────────────────────────────────

def sensitivity_eps(eps_values: list[float] | None = None) -> list[dict]:
    """Vary all tenants' SLA risk tolerance eps_i uniformly.

    Tighter eps (smaller value) -> larger kappa -> stricter capacity constraint
    -> fewer workloads placed -> higher infra or SLA penalty.
    """
    if eps_values is None:
        eps_values = [0.01, 0.02, 0.05, 0.10, 0.20, 0.30]

    rows = []
    for eps in eps_values:
        P = build_synthetic_data()
        for i in P['T']:
            P['eps_i'][i] = eps
        obj = _solve_silent(P)
        rows.append({"eps": eps, "obj": obj})
        print(f"  eps={eps:.2f}  obj={obj}")
    return rows


# ── 2. Migration disruption budget sweep ──────────────────────────────────

def sensitivity_migration_budget(delta_values: list[int] | None = None) -> list[dict]:
    """Vary Delta_i (per-tenant max migrations per slot) uniformly.

    delta=0 forces all workloads to stay on their initial node across periods.
    Higher delta allows more relocation at the cost of QPS churn.
    """
    if delta_values is None:
        delta_values = [0, 1, 2, 3, 5, 10]

    rows = []
    for delta in delta_values:
        P = build_synthetic_data()
        for i in P['T']:
            P['Delta_i'][i] = delta
        obj = _solve_silent(P)
        rows.append({"delta": delta, "obj": obj})
        print(f"  delta={delta}  obj={obj}")
    return rows


# ── 3. Node capacity sweep ─────────────────────────────────────────────────

def sensitivity_node_capacity(cap_values: list[float] | None = None) -> list[dict]:
    """Vary uniform node capacity C[n,r] for all n and r.

    Higher capacity allows more workloads per node, reducing active node count
    and infra cost.
    """
    if cap_values is None:
        cap_values = [5.0, 7.5, 10.0, 12.5, 15.0, 20.0]

    rows = []
    for cap in cap_values:
        P = build_synthetic_data()
        for key in P['C']:
            P['C'][key] = cap
        obj = _solve_silent(P)
        rows.append({"cap": cap, "obj": obj})
        print(f"  cap={cap:.1f}  obj={obj}")
    return rows


# ── 4. Fairness weight sweep ───────────────────────────────────────────────

def sensitivity_fairness_weight(lam3_values: list[float] | None = None) -> list[dict]:
    """Vary lam[3] (the sigma fairness weight) while holding other weights fixed.

    lam3=0 turns off fairness pressure; high values push the solver to
    equalise tenant satisfaction at the cost of other objectives.
    """
    if lam3_values is None:
        lam3_values = [0.0, 1.0, 2.0, 5.0, 10.0, 20.0]

    rows = []
    for lam3 in lam3_values:
        P = build_synthetic_data()
        P['lam'][3] = lam3
        obj = _solve_silent(P)
        rows.append({"lam3": lam3, "obj": obj})
        print(f"  lam3={lam3:.1f}  obj={obj}")
    return rows


# ── 5. SLA penalty weight sweep ────────────────────────────────────────────

def sensitivity_sla_weight(lam1_values: list[float] | None = None) -> list[dict]:
    """Vary lam[1] (SLA penalty weight).

    Higher lam1 penalises latency violations more, driving the solver to
    use stricter isolation primitives or reject tenants that cause interference.
    """
    if lam1_values is None:
        lam1_values = [1.0, 5.0, 10.0, 20.0, 50.0]

    rows = []
    for lam1 in lam1_values:
        P = build_synthetic_data()
        P['lam'][1] = lam1
        obj = _solve_silent(P)
        rows.append({"lam1": lam1, "obj": obj})
        print(f"  lam1={lam1:.1f}  obj={obj}")
    return rows


# ── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== Sensitivity: eps_i (SLA risk tolerance) ===")
    rows_eps = sensitivity_eps()
    _print_table(rows_eps, "eps")

    print("\n=== Sensitivity: Delta_i (migration disruption budget) ===")
    rows_mig = sensitivity_migration_budget()
    _print_table(rows_mig, "delta")

    print("\n=== Sensitivity: node capacity C[n,r] ===")
    rows_cap = sensitivity_node_capacity()
    _print_table(rows_cap, "cap")

    print("\n=== Sensitivity: fairness weight lam[3] ===")
    rows_fair = sensitivity_fairness_weight()
    _print_table(rows_fair, "lam3")

    print("\n=== Sensitivity: SLA penalty weight lam[1] ===")
    rows_sla = sensitivity_sla_weight()
    _print_table(rows_sla, "lam1")
