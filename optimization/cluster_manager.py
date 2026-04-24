"""
cluster_manager.py
──────────────────
The central simulation driver for the multi-tenant cluster scheduler.

How the pieces fit together
────────────────────────────
  simulation_data.py      config constants, dataclasses, sampling helpers
  optimizer_google_or.py  MILP solver  (goal_programming_v4)
  cluster_manager.py      orchestration — THIS FILE
  test_cluster_manager.py integration tests

Key concepts
─────────────
Job lifetime
    Every job placed by the optimizer is assigned a random lifetime drawn
    from [MIN_LIFETIME_SEC, MAX_LIFETIME_SEC].  The cluster manager tracks
    all running jobs in self._running_jobs.  At the start of each batch,
    expired jobs are removed and their memory is returned to the node.
    This replaces the old MEMORY_RELEASE_FRAC approach with a proper
    per-job lifecycle.

Node used_mb (U_n)
    The optimizer needs U_n — the current memory usage on each node.
    Rather than accumulating it as a running total, the cluster manager
    RECOMPUTES it from scratch each round by summing act_mem_mb of all
    RunningJobs on that node.  This is always accurate and self-correcting.

Actual memory vs predicted memory
    The prediction layer (teammates' model) produces pred_mem_mb = m̂_j.
    For this simulation we assume:
        act_mem = pred_mem                     (most runs, no spike)
        act_mem = pred_mem × (1 + spike_frac)  (SPIKE_PROB fraction of jobs)
    spike_frac ~ Uniform(0, SPIKE_MAX_FRAC) when a spike occurs.
    The optimizer plans using pred_mem; act_mem is what is actually consumed.

Timestamps (simulated time)
    Each batch advances a simulated clock by BATCH_DURATION_SEC seconds.
    Jobs receive:
        arrival_timestamp    — when the job entered the waiting queue
        scheduling_timestamp — when the optimizer placed it on a node
    wait_sec = (scheduling_timestamp − arrival_timestamp).total_seconds()

Feedback loops (goal_programming_v4 §4)
    SLA feedback   → v̄_n is updated once per batch; the optimizer uses it
                     to shrink available capacity on struggling nodes (θ_n).
    Fairness       → W_t is recomputed after every placement group; the
                     optimizer uses it to boost ω_t for waiting tenants.

Usage
─────
    from cluster_manager import ClusterManager
    cm     = ClusterManager(seed=42)
    result = cm.run(num_batches=10)
    print(result)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np

from simulation_data import (
    Job, NodeState,
    generate_jobs, generate_nodes,
    compute_violation_rate, compute_effective_capacity, compute_remaining,
    sample_spike_fraction,
    JOBS_PER_ROUND, GAMMA, ALPHA, K_WINDOW,
    MAX_PLACEMENT_RETRIES, MIN_LIFETIME_SEC, MAX_LIFETIME_SEC,
    BATCH_DURATION_SEC,
)
from optimizer_google_or import solve


# ═══════════════════════════════════════════════════════════════════════════════
# § RUNNING JOB — tracks a placed job during its execution
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class RunningJob:
    """
    A job that has been placed by the optimizer and is currently running.

    The cluster manager holds a list of RunningJob objects.  Each batch,
    expired jobs (end_time ≤ sim_time) are removed and their memory freed.

    Fields:
        job           the original Job object (carries req_mem, pred_mem, etc.)
        node_id       the node the job was placed on
        act_mem_mb    actual memory consumed = pred_mem × (1 + spike_frac)
                      This is what is physically charged against the node.
        is_spike      True if act_mem_mb > job.pred_mem_mb
        start_time    simulated UTC datetime when the job started running
        lifetime_sec  randomly assigned job duration in simulated seconds
    """
    job:          Job
    node_id:      int
    act_mem_mb:   float   # actual memory (pred_mem + optional spike)
    is_spike:     bool    # True  → spike occurred; act_mem > pred_mem
    start_time:   datetime
    lifetime_sec: float

    @property
    def end_time(self) -> datetime:
        """When this job is considered complete (start + lifetime)."""
        return self.start_time + timedelta(seconds=self.lifetime_sec)

    def has_expired(self, now: datetime) -> bool:
        """Returns True if the job's lifetime has passed by simulated time now."""
        return now >= self.end_time


