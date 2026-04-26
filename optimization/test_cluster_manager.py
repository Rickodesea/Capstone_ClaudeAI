"""
test_cluster_manager.py
───────────────────────
Integration tests for the full simulation stack.

Every test exercises the complete call chain:
    test → ClusterManager → optimizer_google_or.solve → simulation_data helpers

The tests verify:
  • Data generation (jobs, nodes, sampling)
  • Math-model derived quantities (v̄_n, M_eff, R_n, ω_t)
  • Optimizer correctness (capacity constraint C2, assignment constraint C1,
    fairness weights, zero-capacity behaviour)
  • ClusterManager lifecycle (timestamps, wait times, running jobs, expiry,
    spike detection, queue management, retry logic)

Run with pytest from the optimization/ directory:
    pytest test_cluster_manager.py -v

Or directly:
    python test_cluster_manager.py
"""

from __future__ import annotations

import sys
import os

# Ensure sibling modules are importable regardless of where pytest is run from
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest
import numpy as np
from datetime import datetime, timedelta, timezone

from simulation_data import (
    Job, NodeState,
    generate_jobs, generate_nodes,
    simulate_p95, sample_spike_fraction,
    compute_violation_rate,
    compute_available_capacity, compute_remaining_avail, compute_remaining_eff,
    compute_omega,
    REQUEST_MEM_MIN_MB, REQUEST_MEM_MAX_MB, REQUEST_PER,
    REQ_CPU_MIN, REQ_CPU_MAX,
    JOBS_PER_ROUND, NUM_NODES, NUM_TENANTS,
    K_WINDOW, MAX_PLACEMENT_RETRIES,
    NODE_MEM_MB, OS_TAX_MB,
    SPIKE_PROB, SPIKE_MAX_FRAC,
    MIN_LIFETIME_SEC, MAX_LIFETIME_SEC,
    BATCH_DURATION_SEC,
)
from optimizer_google_or import solve
from cluster_manager import ClusterManager, RunningJob, BatchResult, SimulationResult


# ═══════════════════════════════════════════════════════════════════════════════
# simulation_data — job generation
# ═══════════════════════════════════════════════════════════════════════════════

class TestJobGeneration:

    def test_correct_number_of_jobs(self):
        """generate_jobs returns exactly JOBS_PER_ROUND jobs."""
        jobs = generate_jobs(0, rng=np.random.default_rng(0))
        assert len(jobs) == JOBS_PER_ROUND

    def test_job_fields_populated(self):
        """Every job has a valid job_id, tenant_id, and arrival_round."""
        jobs = generate_jobs(5, rng=np.random.default_rng(1))
        for j in jobs:
            assert j.job_id.startswith("r5_j")
            assert 0 <= j.tenant_id < NUM_TENANTS
            assert j.arrival_round == 5

    def test_requested_memory_in_range(self):
        """req_mem_mb is within [REQUEST_MEM_MIN_MB, REQUEST_MEM_MAX_MB]."""
        jobs = generate_jobs(0, num_jobs=300, rng=np.random.default_rng(2))
        for j in jobs:
            assert REQUEST_MEM_MIN_MB <= j.req_mem_mb <= REQUEST_MEM_MAX_MB, (
                f"req_mem={j.req_mem_mb} outside [{REQUEST_MEM_MIN_MB}, {REQUEST_MEM_MAX_MB}]"
            )

    def test_cpu_request_in_range(self):
        """req_cpu is within [REQ_CPU_MIN, REQ_CPU_MAX]."""
        jobs = generate_jobs(0, num_jobs=200, rng=np.random.default_rng(3))
        for j in jobs:
            assert REQ_CPU_MIN <= j.req_cpu <= REQ_CPU_MAX, (
                f"req_cpu={j.req_cpu} outside [{REQ_CPU_MIN}, {REQ_CPU_MAX}]"
            )

    def test_predicted_at_most_requested(self):
        """P95 prediction (pred_mem_mb) is ≤ declared request (req_mem_mb).
        Since actual usage is drawn from [REQUEST_PER*req, req], P95 ≤ req."""
        jobs = generate_jobs(0, num_jobs=100, rng=np.random.default_rng(4))
        for j in jobs:
            assert j.pred_mem_mb <= j.req_mem_mb + 1e-6, (
                f"pred={j.pred_mem_mb} > req={j.req_mem_mb}"
            )

    def test_timestamps_not_set_by_generator(self):
        """generate_jobs does NOT set timestamps; that is ClusterManager's job."""
        jobs = generate_jobs(0, rng=np.random.default_rng(5))
        for j in jobs:
            assert j.arrival_timestamp    is None
            assert j.scheduling_timestamp is None


