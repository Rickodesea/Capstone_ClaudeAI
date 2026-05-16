"""
cluster_manager.py
──────────────────
Central simulation driver for the multi-tenant cluster scheduler.
Copied from optimization/cluster_manager.py — __main__ block removed.

Call _run_batch(batch_id) once per step from the FastAPI layer.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np

from simulation_data import (
    Job, NodeState,
    generate_jobs, generate_nodes,
    compute_violation_rate, compute_available_capacity,
    compute_remaining_avail, compute_remaining_eff,
    compute_utilization_weight, compute_node_weight,
    sample_spike_fraction,
    JOBS_PER_ROUND, K_WINDOW,
    MAX_PLACEMENT_RETRIES, MAX_JOBS_PER_SOLVE,
    MIN_LIFETIME_SEC, MAX_LIFETIME_SEC, BATCH_DURATION_SEC,
    NODE_MEM_MB, OS_TAX_MB, NODE_CPU_CORES, NUM_NODES, NUM_TENANTS,
    SPIKE_PROB, SPIKE_MAX_FRAC, NUM_BATCHES,
    REQUEST_MEM_MIN_MB, REQUEST_MEM_MAX_MB,
)
from optimizer_google_or import solve


@dataclass
class RunningJob:
    job:          Job
    node_id:      int
    act_mem_mb:   float
    is_spike:     bool
    start_time:   datetime
    lifetime_sec: float

    @property
    def end_time(self) -> datetime:
        return self.start_time + timedelta(seconds=self.lifetime_sec)

    def has_expired(self, now: datetime) -> bool:
        return now >= self.end_time


@dataclass
class BatchResult:
    batch_id:                int
    jobs_generated:          int
    jobs_placed:             int
    queue_size_after:        int
    solver_calls:            int
    consecutive_failures:    int
    node_violations:         int
    spike_count:             int
    physical_overflow_count: int
    jobs_expired:            int
    nodes_assigned:          int
    total_nodes_used:        int
    avg_eff_mem_pct:         float
    avg_phys_mem_pct:        float
    avg_eff_active_pct:      float


@dataclass
class SimulationResult:
    num_batches:      int
    total_generated:  int
    total_placed:     int
    final_queue_size: int
    total_violations: int
    total_spikes:     int
    total_overflows:  int
    total_expired:    int
    batch_results:    list[BatchResult]
    final_W_t:        dict[int, float]

    def placement_rate(self) -> float:
        return self.total_placed / max(1, self.total_generated)


class ClusterManager:
    """
    Orchestrates the multi-tenant cluster scheduling simulation.

    Call _run_batch(batch_id) once per simulation step from the API layer.
    """

    def __init__(
        self,
        seed:           Optional[int]  = None,
        verbose:        bool           = False,
        jobs_per_round: Optional[int]  = None,
        k_window:       Optional[int]  = None,
        log_file:       Optional[str]  = None,
    ) -> None:
        self.rng     = np.random.default_rng(seed)
        self.verbose = verbose
        self._log_handle = open(log_file, "w", encoding="utf-8") if log_file else None

        self._jobs_per_round = jobs_per_round if jobs_per_round is not None else JOBS_PER_ROUND
        self._k_window       = k_window       if k_window       is not None else K_WINDOW

        self.nodes: list[NodeState] = generate_nodes(self.rng)

        self.job_queue: list[Job] = []
        self._staged_queue: list[Job] = []   # generated this step, placed next step
        self._running_jobs: list[RunningJob] = []
        self.scheduling_log: dict[str, dict] = {}

        self.W_t: dict[int, float] = {}
        self._tenant_wait_times: dict[int, deque] = {}

        self.sim_time: datetime = datetime.now(timezone.utc)

        self._preload_nodes()
        self._refresh_node_states(record_history=False)

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def run(self, num_batches: int) -> SimulationResult:
        batch_results: list[BatchResult] = []
        for batch_id in range(num_batches):
            result = self._run_batch(batch_id)
            batch_results.append(result)

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
        self.sim_time += timedelta(seconds=BATCH_DURATION_SEC)

        expired_count = self._expire_jobs()
        node_violations_start = self._refresh_node_states(record_history=True)

        # Promote last step's staged jobs into the active queue so they're placed now
        self.job_queue.extend(self._staged_queue)
        self._staged_queue = []

        # Generate new jobs into staged — visible in queue but placed next step
        new_jobs = self._make_jobs(batch_id)
        self._staged_queue = new_jobs

        solver_calls         = 0
        placed_this_batch    = 0
        spikes_this_batch    = 0
        overflows_this_batch = 0
        consecutive_failures = 0
        nodes_assigned_set   = set()

        while self.job_queue:
            if consecutive_failures >= MAX_PLACEMENT_RETRIES:
                break

            self._refresh_node_states(record_history=False)

            queue_slice = sorted(
                self.job_queue, key=lambda j: j.arrival_round
            )[:MAX_JOBS_PER_SOLVE]

            placements = solve(
                jobs  = queue_slice,
                nodes = self.nodes,
                W_t   = self.W_t,
                K     = self._k_window,
            )
            solver_calls += 1

            placed_jobs: list[Job] = [
                j for j in queue_slice
                if placements.get(j.job_id) is not None
            ]

            if not placed_jobs:
                consecutive_failures += 1
                continue

            consecutive_failures = 0

            for j in placed_jobs:
                nid = placements[j.job_id]
                nodes_assigned_set.add(nid)

                rj = self._start_job(j, nid)

                wait_sec = (
                    j.scheduling_timestamp - j.arrival_timestamp
                ).total_seconds()

                self.scheduling_log[j.job_id] = {
                    "tenant_id":             j.tenant_id,
                    "job_id":                j.job_id,
                    "arrival_batch":         j.arrival_round,
                    "scheduled_batch":       batch_id,
                    "arrival_timestamp":     j.arrival_timestamp.isoformat(),
                    "scheduling_timestamp":  j.scheduling_timestamp.isoformat(),
                    "wait_sec":              wait_sec,
                    "req_mem_mb":            j.req_mem_mb,
                    "pred_mem_mb":           j.pred_mem_mb,
                    "act_mem_mb":            rj.act_mem_mb,
                    "req_cpu":               j.req_cpu,
                    "is_spike":              rj.is_spike,
                    "lifetime_sec":          rj.lifetime_sec,
                    "node_id":               nid,
                }

                if rj.is_spike:
                    spikes_this_batch += 1

                if j.tenant_id not in self._tenant_wait_times:
                    self._tenant_wait_times[j.tenant_id] = deque(maxlen=self._k_window)
                self._tenant_wait_times[j.tenant_id].append(wait_sec)

            placed_ids = {j.job_id for j in placed_jobs}
            self.job_queue = [
                j for j in self.job_queue if j.job_id not in placed_ids
            ]
            placed_this_batch += len(placed_jobs)

            self._update_W_t()

            self._refresh_node_states(record_history=False)
            for n in self.nodes:
                if n.used_mb + n.os_tax_mb > n.capacity_mb:
                    overflows_this_batch += 1

        active_node_ids  = {rj.node_id for rj in self._running_jobs}
        total_nodes_used = len(active_node_ids)

        eff_pcts  = []
        phys_pcts = []
        for n in self.nodes:
            phys_pcts.append((n.used_mb / n.capacity_mb) * 100)
            m_cap = compute_available_capacity(n)
            eff_pcts.append((n.used_mb / max(1, m_cap)) * 100)

        active_eff_pcts = [p for n, p in zip(self.nodes, eff_pcts) if n.used_mb > 0]
        avg_eff_active  = (sum(active_eff_pcts) / len(active_eff_pcts)
                           if active_eff_pcts else 0.0)

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
            avg_eff_active_pct      = avg_eff_active,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _preload_nodes(self) -> None:
        for n in self.nodes:
            continue   # preload disabled — nodes start empty for clean demo

    def _make_jobs(self, batch_id: int) -> list[Job]:
        jobs = generate_jobs(batch_id, num_jobs=self._jobs_per_round, rng=self.rng)
        for j in jobs:
            j.arrival_timestamp = self.sim_time
        return jobs

    def _expire_jobs(self) -> int:
        active, expired = [], []
        for rj in self._running_jobs:
            (expired if rj.has_expired(self.sim_time) else active).append(rj)
        self._running_jobs = active
        return len(expired)

    def _compute_node_used_mb(self) -> dict[int, float]:
        used: dict[int, float] = {n.node_id: 0.0 for n in self.nodes}
        for rj in self._running_jobs:
            used[rj.node_id] += rj.act_mem_mb
        return used

    def _refresh_node_states(self, record_history: bool) -> int:
        used       = self._compute_node_used_mb()
        violations = 0

        for n in self.nodes:
            n.used_mb = used[n.node_id]
            m_cap     = compute_available_capacity(n)
            in_violation = n.used_mb > m_cap

            if record_history:
                n.violation_history.append(in_violation)

            if in_violation:
                violations += 1

        return violations

    def _start_job(self, job: Job, node_id: int) -> RunningJob:
        job.scheduling_timestamp = self.sim_time

        spike_frac   = sample_spike_fraction(self.rng)
        act_mem_mb   = job.pred_mem_mb * (1.0 + spike_frac)
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
        self.W_t = {
            t: sum(ws) / len(ws)
            for t, ws in self._tenant_wait_times.items()
        }
