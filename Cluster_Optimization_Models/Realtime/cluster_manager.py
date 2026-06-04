"""
cluster_manager.py
──────────────────
Central simulation driver for the multi-tenant cluster scheduler.

Architecture
─────────────
Each simulation interval:
  1. Advance simulated clock by BATCH_DURATION_SEC.
  2. Expire running jobs whose lifetime has elapsed; free their memory.
  3. Recompute U_n from running jobs; record SLA violation history (once per interval).
  4. Generate new jobs and add to queue (stamps arrival_timestamp).
  5. Per-group scheduling loop (one pass through all groups):
       For each tenant group (tenant_ids, machine_ids):
         a. Filter queue to jobs belonging to this tenant group (not yet placed).
         b. Filter nodes to machines assigned to this group.
         c. Call MILP solver -> placement dict.
         d. Place placed jobs; bump W_t by one interval for each unplaced job's tenant.
  6. Update W_t (per-tenant average wait time for fairness).

Key concepts
────────────
Per-group model calls
    The Realtime model is called once per tenant group per interval.
    The Cluster Manager handles all tenant/machine filtering before the call.
    The Realtime model receives only the filtered job list and machine list —
    no knowledge of tenants, plan-ahead, or machine assignments.

All jobs always sent
    The Cluster Manager sends ALL queued jobs for a tenant group to the solver.
    No cap on number of jobs per solve call. If nodes are saturated, the solver
    returns None for unplaced jobs and the retry counter increments.

Wait-time bump for unplaced jobs
    When a job fails to be placed, the Cluster Manager records a synthetic
    wait event (1 interval duration) for that tenant in the W_t rolling window.
    This raises ω_delay,t in the next solve call, boosting the tenant's priority.

Job lifetime
    Every placed job is assigned a random lifetime [MIN_LIFETIME_SEC, MAX_LIFETIME_SEC].
    Once elapsed (simulated time), the job is removed and its memory freed.
    This is tracked by the Cluster Manager, not the Realtime model.
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
    MIN_LIFETIME_SEC, MAX_LIFETIME_SEC, BATCH_DURATION_SEC,
    NODE_MEM_MB, OS_TAX_MB, NODE_CPU_CORES, NUM_NODES, NUM_TENANTS,
    SPIKE_PROB, SPIKE_MAX_FRAC, NUM_BATCHES,
    REQUEST_MEM_MIN_MB, REQUEST_MEM_MAX_MB,
)
from realtime_optimizer import solve


# ═══════════════════════════════════════════════════════════════════════════════
# § RUNNING JOB
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class RunningJob:
    """
    A job that has been placed and is currently running.

    Fields:
        job           original Job object
        node_id       machine the job is placed on
        act_mem_mb    actual memory consumed (pred_mem × (1 + spike_frac))
        is_spike      True if act_mem_mb > job.pred_mem_mb
        start_time    simulated UTC datetime when the job started
        lifetime_sec  randomly assigned job duration in simulated seconds
    """
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


# ═══════════════════════════════════════════════════════════════════════════════
# § RESULT DATACLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class BatchResult:
    """Statistics for one scheduling interval."""
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
    """Aggregate statistics across all simulation intervals."""
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

    def __str__(self) -> str:
        lines = [
            f"SimulationResult — {self.num_batches} intervals",
            f"  generated  : {self.total_generated}",
            f"  placed     : {self.total_placed}  ({self.placement_rate():.1%})",
            f"  queue left : {self.final_queue_size}",
            f"  violations : {self.total_violations}  (U_n > M_n^cap)",
            f"  spikes     : {self.total_spikes}  (act > pred)",
            f"  overflows  : {self.total_overflows}  (U_n + tax > capacity)",
            f"  expired    : {self.total_expired}",
        ]
        if self.batch_results:
            n = len(self.batch_results)
            avg_placed  = sum(r.jobs_placed         for r in self.batch_results) / n
            avg_queue   = sum(r.queue_size_after     for r in self.batch_results) / n
            avg_eff     = sum(r.avg_eff_mem_pct      for r in self.batch_results) / n
            avg_phys    = sum(r.avg_phys_mem_pct     for r in self.batch_results) / n
            avg_solves  = sum(r.solver_calls         for r in self.batch_results) / n
            lines += [
                f"  avg placed/interval  : {avg_placed:.1f}",
                f"  avg queue/interval   : {avg_queue:.1f}",
                f"  avg eff mem %        : {avg_eff:.1f}%",
                f"  avg phys mem %       : {avg_phys:.1f}%",
                f"  avg solver calls     : {avg_solves:.1f}",
            ]
        if self.final_W_t:
            waits = list(self.final_W_t.values())
            avg_w = sum(waits) / len(waits)
            lines += [
                f"  W_t final  : { {t: round(w, 1) for t, w in self.final_W_t.items()} } sec",
                f"  wait spread: {min(waits):.1f}s to {max(waits):.1f}s  (avg {avg_w:.1f}s)",
            ]
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# § CLUSTER MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

class ClusterManager:
    """
    Orchestrates the multi-tenant cluster scheduling simulation.

    Responsibilities:
      • Maintain a shared job queue across intervals
      • Generate new jobs each interval and stamp arrival timestamps
      • Per-interval: loop through tenant groups from plan-ahead output,
        filter jobs and machines, call the Realtime model for each group
      • Track running jobs with lifetimes; expire when done
      • Maintain per-tenant average wait time W_t for fairness feedback
      • Bump W_t for unplaced jobs (failed-placement wait signal)
    """

    def __init__(
        self,
        seed:               Optional[int] = None,
        verbose:            bool          = True,
        jobs_per_round:     Optional[int] = None,
        k_window:           Optional[int] = None,
        log_file:           Optional[str] = "simulation_log.txt",
        use_prediction_api: bool          = False,
    ) -> None:
        self.rng     = np.random.default_rng(seed)
        self.verbose = verbose
        self._log_handle = open(log_file, "w", encoding="utf-8") if log_file else None
        self._use_prediction_api = use_prediction_api

        self._jobs_per_round = jobs_per_round if jobs_per_round is not None else JOBS_PER_ROUND
        self._k_window       = k_window       if k_window       is not None else K_WINDOW

        self.nodes: list[NodeState] = generate_nodes(self.rng)
        self.job_queue:    list[Job]        = []
        self._running_jobs: list[RunningJob] = []
        self.scheduling_log: dict[str, dict] = {}

        self.W_t: dict[int, float]               = {}
        self._tenant_wait_times: dict[int, deque] = {}

        self.sim_time: datetime = datetime.now(timezone.utc)
        self._refresh_node_states(record_history=False)

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def run(
        self,
        num_batches: int,
        plan_output: dict | None = None,
        solver:      str  = "GUROBI",
        iterative:   bool = True,
        batch_jobs:  int  = 32,
        batch_nodes: int  = 32,
    ) -> SimulationResult:
        """
        Run the simulation for num_batches intervals.

        Parameters
        ----------
        num_batches  : number of scheduling intervals
        plan_output  : plan-ahead output dict (from extract_plan_output()).
                       If None, all jobs compete for all nodes (no grouping).
        solver       : integer backend label (shown in startup banner)
        iterative    : whether the iterative RT solver is active (shown in banner)
        batch_jobs   : iterative batch size for jobs (shown in banner)
        batch_nodes  : iterative batch size for nodes (shown in banner)

        Returns SimulationResult with per-interval and aggregate statistics.
        """
        batch_results: list[BatchResult] = []
        batch_id = -1

        if self.verbose:
            self._print_startup(solver=solver, iterative=iterative,
                                batch_jobs=batch_jobs, batch_nodes=batch_nodes)
            print(
                f"{'Intvl':>5}  {'New':>6} {'Placed':>6}  {'Queue':>5}  "
                f"{'Assign':>6}  {'Used':>5}  "
                f"{'Spike':>5}  {'Ovrflw':>6}  {'Viols':>5}  "
                f"{'Util % (U/M)':>14}  {'Eff% (U/C)':>12}  {'Eff% (Active)':>14}"
            )
            print("-" * 112)

        try:
            for batch_id in range(num_batches):
                result = self._run_batch(batch_id, plan_output)
                batch_results.append(result)
                if self.verbose:
                    self._print_batch(result)
        except KeyboardInterrupt:
            if self.verbose:
                print(
                    f"\n[Interrupted]  Stopped after interval {batch_id}  "
                    f"({len(batch_results)} intervals completed)."
                )

        if self.verbose:
            print("-" * 112)

        if self._log_handle:
            self._log_handle.close()
            self._log_handle = None

        return SimulationResult(
            num_batches      = num_batches,
            total_generated  = sum(r.jobs_generated         for r in batch_results),
            total_placed     = sum(r.jobs_placed             for r in batch_results),
            final_queue_size = len(self.job_queue),
            total_violations = sum(r.node_violations         for r in batch_results),
            total_spikes     = sum(r.spike_count             for r in batch_results),
            total_overflows  = sum(r.physical_overflow_count for r in batch_results),
            total_expired    = sum(r.jobs_expired            for r in batch_results),
            batch_results    = batch_results,
            final_W_t        = dict(self.W_t),
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Internal: interval execution
    # ─────────────────────────────────────────────────────────────────────────

    def _run_batch(
        self,
        batch_id:    int,
        plan_output: dict | None = None,
    ) -> BatchResult:
        """Execute one scheduling interval and return its statistics."""

        # Step 1: Advance simulated clock
        self.sim_time += timedelta(seconds=BATCH_DURATION_SEC)

        # Step 2: Expire completed jobs
        expired_count = self._expire_jobs()

        # Step 3: Recompute U_n and record SLA violation history
        node_violations_start = self._refresh_node_states(record_history=True)

        # Step 4: Generate new jobs
        new_jobs = self._make_jobs(batch_id)
        self.job_queue.extend(new_jobs)

        # Step 5: Per-group scheduling (one pass — unplaced jobs stay in queue)
        solver_calls         = 0
        spikes_this_batch    = 0
        overflows_this_batch = 0
        nodes_assigned_set   = set()
        placed_ids: set[str] = set()

        groups = self._get_groups(plan_output, batch_id)

        for group in groups:
            tenant_ids  = set(group["tenant_ids"])
            machine_ids = set(group["machine_ids"])

            # Oldest jobs first so the solver can prioritise long-waiting jobs
            group_jobs = sorted(
                (
                    j for j in self.job_queue
                    if j.job_id not in placed_ids
                    and (not tenant_ids or j.tenant_id in tenant_ids)
                ),
                key=lambda j: j.arrival_round,
            )
            group_nodes = [
                n for n in self.nodes
                if not machine_ids or n.node_id in machine_ids
            ]

            if not group_jobs or not group_nodes:
                continue

            self._refresh_node_states(record_history=False)

            placements = solve(
                jobs          = group_jobs,
                nodes         = group_nodes,
                W_t           = self.W_t,
                K             = self._k_window,
                time_limit_ms = 10_000,
            )
            solver_calls += 1

            placed_jobs: list[Job] = [
                j for j in group_jobs if placements.get(j.job_id) is not None
            ]
            unplaced_jobs: list[Job] = [
                j for j in group_jobs if placements.get(j.job_id) is None
            ]

            self._bump_wait_for_unplaced(unplaced_jobs)

            for j in placed_jobs:
                nid = placements[j.job_id]
                placed_ids.add(j.job_id)
                nodes_assigned_set.add(nid)
                rj = self._start_job(j, nid)

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

        # Remove all placed jobs from queue after the full group pass
        self.job_queue = [j for j in self.job_queue if j.job_id not in placed_ids]
        placed_this_batch = len(placed_ids)
        self._update_W_t()

        # Check for physical overflow
        self._refresh_node_states(record_history=False)
        for n in self.nodes:
            if n.used_mb + n.os_tax_mb > n.capacity_mb:
                overflows_this_batch += 1

        # Compute batch statistics
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
            consecutive_failures    = 0,
            node_violations         = node_violations_start,
            spike_count             = spikes_this_batch,
            physical_overflow_count = overflows_this_batch,
            jobs_expired            = expired_count,
            nodes_assigned          = len(nodes_assigned_set),
            total_nodes_used        = total_nodes_used,
            avg_eff_mem_pct         = sum(eff_pcts) / max(1, len(eff_pcts)),
            avg_phys_mem_pct        = sum(phys_pcts) / max(1, len(phys_pcts)),
            avg_eff_active_pct      = avg_eff_active,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _get_groups(self, plan_output: dict | None, batch_id: int) -> list[dict]:
        """
        Resolve the tenant groups for the current interval.

        If plan_output is provided, select the interval group list using
        batch_id mod number of intervals. Otherwise, create a single group
        containing all tenants on all machines (no grouping).
        """
        if plan_output and "intervals" in plan_output:
            intervals = plan_output["intervals"]
            h = batch_id % len(intervals)
            return intervals[h]["groups"]
        # Fallback: one group with all tenants and all machines
        all_tenants = sorted({j.tenant_id for j in self.job_queue})
        all_machine_ids = [n.node_id for n in self.nodes]
        return [{"tenant_ids": all_tenants, "machine_ids": all_machine_ids, "exclusive": False}]

    def _bump_wait_for_unplaced(self, unplaced_jobs: list[Job]) -> None:
        """
        Record a synthetic wait event for each unplaced job's tenant.

        This ensures that tenants whose jobs cannot be placed immediately
        accumulate wait time in the W_t rolling window, raising their
        ω_delay weight in the next solver call.
        """
        interval_duration = float(BATCH_DURATION_SEC)
        tenant_ids_seen = set()
        for j in unplaced_jobs:
            if j.tenant_id in tenant_ids_seen:
                continue
            tenant_ids_seen.add(j.tenant_id)
            if j.tenant_id not in self._tenant_wait_times:
                self._tenant_wait_times[j.tenant_id] = deque(maxlen=self._k_window)
            self._tenant_wait_times[j.tenant_id].append(interval_duration)
        if tenant_ids_seen:
            self._update_W_t()

    def _make_jobs(self, batch_id: int) -> list[Job]:
        jobs = generate_jobs(batch_id, num_jobs=self._jobs_per_round, rng=self.rng)
        if self._use_prediction_api:
            # Replace synthesised predictions with values from the prediction API.
            # Falls back to synthesised values if the API is unavailable or the
            # collection/tenant is not in the dataset.
            try:
                import sys as _sys, os as _os
                _pred_dir = _os.path.join(
                    _os.path.dirname(_os.path.abspath(__file__)), "..", "Prediction"
                )
                if _pred_dir not in _sys.path:
                    _sys.path.insert(0, _pred_dir)
                from prediction_api import predict_realtime
                for j in jobs:
                    result = predict_realtime(tenant_id=str(j.tenant_id))
                    j.pred_mem_mb  = float(result["pred_mem_mb"])
                    j.pred_cpu_p95 = float(result["pred_cpu_p95"])
            except Exception:
                pass  # silently fall back to synthesised predictions
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

    # ─────────────────────────────────────────────────────────────────────────
    # Logging / display
    # ─────────────────────────────────────────────────────────────────────────

    def _write_log(self, line: str) -> None:
        if self._log_handle:
            self._log_handle.write(line + "\n")
            self._log_handle.flush()

    def _print_startup(self, solver: str = "GUROBI", iterative: bool = True,
                       batch_jobs: int = 32, batch_nodes: int = 32) -> None:
        print("=" * 95)
        print("  Cluster Simulation Configuration")
        print("=" * 95)
        print(f"  Nodes              : {NUM_NODES}")
        print(f"  Tenants            : {NUM_TENANTS}")
        print(f"  Jobs/interval      : {self._jobs_per_round}")
        print(f"  K window           : {self._k_window}")
        print(f"  Job lifetime       : {MIN_LIFETIME_SEC:.0f}-{MAX_LIFETIME_SEC:.0f} s")
        print(f"  Interval duration  : {BATCH_DURATION_SEC} s")
        print(f"  Spike prob/max     : {SPIKE_PROB:.0%} / {SPIKE_MAX_FRAC:.0%}")
        rt_mode = f"iterative (batch={batch_jobs}x{batch_nodes})" if iterative else "no-iterative (single-shot)"
        print(f"  RT solver          : {solver}  [{rt_mode}]")
        print(f"  Prediction API     : {'ON' if self._use_prediction_api else 'OFF (synthetic data)'}")
        print()

    @staticmethod
    def _print_batch(r: BatchResult) -> None:
        print(
            f"{r.batch_id:>5}  {r.jobs_generated:>6} {r.jobs_placed:>6}  {r.queue_size_after:>5}  "
            f"{r.nodes_assigned:>6}  {r.total_nodes_used:>5}  "
            f"{r.spike_count:>5}  {r.physical_overflow_count:>6}  {r.node_violations:>5}  "
            f"{r.avg_phys_mem_pct:>13.1f}%  {r.avg_eff_mem_pct:>11.1f}%  "
            f"{r.avg_eff_active_pct:>13.1f}%"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# § ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse as _ap
    import io as _io
    import sys as _sys

    if hasattr(_sys.stdout, "reconfigure"):
        _sys.stdout.reconfigure(encoding="utf-8")
    else:
        _sys.stdout = _io.TextIOWrapper(_sys.stdout.buffer, encoding="utf-8")

    parser = _ap.ArgumentParser(
        description="Multi-tenant cluster scheduling simulation",
        formatter_class=_ap.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--batches",        type=int,   default=NUM_BATCHES,
                        help="Number of scheduling intervals")
    parser.add_argument("--seed",           type=int,   default=42,
                        help="RNG seed")
    parser.add_argument("--jobs-per-round", type=int,   default=None,
                        help="Jobs generated per interval (default: from simulation_data)")
    parser.add_argument("--solver",         default="GUROBI",
                        help="Integer backend: GUROBI, CBC, SCIP, HIGHS")
    parser.add_argument("--iterative",      default=True,
                        action=_ap.BooleanOptionalAction,
                        help="Use iterative batch-MILP solver (default: True)")
    parser.add_argument("--rt-batch-jobs",  type=int,   default=32,
                        help="Jobs per sub-MILP  — iterative only")
    parser.add_argument("--rt-batch-nodes",    type=int, default=32,
                        help="Nodes per sub-MILP — iterative only")
    parser.add_argument("--use-prediction-api", default=False,
                        action=_ap.BooleanOptionalAction,
                        help="Use Prediction/prediction_api for job predictions (default: False)")
    args = parser.parse_args()

    # ── Inject chosen RT solver ────────────────────────────────────────────────
    import cluster_manager as _cm_mod

    if args.iterative:
        import optimizer_iterative as _oi
        _bj, _bn, _sid = args.rt_batch_jobs, args.rt_batch_nodes, args.solver.upper()
        _cm_mod.solve = lambda jobs, nodes, W_t, K, time_limit_ms=10_000: _oi.solve(
            jobs, nodes, W_t, K, time_limit_ms,
            batch_jobs=_bj, batch_nodes=_bn, solver_id=_sid,
        )
    else:
        import realtime_optimizer as _rt
        _sid = args.solver.upper()
        _cm_mod.solve = lambda jobs, nodes, W_t, K, time_limit_ms=10_000: _rt.solve(
            jobs, nodes, W_t, K, time_limit_ms, solver_id=_sid,
        )

    cm = ClusterManager(
        seed               = args.seed,
        verbose            = True,
        jobs_per_round     = args.jobs_per_round,
        use_prediction_api = args.use_prediction_api,
    )
    result = cm.run(
        num_batches  = args.batches,
        solver       = args.solver.upper(),
        iterative    = args.iterative,
        batch_jobs   = args.rt_batch_jobs,
        batch_nodes  = args.rt_batch_nodes,
    )
    print()
    print(result)
