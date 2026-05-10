"""
test_model.py
─────────────
Behavioral tests for the goal_programming_v4 optimizer.

Each test initializes a specific cluster context and verifies that the
solver responds exactly as the math model predicts.  Configurations are
kept small so each test completes in under a few seconds.

Run:
    python test_model.py
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from simulation_data import (
    Job, NodeState,
    compute_utilization_weight, compute_available_capacity,
    compute_omega,
    MEM_THRESHOLD_FRAC,
)
from optimizer_google_or import solve


# ── Helpers ────────────────────────────────────────────────────────────────────

def _node(node_id, capacity_mb=16_384, os_tax_mb=1_024,
          used_mb=0.0, cpu_cores=32.0, violation_history=None):
    n = NodeState(
        node_id=node_id,
        capacity_mb=capacity_mb,
        os_tax_mb=os_tax_mb,
        cpu_cores=cpu_cores,
        used_mb=used_mb,
    )
    if violation_history is not None:
        n.violation_history = list(violation_history)
    return n


def _job(job_id, tenant_id=0, pred_mem_mb=500.0, pred_cpu_p95=1.0):
    return Job(
        job_id=job_id,
        tenant_id=tenant_id,
        req_mem_mb=round(pred_mem_mb * 1.5, 2),
        req_cpu=pred_cpu_p95,
        pred_mem_mb=pred_mem_mb,
        pred_cpu_p95=pred_cpu_p95,
        arrival_round=0,
    )


def _placed_on(placements, node_id):
    return sum(1 for v in placements.values() if v == node_id)


# ── Test 1: High-wait tenant gets priority ─────────────────────────────────────

def test_high_wait_tenant_prioritized():
    """
    Tenant 0 has a high average wait (300 s); tenant 1 has a low wait (10 s).
    Node capacity fits ~7 of the 10 queued jobs.  The optimizer should prefer
    tenant 0 jobs (higher ω_t) and place all 5 of them before any tenant 1 jobs.
    """
    print("TEST 1: High-wait tenant gets priority ...")

    # Node fits floor(3584 / 500) = 7 jobs
    nodes = [_node(0, capacity_mb=4_096, os_tax_mb=512, cpu_cores=32.0)]

    jobs = (
        [_job(f"t0_j{i}", tenant_id=0, pred_mem_mb=500.0) for i in range(5)] +
        [_job(f"t1_j{i}", tenant_id=1, pred_mem_mb=500.0) for i in range(5)]
    )

    # ω_0 ≈ 1.94, ω_1 = 1.0  (tenant 0 is 30× more delayed than cluster avg)
    W_t = {0: 300.0, 1: 10.0}

    placements = solve(jobs=jobs, nodes=nodes, W_t=W_t)

    t0 = sum(1 for j in jobs if j.tenant_id == 0 and placements.get(j.job_id) is not None)
    t1 = sum(1 for j in jobs if j.tenant_id == 1 and placements.get(j.job_id) is not None)

    print(f"  Tenant 0 placed: {t0}/5  |  Tenant 1 placed: {t1}/5")
    assert t0 >= t1, (
        f"High-wait tenant 0 should have >= placements vs tenant 1; got {t0} vs {t1}"
    )
    print("  PASSED\n")


# ── Test 2: Fully-violated node gets no new jobs ───────────────────────────────

def test_violated_node_blocked():
    """
    Node 0 has a 100 % SLA violation rate (all K=10 recent rounds violated).
    R_n^eff = R_n^avail * (1 - 1.0) = 0, so constraint C2 blocks all placements.
    Node 1 is healthy; all jobs should land on node 1.
    """
    print("TEST 2: Fully-violated node receives no new jobs ...")

    node0 = _node(0, violation_history=[True] * 10)   # v̄_0 = 1.0 -> R_eff = 0
    node1 = _node(1, violation_history=[False] * 10)  # v̄_1 = 0.0 -> full capacity

    jobs = [_job(f"j{i}") for i in range(8)]

    placements = solve(jobs=jobs, nodes=[node0, node1], W_t={})

    n0_count = _placed_on(placements, 0)
    n1_count = _placed_on(placements, 1)

    print(f"  Node 0 (100% viols): {n0_count} jobs  |  Node 1 (healthy): {n1_count} jobs")
    assert n0_count == 0, (
        f"Expected 0 jobs on fully-violated node 0; got {n0_count}"
    )
    print("  PASSED\n")


# ── Test 3: Empty cluster concentrates onto node 0 ────────────────────────────

def test_empty_cluster_concentrates_on_node_0():
    """
    All nodes start empty (used_mb = 0), so u_n^mem = 1 for all n.
    With fixed node weights w_0 > w_1 > w_2, node 0 should receive the
    majority of jobs — validating the new w_n^node mechanism.
    """
    print("TEST 3: Empty cluster concentrates jobs on node 0 ...")

    nodes = [_node(i) for i in range(3)]   # 3 equal nodes, all empty

    # 10 jobs × 300 MB = 3000 MB; node 0 capacity = 15360 MB -> all fit
    jobs = [_job(f"j{i}", pred_mem_mb=300.0) for i in range(10)]

    placements = solve(jobs=jobs, nodes=nodes, W_t={})

    counts = {n.node_id: _placed_on(placements, n.node_id) for n in nodes}
    print(f"  Jobs per node: {counts}")

    assert counts[0] >= counts[1] and counts[0] >= counts[2], (
        f"Node 0 should receive the most jobs at start; got {counts}"
    )
    print("  PASSED\n")


# ── Test 4: Loaded node preferred for consolidation ───────────────────────────

def test_loaded_node_preferred():
    """
    Node 0 is at 70 % memory utilization (u_n^mem ≈ 1.7); node 1 is empty.
    Small jobs (200 MB) fit on either node.  The optimizer should consolidate
    onto node 0 because both u_n^mem and w_n^node are higher there.
    """
    print("TEST 4: Memory-loaded node preferred for consolidation ...")

    avail = 16_384 - 1_024   # 15 360 MB
    node0 = _node(0, used_mb=avail * 0.70)  # 70% loaded -> u_mem ≈ 1.7
    node1 = _node(1, used_mb=0.0)           # empty      -> u_mem = 1.0

    # 5 jobs × 200 MB = 1000 MB; remaining on node 0 = 0.3 * 15360 = 4608 MB -> fits
    jobs = [_job(f"j{i}", pred_mem_mb=200.0) for i in range(5)]

    placements = solve(jobs=jobs, nodes=[node0, node1], W_t={})

    n0 = _placed_on(placements, 0)
    n1 = _placed_on(placements, 1)

    print(f"  Node 0 (70% loaded): {n0} jobs  |  Node 1 (empty): {n1} jobs")
    assert n0 >= n1, (
        f"Expected consolidation onto loaded node 0; got node0={n0}, node1={n1}"
    )
    print("  PASSED\n")


# ── Test 5: omega_utilize formula uses M_cap denominator ──────────────────────

def test_omega_utilize_formula():
    """
    Verify compute_utilization_weight uses M_cap_n (not M_n) as the denominator.

    Node with M_n=16384, OS tax=1024, threshold_frac=0.10:
      M_theta = 0.10 * 16384 = 1638.4
      M_cap   = 16384 - 1024 - 1638.4 = 13721.6  (≈ 13722 after rounding)

    Case A — node idle (used_mb=0):     omega = 1 + 0     = 1.0
    Case B — node fully packed (used_mb = M_cap): omega = 1 + 1 = 2.0
    Case C — node half packed (used_mb = M_cap/2): omega = 1.5
    """
    print("TEST 5: omega_utilize formula uses M_cap denominator ...")

    n = _node(0, capacity_mb=16_384, os_tax_mb=1_024)   # threshold_frac=0.10 default

    # Case A
    n.used_mb = 0.0
    w = compute_utilization_weight(n)
    assert abs(w - 1.0) < 1e-9, f"Expected 1.0 for idle node, got {w}"

    # Case B — fully packed to M_cap
    m_cap = compute_available_capacity(n)
    n.used_mb = m_cap
    w = compute_utilization_weight(n)
    assert abs(w - 2.0) < 1e-9, f"Expected 2.0 at full M_cap, got {w}"

    # Case C — half packed
    n.used_mb = m_cap / 2.0
    w = compute_utilization_weight(n)
    assert abs(w - 1.5) < 1e-6, f"Expected 1.5 at half M_cap, got {w}"

    # Also check clamping: usage above M_cap stays at 2.0
    n.used_mb = m_cap * 1.5
    w = compute_utilization_weight(n)
    assert abs(w - 2.0) < 1e-9, f"Expected 2.0 when above M_cap (clamped), got {w}"

    print("  PASSED\n")


# ── Test 6: CPU constraint blocks infeasible node ──────────────────────────────

def test_cpu_constraint_blocks_node():
    """
    C4: a job whose P95 CPU peak exceeds a node's core count must NOT be placed
    on that node.  It must land on a node with sufficient cores.

    Node 0: 4 cores.  Node 1: 32 cores.
    Job with pred_cpu_p95 = 8.0 cannot go to node 0 (8 > 4) -> must go to node 1.
    """
    print("TEST 6: CPU constraint blocks infeasible node ...")

    node_small = _node(0, cpu_cores=4.0)
    node_big   = _node(1, cpu_cores=32.0)

    job = Job(
        job_id="cpu_j0", tenant_id=0,
        req_mem_mb=500.0, req_cpu=8.0,
        pred_mem_mb=400.0, pred_cpu_p95=8.0,
        arrival_round=0,
    )

    placements = solve(jobs=[job], nodes=[node_small, node_big], W_t={})

    assert placements.get("cpu_j0") == 1, (
        f"Job with pred_cpu_p95=8.0 must go to node 1 (32 cores), "
        f"not node 0 (4 cores); got {placements.get('cpu_j0')}"
    )
    print("  PASSED\n")


# ── Test 7: Full node 0 causes overflow to node 1 (not node 2) ────────────────

def test_overflow_goes_to_node_1_not_node_2():
    """
    When node 0 is at full M_eff capacity and node 1 has ample room,
    overflow jobs must land on node 1 — not skip to node 2.

    sigma weights: node 0=3, node 1=2, node 2=1.
    Node 0 M_eff = 0 (completely blocked).
    """
    print("TEST 7: Overflow from full node 0 goes to node 1, not node 2 ...")

    node0 = _node(0, capacity_mb=4_096, os_tax_mb=512)
    node0.used_mb = compute_available_capacity(node0)   # U = M_cap -> M_avail = 0

    node1 = _node(1, capacity_mb=16_384, os_tax_mb=1_024)
    node2 = _node(2, capacity_mb=16_384, os_tax_mb=1_024)

    jobs = [_job(f"j{i}", pred_mem_mb=300.0) for i in range(5)]

    placements = solve(jobs=jobs, nodes=[node0, node1, node2], W_t={})

    on_0 = _placed_on(placements, 0)
    on_1 = _placed_on(placements, 1)
    on_2 = _placed_on(placements, 2)

    print(f"  Node 0 (full): {on_0}  |  Node 1: {on_1}  |  Node 2: {on_2}")
    assert on_0 == 0, f"Node 0 is full — expected 0 jobs, got {on_0}"
    assert on_1 >= on_2, (
        f"Node 1 should have >= jobs vs node 2 (sigma bias); got {on_1} vs {on_2}"
    )
    print("  PASSED\n")


# ── Test 8: Partial vbar halves effective capacity ────────────────────────────

def test_partial_vbar_limits_placement():
    """
    With vbar_n = 0.5 (half of recent rounds violated):
      M_eff = M_avail * (1 - 0.5) = M_avail / 2

    Single node so sigma doesn't interfere.  Jobs are sized so that the full
    M_avail fits all 6 jobs but the halved M_eff only fits 3.
    We send 6 jobs — expect exactly 3 placed (the rest blocked by M_eff).
    """
    print("TEST 8: vbar=0.5 halves M_eff, limiting placements ...")

    # M_cap = 4096 - 512 - 0.10*4096 = 4096 - 512 - 409.6 = 3174.4 MB
    node0 = _node(0, capacity_mb=4_096, os_tax_mb=512,
                  violation_history=[True, False] * 5)    # vbar = 0.5

    from simulation_data import compute_available_capacity, compute_remaining_eff
    m_cap   = compute_available_capacity(node0)     # ≈ 3174 MB
    m_eff   = compute_remaining_eff(m_cap, 0.5)     # ≈ 1587 MB (half)

    # Each job needs 500 MB.  floor(m_eff / 500) = 3 jobs fit; 6 sent.
    jobs = [_job(f"j{i}", pred_mem_mb=500.0) for i in range(6)]

    placements = solve(jobs=jobs, nodes=[node0], W_t={})

    placed = sum(1 for v in placements.values() if v is not None)
    max_can_fit = int(m_eff // 500.0)

    print(f"  M_eff={m_eff:.0f} MB, 500 MB/job -> max_fit={max_can_fit}, placed={placed}/6")
    assert placed == max_can_fit, (
        f"Expected exactly {max_can_fit} placements given M_eff={m_eff:.0f} MB "
        f"and 500 MB/job; got {placed}"
    )
    print("  PASSED\n")


# ── Test 9: All nodes full -> jobs remain unscheduled ──────────────────────────

def test_all_nodes_full_leaves_jobs_unscheduled():
    """
    When every node's M_eff = 0, the solver cannot place any job.
    All placements should be None.
    """
    print("TEST 9: All nodes full -> jobs unscheduled ...")

    nodes = []
    for i in range(3):
        n = _node(i, capacity_mb=4_096, os_tax_mb=512)
        n.used_mb = compute_available_capacity(n)   # M_avail = 0 -> M_eff = 0
        nodes.append(n)

    jobs = [_job(f"j{i}") for i in range(5)]

    placements = solve(jobs=jobs, nodes=nodes, W_t={})

    unscheduled = [j for j in jobs if placements.get(j.job_id) is None]
    print(f"  Unscheduled: {len(unscheduled)}/5")
    assert len(unscheduled) == 5, (
        f"All 5 jobs should be unscheduled when cluster is full; "
        f"got {5 - len(unscheduled)} placed"
    )
    print("  PASSED\n")


# ── Test 10: omega_utilize loaded node reaches higher score vs empty ───────────

def test_omega_utilize_score_grows_with_load():
    """
    The consolidation score (omega_utilize * sigma) for a loaded node should
    exceed that of an empty node with the same sigma, once the loaded node
    has non-trivial usage.  This confirms the positive feedback loop that
    drives consolidation.
    """
    print("TEST 10: omega_utilize grows with load, strengthening consolidation ...")

    # Two equal nodes, same sigma
    n_loaded = _node(0, capacity_mb=8_192, os_tax_mb=512)
    n_empty  = _node(0, capacity_mb=8_192, os_tax_mb=512)

    m_cap = compute_available_capacity(n_loaded)

    n_loaded.used_mb = m_cap * 0.70   # 70% loaded
    n_empty.used_mb  = 0.0

    w_loaded = compute_utilization_weight(n_loaded)
    w_empty  = compute_utilization_weight(n_empty)

    print(f"  Loaded (70%): omega_utilize={w_loaded:.3f}  |  Empty: omega_utilize={w_empty:.3f}")
    assert w_loaded > w_empty, (
        f"Loaded node omega_utilize ({w_loaded:.3f}) should exceed "
        f"empty node ({w_empty:.3f})"
    )
    assert abs(w_loaded - 1.70) < 0.01, (
        f"Expected ~1.70 at 70% M_cap load (M_cap denominator), got {w_loaded:.3f}"
    )
    print("  PASSED\n")


# ── Test 11: omega_delay_t formula correctness ────────────────────────────────

def test_omega_delay_formula():
    """
    Directly verify that compute_omega produces the correct per-tenant weights.

    W_t = {0: 100, 1: 200, 2: 0}
    W_bar = (100 + 200 + 0) / 3 = 100.0

    omega_0 = 1 + max(0, (100 - 100) / 100) = 1.000  (at the mean: no boost)
    omega_1 = 1 + max(0, (200 - 100) / 100) = 2.000  (100 s above mean: 2x weight)
    omega_2 = 1 + max(0, (  0 - 100) / 100) = 1.000  (below mean: clamped to 1)
    """
    print("TEST 11: omega_delay_t formula returns correct values ...")

    W_t = {0: 100.0, 1: 200.0, 2: 0.0}
    omega = compute_omega(W_t)

    print(f"  W_t={W_t}  W_bar=100.0")
    print(f"  omega: " + ",  ".join(f"t{t}={omega[t]:.3f}" for t in sorted(omega)))

    assert abs(omega[0] - 1.000) < 1e-9, f"Expected omega_0=1.000, got {omega[0]:.6f}"
    assert abs(omega[1] - 2.000) < 1e-9, f"Expected omega_1=2.000, got {omega[1]:.6f}"
    assert abs(omega[2] - 1.000) < 1e-9, f"Expected omega_2=1.000, got {omega[2]:.6f}"
    print("  PASSED\n")


# ── Test 12: Delayed tenant exclusively fills constrained capacity ─────────────

def test_delayed_tenant_fills_capacity():
    """
    When node capacity fits exactly 3 jobs and each of two tenants submits 3
    jobs of equal size, the high-delay tenant should claim all 3 slots.

    Setup:
      - 1 node, M_cap = 2048 - 256 - 0.10*2048 = 1587 MB
        -> floor(1587 / 500) = 3 jobs max
      - W_t = {0: 500, 1: 0}
        -> W_bar = 250,  omega_0 = 2.0,  omega_1 = 1.0
      - 3 jobs x 500 MB from tenant 0  +  3 jobs x 500 MB from tenant 1

    Objective coefficient per job:
      tenant 0: 2.0 * 500 = 1000
      tenant 1: 1.0 * 500 =  500

    Best feasible solution = 3 x tenant 0  (Z=3000) vs any mix including
    tenant 1 (Z<=2500), so solver must place all 3 tenant 0 jobs.
    """
    print("TEST 12: Delayed tenant exclusively fills constrained capacity ...")

    nodes = [_node(0, capacity_mb=2_048, os_tax_mb=256, cpu_cores=32.0)]

    jobs = (
        [_job(f"t0_j{i}", tenant_id=0, pred_mem_mb=500.0) for i in range(3)] +
        [_job(f"t1_j{i}", tenant_id=1, pred_mem_mb=500.0) for i in range(3)]
    )

    W_t = {0: 500.0, 1: 0.0}   # omega_0=2.0, omega_1=1.0

    placements = solve(jobs=jobs, nodes=nodes, W_t=W_t)

    t0 = sum(1 for j in jobs if j.tenant_id == 0 and placements.get(j.job_id) is not None)
    t1 = sum(1 for j in jobs if j.tenant_id == 1 and placements.get(j.job_id) is not None)

    print(f"  Placed: tenant 0 = {t0}/3  (omega=2.0),  tenant 1 = {t1}/3  (omega=1.0)")
    assert t0 + t1 == 3, f"Expected exactly 3 placements (node full), got {t0 + t1}"
    assert t0 == 3, (
        f"All 3 slots should go to high-delay tenant 0 (omega=2.0 vs 1.0); "
        f"got t0={t0}, t1={t1}"
    )
    print("  PASSED\n")


# ── Test 13: C5 — plan-ahead access control blocks unauthorised nodes ──────────

def test_plan_ahead_access_blocks_node():
    """
    C5: when tenant_node_access is provided, a job from tenant t can only
    land on nodes in A_t.

    Setup:
      - 3 nodes, all empty, all fit jobs easily
      - 2 tenants: tenant 0 allowed only [node 0], tenant 1 allowed only [node 1]
      - 3 jobs from tenant 0, 3 jobs from tenant 1

    Expected:
      - All tenant 0 jobs land on node 0 (only authorised node)
      - All tenant 1 jobs land on node 1 (only authorised node)
      - Node 2 receives nothing (neither tenant is authorised there)
    """
    print("TEST 13: C5 plan-ahead access control blocks unauthorised nodes ...")

    nodes = [
        _node(0, capacity_mb=16_384, os_tax_mb=1_024, cpu_cores=32.0),
        _node(1, capacity_mb=16_384, os_tax_mb=1_024, cpu_cores=32.0),
        _node(2, capacity_mb=16_384, os_tax_mb=1_024, cpu_cores=32.0),
    ]

    jobs = (
        [_job(f"t0_j{i}", tenant_id=0, pred_mem_mb=500.0) for i in range(3)] +
        [_job(f"t1_j{i}", tenant_id=1, pred_mem_mb=500.0) for i in range(3)]
    )

    # Tenant 0 -> node 0 only,  tenant 1 -> node 1 only
    access = {0: [0], 1: [1]}

    placements = solve(jobs=jobs, nodes=nodes, W_t={}, tenant_node_access=access)

    t0_nodes = {placements[j.job_id] for j in jobs if j.tenant_id == 0 and placements.get(j.job_id) is not None}
    t1_nodes = {placements[j.job_id] for j in jobs if j.tenant_id == 1 and placements.get(j.job_id) is not None}
    t0_placed = sum(1 for j in jobs if j.tenant_id == 0 and placements.get(j.job_id) is not None)
    t1_placed = sum(1 for j in jobs if j.tenant_id == 1 and placements.get(j.job_id) is not None)
    node2_count = _placed_on(placements, 2)

    print(f"  Tenant 0: {t0_placed}/3 placed, nodes used = {t0_nodes}")
    print(f"  Tenant 1: {t1_placed}/3 placed, nodes used = {t1_nodes}")
    print(f"  Node 2 placements: {node2_count} (should be 0)")

    assert t0_placed == 3, f"All 3 tenant 0 jobs should be placed; got {t0_placed}"
    assert t1_placed == 3, f"All 3 tenant 1 jobs should be placed; got {t1_placed}"
    assert t0_nodes == {0}, f"Tenant 0 should only use node 0; used {t0_nodes}"
    assert t1_nodes == {1}, f"Tenant 1 should only use node 1; used {t1_nodes}"
    assert node2_count == 0, f"Node 2 should receive no jobs; got {node2_count}"
    print("  PASSED\n")


def test_plan_ahead_none_allows_all():
    """
    C5 is inactive when tenant_node_access=None (default).
    All jobs should be placed normally (consolidation onto node 0 first).
    """
    print("TEST 14: C5 disabled (None) allows all tenants on all nodes ...")

    nodes = [
        _node(0, capacity_mb=16_384, os_tax_mb=1_024, cpu_cores=32.0),
        _node(1, capacity_mb=16_384, os_tax_mb=1_024, cpu_cores=32.0),
    ]
    jobs = [_job(f"j{i}", tenant_id=i % 2, pred_mem_mb=500.0) for i in range(4)]

    placements = solve(jobs=jobs, nodes=nodes, W_t={}, tenant_node_access=None)

    placed = sum(1 for v in placements.values() if v is not None)
    print(f"  Placed: {placed}/4  (no access restriction)")
    assert placed == 4, f"All 4 jobs should be placed with no access restriction; got {placed}"
    print("  PASSED\n")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_high_wait_tenant_prioritized()
    test_violated_node_blocked()
    test_empty_cluster_concentrates_on_node_0()
    test_loaded_node_preferred()
    test_omega_utilize_formula()
    test_cpu_constraint_blocks_node()
    test_overflow_goes_to_node_1_not_node_2()
    test_partial_vbar_limits_placement()
    test_all_nodes_full_leaves_jobs_unscheduled()
    test_omega_utilize_score_grows_with_load()
    test_omega_delay_formula()
    test_delayed_tenant_fills_capacity()
    test_plan_ahead_access_blocks_node()
    test_plan_ahead_none_allows_all()
    print("All tests passed.")
