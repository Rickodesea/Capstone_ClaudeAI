"""
optimizer_google_or.py
──────────────────────
MILP solver for one scheduling round, implemented with Google OR-Tools.

Decision variable  (§1)
    x_{jn} ∈ {0,1}   1 = job j placed on node n, 0 = not placed

Objective  (§5)
    max Z = Σ_{j∈J} Σ_{n∈N}  ω_{delay,t(j)} · P̂_j^mem · u_n^mem · σ_n^consolid · x_{jn}

Constraints  (§6)
    C1: Σ_{n∈N} x_{jn}              ≤ 1          ∀ j∈J
    C2: Σ_{j∈J} P̂_j^mem · x_{jn}   ≤ M_n^eff    ∀ n∈N
    C3: x_{jn} ∈ {0,1}
    C4: x_{jn} = 0  if P̂_j^CPU > C_n             ∀ j,n
    C5: x_{jn} = 0  if n ∉ A_{t(j)}              ∀ j,n
"""

from __future__ import annotations

from ortools.linear_solver import pywraplp

from simulation_data import (
    Job, NodeState,
    compute_violation_rate,
    compute_available_capacity,
    compute_remaining_avail,
    compute_remaining_eff,
    compute_utilization_weight,
    compute_node_weight,
    compute_omega,
    K_WINDOW, NUM_TENANTS,
)

SOLVER_ID: str = "CBC"


def solve(
    jobs:               list[Job],
    nodes:              list[NodeState],
    W_t:                dict[int, float],
    K:                  int = K_WINDOW,
    tenant_node_access: dict[int, list[int]] | None = None,
) -> dict[str, int | None]:
    solver = pywraplp.Solver.CreateSolver(SOLVER_ID)
    if solver is None:
        fallback = "SCIP" if SOLVER_ID == "CBC" else "CBC"
        solver   = pywraplp.Solver.CreateSolver(fallback)
    if solver is None:
        raise RuntimeError(
            f"OR-Tools: neither '{SOLVER_ID}' nor the fallback solver is available. "
            "Install ortools-python (pip install ortools)."
        )

    solver.set_time_limit(10_000)

    v_bar: dict[int, float] = {
        n.node_id: compute_violation_rate(n.violation_history, K)
        for n in nodes
    }
    m_cap: dict[int, float] = {
        n.node_id: compute_available_capacity(n)
        for n in nodes
    }
    r_avail: dict[int, float] = {
        n.node_id: compute_remaining_avail(n, m_cap[n.node_id])
        for n in nodes
    }
    R: dict[int, float] = {
        n.node_id: compute_remaining_eff(r_avail[n.node_id], v_bar[n.node_id])
        for n in nodes
    }

    all_tenants = list(range(NUM_TENANTS))
    omega_raw   = compute_omega({t: W_t.get(t, 0.0) for t in all_tenants})
    omega: dict[int, float] = {t: omega_raw.get(t, 1.0) for t in all_tenants}

    u_mem: dict[int, float] = {
        n.node_id: compute_utilization_weight(n)
        for n in nodes
    }

    w_node: dict[int, float] = {
        n.node_id: compute_node_weight(n.node_id, len(nodes))
        for n in nodes
    }

    lp_relax = (SOLVER_ID == "GLOP")

    x: dict[tuple[str, int], pywraplp.Variable] = {}
    for j in jobs:
        for n in nodes:
            var_name = f"x_{j.job_id}_{n.node_id}"
            cpu_fits   = j.pred_cpu_p95 <= n.cpu_cores
            has_access = (
                tenant_node_access is None
                or n.node_id in tenant_node_access.get(j.tenant_id, [])
            )
            ub = 1 if (cpu_fits and has_access) else 0
            x[j.job_id, n.node_id] = (
                solver.NumVar(0.0, float(ub), var_name)
                if lp_relax
                else solver.IntVar(0, ub, var_name)
            )

    obj = solver.Objective()
    for j in jobs:
        w = omega.get(j.tenant_id, 1.0)
        for n in nodes:
            obj.SetCoefficient(
                x[j.job_id, n.node_id],
                w * j.pred_mem_mb * u_mem[n.node_id] * w_node[n.node_id]
            )
    obj.SetMaximization()

    for j in jobs:
        ct = solver.Constraint(0.0, 1.0, f"c1_{j.job_id}")
        for n in nodes:
            ct.SetCoefficient(x[j.job_id, n.node_id], 1.0)

    for n in nodes:
        ct = solver.Constraint(0.0, R[n.node_id], f"c2_{n.node_id}")
        for j in jobs:
            ct.SetCoefficient(x[j.job_id, n.node_id], j.pred_mem_mb)

    status = solver.Solve()

    if status not in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
        return {j.job_id: None for j in jobs}

    placements: dict[str, int | None] = {}
    for j in jobs:
        assigned: int | None = None
        for n in nodes:
            if x[j.job_id, n.node_id].solution_value() > 0.5:
                assigned = n.node_id
                break
        placements[j.job_id] = assigned

    return placements
