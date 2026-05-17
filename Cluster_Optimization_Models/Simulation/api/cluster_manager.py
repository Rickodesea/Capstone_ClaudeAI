"""
cluster_manager.py  (Simulation copy)
──────────────────────────────────────
Near-identical to Realtime/cluster_manager.py with two additions:
  1. Accepts a sim_config dict and passes it to generate_nodes/generate_jobs
     so all topology and workload parameters are live-configurable via the API.
  2. _run_batch issues ONE solver call per step instead of a retry loop.
     This keeps the API responsive — unplaced jobs carry over to the next step
     with increasing wait times rather than causing a multi-second UI freeze.
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
    MAX_JOBS_PER_SOLVE,
    MIN_LIFETIME_SEC, MAX_LIFETIME_SEC, BATCH_DURATION_SEC,
    NUM_NODES, NUM_TENANTS,
    SPIKE_PROB,
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


class ClusterManager:
    """
    Orchestrates the multi-tenant cluster scheduling simulation.
    Call _run_batch(batch_id) once per simulation step from the API layer.

    Parameters
    ----------
    sim_config : dict
        Runtime overrides for simulation_data constants.  All keys are optional.
        See simulation_data.generate_nodes / generate_jobs for accepted keys.
    """

    def __init__(
        self,
        seed:           Optional[int]  = None,
        verbose:        bool           = False,
        jobs_per_round: Optional[int]  = None,
        k_window:       Optional[int]  = None,
        log_file:       Optional[str]  = None,
        sim_config:     Optional[dict] = None,
    ) -> None:
        self.rng        = np.random.default_rng(seed)
        self.verbose    = verbose
        self._log_handle = open(log_file, "w", encoding="utf-8") if log_file else None

        self._sim_config     = dict(sim_config or {})
        self._jobs_per_round = (jobs_per_round if jobs_per_round is not None
                                else int(self._sim_config.get('jobs_per_round', JOBS_PER_ROUND)))
        self._k_window       = (k_window if k_window is not None
                                else int(self._sim_config.get('k_window', K_WINDOW)))
        self._sim_config['jobs_per_round'] = self._jobs_per_round
        self._sim_config['k_window']       = self._k_window

        self.nodes: list[NodeState] = generate_nodes(self.rng, config=self._sim_config)

        self.job_queue:     list[Job]        = []
        self._staged_queue: list[Job]        = []
        self._running_jobs: list[RunningJob] = []
        self.scheduling_log: dict[str, dict] = {}

        self.W_t: dict[int, float] = {}
        self._tenant_wait_times: dict[int, deque] = {}

        self.sim_time: datetime = datetime.now(timezone.utc)

        self._refresh_node_states(record_history=False)

    # ─────────────────────────────────────────────────────────────────────────
    # Internal: batch execution — ONE solver call per step
    # ─────────────────────────────────────────────────────────────────────────

    def _run_batch(self, batch_id: int) -> BatchResult:
        self.sim_time += timedelta(seconds=BATCH_DURATION_SEC)

        expired_count         = self._expire_jobs()
        node_violations_start = self._refresh_node_states(record_history=True)

        # Promote last step's staged jobs into the active queue
        self.job_queue.extend(self._staged_queue)
        self._staged_queue = []

        # Generate new jobs → staged (visible in queue, placed next step)
        new_jobs = self._make_jobs(batch_id)
        self._staged_queue = new_jobs

        placed_this_batch    = 0
        spikes_this_batch    = 0
        overflows_this_batch = 0
        consecutive_failures = 0
        nodes_assigned_set   = set()
        solver_calls         = 0

        # ── ONE solver call per step ──────────────────────────────────────────
        if self.job_queue:
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
            solver_calls = 1

            placed_jobs = [
                j for j in queue_slice
                if placements.get(j.job_id) is not None
            ]
            if not placed_jobs:
                consecutive_failures = 1

            for j in placed_jobs:
                nid = placements[j.job_id]
                nodes_assigned_set.add(nid)
                rj  = self._start_job(j, nid)

                wait_sec = (j.scheduling_timestamp - j.arrival_timestamp).total_seconds()
                self.scheduling_log[j.job_id] = {
                    "tenant_id":            j.tenant_id,
                    "job_id":               j.job_id,
                    "arrival_batch":        j.arrival_round,
                    "scheduled_batch":      batch_id,
                    "arrival_timestamp":    j.arrival_timestamp.isoformat(),
                    "scheduling_timestamp": j.scheduling_timestamp.isoformat(),
                    "wait_sec":             wait_sec,
                    "req_mem_mb":           j.req_mem_mb,
                    "pred_mem_mb":          j.pred_mem_mb,
                    "act_mem_mb":           rj.act_mem_mb,
                    "req_cpu":              j.req_cpu,
                    "is_spike":             rj.is_spike,
                    "lifetime_sec":         rj.lifetime_sec,
                    "node_id":              nid,
                }
                if rj.is_spike:
                    spikes_this_batch += 1
                if j.tenant_id not in self._tenant_wait_times:
                    self._tenant_wait_times[j.tenant_id] = deque(maxlen=self._k_window)
                self._tenant_wait_times[j.tenant_id].append(wait_sec)

            placed_ids = {j.job_id for j in placed_jobs}
            self.job_queue = [j for j in self.job_queue if j.job_id not in placed_ids]
            placed_this_batch = len(placed_jobs)

            if placed_jobs:
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
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _make_jobs(self, batch_id: int) -> list[Job]:
        jobs = generate_jobs(
            round_num = batch_id,
            num_jobs  = self._jobs_per_round,
            rng       = self.rng,
            config    = self._sim_config,
        )
        for j in jobs:
            j.arrival_timestamp = self.sim_time
            j.arrival_round     = batch_id + 1
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
            n.used_mb    = used[n.node_id]
            m_cap        = compute_available_capacity(n)
            in_overflow  = n.used_mb > m_cap
            in_violation = n.used_mb > n.capacity_mb
            if record_history:
                n.overflow_history.append(in_overflow)
                n.violation_history.append(in_violation)
            if in_overflow:
                violations += 1
        return violations

    def _start_job(self, job: Job, node_id: int) -> RunningJob:
        job.scheduling_timestamp = self.sim_time
        spike_prob   = float(self._sim_config.get('spike_prob_pct', SPIKE_PROB * 100)) / 100.0
        spike_frac   = sample_spike_fraction(self.rng, spike_prob=spike_prob)
        act_mem_mb   = job.pred_mem_mb * (1.0 + spike_frac)
        min_life     = float(self._sim_config.get('min_lifetime_sec', MIN_LIFETIME_SEC))
        max_life     = float(self._sim_config.get('max_lifetime_sec', MAX_LIFETIME_SEC))
        lifetime_sec = float(self.rng.uniform(min_life, max_life))
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
