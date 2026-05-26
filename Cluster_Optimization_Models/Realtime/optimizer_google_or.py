"""
optimizer_google_or.py
──────────────────────
MILP solver for one scheduling call. Assigns jobs to machines.

The model is stateless and timeless — it has no concept of intervals, tenants,
or machine assignments from the plan-ahead. The Cluster Manager handles all
tenant-group filtering before calling this function.

Decision variable
─────────────────
    x_{jn} ∈ {0,1}   1 = job j placed on machine n

Objective
─────────
    max Z = Σ_{j∈J} Σ_{n∈N}  ω_{t(j)} · P̂_j^mem · u_n^mem · σ_n^consolid · x_{jn}

Constraints
───────────
    C1: Σ_{n∈N} x_{jn}             ≤ 1          ∀ j∈J   (one machine per job)
    C2: Σ_{j∈J} P̂_j^mem · x_{jn}  ≤ M_n^eff    ∀ n∈N   (machine memory capacity)
    C3: x_{jn} ∈ {0,1}                                    (binary domain)
    C4: x_{jn} = 0  if P̂_j^CPU > C_n            ∀ j,n   (CPU fitment — upper bound 0)

Note: C5 (plan-ahead access control) has been removed. The Cluster Manager
filters machines to only those assigned to the current tenant group before
calling this function.

Derived node quantities (computed before each call)
────────────────────────────────────────────────────
    v̄_n      = fraction of last K intervals where U_n > M_n^cap  (SLA violation rate)
    M_n^cap   = M_n − M_n^tax − M_n^θ                            (schedulable capacity)
    M_n^avail = M_n^cap − U_n                                     (remaining capacity)
    M_n^eff   = max(0, M_n^avail × (1 − v̄_n))                    (RHS of C2)
    u_n^mem   = 1 + clamp(U_n / M_n^cap, [0,1])                  (utilization weight ∈ [1,2])
    σ_n^consolid = |N| − n                                        (consolidation bias)

Solver choice
─────────────
    SOLVER_ID = "CBC"  → exact MILP (default)
    SOLVER_ID = "GLOP" → LP relaxation (fast, large instances)
    SOLVER_ID = "SCIP" → exact MILP via SCIP
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


# ── Public API ─────────────────────────────────────────────────────────────────

def solve(
    jobs:           list[Job],
    nodes:          list[NodeState],
    W_t:            dict[int, float],
    K:              int = K_WINDOW,
    time_limit_ms:  int = 10_000,
) -> dict[str, int | None]:
    """
    Solve one scheduling call and return the placement assignment.

    The jobs and nodes passed here are already filtered by the Cluster Manager
    to a single tenant group and its assigned machines. The model has no
    knowledge of tenants, plan-ahead groups, or which machines belong where.

    Parameters
    ----------
    jobs          : pending jobs for this solve call (filtered to one tenant group)
    nodes         : available machines for this solve call (filtered to one machine group)
    W_t           : per-tenant average scheduling delay over last K intervals.
                    Pass an empty dict for the very first call (no history yet).
    K             : rolling window length for v̄_n^SLA and ω_delay,t
    time_limit_ms : solver wall-clock limit in milliseconds (default 10 s).
                    Use a shorter value (e.g., 2000) for interactive simulation.

    Returns
    -------
    dict  job_id -> node_id (int) if placed, None if unscheduled.
          Unscheduled jobs return to the queue; their tenant's W_t grows, which
          raises ω_delay,t and makes them more attractive in the next call.
    """

    solver = pywraplp.Solver.CreateSolver(SOLVER_ID)
    if solver is None:
        fallback = "SCIP" if SOLVER_ID == "CBC" else "CBC"
        solver   = pywraplp.Solver.CreateSolver(fallback)
    if solver is None:
        raise RuntimeError(
            f"OR-Tools: neither '{SOLVER_ID}' nor the fallback solver is available. "
            "Install ortools-python (pip install ortools)."
        )

    solver.set_time_limit(time_limit_ms)

    if not jobs or not nodes:
        return {j.job_id: None for j in jobs}

    # ── Derived node quantities ────────────────────────────────────────────
    v_bar: dict[int, float] = {
        n.node_id: compute_violation_rate(n.overflow_history, K)
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
    u_mem: dict[int, float] = {
        n.node_id: compute_utilization_weight(n)
        for n in nodes
    }
    w_node: dict[int, float] = {
        n.node_id: compute_node_weight(i, len(nodes))
        for i, n in enumerate(nodes)
    }

    # ── Tenant delay weights ───────────────────────────────────────────────
    all_tenants = sorted({j.tenant_id for j in jobs}) if jobs else list(range(NUM_TENANTS))
    omega_raw   = compute_omega({t: W_t.get(t, 0.0) for t in all_tenants})
    omega: dict[int, float] = {t: omega_raw.get(t, 1.0) for t in all_tenants}

    # ── Decision variables ─────────────────────────────────────────────────
    lp_relax = (SOLVER_ID == "GLOP")

    x: dict[tuple[str, int], pywraplp.Variable] = {}
    for j in jobs:
        for n in nodes:
            var_name = f"x_{j.job_id}_{n.node_id}"
            cpu_fits = j.pred_cpu_p95 <= n.cpu_cores    # C4: CPU fitment
            ub = 1 if cpu_fits else 0
            x[j.job_id, n.node_id] = (
                solver.NumVar(0.0, float(ub), var_name)
                if lp_relax
                else solver.IntVar(0, ub, var_name)
            )

    # ── Objective: maximise weighted memory placement ──────────────────────
    obj = solver.Objective()
    for j in jobs:
        w = omega.get(j.tenant_id, 1.0)
        for n in nodes:
            obj.SetCoefficient(
                x[j.job_id, n.node_id],
                w * j.pred_mem_mb * u_mem[n.node_id] * w_node[n.node_id]
            )
    obj.SetMaximization()

    # ── C1: At most one machine per job ────────────────────────────────────
    for j in jobs:
        ct = solver.Constraint(0.0, 1.0, f"c1_{j.job_id}")
        for n in nodes:
            ct.SetCoefficient(x[j.job_id, n.node_id], 1.0)

    # ── C2: Machine memory capacity ────────────────────────────────────────
    for n in nodes:
        ct = solver.Constraint(0.0, R[n.node_id], f"c2_{n.node_id}")
        for j in jobs:
            ct.SetCoefficient(x[j.job_id, n.node_id], j.pred_mem_mb)

    # ── Solve ──────────────────────────────────────────────────────────────
    status = solver.Solve()

    if status not in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
        return {j.job_id: None for j in jobs}

    # ── Extract placements ─────────────────────────────────────────────────
    placements: dict[str, int | None] = {}
    for j in jobs:
        assigned: int | None = None
        for n in nodes:
            if x[j.job_id, n.node_id].solution_value() > 0.5:
                assigned = n.node_id
                break
        placements[j.job_id] = assigned

    return placements
