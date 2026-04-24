"""
optimizer_pulp.py
─────────────────
Cluster job scheduler — PuLP implementation.

Solves the identical MILP as optimizer_google_or.py.  PuLP wraps the
open-source CBC solver by default.  The model syntax reads almost like the
mathematical formulation, which makes it convenient for academic work.
Switching to Gurobi, CPLEX, or GLPK is a one-line change via SOLVER below.

    max  Z = Σ_{j∈J} Σ_{n∈N}  ω_{t(j)} · m̂_j · x_{jn}

    s.t.
        C1: Σ_{n∈N}  x_{jn}          ≤ 1    ∀ j∈J
        C2: Σ_{j∈J}  m̂_j · x_{jn}   ≤ R_n  ∀ n∈N
        C3: x_{jn} ∈ {0,1}
"""

from __future__ import annotations

import pulp

from simulation_data import (
    Job, NodeState,
    compute_violation_rate, compute_effective_capacity, compute_remaining,
    compute_omega,
    generate_jobs, generate_nodes, apply_placements,
    K_WINDOW, GAMMA, ALPHA, NUM_TENANTS, NUM_ROUNDS,
)

# Swap in any PuLP-compatible solver, e.g.:
#   pulp.GLPK_CMD(msg=0)   — GLPK (must be installed separately)
#   pulp.CPLEX_CMD()        — CPLEX
#   pulp.GUROBI_CMD()       — Gurobi
SOLVER: pulp.LpSolver | None = pulp.PULP_CBC_CMD(msg=0)


def solve(
    jobs:  list[Job],
    nodes: list[NodeState],
    W_t:   dict[int, float],
    gamma: float = GAMMA,
    alpha: float = ALPHA,
    K:     int   = K_WINDOW,
) -> dict[str, int | None]:
    """
    Solve one scheduling round.

    Parameters
    ----------
    jobs   : pending jobs this round
    nodes  : current node states
    W_t    : avg wait time per tenant in rounds; pass {} for the first round
    gamma  : SLA sensitivity coefficient
    alpha  : fairness responsiveness coefficient
    K      : rolling window length for violation rate

    Returns
    -------
    dict mapping job_id → node_id (int) if placed, or None if unscheduled.
    """
    # ── Derived node quantities ────────────────────────────────────────────
    v_bar = {n.node_id: compute_violation_rate(n.violation_history, K) for n in nodes}
    m_eff = {n.node_id: compute_effective_capacity(n, v_bar[n.node_id], gamma) for n in nodes}
    R     = {n.node_id: compute_remaining(n, m_eff[n.node_id]) for n in nodes}

    # ── Tenant weights ─────────────────────────────────────────────────────
    all_tenants = list(range(NUM_TENANTS))
    omega       = compute_omega({t: W_t.get(t, 0.0) for t in all_tenants}, alpha)
    omega       = {t: omega.get(t, 1.0) for t in all_tenants}

    # ── Problem ────────────────────────────────────────────────────────────
    prob = pulp.LpProblem("cluster_scheduling", pulp.LpMaximize)

    # ── Decision variables x_{jn} ∈ {0,1} ────────────────────────────────
    x = {
        (j.job_id, n.node_id): pulp.LpVariable(
            f"x_{j.job_id}_{n.node_id}", cat=pulp.const.LpBinary
        )
        for j in jobs
        for n in nodes
    }

    # ── Objective: maximise weighted memory utilisation ────────────────────
    prob += pulp.lpSum(
        omega.get(j.tenant_id, 1.0) * j.predicted_mem_mb * x[j.job_id, n.node_id]
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
            pulp.lpSum(j.predicted_mem_mb * x[j.job_id, n.node_id] for j in jobs)
            <= R[n.node_id],
            f"c2_{n.node_id}",
        )

    # ── Solve ──────────────────────────────────────────────────────────────
    prob.solve(SOLVER)

    if prob.status != pulp.constants.LpStatusOptimal:
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
    import numpy as np

    rng   = np.random.default_rng(seed=42)
    nodes = generate_nodes(rng)
    log:  dict[str, tuple[int, int, int]] = {}
    W_t:  dict[int, float] = {}

    print(f"PuLP (CBC) — {NUM_ROUNDS} rounds\n")
    print(f"{'Round':>5}  {'Jobs':>5}  {'Placed':>6}  {'Queued':>6}  {'Violations':>10}")
    print("─" * 42)

    for r in range(NUM_ROUNDS):
        jobs       = generate_jobs(r, rng=rng)
        placements = solve(jobs, nodes, W_t)

        placed_by_node: dict[int, list[Job]] = {n.node_id: [] for n in nodes}
        violations = 0
        for j in jobs:
            nid = placements.get(j.job_id)
            if nid is not None:
                placed_by_node[nid].append(j)
                log[j.job_id] = (j.tenant_id, j.arrival_round, r)

        for n in nodes:
            if placed_by_node[n.node_id]:
                if apply_placements(n, placed_by_node[n.node_id], rng):
                    violations += 1

        tenant_delays: dict[int, list[int]] = {}
        for _, (tid, arr, sch) in log.items():
            tenant_delays.setdefault(tid, []).append(sch - arr)
        W_t = {t: sum(ds) / len(ds) for t, ds in tenant_delays.items()}

        placed = sum(1 for v in placements.values() if v is not None)
        print(f"{r:>5}  {len(jobs):>5}  {placed:>6}  {len(jobs)-placed:>6}  {violations:>10}")

    print("─" * 42)
    print(f"\nFinal avg wait per tenant: { {t: round(w, 2) for t, w in W_t.items()} }")


if __name__ == "__main__":
    main()