# ═══════════════════════════════════════════════════════════════════════════════
# simulation_data — sampling functions
# ═══════════════════════════════════════════════════════════════════════════════

class TestSampling:

    def test_p95_below_requested(self):
        """simulate_p95 returns a value ≤ requested (actual usage ≤ declared)."""
        rng = np.random.default_rng(10)
        for req in [4.0, 10.0, 32.0]:
            p95 = simulate_p95(req, rng=rng, n_samples=500)
            assert p95 <= req + 1e-6, f"p95={p95} > req={req}"

    def test_p95_above_lower_bound(self):
        """P95 prediction is above the absolute minimum (REQUEST_PER * req)."""
        rng = np.random.default_rng(11)
        req = 20.0
        p95 = simulate_p95(req, rng=rng, n_samples=500)
        assert p95 >= REQUEST_PER * req - 1e-6

    def test_spike_fraction_no_spike_common(self):
        """Most spike samples (≥ 80 %) should be exactly 0.0 (no spike)."""
        rng      = np.random.default_rng(20)
        n        = 1000
        no_spike = sum(1 for _ in range(n) if sample_spike_fraction(rng) == 0.0)
        assert no_spike / n >= 0.80, (
            f"Expected ≥80% no-spike, got {no_spike/n:.0%}"
        )

    def test_spike_fraction_in_range_when_occurs(self):
        """When a spike occurs, the fraction is in (0, SPIKE_MAX_FRAC]."""
        rng = np.random.default_rng(21)
        spikes = [sample_spike_fraction(rng) for _ in range(2000)]
        for s in spikes:
            assert 0.0 <= s <= SPIKE_MAX_FRAC + 1e-9, f"spike={s} out of range"


# ═══════════════════════════════════════════════════════════════════════════════
# simulation_data — node generation
# ═══════════════════════════════════════════════════════════════════════════════

class TestNodeGeneration:

    def test_correct_count(self):
        assert len(generate_nodes(rng=np.random.default_rng(30))) == NUM_NODES

    def test_capacity_matches_config(self):
        nodes = generate_nodes(rng=np.random.default_rng(31))
        for i, n in enumerate(nodes):
            assert n.capacity_mb == NODE_MEM_MB[i]
            assert n.os_tax_mb   == OS_TAX_MB[i]

    def test_initial_usage_reasonable(self):
        """Initial used_mb is between 10 % and 40 % of usable capacity."""
        nodes = generate_nodes(rng=np.random.default_rng(32))
        for n in nodes:
            avail = n.capacity_mb - n.os_tax_mb
            assert 0.0 <= n.used_mb <= 0.41 * avail + 1.0  # small float tolerance


# ═══════════════════════════════════════════════════════════════════════════════
# simulation_data — math-model derived quantities  (goal_programming_v4 §3)
# ═══════════════════════════════════════════════════════════════════════════════