# ═══════════════════════════════════════════════════════════════════════════════
# § RESULT DATACLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class BatchResult:
    """
    Statistics captured at the end of one scheduling epoch (batch).

    Attributes:
        batch_id               zero-based index of this batch
        jobs_generated         new jobs added to the queue this batch
        jobs_placed            jobs successfully scheduled this batch
        queue_size_after       jobs still waiting after this batch ends
        solver_calls           how many times the MILP was invoked this batch
        consecutive_failures   how many consecutive zero-placement calls at exit
                               0 = queue drained cleanly
                               ≥ MAX_PLACEMENT_RETRIES = gave up (nodes full)
        node_violations        number of nodes where U_n > M_eff at batch start
                               (SLA violation — exceeds safety threshold)
        spike_count            number of placed jobs whose act_mem > pred_mem
        physical_overflow_count number of nodes where U_n + τ_n > M_n
                               (critical: tenant jobs + OS exceed physical RAM)
        jobs_expired           running jobs that completed and were removed
    """
    batch_id:                int
    jobs_generated:          int
    jobs_placed:             int
    queue_size_after:        int
    solver_calls:            int
    consecutive_failures:    int
    node_violations:         int   # U_n > M_eff
    spike_count:             int   # act_mem > pred_mem
    physical_overflow_count: int   # U_n + τ_n > M_n  (critical)
    jobs_expired:            int
    nodes_assigned:          int  # Unique nodes that received a job THIS batch
    total_nodes_used:        int  # Total nodes with at least one running job
    avg_eff_mem_pct:         float  # (U_n / M_eff) * 100
    avg_phys_mem_pct:        float  # (U_n / M_n) * 100


@dataclass
class SimulationResult:
    """Aggregate statistics across all batches of a simulation run."""
    num_batches:      int
    total_generated:  int
    total_placed:     int
    final_queue_size: int
    total_violations: int   # SLA: U_n > M_eff
    total_spikes:     int   # job-level: act_mem > pred_mem
    total_overflows:  int   # critical: U_n + τ_n > M_n
    total_expired:    int   # jobs that completed naturally
    batch_results:    list[BatchResult]
    final_W_t:        dict[int, float]   # avg wait per tenant (seconds)

    def placement_rate(self) -> float:
        """Fraction of generated jobs that were eventually scheduled."""
        return self.total_placed / max(1, self.total_generated)

    def __str__(self) -> str:
        lines = [
            f"SimulationResult — {self.num_batches} batches",
            f"  generated : {self.total_generated}",
            f"  placed    : {self.total_placed}  ({self.placement_rate():.1%})",
            f"  queue left: {self.final_queue_size}",
            f"  violations: {self.total_violations}  (U_n > M_eff)",
            f"  spikes    : {self.total_spikes}      (act > pred)",
            f"  overflows : {self.total_overflows}   (U_n + tax > capacity)",
            f"  expired   : {self.total_expired}",
            f"  W_t final : { {t: round(w, 1) for t, w in self.final_W_t.items()} } sec",
        ]
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# § CLUSTER MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

