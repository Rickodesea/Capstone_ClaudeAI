"""
optimizer_google_or.py
──────────────────────
MILP solver for one scheduling round, implemented with Google OR-Tools.

Mathematical model reference
─────────────────────────────
This file implements the formulation documented in real_time_optimization_model.tex.
Every section reference below (§1 – §7) points to that document.

Decision variable  (§1)
    x_{jn} ∈ {0,1}   1 = job j placed on node n, 0 = not placed

Objective  (§5)
    max Z = Σ_{j∈J} Σ_{n∈N}  ω_{delay,t(j)} · P̂_j^mem · u_n^mem · σ_n^consolid · x_{jn}

Constraints  (§6)
    C1: Σ_{n∈N} x_{jn}              ≤ 1          ∀ j∈J   (one node per job)
    C2: Σ_{j∈J} P̂_j^mem · x_{jn}   ≤ M_n^eff    ∀ n∈N   (node memory capacity)
    C3: x_{jn} ∈ {0,1}                                     (binary domain)
    C4: x_{jn} = 0  if P̂_j^CPU > C_n             ∀ j,n   (per-pair CPU fitment)
    C5: x_{jn} = 0  if n ∉ A_{t(j)}              ∀ j,n   (plan-ahead access control)

        A_{t(j)} is the set of nodes that tenant t(j) is authorised to use at the
        current scheduling time, as determined by the plan-ahead model output.
        When no access map is provided all tenants may use all nodes (default).

Key parameters before each solve call  (§3)
    v̄_n^SLA     rolling SLA violation rate on node n (last K rounds)
    M_n^cap      = M_n - M_n^tax - M_n^theta       (capacity after OS tax + threshold)
    M_n^avail    = M_n^cap - U_n^mem               (remaining capacity)
    M_n^eff      = max(0, M_n^avail * (1 - v̄_n^SLA))  (RHS of C2)
    u_n^mem      = 1 + clamp(U_n^mem / M_n, 0, 1)      (utilization weight ∈ [1,2])
    ω_delay,t    = 1 + max(0, (W̄_t - W̄) / max(1, W̄)) (tenant delay weight, K-window)
    A_t          = set of node IDs authorised for tenant t this round (from plan-ahead)

Solver choice
─────────────
Uses pywraplp (OR-Tools linear/integer programming API).
SOLVER_ID = "CBC"  → exact MILP; always bundled with ortools-python.
SOLVER_ID = "GLOP" → LP relaxation (continuous x_{jn} ∈ [0,1], then round);
                     use for very large instances where exact MILP is too slow.
SOLVER_ID = "SCIP" → exact MILP via SCIP, faster on large problems if compiled.
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

# ── Solver selection ───────────────────────────────────────────────────────────
SOLVER_ID: str = "CBC"   # "CBC" | "GLOP" | "SCIP"


# ── Public API ─────────────────────────────────────────────────────────────────

def solve(
    jobs:               list[Job],
    nodes:              list[NodeState],
    W_t:                dict[int, float],
    K:                  int = K_WINDOW,
    tenant_node_access: dict[int, list[int]] | None = None,
) -> dict[str, int | None]:
    """
    Solve one scheduling round and return the placement assignment.

    Parameters
    ----------
    jobs               : pending jobs in the queue this round  (§2 — set J)
    nodes              : current state of all cluster nodes    (§2 — set N)
    W_t                : avg scheduling delay per tenant over last K rounds (§3 — W̄_t).
                         Maintained as a rolling K-window by ClusterManager.
                         Pass an empty dict for the very first round (no history yet).
    K                  : rolling window length for v̄_n^SLA and ω_delay,t (§3)
    tenant_node_access : plan-ahead access map — tenant_id -> [allowed node_ids]  (§6 C5).
                         When provided, a job from tenant t can only be placed on
                         nodes in tenant_node_access[t].  Tenants absent from the
                         map are treated as having no access to any node (all blocked).
                         Pass None (default) to disable access control — all tenants
                         may use all nodes, preserving backward-compatible behaviour.

    Returns
    -------
    dict  job_id -> node_id (int) if the job was placed, or None if unscheduled.
          Unscheduled jobs return to the queue; their wait time grows, which
          raises their ω_delay,t and makes them more attractive in the next round.
    """

    # ── Create solver instance ─────────────────────────────────────────────
    solver = pywraplp.Solver.CreateSolver(SOLVER_ID)
    if solver is None:
        fallback = "SCIP" if SOLVER_ID == "CBC" else "CBC"
        solver   = pywraplp.Solver.CreateSolver(fallback)
    if solver is None:
        raise RuntimeError(
            f"OR-Tools: neither '{SOLVER_ID}' nor the fallback solver is available. "
            "Install ortools-python (pip install ortools)."
        )

    solver.set_time_limit(10_000)   # 10 seconds max per solve call

    # ── §3: Derived node quantities ────────────────────────────────────────
    #
    # For each node n we compute:
    #   v̄_n^SLA  = fraction of last K rounds where U_n^mem > M_n^cap
    #   M_n^cap   = M_n - M_n^tax - M_n^theta        (capacity after tax + threshold)
    #   M_n^avail = M_n^cap - U_n^mem                (remaining capacity)
    #   M_n^eff   = max(0, M_n^avail * (1 - v̄_n^SLA)) (capacity offered to new jobs)
    #
    # Reference: §3 Derived, Appendix A

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

    # ── §3: Tenant delay weights ───────────────────────────────────────────
    #
    # ω_delay,t = 1 + max(0, (W̄_t − W̄) / max(1, W̄))
    #
    # Computed over the last K scheduling rounds (rolling window maintained by
    # ClusterManager via a K-size deque per tenant).
    # When W_t is empty (first round), all tenants get ω = 1.0 (equal weight).

    all_tenants = list(range(NUM_TENANTS))
    omega_raw   = compute_omega({t: W_t.get(t, 0.0) for t in all_tenants})
    omega: dict[int, float] = {t: omega_raw.get(t, 1.0) for t in all_tenants}

    # ── §3: Memory utilization weights (omega_n^utilize) ─────────────────
    #
    # omega_n^utilize = 1 + min(1, U_n^mem / max(1, M_n^cap))   ∈ [1, 2]
    #
    # Denominator is M_n^cap (schedulable capacity) so the weight reaches 2
    # exactly when the node is fully packed — stronger consolidation signal
    # than using physical M_n. Applied in the objective to consolidate jobs
    # onto memory-busier nodes. C2 prevents infeasible placements.

    u_mem: dict[int, float] = {
        n.node_id: compute_utilization_weight(n)
        for n in nodes
    }

    # ── §3: Fixed node consolidation weights (σ_n^consolid) ──────────────
    #
    # σ_n^consolid = |N| - n   ∈ {1, 2, …, |N|}
    #
    # Node 0 gets the highest weight; biases the objective toward lower-indexed
    # nodes first, consolidating even at batch 0 when u_n^mem = 1 for all nodes.

    w_node: dict[int, float] = {
        n.node_id: compute_node_weight(n.node_id, len(nodes))
        for n in nodes
    }

    # ── §1 + §6 C3: Decision variables x_{jn} ∈ {0,1} ────────────────────

    lp_relax = (SOLVER_ID == "GLOP")

    # ── §6 C4: Per-pair CPU fitment ───────────────────────────────────────
    #
    # x_{jn} = 0 when P̂_j^CPU > C_n.
    # Blocks placing a job whose P95 CPU peak exceeds the node's total cores.

    # ── §6 C5: Plan-ahead access control ─────────────────────────────────
    #
    # x_{jn} = 0 when n ∉ A_{t(j)}.
    # A_{t(j)} is provided by the plan-ahead model for the current time slot.
    # When tenant_node_access is None, access control is disabled (all pairs
    # are unrestricted) so the model remains backward compatible.

    x: dict[tuple[str, int], pywraplp.Variable] = {}
    for j in jobs:
        for n in nodes:
            var_name = f"x_{j.job_id}_{n.node_id}"
            cpu_fits = j.pred_cpu_p95 <= n.cpu_cores        # C4
            has_access = (
                tenant_node_access is None                   # C5: disabled
                or n.node_id in tenant_node_access.get(j.tenant_id, [])
            )
            ub = 1 if (cpu_fits and has_access) else 0
            x[j.job_id, n.node_id] = (
                solver.NumVar(0.0, float(ub), var_name)
                if lp_relax
                else solver.IntVar(0, ub, var_name)
            )

    # ── §5: Objective — maximise weighted memory placement ─────────────────
    #
    # max Z = Σ_{j∈J} Σ_{n∈N}  ω_{delay,t(j)} · P̂_j^mem · u_n^mem · σ_n^consolid · x_{jn}

    obj = solver.Objective()
    for j in jobs:
        w = omega.get(j.tenant_id, 1.0)          # ω_{delay,t(j)}
        for n in nodes:
            obj.SetCoefficient(
                x[j.job_id, n.node_id],
                w * j.pred_mem_mb * u_mem[n.node_id] * w_node[n.node_id]
            )
    obj.SetMaximization()

    # ── §6 C1: At most one node per job ───────────────────────────────────

    for j in jobs:
        ct = solver.Constraint(0.0, 1.0, f"c1_{j.job_id}")
        for n in nodes:
            ct.SetCoefficient(x[j.job_id, n.node_id], 1.0)

    # ── §6 C2: Node memory capacity ────────────────────────────────────────
    #
    # Σ_{j∈J} P̂_j^mem · x_{jn} ≤ M_n^eff   ∀ n∈N

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
