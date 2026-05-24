"""
interface.py  (Simulation)
──────────────────────────
Connects the three model layers for the interactive simulation.

Load order (enforced by main.py):
  1. main.py imports simulation_config (DEFAULT_CONFIG, load_config)
  2. main.py imports this file, which:
       a. registers simulation_config as sys.modules["simulation_data"] so
          Realtime modules get our Job/NodeState classes (class identity)
       b. adds Realtime/ to sys.path
       c. imports MILP solver and dataclasses from Realtime/

Architecture mirrors Pipeline/interface.py but with:
  • Per-group solver calls per step (one call per tenant group from plan-ahead)
  • Staged queue: new jobs show wait=0, accumulate wait from the next step
  • Unplaced jobs get a wait bump of batch_duration_sec per step they wait
  • Plan-ahead: tries PlanAhead/plan_ahead_optimizer (Gurobi); falls back
    to a numpy mock that produces the same output shape
  • SimulationState exposes full batch stats and running totals for the frontend
"""

from __future__ import annotations

import sys
import os
from pathlib import Path
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np

# ── Path setup ────────────────────────────────────────────────────────────────
_API_DIR  = Path(__file__).resolve().parent
_ROOT     = _API_DIR.parent.parent          # Cluster_Optimization_Models/
_REALTIME = _ROOT / "Realtime"
_PLANAHEAD = _ROOT / "PlanAhead"

if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))
if str(_REALTIME) not in sys.path:
    sys.path.insert(1, str(_REALTIME))

# ── Register simulation_config as "simulation_data" for Realtime compat ──────
#    Realtime modules do `from simulation_data import Job, NodeState, …`.
#    Registering here ensures they get our classes, not the Realtime originals,
#    so all Job/NodeState instances share the same Python class identity.
import simulation_config as _sc
sys.modules.setdefault('simulation_data', _sc)

# ── Realtime imports ──────────────────────────────────────────────────────────
from simulation_config import (
    Job, NodeState,
    generate_nodes, generate_jobs,
    compute_available_capacity, compute_violation_rate,
    sample_spike_fraction,
    JOBS_PER_ROUND, K_WINDOW,
    MIN_LIFETIME_SEC, MAX_LIFETIME_SEC, BATCH_DURATION_SEC,
    NUM_NODES, NUM_TENANTS, SPIKE_PROB, DEFAULT_CONFIG,
)
from optimizer_google_or import solve
from cluster_manager import RunningJob, BatchResult

# ── Plan-ahead availability ───────────────────────────────────────────────────
_HAS_GUROBI = False
if str(_PLANAHEAD) not in sys.path:
    sys.path.insert(2, str(_PLANAHEAD))
try:
    from plan_ahead_data import build_synthetic_data, make_gurobi_env
    from plan_ahead_optimizer import build_model, extract_plan_output
    from gurobipy import GRB
    _HAS_GUROBI = True
except Exception:
    pass


# ═══════════════════════════════════════════════════════════════════════════════
# § PLAN-AHEAD  (Gurobi or numpy mock, same output shape)
# ═══════════════════════════════════════════════════════════════════════════════

