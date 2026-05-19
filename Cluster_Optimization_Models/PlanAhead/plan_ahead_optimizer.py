"""
plan_ahead_optimizer.py
────────────────────────
MISOCP build, solve, and output extraction for the plan-ahead model.

Key design properties:
  • No workloads — tenants have usage profiles u[i,h] instead.
  • No hard access control — assignment is a priority hint, not a constraint.
  • No isolation primitives, McCormick terms, or migration variables.
  • Variance-extended Cantelli capacity constraint (C1a + C1b) gives
    probabilistic safety guarantees without requiring per-workload covariance.
  • Result is a TenantAccessSchedule: nodes where the real-time model
    should boost the objective coefficient for tenant i in period h.

Variables
---------
f[i,n,h]  ≥ 0        continuous allocation of node n capacity to tenant i in period h
y[i,n,h]  ∈ {0,1}    1 iff tenant i is prioritised on node n in period h
z[n,h]    ∈ {0,1}    1 iff node n is active in period h
a[i]      ∈ {0,1}    1 iff tenant i is admitted for this planning horizon
sigma     ≥ 0        minimum demand-satisfaction ratio across admitted tenants
t[n,h]    ≥ 0        Cantelli slack — upper bound on weighted demand std dev

Constraints
-----------
C1a Capacity (linear)  : sum_i f[i,n,h] + κ·t[n,h] ≤ C[n]·z[n,h]         ∀ n,h
C1b Cone (SOCP)        : sum_i σ²[i,h]·y[i,n,h] ≤ t[n,h]²                 ∀ n,h
C2 Priority link       : f[i,n,h] ≤ C[n]·y[i,n,h]                          ∀ i,n,h
C3 Demand              : sum_n f[i,n,h] ≥ u[i,h]·a[i]                       ∀ i,h
C4 Fairness            : sigma ≤ (sum_{n,h} f[i,n,h])/(sum_h u[i,h])
                                 + (1−a[i])                                   ∀ i
C5 Node active         : z[n,h] ≥ y[i,n,h]                                  ∀ i,n,h

Uncertainty model (Cantelli)
-----------------------------
σ²[i,h] = (sigma_frac × u[i,h])²   — demand variance proportional to usage profile.
κ = sqrt((1-ε)/ε)                   — one-sided Cantelli safety factor for tail prob ε.
C1a+C1b together ensure: P[sum_i D_i,n,h ≤ C[n]·z[n,h]] ≥ 1-ε
where D_i,n,h ~ mean f[i,n,h], var σ²[i,h]·y[i,n,h].

Objective
---------
Minimize: λ₀·infra_cost − λ₁·admission_revenue − λ₂·sigma
"""

from __future__ import annotations

import gurobipy as gp
from gurobipy import GRB


# ── Build model ────────────────────────────────────────────────────────────

def build_model(P: dict, env: gp.Env, use_socp: bool = True) -> tuple[gp.Model, dict]:
    """Build and return the plan-ahead model and its decision-variable dict.

    Parameters
    ----------
    P        : parameter dict from build_synthetic_data()
    env      : Gurobi environment from make_gurobi_env()
    use_socp : True  → MISOCP with Cantelli capacity constraint (C1a + C1b).
               False → plain MILP with linear capacity constraint (C1).
               Default True (standalone use); simulation passes False for speed.

    Returns
    -------
    (model, vars_dict)
        vars_dict keys: f, y, z, a, sigma  (+t when use_socp=True)
    """
    T, N, H = P['T'], P['N'], P['H']
    C       = P['C']
    u       = P['u']
    sigma2  = P.get('sigma2', {})   # dict[(i,h) -> float]; empty = no uncertainty
    kappa   = P.get('kappa', 0.0)   # Cantelli factor; 0 = deterministic fallback

    m = gp.Model("PlanAhead", env=env)

    # ── Decision variables ─────────────────────────────────────────────────

    f = m.addVars(
        [(i, n, h) for i in T for n in N for h in H],
        lb=0.0, name="f"
    )
    y = m.addVars(
        [(i, n, h) for i in T for n in N for h in H],
        vtype=GRB.BINARY, name="y"
    )
    z = m.addVars(
        [(n, h) for n in N for h in H],
        vtype=GRB.BINARY, name="z"
    )
    t = m.addVars([(n, h) for n in N for h in H], lb=0.0, name="t") if use_socp else None
    a     = m.addVars(T, vtype=GRB.BINARY, name="a")
    sigma = m.addVar(lb=0.0, ub=1.0, name="sigma")

    m.update()

    # ── C1: Capacity constraint ────────────────────────────────────────────
    #
    # MILP mode (use_socp=False):
    #   C1 (linear):  sum_i f[i,n,h] ≤ C[n]·z[n,h]
    #
    # SOCP mode (use_socp=True):
    #   C1a (linear): sum_i f[i,n,h] + κ·t[n,h] ≤ C[n]·z[n,h]
    #   C1b (cone):   sum_i σ²[i,h]·y[i,n,h] ≤ t[n,h]²
    #   Together: P[ actual usage ≤ C[n]·z[n,h] ] ≥ 1−ε
    for n in N:
        for h in H:
            if use_socp:
                m.addConstr(
                    gp.quicksum(f[i, n, h] for i in T) + kappa * t[n, h] <= C[n] * z[n, h],
                    name=f"C1a_{n}_{h}"
                )
                if sigma2 and kappa > 0:
                    m.addQConstr(
                        t[n, h] * t[n, h] >= gp.quicksum(
                            sigma2.get((i, h), 0.0) * y[i, n, h] for i in T
                        ),
                        name=f"C1b_{n}_{h}"
                    )
            else:
                m.addConstr(
                    gp.quicksum(f[i, n, h] for i in T) <= C[n] * z[n, h],
                    name=f"C1_{n}_{h}"
                )

    # ── C2: Priority link ───────────────────────────────────────────────────
    # If y[i,n,h]=0 then f[i,n,h]=0; y=1 signals a priority assignment.
    for i in T:
        for n in N:
            for h in H:
                m.addConstr(f[i, n, h] <= C[n] * y[i, n, h], name=f"C2_{i}_{n}_{h}")

    # ── C3: Demand satisfaction ─────────────────────────────────────────────
    # Admitted tenants must receive at least their estimated demand each period.
    for i in T:
        for h in H:
            m.addConstr(
                gp.quicksum(f[i, n, h] for n in N) >= u[i, h] * a[i],
                name=f"C3_{i}_{h}"
            )

    # ── C4: Fairness (min demand-satisfaction ratio over admitted tenants) ──
    # sigma <= total_alloc_i / total_demand_i + (1 - a[i])
    # The (1-a[i]) term deactivates the constraint for rejected tenants.
    EPS = 1e-9
    for i in T:
        total_demand = sum(u[i, h] for h in H)
        if total_demand < EPS:
            continue
        total_alloc = gp.quicksum(f[i, n, h] for n in N for h in H)
        m.addConstr(
            sigma <= total_alloc / total_demand + (1 - a[i]),
            name=f"C4_{i}"
        )

    # ── C5: Node activation ─────────────────────────────────────────────────
    # A node must be marked active if any tenant has a priority assignment on it.
    for n in N:
        for h in H:
            for i in T:
                m.addConstr(z[n, h] >= y[i, n, h], name=f"C5_{i}_{n}_{h}")

    # ── Objective ──────────────────────────────────────────────────────────
    lam = P['lam']

    infra_cost    = gp.quicksum(P['pi_n'][n] * z[n, h] for n in N for h in H)
    admission_rev = gp.quicksum(
        (P['pi_bar'][i] - P['v_op'][i]) * a[i] for i in T
    )

    m.setObjective(
        lam[0] * infra_cost - lam[1] * admission_rev - lam[2] * sigma,
        GRB.MINIMIZE
    )

    vars_ = dict(f=f, y=y, z=z, t=t, a=a, sigma=sigma)  # t is None when use_socp=False
    return m, vars_