class ClusterManager:
    """
    Orchestrates the multi-tenant cluster scheduling simulation.

    Responsibilities:
      • Maintain a shared job queue across batches
      • Generate new jobs each batch and stamp arrival timestamps
      • Call the MILP optimizer to place queued jobs
      • Track running jobs with lifetimes; expire them when done
      • Compute node U_n from running jobs (accurate, no drift)
      • Update SLA violation history (once per batch)
      • Record scheduling log with timestamps and wait times
      • Maintain per-tenant average wait time W_t for the fairness feedback

    Parameters
    ----------
    seed    : RNG seed for full reproducibility (None = non-deterministic)
    verbose : print a one-line summary after each batch
    """

    def __init__(self, seed: Optional[int] = None, verbose: bool = True) -> None:
        self.rng     = np.random.default_rng(seed)
        self.verbose = verbose

        # ── Cluster state ──────────────────────────────────────────────────
        # Nodes are initialised with some pre-existing usage; recomputed
        # from running jobs from batch 1 onward.
        self.nodes: list[NodeState] = generate_nodes(self.rng)

        # ── Job queue (persists across batches) ────────────────────────────
        # Un-placed jobs stay here, accumulating wait time that eventually
        # raises their ω_t weight so the optimizer prefers them later.
        self.job_queue: list[Job] = []

        # ── Running jobs (the "active set") ───────────────────────────────
        # Every placed job lives here until its lifetime expires.
        # node.used_mb is computed by summing act_mem_mb of jobs here.
        self._running_jobs: list[RunningJob] = []

        # ── Scheduling log ─────────────────────────────────────────────────
        # One entry per placed job.  Written when a job is scheduled.
        # Used to compute per-tenant wait-time averages.
        self.scheduling_log: dict[str, dict] = {}

        # ── Fairness state (goal_programming_v4 §3, §4) ───────────────────
        # W_t  : current per-tenant average wait time (seconds).
        #        Fed to optimizer so it can compute ω_t weights.
        # _tenant_wait_times : raw list of individual wait times per tenant,
        #        used to update W_t after each placement group.
        self.W_t: dict[int, float] = {}
        self._tenant_wait_times: dict[int, list[float]] = {}

        # ── Simulated clock ────────────────────────────────────────────────
        # sim_time advances by BATCH_DURATION_SEC at the start of each batch.
        # All timestamps on jobs are expressed in this simulated time so that
        # wait_sec is meaningful across batches.
        self.sim_time: datetime = datetime.now(timezone.utc)

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def run(self, num_batches: int) -> SimulationResult:
        """
        Run the simulation for num_batches scheduling epochs.

        Each batch:
          1. Advance the simulated clock by BATCH_DURATION_SEC.
          2. Expire running jobs whose lifetime has passed; free their memory.
          3. Recompute each node's U_n from remaining running jobs.
          4. Record one SLA violation history entry per node (for v̄_n).
          5. Generate JOBS_PER_ROUND new jobs; stamp arrival_timestamp.
          6. Repeat: call MILP solver → update state → until queue empty
             or MAX_PLACEMENT_RETRIES consecutive zero-placement calls.

        Returns a SimulationResult with per-batch and aggregate statistics.
        """
        batch_results: list[BatchResult] = []

        if self.verbose:
            print(
                f"{'Batch':>5}  {'New':>6} {'Placed':>6}  {'Queue':>5}  "
                f"{'Viols':>5} {'Spike':>5}  {'Ovrflw':>6}  "
                f"{'Assign':>6}  {'Used':>5}  "
                f"{'Eff Mem %':>10}  {'Phys Mem %':>10}"
            )
            print("─" * 100)

        for batch_id in range(num_batches):
            result = self._run_batch(batch_id)
            batch_results.append(result)
            if self.verbose:
                self._print_batch(result)

        if self.verbose:
            print("─" * 100)

        return SimulationResult(
            num_batches      = num_batches,
            total_generated  = sum(r.jobs_generated          for r in batch_results),
            total_placed     = sum(r.jobs_placed              for r in batch_results),
            final_queue_size = len(self.job_queue),
            total_violations = sum(r.node_violations          for r in batch_results),
            total_spikes     = sum(r.spike_count              for r in batch_results),
            total_overflows  = sum(r.physical_overflow_count  for r in batch_results),
            total_expired    = sum(r.jobs_expired             for r in batch_results),
            batch_results    = batch_results,
            final_W_t        = dict(self.W_t),
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Internal: batch execution
    # ─────────────────────────────────────────────────────────────────────────

    def _run_batch(self, batch_id: int) -> BatchResult:
        """Execute one scheduling epoch and return its statistics."""

        # ── Step 1: Advance simulated clock ───────────────────────────────
        # From this point on, sim_time represents "now" for this batch.
        # All new jobs and placed jobs get timestamps = sim_time.
        self.sim_time += timedelta(seconds=BATCH_DURATION_SEC)

        # ── Step 2: Expire completed jobs ─────────────────────────────────
        # Remove running jobs whose lifetime has passed.
        # Their memory is freed implicitly — _compute_node_used_mb() will
        # no longer include them when we recompute U_n next.
        expired_count = self._expire_jobs()

        # ── Step 3: Recompute U_n and record SLA violation history ────────
        # U_n = sum of act_mem_mb for all still-running jobs on node n.
        # We record ONE violation entry per node per batch (for v̄_n rolling
        # window).  Within a batch, node.used_mb is updated for the solver
        # but violation_history is NOT appended again — only once per batch.
        #
        # This implements the SLA feedback loop from §4:
        #   violation → v̄_n rises → θ_n grows → M_eff shrinks → R_n shrinks
        #   → optimizer places fewer jobs there → over time violations subside
        node_violations_start = self._refresh_node_states(record_history=True)

        # ── Step 4: Generate new jobs and add to queue ────────────────────
        # arrival_timestamp is set to sim_time (current simulated time).
        # New jobs join any un-placed jobs from previous batches.
        new_jobs = self._make_jobs(batch_id)
        self.job_queue.extend(new_jobs)

        # ── Step 5: Scheduling loop ────────────────────────────────────────
        # Call the optimizer repeatedly until the queue drains or
        # MAX_PLACEMENT_RETRIES consecutive zero-placement calls occur.
        solver_calls         = 0
        placed_this_batch    = 0
        spikes_this_batch    = 0
        overflows_this_batch = 0
        consecutive_failures = 0
        nodes_assigned_set   = set()

        while self.job_queue:

            if consecutive_failures >= MAX_PLACEMENT_RETRIES:
                # The solver cannot place anything more (nodes saturated).
                # Remaining jobs carry over to the next batch, where they
                # accumulate more wait time → higher ω_t → better priority.
                break

            # Refresh U_n before each solver call so the optimizer sees
            # the most up-to-date remaining capacity R_n.
            # (Do NOT record violation history again within the same batch.)
            self._refresh_node_states(record_history=False)

            # ── Call the MILP optimizer (goal_programming_v4 §5–§7) ───────
            #
            # Inputs from this manager:
            #   job_queue : set J   — pending jobs with pred_mem_mb (m̂_j)
            #   nodes     : set N   — current state including U_n and v̄_n
            #   W_t       : W̄_t   — per-tenant average wait (→ ω_t weights)
            #
            # The solver returns: job_id → node_id | None
            placements = solve(
                jobs  = self.job_queue,
                nodes = self.nodes,
                W_t   = self.W_t,
                gamma = GAMMA,
                alpha = ALPHA,
                K     = K_WINDOW,
            )
            solver_calls += 1

            # Split queue: placed vs still waiting
            placed_jobs: list[Job] = [
                j for j in self.job_queue
                if placements.get(j.job_id) is not None
            ]

            if not placed_jobs:
                # Zero placements this call — nodes may be near capacity
                consecutive_failures += 1
                continue

            consecutive_failures = 0   # reset: we made progress

            # ── Process each placed job ────────────────────────────────────
            for j in placed_jobs:
                nid = placements[j.job_id]
                nodes_assigned_set.add(nid)

                # Create a RunningJob with simulated act_mem and lifetime.
                # This sets j.scheduling_timestamp = sim_time.
                rj = self._start_job(j, nid)

                # Compute wait time (seconds in simulated time)
                wait_sec = (
                    j.scheduling_timestamp - j.arrival_timestamp
                ).total_seconds()

                # Write full placement record to the scheduling log
                self.scheduling_log[j.job_id] = {
                    # Identity
                    "tenant_id":             j.tenant_id,
                    "job_id":                j.job_id,
                    # Batch indices
                    "arrival_batch":         j.arrival_round,
                    "scheduled_batch":       batch_id,
                    # Wall-clock (simulated) timestamps
                    "arrival_timestamp":     j.arrival_timestamp.isoformat(),
                    "scheduling_timestamp":  j.scheduling_timestamp.isoformat(),
                    # Wait time
                    "wait_sec":              wait_sec,
                    # Memory fields (all three for comparison)
                    "req_mem_mb":            j.req_mem_mb,    # declared
                    "pred_mem_mb":           j.pred_mem_mb,   # m̂_j  (optimizer input)
                    "act_mem_mb":            rj.act_mem_mb,   # actual consumption
                    # CPU (not used in optimizer — future work)
                    "req_cpu":               j.req_cpu,
                    # Spike / lifetime
                    "is_spike":              rj.is_spike,
                    "lifetime_sec":          rj.lifetime_sec,
                    # Placement
                    "node_id":               nid,
                }

                # Track spike
                if rj.is_spike:
                    spikes_this_batch += 1

                # Accumulate wait time for this tenant (used to recompute W_t)
                self._tenant_wait_times.setdefault(j.tenant_id, []).append(wait_sec)

            # ── Remove placed jobs from queue ──────────────────────────────
            placed_ids = {j.job_id for j in placed_jobs}
            self.job_queue = [
                j for j in self.job_queue if j.job_id not in placed_ids
            ]
            placed_this_batch += len(placed_jobs)

            # ── Update W_t (fairness feedback §4) ─────────────────────────
            # Recompute per-tenant average wait times so the NEXT solver
            # call within this batch (or the next batch) uses fresh ω_t.
            self._update_W_t()

            # ── Check for physical memory overflow ─────────────────────────
            # After placing this group, refresh U_n and check if any node
            # has tenant jobs + OS tax exceeding physical RAM.
            # This is more severe than an SLA violation (which uses M_eff
            # which already includes the safety buffer).
            self._refresh_node_states(record_history=False)
            for n in self.nodes:
                # Physical overflow: job memory + OS overhead > physical RAM
                if n.used_mb + n.os_tax_mb > n.capacity_mb:
                    overflows_this_batch += 1
                    # Note: this counts node-events, so a node can be counted
                    # multiple times if multiple placement groups trigger it.

        # Count nodes that have at least one job currently running
        active_node_ids = {rj.node_id for rj in self._running_jobs}
        total_nodes_used = len(active_node_ids)

        # Calc mem %
        eff_pcts = []
        phys_pcts = []
        for n in self.nodes:
            # Physical usage: U_n / M_n
            phys_pcts.append((n.used_mb / n.capacity_mb) * 100)
            
            # Effective usage: U_n / M_eff
            v_bar = compute_violation_rate(n.violation_history, K_WINDOW)
            m_eff = compute_effective_capacity(n, v_bar, GAMMA)
            eff_pcts.append((n.used_mb / max(1, m_eff)) * 100)

        return BatchResult(
            batch_id                = batch_id,
            jobs_generated          = len(new_jobs),
            jobs_placed             = placed_this_batch,
            queue_size_after        = len(self.job_queue),
            solver_calls            = solver_calls,
            consecutive_failures    = consecutive_failures,
            node_violations         = node_violations_start,
            spike_count             = spikes_this_batch,
            physical_overflow_count = overflows_this_batch,
            jobs_expired            = expired_count,
            nodes_assigned          = len(nodes_assigned_set),
            total_nodes_used        = total_nodes_used,
            avg_eff_mem_pct         = sum(eff_pcts) / len(eff_pcts),
            avg_phys_mem_pct        = sum(phys_pcts) / len(phys_pcts), 
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _make_jobs(self, batch_id: int) -> list[Job]:
        """
        Generate JOBS_PER_ROUND new jobs for this batch.

        Stamps each job's arrival_timestamp = sim_time so that wait times
        can be computed in seconds when the job is eventually scheduled.
        CPU fields are generated but not used in the optimizer.
        """
        jobs = generate_jobs(batch_id, rng=self.rng)
        for j in jobs:
            # arrival_timestamp is set here, when the job enters the queue.
            # scheduling_timestamp will be set in _start_job() when placed.
            j.arrival_timestamp = self.sim_time
        return jobs

    def _expire_jobs(self) -> int:
        """
        Remove running jobs whose simulated lifetime has elapsed.

        A job is expired when sim_time >= job.end_time.  Since node.used_mb
        is computed dynamically from _running_jobs, removing expired jobs
        automatically frees their memory — no explicit decrement needed.

        Returns the count of jobs that were removed.
        """
        active, expired = [], []
        for rj in self._running_jobs:
            (expired if rj.has_expired(self.sim_time) else active).append(rj)
        self._running_jobs = active
        return len(expired)

    def _compute_node_used_mb(self) -> dict[int, float]:
        """
        Compute U_n for each node by summing act_mem_mb of all running jobs.

        This is the correct way to compute U_n — it's always consistent
        with the actual active job set and automatically reflects expiry.
        Returns a dict {node_id: used_mb}.
        """
        used: dict[int, float] = {n.node_id: 0.0 for n in self.nodes}
        for rj in self._running_jobs:
            used[rj.node_id] += rj.act_mem_mb
        return used

    def _refresh_node_states(self, record_history: bool) -> int:
        """
        Recompute U_n for all nodes and optionally record SLA violation history.

        Called at the start of each batch (record_history=True) and before
        each solver call within a batch (record_history=False).

        When record_history=True:
          Appends one bool per node to violation_history.
          A violation = (U_n > M_eff), i.e. actual job memory exceeds the
          safety-adjusted capacity.  This feeds the SLA feedback loop (§4):
          the rolling rate v̄_n is computed from this history each solver call.

        Returns the count of nodes currently in violation (U_n > M_eff).
        """
        used       = self._compute_node_used_mb()
        violations = 0

        for n in self.nodes:
            n.used_mb = used[n.node_id]   # update U_n

            # Compute M_eff using the current violation rate v̄_n.
            # Note: v̄_n itself is computed from violation_history, which we
            # are ABOUT to extend (if record_history=True), so this read
            # uses the history UP TO the previous batch — correct behaviour.
            v_bar = compute_violation_rate(n.violation_history, K_WINDOW)
            m_eff = compute_effective_capacity(n, v_bar, GAMMA)

            in_violation = n.used_mb > m_eff  # U_n > M_eff → SLA breach

            if record_history:
                # Append once per batch so K_WINDOW covers K past batches
                n.violation_history.append(in_violation)

            if in_violation:
                violations += 1

        return violations

    def _start_job(self, job: Job, node_id: int) -> RunningJob:
        """
        Register a placed job as running.

        Actions:
          1. Set job.scheduling_timestamp = sim_time.
          2. Sample a spike fraction (usually 0; ~10 % of jobs spike).
          3. Compute act_mem_mb = pred_mem × (1 + spike_frac).
             This is the memory that will be charged to the node.
          4. Draw a random lifetime in [MIN_LIFETIME_SEC, MAX_LIFETIME_SEC].
          5. Create and store a RunningJob.

        The RunningJob is added to self._running_jobs, which means future
        calls to _compute_node_used_mb() will include its act_mem_mb in U_n.
        """
        # Stamp the scheduling time (simulated wall-clock)
        job.scheduling_timestamp = self.sim_time

        # Determine actual memory consumption
        # Most of the time spike_frac = 0 (no spike).
        # About SPIKE_PROB = 10 % of the time, a spike adds 0–20 % extra.
        spike_frac  = sample_spike_fraction(self.rng)
        act_mem_mb  = job.pred_mem_mb * (1.0 + spike_frac)

        # Assign a random job lifetime drawn uniformly from the configured range
        lifetime_sec = float(self.rng.uniform(MIN_LIFETIME_SEC, MAX_LIFETIME_SEC))

        rj = RunningJob(
            job          = job,
            node_id      = node_id,
            act_mem_mb   = act_mem_mb,
            is_spike     = spike_frac > 0.0,
            start_time   = self.sim_time,
            lifetime_sec = lifetime_sec,
        )
        self._running_jobs.append(rj)
        return rj

    def _update_W_t(self) -> None:
        """
        Recompute W_t — per-tenant average scheduling delay in seconds.

        W_t is the W̄_t parameter from the math model (§3, Fairness Feedback).
        It is the average of all wait times ever recorded for each tenant.
        Longer-waiting tenants get higher ω_t weights in the next solver call.

        self._tenant_wait_times grows monotonically; W_t converges as more
        jobs are scheduled over the simulation.
        """
        self.W_t = {
            t: sum(ws) / len(ws)
            for t, ws in self._tenant_wait_times.items()
        }

    @staticmethod
    def _print_batch(r: BatchResult) -> None:
        print(
            f"{r.batch_id:>5}  {r.jobs_generated:>6} {r.jobs_placed:>6}  {r.queue_size_after:>5}  "
            f"{r.node_violations:>5} {r.spike_count:>5}  {r.physical_overflow_count:>6}  "
            f"{r.nodes_assigned:>6}  {r.total_nodes_used:>5}  "
            f"{r.avg_eff_mem_pct:>9.1f}%  {r.avg_phys_mem_pct:>9.1f}%"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# § SCRIPT ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    num_batches = int(sys.argv[1]) if len(sys.argv) > 1 else 10

    cm     = ClusterManager(seed=42, verbose=True)
    result = cm.run(num_batches)
    print()
    print(result)
