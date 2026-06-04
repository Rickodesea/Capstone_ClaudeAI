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

Solver choice
─────────────
    SOLVER_ID = "CBC"    → exact MILP via OR-Tools CBC  (default)
    SOLVER_ID = "SCIP"   → exact MILP via OR-Tools SCIP
    SOLVER_ID = "GUROBI" → exact MILP via Gurobi (requires PlanAhead/.env credentials)
    SOLVER_ID = "HIGHS"  → exact MILP via HiGHS (pip install highspy)
    SOLVER_ID = "GLOP"   → LP relaxation (fast; legacy simulation use only —
                           does NOT guarantee integer 1/0 assignments)

Backend abstraction
────────────────────
    CBC, SCIP, GUROBI are dispatched through solver_backends.py.
    Add a new integer solver by implementing SolverBackend there and
    registering it in solver_backends.AVAILABLE_BACKENDS.
    GLOP is kept inline for backward compatibility with pipeline_configs.py.
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
from solver_backends import precompute, get_backend

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

    Parameters
    ----------
    jobs          : pending jobs for this solve call (filtered to one tenant group)
    nodes         : available machines for this solve call (filtered to one machine group)
    W_t           : per-tenant average scheduling delay over last K intervals
    K             : rolling window length for v̄_n^SLA and ω_delay,t
    time_limit_ms : solver wall-clock limit in milliseconds (default 10 s)

    Returns
    -------
    dict  job_id -> node_id (int) if placed, None if unscheduled.
    """
    if not jobs or not nodes:
        return {j.job_id: None for j in jobs}

    sid = SOLVER_ID.upper()

    # ── Integer backends — dispatched through solver_backends.py ──────────────
    if sid in ("CBC", "SCIP", "GUROBI", "HIGHS"):
        data = precompute(jobs, nodes, W_t, K)
        return get_backend(sid).solve(data, time_limit_ms)

    # ── GLOP — LP relaxation (legacy simulation path) ─────────────────────────
    # NOTE: GLOP does NOT guarantee integer 1/0 values for x[j,n].
    # It is kept here only for backward compatibility with simulation configs
    # that set realtime_solver="GLOP" (e.g. pipeline_configs.py SAMPLE_3).
    # For any analysis requiring certified integer solutions, use CBC/SCIP/GUROBI.
    if sid == "GLOP":
        return _solve_glop(jobs, nodes, W_t, K, time_limit_ms)

    # ── Unknown solver — fall back to CBC ─────────────────────────────────────
    data = precompute(jobs, nodes, W_t, K)
    return get_backend("CBC").solve(data, time_limit_ms)


# ── GLOP inline implementation (LP relaxation, legacy only) ───────────────────

def _solve_glop(
    jobs:          list[Job],
    nodes:         list[NodeState],
    W_t:           dict[int, float],
    K:             int,
    time_limit_ms: int,
) -> dict[str, int | None]:
    """LP relaxation via GLOP. x[j,n] ∈ [0,1] — fractions possible."""
    data = precompute(jobs, nodes, W_t, K)

    solver = pywraplp.Solver.CreateSolver("GLOP")
    if solver is None:
        solver = pywraplp.Solver.CreateSolver("CBC")
    if solver is None:
        raise RuntimeError("OR-Tools: no solver available.")

    solver.set_time_limit(time_limit_ms)

    x: dict[tuple, pywraplp.Variable] = {}
    for j in jobs:
        for n in nodes:
            ub = 1 if j.pred_cpu_p95 <= n.cpu_cores else 0
            x[j.job_id, n.node_id] = solver.NumVar(0.0, float(ub), f"x_{j.job_id}_{n.node_id}")

    obj = solver.Objective()
    for j in jobs:
        w = data.omega.get(j.tenant_id, 1.0)
        for n in nodes:
            obj.SetCoefficient(
                x[j.job_id, n.node_id],
                w * j.pred_mem_mb * data.u_mem[n.node_id] * data.w_node[n.node_id],
            )
    obj.SetMaximization()

    for j in jobs:
        ct = solver.Constraint(0.0, 1.0, f"c1_{j.job_id}")
        for n in nodes:
            ct.SetCoefficient(x[j.job_id, n.node_id], 1.0)

    for n in nodes:
        ct = solver.Constraint(0.0, data.R[n.node_id], f"c2_{n.node_id}")
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