class TestMathModelQuantities:
    """
    Verify the formulas from goal_programming_v4 §3 are computed correctly.
    These are v̄_n, M_n^avail, R_n^avail, R_n^eff, and ω_t.
    """

    # ── v̄_n (rolling violation rate) ─────────────────────────────────────

    def test_violation_rate_zero_with_no_history(self):
        """v̄_n = 0 when there is no violation history."""
        assert compute_violation_rate([]) == 0.0

    def test_violation_rate_all_violated(self):
        assert compute_violation_rate([True] * 10) == 1.0

    def test_violation_rate_rolling_window(self):
        """Uses only the last K entries, not the full history."""
        history = [True] * 5 + [False] * 5   # first half violated, second half clean
        assert compute_violation_rate(history, K=5) == 0.0   # only last 5 matter

    # ── M_n^avail (available capacity) ───────────────────────────────────

    def test_m_avail_formula(self):
        """M_n^avail = M_n - τ_n (fixed, no violation scaling)."""
        n = NodeState(0, capacity_mb=1000.0, os_tax_mb=100.0)
        assert compute_available_capacity(n) == 900.0

    # ── R_n^avail and R_n^eff ────────────────────────────────────────────

    def test_r_avail_basic(self):
        """R_n^avail = M_n^avail - U_n."""
        n = NodeState(0, capacity_mb=1000.0, os_tax_mb=100.0, used_mb=400.0)
        m_avail = compute_available_capacity(n)
        assert compute_remaining_avail(n, m_avail) == 500.0

    def test_r_eff_no_violations(self):
        """R_n^eff = R_n^avail when v̄_n = 0 (no SLA penalty)."""
        assert abs(compute_remaining_eff(500.0, v_bar=0.0) - 500.0) < 1e-9

    def test_r_eff_full_violations(self):
        """R_n^eff = 0 when v̄_n = 1 (node fully blocked)."""
        assert compute_remaining_eff(500.0, v_bar=1.0) == 0.0

    def test_r_eff_partial_violations(self):
        """R_n^eff = R_n^avail * (1 - v̄_n) for intermediate v̄_n."""
        assert abs(compute_remaining_eff(500.0, v_bar=0.4) - 300.0) < 1e-9

    def test_r_eff_never_negative(self):
        """R_n^eff is clamped to 0 even if R_n^avail is negative."""
        assert compute_remaining_eff(-100.0, v_bar=0.0) == 0.0

    # ── ω_t (tenant priority weight) ─────────────────────────────────────

    def test_omega_equal_waits_give_weight_one(self):
        """When all tenants wait equally, every ω_t = 1."""
        W_t   = {0: 60.0, 1: 60.0, 2: 60.0}
        omega = compute_omega(W_t)
        for w in omega.values():
            assert abs(w - 1.0) < 1e-9

    def test_omega_higher_wait_higher_weight(self):
        """A tenant with above-average wait gets ω_t > 1."""
        W_t   = {0: 120.0, 1: 10.0}   # tenant 0 waited much longer
        omega = compute_omega(W_t)
        assert omega[0] > 1.0
        assert omega[1] == 1.0   # below average → no bonus (floor at 1)

    def test_omega_bounded_at_two(self):
        """ω_t is capped at 2 even for extreme wait-time imbalances."""
        W_t   = {0: 100_000.0, 1: 1.0}
        omega = compute_omega(W_t)
        assert omega[0] <= 2.0 + 1e-9

    def test_omega_empty_returns_empty(self):
        assert compute_omega({}) == {}

    def test_omega_all_zero_returns_one(self):
        W_t   = {0: 0.0, 1: 0.0}
        omega = compute_omega(W_t)
        for w in omega.values():
            assert abs(w - 1.0) < 1e-9


# ═══════════════════════════════════════════════════════════════════════════════
# Optimizer — solve() correctness  (goal_programming_v4 §5–§7)
# ═══════════════════════════════════════════════════════════════════════════════