# ── Solve and report ───────────────────────────────────────────────────────

def solve_and_report(model: gp.Model, vars_: dict, P: dict) -> None:
    """Set solver parameters, optimize, and print a human-readable summary."""
    model.Params.TimeLimit    = 300
    model.Params.MIPGap       = 0.01
    model.Params.LogToConsole = 1

    model.optimize()

    if model.Status not in (GRB.OPTIMAL, GRB.TIME_LIMIT, GRB.SUBOPTIMAL):
        print(f"Model status: {model.Status} — no feasible solution found.")
        return

    T, N, H = P['T'], P['N'], P['H']
    f, y, z, a, sigma = vars_['f'], vars_['y'], vars_['z'], vars_['a'], vars_['sigma']

    print(f"\n=== Plan-Ahead Solution (obj = {model.ObjVal:.4f}) ===")

    print("\nAdmitted tenants:")
    for i in T:
        print(f"  tenant {i}: {'ADMITTED' if a[i].X > 0.5 else 'REJECTED'}")

    print("\nPriority assignments (y=1)  [alloc / demand per period]:")
    for i in T:
        if a[i].X <= 0.5:
            continue
        for h in H:
            nodes  = [n for n in N if y[i, n, h].X > 0.5]
            alloc  = sum(f[i, n, h].X for n in N)
            demand = P['u'][i, h]
            print(f"  tenant {i}  period {h}:  nodes={nodes}  "
                  f"alloc={alloc:.2f} / demand={demand:.2f}")

    print("\nActive nodes per period:")
    for n in N:
        active_h = [h for h in H if z[n, h].X > 0.5]
        if active_h:
            print(f"  node {n}: periods {active_h}")

    print(f"\nFairness sigma (min demand-satisfaction ratio): {sigma.X:.4f}")


# ── Output interface: TenantAccessSchedule ────────────────────────────────

def extract_tenant_access_schedule(
    vars_: dict,
    P: dict,
) -> dict[tuple[int, int], list[int]]:
    """Extract priority schedule from solved y variables.

    Returns
    -------
    TenantAccessSchedule : dict[(tenant_id, period) -> list[node_id]]
        Nodes where y[i,n,h]=1 — the priority hint passed to the real-time
        solver.  The real-time model boosts the objective coefficient for
        (tenant, node) pairs in this set; it does NOT block other nodes.
    """
    T, N, H = P['T'], P['N'], P['H']
    y = vars_['y']

    schedule: dict[tuple[int, int], list[int]] = {}
    for i in T:
        for h in H:
            nodes = [n for n in N if y[i, n, h].X > 0.5]
            schedule[(i, h)] = nodes
    return schedule


# ── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from plan_ahead_data import build_synthetic_data, make_gurobi_env

    P   = build_synthetic_data()
    env = make_gurobi_env()
    m, vars_ = build_model(P, env)
    solve_and_report(m, vars_, P)

    schedule = extract_tenant_access_schedule(vars_, P)
    print("\nTenantAccessSchedule (priority hints):")
    for key, nodes in sorted(schedule.items()):
        print(f"  tenant={key[0]}, period={key[1]}: nodes={nodes}")
