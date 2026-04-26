"""
optimizer_pulp.py
─────────────────
Cluster job scheduler — PuLP implementation.

Solves the identical MILP as optimizer_google_or.py.  PuLP wraps the
open-source CBC solver by default.  The model syntax reads almost like the
mathematical formulation, which makes it convenient for academic work.
Switching to Gurobi, CPLEX, or GLPK is a one-line change via SOLVER below.

    max  Z = Σ_{j∈J} Σ_{n∈N}  ω_{t(j)} · m̂_j · φ_n · x_{jn}

    s.t.
        C1: Σ_{n∈N}  x_{jn}          ≤ 1    ∀ j∈J
        C2: Σ_{j∈J}  m̂_j · x_{jn}   ≤ R_n  ∀ n∈N
        C3: x_{jn} ∈ {0,1}

Where:
    φ_n = 1 + δ · u_n    (bin-packing affinity; u_n = U_n / max(1, M_eff_n))
    ω_t = 1 + α · max(0, (W̄_t − W̄) / W̄)   (fairness weight)
    R_n = max(0, M_eff_n − U_n)               (remaining capacity)
    M_eff_n = M_n(1 − γ·v̄_n) − τ_n           (effective usable memory)
"""

from __future__ import annotations

import pulp

from simulation_data import (
    Job, NodeState,
    compute_violation_rate, compute_effective_capacity, compute_remaining,
    compute_node_affinity, compute_omega,
    K_WINDOW, GAMMA, ALPHA, DELTA, NUM_TENANTS, NUM_ROUNDS,
)

# Swap in any PuLP-compatible solver, e.g.:
#   pulp.GLPK_CMD(msg=0)   — GLPK (must be installed separately)
#   pulp.CPLEX_CMD()        — CPLEX
#   pulp.GUROBI_CMD()       — Gurobi
SOLVER: pulp.LpSolver | None = pulp.PULP_CBC_CMD(msg=0, timeLimit=10)


def solve(
    jobs:  list[Job],
    nodes: list[NodeState],
    W_t:   dict[int, float],
    gamma: float = GAMMA,
    alpha: float = ALPHA,
    K:     int   = K_WINDOW,
    delta: float = DELTA,
) -> dict[str, int | None]:
    """
    Solve one scheduling round.

    Parameters
    ----------
    jobs   : pending jobs this round
    nodes  : current node states
    W_t    : avg wait time per tenant in seconds; pass {} for the first round
    gamma  : SLA sensitivity coefficient γ
    alpha  : fairness responsiveness coefficient α
    K      : rolling window length for violation rate v̄_n
    delta  : bin-packing affinity sensitivity δ  (φ_n = 1 + δ·u_n)

    Returns
    -------
    dict mapping job_id → node_id (int) if placed, or None if unscheduled.
    """
    # ── Derived node quantities ────────────────────────────────────────────
    v_bar = {n.node_id: compute_violation_rate(n.violation_history, K)        for n in nodes}
    m_eff = {n.node_id: compute_effective_capacity(n, v_bar[n.node_id], gamma) for n in nodes}
    R     = {n.node_id: compute_remaining(n, m_eff[n.node_id])                for n in nodes}

    # ── Tenant weights ─────────────────────────────────────────────────────
    all_tenants = list(range(NUM_TENANTS))
    omega_raw   = compute_omega({t: W_t.get(t, 0.0) for t in all_tenants}, alpha)
    omega       = {t: omega_raw.get(t, 1.0) for t in all_tenants}

    # ── Node bin-packing affinity weights ──────────────────────────────────
    phi = {
        n.node_id: compute_node_affinity(n, m_eff[n.node_id], delta)
        for n in nodes
    }

    # ── Problem ────────────────────────────────────────────────────────────
    prob = pulp.LpProblem("cluster_scheduling", pulp.LpMaximize)

    # ── Decision variables x_{jn} ∈ {0,1} (C3) ───────────────────────────
    x = {
        (j.job_id, n.node_id): pulp.LpVariable(
            f"x_{j.job_id}_{n.node_id}", cat=pulp.const.LpBinary
        )
        for j in jobs
        for n in nodes
    }

    # ── Objective: max Z = Σ ω_t · m̂_j · φ_n · x_{jn} ───────────────────
    prob += pulp.lpSum(
        omega.get(j.tenant_id, 1.0) * j.pred_mem_mb * phi[n.node_id] * x[j.job_id, n.node_id]
        for j in jobs
        for n in nodes
    ), "weighted_utilisation"

    # ── C1: at most one node per job ───────────────────────────────────────
    for j in jobs:
        prob += (
            pulp.lpSum(x[j.job_id, n.node_id] for n in nodes) <= 1,
            f"c1_{j.job_id}",
        )

    # ── C2: node capacity ─────────────────────────────────────────────────
    for n in nodes:
        prob += (
            pulp.lpSum(j.pred_mem_mb * x[j.job_id, n.node_id] for j in jobs)
            <= R[n.node_id],
            f"c2_{n.node_id}",
        )

    # ── Solve ──────────────────────────────────────────────────────────────
    prob.solve(SOLVER)

    # Accept Optimal (1) or Feasible solutions from solvers with time limits
    if prob.status not in (pulp.constants.LpStatusOptimal,):
        # Check if we got any feasible assignment despite non-optimal status
        has_any = any(
            pulp.value(x[j.job_id, n.node_id]) is not None
            for j in jobs for n in nodes
        )
        if not has_any:
            return {j.job_id: None for j in jobs}

    # ── Extract placements ─────────────────────────────────────────────────
    placements: dict[str, int | None] = {}
    for j in jobs:
        assigned = None
        for n in nodes:
            val = pulp.value(x[j.job_id, n.node_id])
            if val is not None and val > 0.5:
                assigned = n.node_id
                break
        placements[j.job_id] = assigned

    return placements


# ── Demo ───────────────────────────────────────────────────────────────────────

def main() -> None:
    from cluster_manager import ClusterManager
    cm     = ClusterManager(seed=42, verbose=True)
    result = cm.run(NUM_ROUNDS)
    print()
    print(result)


if __name__ == "__main__":
    main()