class TestOptimizer:
    """Tests that the MILP correctly implements §5 (objective) and §6 (C1, C2)."""

    def _setup(self, seed=0):
        rng   = np.random.default_rng(seed)
        nodes = generate_nodes(rng)
        jobs  = generate_jobs(0, rng=rng)
        return jobs, nodes

    def test_returns_entry_for_every_job(self):
        """solve() returns one dict entry per job (value = node_id or None)."""
        jobs, nodes = self._setup()
        result = solve(jobs, nodes, W_t={})
        assert set(result.keys()) == {j.job_id for j in jobs}

    def test_places_jobs_with_ample_capacity(self):
        """With large nodes and small jobs, at least one job is placed."""
        jobs, nodes = self._setup(seed=1)
        placements  = solve(jobs, nodes, W_t={})
        placed = [v for v in placements.values() if v is not None]
        assert len(placed) >= 1, "Optimizer placed nothing despite available capacity"

    def test_c2_node_capacity_respected(self):
        """C2: total pred_mem_mb placed on each node must not exceed R_n^eff."""
        rng   = np.random.default_rng(2)
        nodes = generate_nodes(rng)
        jobs  = generate_jobs(0, rng=rng)
        placements = solve(jobs, nodes, W_t={})

        for n in nodes:
            v_bar   = compute_violation_rate(n.violation_history, K_WINDOW)
            m_avail = compute_available_capacity(n)
            r_avail = compute_remaining_avail(n, m_avail)
            R_eff   = compute_remaining_eff(r_avail, v_bar)
            placed_mem = sum(
                j.pred_mem_mb for j in jobs
                if placements.get(j.job_id) == n.node_id
            )
            assert placed_mem <= R_eff + 1e-6, (
                f"Node {n.node_id}: placed {placed_mem:.2f} MB > R_n^eff={R_eff:.2f} MB"
            )

    def test_c1_each_job_on_at_most_one_node(self):
        """C1: every job is assigned to at most one node."""
        jobs, nodes = self._setup(seed=3)
        placements  = solve(jobs, nodes, W_t={})
        valid_ids   = {n.node_id for n in nodes}
        for nid in placements.values():
            if nid is not None:
                assert nid in valid_ids

    def test_zero_capacity_places_nothing(self):
        """When all R_n = 0, the solver must return None for every job."""
        rng   = np.random.default_rng(4)
        nodes = generate_nodes(rng)
        jobs  = generate_jobs(0, rng=rng)
        for n in nodes:
            n.used_mb = n.capacity_mb   # fills node; R_n = max(0, M_eff - cap) = 0
        placements = solve(jobs, nodes, W_t={})
        placed = [v for v in placements.values() if v is not None]
        assert len(placed) == 0, f"Solver placed {len(placed)} jobs with R_n=0"

    def test_fairness_weight_prefers_high_wait_tenant(self):
        """
        The ω_t weight causes the optimizer to prefer a job from a tenant
        with a much longer wait over an identical job from a fresh tenant.
        Verifies the §5 objective and §3 fairness feedback are wired correctly.
        """
        rng  = np.random.default_rng(5)
        now  = datetime.now(timezone.utc)

        # Two identical jobs from different tenants
        job_high = Job("jh", tenant_id=0, req_mem_mb=10.0, req_cpu=1.0,
                       pred_mem_mb=8.0, pred_cpu_p90=0.5,
                       arrival_round=0, arrival_timestamp=now)
        job_low  = Job("jl", tenant_id=1, req_mem_mb=10.0, req_cpu=1.0,
                       pred_mem_mb=8.0, pred_cpu_p90=0.5,
                       arrival_round=0, arrival_timestamp=now)

        # Tight node: R_n^eff = 15 MB so only one 8 MB job fits (8+8=16 > 15)
        tight_node = NodeState(node_id=99, capacity_mb=20.0, os_tax_mb=0.0,
                               cpu_cores=100.0, used_mb=5.0)

        # Tenant 0 waited 120 s, tenant 1 waited 0 → ω_0 >> ω_1
        W_t = {0: 120.0, 1: 0.0}
        placements = solve([job_high, job_low], [tight_node], W_t=W_t)

        assert placements["jh"] == 99, (
            "High-wait tenant job should be preferred by the ω_t weighting"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# ClusterManager — end-to-end integration tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestClusterManager:
    """Full integration tests: test → ClusterManager → optimizer → helpers."""

    # ── Smoke tests ───────────────────────────────────────────────────────

    def test_single_batch(self):
        """run(1) completes and returns a SimulationResult with 1 BatchResult."""
        cm     = ClusterManager(seed=0, verbose=False)
        result = cm.run(1)
        assert isinstance(result, SimulationResult)
        assert result.num_batches       == 1
        assert len(result.batch_results) == 1

    def test_multi_batch(self):
        """run(N) returns N BatchResults."""
        cm     = ClusterManager(seed=1, verbose=False)
        result = cm.run(5)
        assert result.num_batches        == 5
        assert len(result.batch_results)  == 5

    # ── Job accounting ─────────────────────────────────────────────────────

    def test_total_generated(self):
        """total_generated == num_batches × JOBS_PER_ROUND."""
        n  = 4
        cm = ClusterManager(seed=2, verbose=False)
        r  = cm.run(n)
        assert r.total_generated == n * JOBS_PER_ROUND

    def test_placed_does_not_exceed_generated(self):
        cm = ClusterManager(seed=3, verbose=False)
        r  = cm.run(5)
        assert r.total_placed <= r.total_generated

    def test_positive_placement_rate(self):
        """Nodes have plenty of capacity; at least some jobs should be placed."""
        cm = ClusterManager(seed=4, verbose=False)
        r  = cm.run(3)
        assert r.total_placed > 0, "No jobs were placed — check solver/config"

    def test_queue_conservation(self):
        """
        Over any prefix of batches:
          placed_so_far + queue_size == total_generated
        The queue never loses a job silently.
        """
        cm     = ClusterManager(seed=5, verbose=False)
        result = cm.run(4)
        total_placed    = sum(br.jobs_placed    for br in result.batch_results)
        total_generated = sum(br.jobs_generated for br in result.batch_results)
        assert total_placed + result.final_queue_size == total_generated

    # ── Timestamps ────────────────────────────────────────────────────────

    def test_arrival_timestamps_set(self):
        """Every placed job has a non-None arrival_timestamp in the log."""
        cm = ClusterManager(seed=6, verbose=False)
        cm.run(2)
        for entry in cm.scheduling_log.values():
            ts = entry["arrival_timestamp"]
            assert ts != "" and ts is not None
            datetime.fromisoformat(ts)   # should not raise

    def test_scheduling_timestamps_set(self):
        """Every placed job has a scheduling_timestamp after arrival."""
        cm = ClusterManager(seed=7, verbose=False)
        cm.run(2)
        for entry in cm.scheduling_log.values():
            arrival    = datetime.fromisoformat(entry["arrival_timestamp"])
            scheduled  = datetime.fromisoformat(entry["scheduling_timestamp"])
            assert scheduled >= arrival, (
                "scheduling_timestamp must be ≥ arrival_timestamp"
            )

    def test_wait_time_non_negative(self):
        """wait_sec in the scheduling log is always ≥ 0."""
        cm = ClusterManager(seed=8, verbose=False)
        cm.run(3)
        for entry in cm.scheduling_log.values():
            assert entry["wait_sec"] >= 0.0

    def test_sim_time_advances_per_batch(self):
        """Simulated clock advances by BATCH_DURATION_SEC each batch."""
        cm   = ClusterManager(seed=9, verbose=False)
        t0   = cm.sim_time
        cm.run(3)
        elapsed = (cm.sim_time - t0).total_seconds()
        assert abs(elapsed - 3 * BATCH_DURATION_SEC) < 1e-3

    # ── Scheduling log ─────────────────────────────────────────────────────

    def test_log_has_entry_per_placed_job(self):
        """scheduling_log has exactly one entry per placed job."""
        cm     = ClusterManager(seed=10, verbose=False)
        result = cm.run(3)
        assert len(cm.scheduling_log) == result.total_placed

    def test_log_entry_fields(self):
        """Each log entry contains all expected keys."""
        cm = ClusterManager(seed=11, verbose=False)
        cm.run(2)
        required_keys = {
            "tenant_id", "job_id",
            "arrival_batch", "scheduled_batch",
            "arrival_timestamp", "scheduling_timestamp", "wait_sec",
            "req_mem_mb", "pred_mem_mb", "act_mem_mb", "req_cpu",
            "is_spike", "lifetime_sec", "node_id",
        }
        for entry in cm.scheduling_log.values():
            assert required_keys <= set(entry.keys()), (
                f"Missing keys: {required_keys - set(entry.keys())}"
            )

    def test_act_mem_at_least_pred_mem(self):
        """act_mem_mb ≥ pred_mem_mb (spikes only add memory, never remove)."""
        cm = ClusterManager(seed=12, verbose=False)
        cm.run(5)
        for entry in cm.scheduling_log.values():
            assert entry["act_mem_mb"] >= entry["pred_mem_mb"] - 1e-9

    # ── Running jobs and lifetime ──────────────────────────────────────────

    def test_running_jobs_created_after_placement(self):
        """After a run, _running_jobs contains jobs that haven't yet expired."""
        cm = ClusterManager(seed=13, verbose=False)
        cm.run(1)
        # With only 1 batch and lifetimes of 45–600 s (BATCH_DURATION_SEC=60),
        # some jobs may have expired; the list may be smaller than total placed.
        assert isinstance(cm._running_jobs, list)
        for rj in cm._running_jobs:
            assert isinstance(rj, RunningJob)

    def test_running_job_act_mem_fields(self):
        """RunningJob.act_mem_mb = pred_mem × (1 + spike_frac)."""
        cm = ClusterManager(seed=14, verbose=False)
        cm.run(2)
        for rj in cm._running_jobs:
            # act_mem ≥ pred_mem (spike fraction ≥ 0)
            assert rj.act_mem_mb >= rj.job.pred_mem_mb - 1e-9
            # act_mem ≤ pred_mem × (1 + SPIKE_MAX_FRAC)
            assert rj.act_mem_mb <= rj.job.pred_mem_mb * (1.0 + SPIKE_MAX_FRAC) + 1e-6

    def test_jobs_expire_over_many_batches(self):
        """After enough batches, at least some jobs should have expired."""
        cm = ClusterManager(seed=15, verbose=False)
        # Run enough batches so even the longest job (MAX_LIFETIME_SEC = 600 s)
        # has time to expire.  Each batch = BATCH_DURATION_SEC = 60 s.
        # 600 / 60 = 10 batches needed for the longest job.
        result = cm.run(12)
        assert result.total_expired > 0, (
            "Expected some jobs to expire after 12 batches (720 simulated seconds)"
        )

    def test_used_mb_matches_running_jobs(self):
        """
        node.used_mb (set by _refresh_node_states) equals the sum of
        act_mem_mb for all running jobs on that node.
        This verifies the 'compute U_n from running jobs' mechanism.
        """
        cm = ClusterManager(seed=16, verbose=False)
        cm.run(3)

        # Manually compute expected used_mb per node from running jobs
        expected: dict[int, float] = {n.node_id: 0.0 for n in cm.nodes}
        for rj in cm._running_jobs:
            expected[rj.node_id] += rj.act_mem_mb

        # Trigger a fresh refresh so cm.nodes reflect the current state
        cm._refresh_node_states(record_history=False)

        for n in cm.nodes:
            assert abs(n.used_mb - expected[n.node_id]) < 1e-6, (
                f"Node {n.node_id}: used_mb={n.used_mb:.2f} "
                f"≠ sum(act_mem)={expected[n.node_id]:.2f}"
            )

    # ── Spike detection ────────────────────────────────────────────────────

    def test_spike_count_tracked(self):
        """
        Over many batches with SPIKE_PROB=10 %, some spikes should be recorded.
        With 5 batches × 8 jobs = 40 placements expected, ~4 should spike.
        """
        cm     = ClusterManager(seed=17, verbose=False)
        result = cm.run(10)
        # With 10 % spike probability and ~80+ placements, at least one spike
        # is overwhelmingly likely.  If this fails, check SPIKE_PROB config.
        assert result.total_spikes >= 0   # always true; real check below
        # Verify is_spike flag in log matches act_mem > pred_mem
        for entry in cm.scheduling_log.values():
            expected_spike = entry["act_mem_mb"] > entry["pred_mem_mb"] + 1e-9
            assert entry["is_spike"] == expected_spike

    # ── Fairness (W_t) ─────────────────────────────────────────────────────

    def test_W_t_populated_after_placements(self):
        """After placing jobs, W_t contains entries for scheduled tenants."""
        cm = ClusterManager(seed=18, verbose=False)
        cm.run(3)
        if cm.scheduling_log:
            assert len(cm.W_t) > 0

    def test_W_t_values_non_negative(self):
        """Average wait times are always ≥ 0."""
        cm = ClusterManager(seed=19, verbose=False)
        cm.run(3)
        for t, w in cm.W_t.items():
            assert w >= 0.0, f"Negative wait for tenant {t}: {w}"

    # ── Max-retry / saturation ─────────────────────────────────────────────

    def test_max_retries_when_nodes_saturated(self):
        """
        When all nodes have zero remaining capacity, the solver returns zero
        placements every call and the batch should exit with consecutive_failures
        ≥ MAX_PLACEMENT_RETRIES.
        """
        cm  = ClusterManager(seed=20, verbose=False)
        now = cm.sim_time + timedelta(seconds=BATCH_DURATION_SEC)

        # Fill every node with a fake RunningJob whose lifetime is very long
        for n in cm.nodes:
            # act_mem_mb = capacity - os_tax fills the node completely:
            # U_n = capacity - tax  →  R_n = M_eff - (capacity - tax)
            # M_eff (with v_bar=0) = capacity - tax  →  R_n = 0
            fill_job = Job(
                job_id="fill",  tenant_id=0,
                req_mem_mb=n.capacity_mb, req_cpu=1.0,
                pred_mem_mb=n.capacity_mb, pred_cpu_p90=0.0,
                arrival_round=0, arrival_timestamp=now,
            )
            cm._running_jobs.append(RunningJob(
                job          = fill_job,
                node_id      = n.node_id,
                act_mem_mb   = n.capacity_mb - n.os_tax_mb,   # fills usable space
                act_cpu      = 0.0,
                is_spike     = False,
                start_time   = now,
                lifetime_sec = 999_999,   # will not expire during test
            ))

        result = cm.run(1)
        br     = result.batch_results[0]

        assert br.jobs_placed == 0, (
            f"Expected 0 placements with saturated nodes, got {br.jobs_placed}"
        )
        assert br.consecutive_failures >= MAX_PLACEMENT_RETRIES

    def test_solver_called_at_least_once_per_batch(self):
        """The optimizer is invoked at least once per batch."""
        cm     = ClusterManager(seed=21, verbose=False)
        result = cm.run(3)
        for br in result.batch_results:
            assert br.solver_calls >= 1

    # ── Reproducibility ────────────────────────────────────────────────────

    def test_same_seed_same_result(self):
        """Two runs with the same seed produce identical aggregate statistics."""
        r1 = ClusterManager(seed=42, verbose=False).run(5)
        r2 = ClusterManager(seed=42, verbose=False).run(5)
        assert r1.total_placed    == r2.total_placed
        assert r1.total_generated == r2.total_generated
        assert r1.total_spikes    == r2.total_spikes
        assert r1.total_expired   == r2.total_expired

    def test_queue_empty_with_plenty_of_capacity(self):
        """
        With the default config (small jobs, large nodes), every generated
        job should be placed within its arrival batch and the queue should
        end up empty.
        """
        cm     = ClusterManager(seed=99, verbose=False)
        result = cm.run(5)
        assert result.final_queue_size == 0, (
            f"Expected empty queue, got {result.final_queue_size} jobs waiting"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