def _mock_plan_ahead(cfg: dict, interval: int) -> dict:
    """
    Fallback plan-ahead (used when Gurobi is unavailable).
    Returns the new format: one group with all tenants and all machines
    for each plan interval.  Also builds tenant_schedule for the frontend
    Tenants panel.
    """
    num_tenants   = int(cfg.get('num_tenants',         NUM_TENANTS))
    num_nodes     = int(cfg.get('num_nodes',           NUM_NODES))
    horizon       = int(cfg.get('plan_ahead_interval', 50))
    access_period = int(cfg.get('access_period',        4))
    n_periods     = max(1, horizon // access_period)
    week_number   = interval // horizon if horizon > 0 else 0

    all_tenant_ids  = list(range(num_tenants))
    all_machine_ids = list(range(num_nodes))

    intervals_out = [
        {
            "interval": h,
            "groups": [
                {
                    "tenant_ids":  all_tenant_ids,
                    "machine_ids": all_machine_ids,
                    "exclusive":   False,
                }
            ],
        }
        for h in range(n_periods)
    ]

    # tenant_schedule for frontend Tenants panel display
    tenant_schedule = {
        str(t): {str(h): all_machine_ids for h in range(n_periods)}
        for t in all_tenant_ids
    }

    slot_labels = [
        f"{h * access_period}–{h * access_period + access_period - 1}i"
        for h in range(n_periods)
    ]
    pos_in_period = interval % horizon if horizon > 0 else 0
    current_slot  = min(pos_in_period // access_period, n_periods - 1)

    return {
        "intervals":        intervals_out,
        "interval":         interval,
        "num_slots":        n_periods,
        "access_period":    access_period,
        "planning_horizon": horizon,
        "slot_labels":      slot_labels,
        "tenant_schedule":  tenant_schedule,
        "current_slot":     current_slot,
        "summary": {
            "avg_nodes_per_tenant": float(num_nodes),
            "isolation_score":      0.0,
            "week_number":          week_number,
        },
    }


def run_plan_ahead(cfg: dict, interval: int) -> dict:
    """
    Run plan-ahead.  Attempts Gurobi; falls back to numpy mock on any error.
    Returns dict with both "intervals" (for cluster manager) and
    "tenant_schedule" (for frontend display).
    """
    if not _HAS_GUROBI:
        return _mock_plan_ahead(cfg, interval)

    try:
        num_tenants    = int(cfg.get('num_tenants',          NUM_TENANTS))
        num_nodes      = int(cfg.get('num_nodes',            NUM_NODES))
        n_always_avail = int(cfg.get('num_always_available', 3))
        exclusive_frac = float(cfg.get('exclusive_frac',     0.0))
        access_period  = int(cfg.get('access_period',        4))
        horizon        = int(cfg.get('plan_ahead_interval',  50))
        n_periods      = max(1, horizon // access_period)
        node_cap       = float(cfg.get('node_capacity',      10.0))
        usage_min      = float(cfg.get('tenant_usage_min',   0.8))
        usage_max      = float(cfg.get('tenant_usage_max',   6.0))
        time_limit     = int(cfg.get('plan_time_limit',      30))
        mip_gap        = float(cfg.get('plan_mip_gap',       0.05))
        use_socp       = bool(int(cfg.get('use_socp',        0)))
        sigma_frac     = float(cfg.get('sigma_frac',         0.20))
        epsilon        = float(cfg.get('cantelli_epsilon',   0.10))

        P = build_synthetic_data(
            seed               = 42,
            n_tenants          = num_tenants,
            n_nodes            = num_nodes,
            n_intervals        = n_periods,
            node_capacity      = node_cap,
            n_always_available = n_always_avail,
            exclusive_frac     = exclusive_frac,
            tenant_usage_min   = usage_min,
            tenant_usage_max   = usage_max,
            sigma_frac         = sigma_frac,
            epsilon            = epsilon,
        )
        env = make_gurobi_env()
        model, vars_ = build_model(P, env, use_socp=use_socp)
        model.Params.TimeLimit    = time_limit
        model.Params.MIPGap       = mip_gap
        model.Params.LogToConsole = 0
        model.optimize()

        if model.Status not in (GRB.OPTIMAL, GRB.TIME_LIMIT, GRB.SUBOPTIMAL):
            return _mock_plan_ahead(cfg, interval)

        plan_out = extract_plan_output(vars_, P)

        # Build tenant_schedule for frontend Tenants panel
        tenant_schedule: dict[str, dict[str, list[int]]] = {str(t): {} for t in P['T']}
        for interval_dict in plan_out["intervals"]:
            h = interval_dict["interval"]
            for g in interval_dict["groups"]:
                for tid in g["tenant_ids"]:
                    tenant_schedule[str(tid)][str(h)] = g["machine_ids"]

        slot_labels = [
            f"{h * access_period}–{h * access_period + access_period - 1}i"
            for h in range(n_periods)
        ]
        pos_in_period = interval % horizon if horizon > 0 else 0
        current_slot  = min(pos_in_period // access_period, n_periods - 1)

        sigma_val = (vars_['sigma'].X
                     if 'sigma' in vars_ and hasattr(vars_['sigma'], 'X')
                     else 0.0)
        avg_nodes = (
            sum(len(ns) for h_dict in tenant_schedule.values() for ns in h_dict.values())
            / max(1, num_tenants * n_periods)
        )

        return {
            "intervals":        plan_out["intervals"],
            "interval":         interval,
            "num_slots":        n_periods,
            "access_period":    access_period,
            "planning_horizon": horizon,
            "slot_labels":      slot_labels,
            "tenant_schedule":  tenant_schedule,
            "current_slot":     current_slot,
            "summary": {
                "avg_nodes_per_tenant": round(avg_nodes, 2),
                "isolation_score":      round(sigma_val, 4),
                "week_number":          interval // horizon if horizon > 0 else 0,
            },
        }
    except Exception:
        return _mock_plan_ahead(cfg, interval)


# ═══════════════════════════════════════════════════════════════════════════════
# § SIMULATION MANAGER  (per-group solver calls, staged queue, config-aware)
# ═══════════════════════════════════════════════════════════════════════════════

class SimulationManager:
    """
    Drives the cluster simulation for the interactive API.

    Differences from Realtime/ClusterManager:
      • One solver call per tenant group per step (mirrors Pipeline architecture)
      • Staged queue: new jobs land in _staged_queue; promoted to job_queue
        next step so they display with wait_intervals = 0 this step
      • Unplaced jobs get a wait bump of batch_duration_sec per step skipped
      • sim_config dict controls all topology and workload parameters
      • plan_output (new format) drives per-group scheduling each step
    """

    def __init__(
        self,
        seed:       int            = 42,
        sim_config: Optional[dict] = None,
    ) -> None:
        self._sim_config = dict(sim_config or DEFAULT_CONFIG)
        self.rng         = np.random.default_rng(seed)

        k  = int(self._sim_config.get('k_window',       K_WINDOW))
        self._jobs_per_round = int(self._sim_config.get('jobs_per_round', JOBS_PER_ROUND))
        self._k_window       = k
        self._num_tenants    = int(self._sim_config.get('num_tenants', NUM_TENANTS))

        self.nodes: list[NodeState] = generate_nodes(self.rng, config=self._sim_config)

        self.job_queue:     list[Job]        = []
        self._staged_queue: list[Job]        = []
        self._running_jobs: list[RunningJob] = []
        self.scheduling_log: dict[str, dict] = {}

        self.W_t: dict[int, float]           = {}
        self._tenant_wait_times: dict[int, deque] = {}

        self.sim_time: datetime = datetime.now(timezone.utc)
        self._refresh_node_states(record_history=False)

    # ─────────────────────────────────────────────────────────────────────────

    def run_step(
        self,
        batch_id:    int,
        plan_output: Optional[dict] = None,
    ) -> BatchResult:
        """
        Execute one scheduling epoch.

        Parameters
        ----------
        batch_id    : current simulation interval (0-indexed)
        plan_output : plan-ahead output dict (new format with "intervals" key),
                      or None to schedule all tenants on all nodes as one group
        """
        cfg = self._sim_config

        batch_sec     = int(cfg.get('batch_duration_sec',     BATCH_DURATION_SEC))
        time_limit_ms = int(cfg.get('realtime_time_limit_ms', 2000))

        self.sim_time += timedelta(seconds=batch_sec)

        expired_count = self._expire_jobs()
        node_violations_start = self._refresh_node_states(record_history=True)

        self.job_queue.extend(self._staged_queue)
        self._staged_queue = []
        new_jobs = self._make_jobs(batch_id)
        self._staged_queue = new_jobs

        placed_ids:      set[str] = set()
        placed_this_batch    = 0
        spikes_this_batch    = 0
        overflows_this_batch = 0
        nodes_assigned_set: set[int] = set()
        solver_calls         = 0
        consecutive_failures = 0
        made_progress        = False

        if self.job_queue:
            groups = self._get_groups_for_interval(plan_output, batch_id)

            for group in groups:
                tenant_ids  = set(group.get("tenant_ids",  []))
                machine_ids = set(group.get("machine_ids", []))

                group_jobs = [
                    j for j in self.job_queue
                    if j.job_id not in placed_ids
                    and (not tenant_ids or j.tenant_id in tenant_ids)
                ]
                self._refresh_node_states(record_history=False)
                group_nodes = [
                    n for n in self.nodes
                    if not machine_ids or n.node_id in machine_ids
                ]

                if not group_jobs or not group_nodes:
                    continue

                placements = solve(
                    jobs          = group_jobs,
                    nodes         = group_nodes,
                    W_t           = self.W_t,
                    K             = self._k_window,
                    time_limit_ms = time_limit_ms,
                )
                solver_calls += 1

                g_placed   = [j for j in group_jobs if placements.get(j.job_id) is not None]
                g_unplaced = [j for j in group_jobs if placements.get(j.job_id) is None]

                self._bump_wait_for_unplaced(g_unplaced, batch_sec)

                if g_placed:
                    made_progress = True

                for j in g_placed:
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

            if not made_progress:
                consecutive_failures = 1

            self.job_queue = [j for j in self.job_queue if j.job_id not in placed_ids]
            placed_this_batch = len(placed_ids)

            if placed_this_batch > 0:
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
            eff_pcts.append((n.used_mb / max(1.0, m_cap)) * 100)

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
            avg_eff_mem_pct         = sum(eff_pcts)  / len(eff_pcts),
            avg_phys_mem_pct        = sum(phys_pcts) / len(phys_pcts),
            avg_eff_active_pct      = avg_eff_active,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _get_groups_for_interval(
        self, plan_output: Optional[dict], batch_id: int
    ) -> list[dict]:
        """Return tenant groups for this simulation step from the plan output."""
        fallback = [{"tenant_ids": [], "machine_ids": [], "exclusive": False}]
        if plan_output is None:
            return fallback
        intervals = plan_output.get("intervals", [])
        if not intervals:
            return fallback
        h = batch_id % len(intervals)
        return intervals[h].get("groups", fallback)

    def _bump_wait_for_unplaced(self, unplaced_jobs: list[Job], batch_sec: int) -> None:
        """Record a synthetic wait event for each unplaced job so W_t rises."""
        for j in unplaced_jobs:
            tid = j.tenant_id
            if tid not in self._tenant_wait_times:
                self._tenant_wait_times[tid] = deque(maxlen=self._k_window)
            self._tenant_wait_times[tid].append(float(batch_sec))
        if unplaced_jobs:
            self._update_W_t()

    def _make_jobs(self, batch_id: int) -> list[Job]:
        cfg          = self._sim_config
        jobs_min     = int(cfg.get('jobs_min_per_round', self._jobs_per_round))
        jobs_max     = int(cfg.get('jobs_max_per_round', jobs_min))
        if jobs_min < jobs_max:
            mean     = (jobs_min + jobs_max) / 2.0
            std      = (jobs_max - jobs_min) / 6.0
            num_jobs = int(np.clip(round(self.rng.normal(mean, std)), jobs_min, jobs_max))
        else:
            num_jobs = max(1, jobs_min)
        num_tenants = int(cfg.get('num_tenants', NUM_TENANTS))
        jobs        = generate_jobs(batch_id, num_jobs=num_jobs,
                                    num_tenants=num_tenants,
                                    rng=self.rng, config=cfg)
        for j in jobs:
            j.arrival_timestamp = self.sim_time
            # arrival_round = batch_id + 1 so wait_intervals = 0 this step
            j.arrival_round     = batch_id + 1
        return jobs

    def _start_job(self, job: Job, node_id: int) -> RunningJob:
        job.scheduling_timestamp = self.sim_time
        cfg        = self._sim_config
        spike_prob = float(cfg.get('spike_prob_pct', SPIKE_PROB * 100)) / 100.0
        spike_frac = sample_spike_fraction(self.rng, spike_prob=spike_prob)
        act_mem_mb = job.pred_mem_mb * (1.0 + spike_frac)
        min_life   = float(cfg.get('min_lifetime_sec', MIN_LIFETIME_SEC))
        max_life   = float(cfg.get('max_lifetime_sec', MAX_LIFETIME_SEC))
        lifetime   = float(self.rng.uniform(min_life, max_life))
        rj = RunningJob(
            job=job, node_id=node_id, act_mem_mb=act_mem_mb,
            is_spike=spike_frac > 0.0, start_time=self.sim_time,
            lifetime_sec=lifetime,
        )
        self._running_jobs.append(rj)
        return rj

    def _expire_jobs(self) -> int:
        active, expired = [], []
        for rj in self._running_jobs:
            (expired if rj.has_expired(self.sim_time) else active).append(rj)
        self._running_jobs = active
        return len(expired)

    def _refresh_node_states(self, record_history: bool) -> int:
        used = {n.node_id: 0.0 for n in self.nodes}
        for rj in self._running_jobs:
            used[rj.node_id] += rj.act_mem_mb
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

    def _update_W_t(self) -> None:
        self.W_t = {
            t: sum(ws) / len(ws)
            for t, ws in self._tenant_wait_times.items()
        }


# ═══════════════════════════════════════════════════════════════════════════════
# § SIMULATION STATE  (manages manager + plan-ahead + history + totals)
# ═══════════════════════════════════════════════════════════════════════════════

MEM_HISTORY_SIZE = 80


class SimulationState:
    """
    Top-level state object for one simulation run.

    Holds the SimulationManager, tracks plan-ahead timing, accumulates
    running totals, and exposes all data needed by the API serializer.
    """

    def __init__(self, cfg: dict) -> None:
        self.cfg             = dict(cfg)
        self.manager         = SimulationManager(seed=42, sim_config=cfg)
        self.interval:       int  = 0
        self.mem_history:         list = []
        self.eff_history:         list = []
        self.eff_active_history:  list = []
        self.placed_history:      list = []
        self.recent_placements: list = []
        self.last_plan_ahead: dict | None = None
        self.last_batch_result: BatchResult | None = None

        # Running cumulative totals
        self._total_generated:  int   = 0
        self._total_placed:     int   = 0
        self._total_expired:    int   = 0
        self._total_spikes:     int   = 0
        self._total_viols:      int   = 0
        self._total_ovrflw:     int   = 0
        self._sum_eff_pct:      float = 0.0
        self._sum_phys_pct:     float = 0.0
        self._sum_act_pct:      float = 0.0
        self._sum_solver_calls: int   = 0
        self._num_steps:        int   = 0

    def step(self) -> None:
        """Advance one scheduling epoch."""
        plan_ahead_i = int(self.cfg.get('plan_ahead_interval', 50))

        cm          = self.manager
        before_keys = set(cm.scheduling_log.keys())
        result      = cm.run_step(self.interval, plan_output=self.last_plan_ahead)
        self.interval += 1
        after_keys  = set(cm.scheduling_log.keys())

        self.recent_placements = [
            {
                "job_id":      cm.scheduling_log[k]["job_id"],
                "tenant_id":   cm.scheduling_log[k]["tenant_id"],
                "node_id":     cm.scheduling_log[k]["node_id"],
                "pred_mem_mb": round(cm.scheduling_log[k]["pred_mem_mb"], 1),
            }
            for k in (after_keys - before_keys)
        ]

        self.mem_history.append(round(result.avg_phys_mem_pct, 2))
        self.eff_history.append(round(result.avg_eff_mem_pct, 2))
        self.eff_active_history.append(round(result.avg_eff_active_pct, 2))
        self.placed_history.append(result.jobs_placed)
        self.last_batch_result = result

        self._total_generated  += result.jobs_generated
        self._total_placed     += result.jobs_placed
        self._total_expired    += result.jobs_expired
        self._total_spikes     += result.spike_count
        self._total_viols      += result.node_violations
        self._total_ovrflw     += result.physical_overflow_count
        self._sum_eff_pct      += result.avg_eff_mem_pct
        self._sum_phys_pct     += result.avg_phys_mem_pct
        self._sum_act_pct      += result.avg_eff_active_pct
        self._sum_solver_calls += result.solver_calls
        self._num_steps        += 1

        # Fire plan-ahead at configured interval
        if self.interval > 0 and self.interval % plan_ahead_i == 0:
            self.last_plan_ahead = run_plan_ahead(self.cfg, self.interval)
        elif self.last_plan_ahead is not None:
            # Advance current_slot pointer so the frontend highlights correctly
            horizon       = int(self.last_plan_ahead.get("planning_horizon", plan_ahead_i))
            access_period = int(self.last_plan_ahead.get("access_period", 4))
            num_slots     = int(self.last_plan_ahead.get("num_slots", 1))
            pos           = self.interval % horizon if horizon > 0 else 0
            self.last_plan_ahead["current_slot"] = min(
                pos // access_period, num_slots - 1
            )

    def trigger_plan_ahead(self) -> dict:
        """Run plan-ahead immediately (on-demand from the frontend button)."""
        self.last_plan_ahead = run_plan_ahead(self.cfg, self.interval)
        return self.last_plan_ahead

    @property
    def sim_totals(self) -> dict:
        n = max(1, self._num_steps)
        q = len(self.manager.job_queue) + len(self.manager._staged_queue)
        return {
            "num_batches":          self._num_steps,
            "k_window":             self.manager._k_window,
            "total_generated":      self._total_generated,
            "total_placed":         self._total_placed,
            "placement_rate":       round(self._total_placed / max(1, self._total_generated) * 100, 1),
            "final_queue_size":     q,
            "total_viols":          self._total_viols,
            "total_spikes":         self._total_spikes,
            "total_ovrflw":         self._total_ovrflw,
            "total_expired":        self._total_expired,
            "avg_placed_per_batch": round(self._total_placed / n, 1),
            "avg_queue_per_batch":  round((self._total_generated - self._total_placed) / n, 1),
            "avg_eff_pct":          round(self._sum_eff_pct  / n, 1),
            "avg_phys_pct":         round(self._sum_phys_pct / n, 1),
            "avg_act_pct":          round(self._sum_act_pct  / n, 1),
            "avg_solver_calls":     round(self._sum_solver_calls / n, 1),
            "final_w_t":            {k: round(v, 1) for k, v in self.manager.W_t.items()},
        }
