"""
optimizer_google_or.py
──────────────────────
MILP solver for one scheduling round, implemented with Google OR-Tools.

Mathematical model reference
─────────────────────────────
This file implements the formulation documented in goal_programming_v4.html.
Every section reference below (§1 – §7) points to that document.

Decision variable  (§1)
    x_{jn} ∈ {0,1}   1 = job j placed on node n, 0 = not placed

Objective  (§5)
    max Z = Σ_{j∈J} Σ_{n∈N}  ω_{t(j)} · m̂_j · u_n · x_{jn}

Constraints  (§6)
    C1: Σ_{n∈N} x_{jn}              ≤ 1          ∀ j∈J   (one node per job)
    C2: Σ_{j∈J} m̂_j · x_{jn}       ≤ R_n^eff    ∀ n∈N   (node capacity)
    C3: x_{jn} ∈ {0,1}                                     (binary domain)

Key derived parameters computed before each solve call  (§3)
    v̄_n  rolling SLA violation rate on node n (last K rounds)
    θ_n  = γ · v̄_n          safety buffer that shrinks usable capacity
    M_n^avail  = M_n - τ_n                   fixed available memory
    R_n^avail  = M_n^avail - U_n             remaining before SLA adjustment
    R_n^eff    = max(0, R_n^avail*(1-v̄_n))  effective capacity (RHS of C2)
    u_n        = 1 + min(1, U_n/M_n^avail)  node utilization weight ∈ [1,2]
    ω_t   = 1 + α·max(0,(W̄_t-W̄)/W̄)   tenant priority weight

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
    compute_remaining_cpu,
    compute_utilization_weight,
    compute_omega,
    K_WINDOW, NUM_TENANTS, ENABLE_CPU_CONSTRAINT,
)

# ── Solver selection ───────────────────────────────────────────────────────────
# Change this to switch solvers without touching any other code.
SOLVER_ID: str = "CBC"   # "CBC" | "GLOP" | "SCIP"


# ── Public API ─────────────────────────────────────────────────────────────────

def solve(
    jobs:  list[Job],
    nodes: list[NodeState],
    W_t:   dict[int, float],
    K:     int = K_WINDOW,
) -> dict[str, int | None]:
    """
    Solve one scheduling round and return the placement assignment.

    This function constructs and solves the MILP from goal_programming_v4.
    It is called once (or several times) per batch by the ClusterManager.

    Parameters
    ----------
    jobs  : pending jobs in the queue this round  (§2 — set J)
    nodes : current state of all cluster nodes    (§2 — set N)
    W_t   : avg scheduling delay per tenant,
            in batches (§3 — W̄_t, Fairness Feedback).
            Pass an empty dict for the very first round (no history yet).
    K     : rolling window length for v̄_n         (§3 — violation rate)

    Returns
    -------
    dict  job_id → node_id (int) if the job was placed, or None if unscheduled.
          Unscheduled jobs return to the queue; their wait time grows, which
          raises their ω_t and makes them more attractive in the next round.
    """

    # ── Create solver instance ─────────────────────────────────────────────
    solver = pywraplp.Solver.CreateSolver(SOLVER_ID)
    if solver is None:
        # Fallback: try the other exact MILP solver
        fallback = "SCIP" if SOLVER_ID == "CBC" else "CBC"
        solver   = pywraplp.Solver.CreateSolver(fallback)
    if solver is None:
        raise RuntimeError(
            f"OR-Tools: neither '{SOLVER_ID}' nor the fallback solver is available. "
            "Install ortools-python (pip install ortools)."
        )

    # Time limit prevents the C++ solver from blocking Python indefinitely
    # when the job queue is large.  FEASIBLE is accepted as a valid result.
    solver.set_time_limit(10_000)   # 10 seconds max per solve call

    # ── §3: Derived node quantities ────────────────────────────────────────
    #
    # For each node n we compute:
    #   v̄_n      = fraction of last K rounds where usage > M_n^avail
    #   M_n^avail = M_n − τ_n                   (fixed available memory)
    #   R_n^avail = M_n^avail − U_n             (remaining before SLA scaling)
    #   R_n^eff   = max(0, R_n^avail·(1−v̄_n))  (capacity offered to new jobs)
    #
    # Reference: goal_programming_v4 §3, "Node Memory — Dynamic"

    v_bar: dict[int, float] = {
        n.node_id: compute_violation_rate(n.violation_history, K)
        for n in nodes
    }
    m_avail: dict[int, float] = {
        n.node_id: compute_available_capacity(n)
        for n in nodes
    }
    r_avail: dict[int, float] = {
        n.node_id: compute_remaining_avail(n, m_avail[n.node_id])
        for n in nodes
    }
    R: dict[int, float] = {
        n.node_id: compute_remaining_eff(r_avail[n.node_id], v_bar[n.node_id])
        for n in nodes
    }

    # ── §3: Tenant priority weights ────────────────────────────────────────
    #
    # ω_t = f_ω(W̄_t, W̄) = 1 + min(1, max(0, (W̄_t − W̄) / max(1, W̄)))
    #
    # ω_t ∈ [1, 2].  Tenants with above-average wait get ω_t > 1 — their
    # jobs contribute more to Z, so the solver prefers placing them.
    #
    # When W_t is empty (first round), all tenants get ω_t = 1.0 (equal weight).
    #
    # Reference: goal_programming_v4 §3, "Fairness Feedback — ω_t"

    all_tenants = list(range(NUM_TENANTS))
    omega_raw   = compute_omega({t: W_t.get(t, 0.0) for t in all_tenants})
    omega: dict[int, float] = {t: omega_raw.get(t, 1.0) for t in all_tenants}

    # ── §3: Node utilization weights ──────────────────────────────────────
    #
    # u_n = 1 + min(1, U_n / max(1, M_n^avail))   ∈ [1, 2]
    #
    # Nodes with higher memory utilization receive a larger objective
    # coefficient, steering the solver to consolidate onto already-busy
    # nodes and leave idle nodes available for power-down.
    # C2 still prevents physically infeasible placements.
    #
    # Reference: goal_programming_v4 §3, "Node Utilization Weight"

    u_n: dict[int, float] = {
        n.node_id: compute_utilization_weight(n, m_avail[n.node_id])
        for n in nodes
    }

    # ── §1 + §6 C3: Decision variables x_{jn} ∈ {0,1} ────────────────────
    #
    # One binary variable per (job, node) pair.
    # IntVar(0,1,...) enforces C3 directly.
    # For LP relaxation (SOLVER_ID == "GLOP"), NumVar(0,1,...) is used instead
    # and the fractional solution is rounded at extraction time.
    #
    # Reference: goal_programming_v4 §1, §6 C3

    lp_relax = (SOLVER_ID == "GLOP")

    x: dict[tuple[str, int], pywraplp.Variable] = {}
    for j in jobs:
        for n in nodes:
            var_name = f"x_{j.job_id}_{n.node_id}"
            x[j.job_id, n.node_id] = (
                solver.NumVar(0.0, 1.0, var_name)   # LP relaxation
                if lp_relax
                else solver.IntVar(0, 1, var_name)  # exact MILP (C3)
            )

    # ── §5: Objective — maximise weighted memory utilisation ───────────────
    #
    # max Z = Σ_{j∈J} Σ_{n∈N}  ω_{t(j)} · m̂_j · u_n · x_{jn}
    #
    # Each placed job contributes its P95 predicted memory m̂_j, scaled by:
    #   ω_{t(j)} ∈ [1,2] — tenant priority (fairness feedback)
    #   u_n      ∈ [1,2] — node utilization weight (consolidation preference)
    #
    # When all ω = 1 and all u_n = 1: plain memory utilisation maximisation.
    # When some tenants are underserved: their jobs get higher ω → preferred.
    # When nodes are busy: higher u_n → solver consolidates onto them first.
    #
    # Reference: goal_programming_v4 §5

    obj = solver.Objective()
    for j in jobs:
        w = omega.get(j.tenant_id, 1.0)          # ω_{t(j)}
        for n in nodes:
            obj.SetCoefficient(
                x[j.job_id, n.node_id],
                w * j.pred_mem_mb * u_n[n.node_id]   # ω_{t(j)} · m̂_j · u_n
            )
    obj.SetMaximization()

    # ── §6 C1: At most one node per job ───────────────────────────────────
    #
    # Σ_{n∈N} x_{jn} ≤ 1   ∀ j∈J
    #
    # ≤ 1 (not = 1): a job may remain unscheduled if no node has room.
    # Unscheduled jobs stay in the ClusterManager queue, accumulating delay
    # that will raise their ω_t in subsequent rounds.
    #
    # Reference: goal_programming_v4 §6, C1

    for j in jobs:
        ct = solver.Constraint(0.0, 1.0, f"c1_{j.job_id}")
        for n in nodes:
            ct.SetCoefficient(x[j.job_id, n.node_id], 1.0)

    # ── §6 C2: Node memory capacity ────────────────────────────────────────
    #
    # Σ_{j∈J} m̂_j · x_{jn} ≤ R_n^eff   ∀ n∈N
    #
    # The total P95 predicted memory of all jobs placed on node n may not
    # exceed R_n^eff (effective remaining after OS tax and SLA scaling).
    # This is the overcommitment mechanism: the model uses m̂_j (P95) rather
    # than the declared request, so it admits more jobs than a naive
    # declared-memory scheduler would.  A violation occurs if the actual
    # usage (the 5 % tail) materialises at runtime.
    #
    # Reference: goal_programming_v4 §6, C2

    for n in nodes:
        ct = solver.Constraint(0.0, R[n.node_id], f"c2_{n.node_id}")
        for j in jobs:
            ct.SetCoefficient(x[j.job_id, n.node_id], j.pred_mem_mb)

    # ── §6 C4: Node CPU capacity (conditional) ────────────────────────────
    #
    # Σ_{j∈J} p̂_j^CPU · x_{jn} ≤ R_n^CPU   ∀ n∈N
    #
    # Only added when ENABLE_CPU_CONSTRAINT=True.  By default this constraint
    # is omitted: the OS time-slices CPU across all processes so cores are not
    # exclusive, and overcommitment is handled by kernel throttling at runtime.
    #
    # Reference: goal_programming_v4 §6, C4

    if ENABLE_CPU_CONSTRAINT:
        R_cpu: dict[int, float] = {
            n.node_id: compute_remaining_cpu(n)
            for n in nodes
        }
        for n in nodes:
            ct = solver.Constraint(0.0, R_cpu[n.node_id], f"c4_{n.node_id}")
            for j in jobs:
                ct.SetCoefficient(x[j.job_id, n.node_id], j.pred_cpu_p90)

    # ── Solve ──────────────────────────────────────────────────────────────
    status = solver.Solve()

    # OPTIMAL = provably best solution; FEASIBLE = valid but not proven optimal
    if status not in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
        # Infeasible or solver error — return all unscheduled
        return {j.job_id: None for j in jobs}

    # ── Extract placements ─────────────────────────────────────────────────
    # Read x_{jn} values from the solved model.
    # A value > 0.5 is treated as 1 (handles LP-relaxation rounding).
    placements: dict[str, int | None] = {}
    for j in jobs:
        assigned: int | None = None
        for n in nodes:
            if x[j.job_id, n.node_id].solution_value() > 0.5:
                assigned = n.node_id
                break   # C1 guarantees at most one node; stop searching
        placements[j.job_id] = assigned

    return placements
