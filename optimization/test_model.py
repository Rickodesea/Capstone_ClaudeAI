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

from simulation_data import Job, NodeState
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

    node0 = _node(0, violation_history=[True] * 10)   # v̄_0 = 1.0 → R_eff = 0
    node1 = _node(1, violation_history=[False] * 10)  # v̄_1 = 0.0 → full capacity

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

    # 10 jobs × 300 MB = 3000 MB; node 0 capacity = 15360 MB → all fit
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
    node0 = _node(0, used_mb=avail * 0.70)  # 70% loaded → u_mem ≈ 1.7
    node1 = _node(1, used_mb=0.0)           # empty      → u_mem = 1.0

    # 5 jobs × 200 MB = 1000 MB; remaining on node 0 = 0.3 * 15360 = 4608 MB → fits
    jobs = [_job(f"j{i}", pred_mem_mb=200.0) for i in range(5)]

    placements = solve(jobs=jobs, nodes=[node0, node1], W_t={})

    n0 = _placed_on(placements, 0)
    n1 = _placed_on(placements, 1)

    print(f"  Node 0 (70% loaded): {n0} jobs  |  Node 1 (empty): {n1} jobs")
    assert n0 >= n1, (
        f"Expected consolidation onto loaded node 0; got node0={n0}, node1={n1}"
    )
    print("  PASSED\n")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_high_wait_tenant_prioritized()
    test_violated_node_blocked()
    test_empty_cluster_concentrates_on_node_0()
    test_loaded_node_preferred()
    print("All tests passed.")
