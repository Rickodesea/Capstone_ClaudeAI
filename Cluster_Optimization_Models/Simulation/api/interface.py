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
  • SimulationManager subclasses ClusterManager (Realtime) — delegates the
    full scheduling loop (_run_batch, _expire_jobs, etc.) to it directly
  • Per-group solver calls per step (one call per tenant group from plan-ahead)
  • Unplaced jobs get a wait bump of batch_duration_sec per step they wait
  • Plan-ahead: tries PlanAhead/plan_ahead_optimizer (Gurobi); falls back
    to a numpy mock that produces the same output shape
  • SimulationState exposes full batch stats and running totals for the frontend
"""

from __future__ import annotations

import sys
import os
from collections import Counter
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
    JOBS_PER_ROUND, K_WINDOW, BATCH_DURATION_SEC,
    MIN_LIFETIME_SEC, MAX_LIFETIME_SEC,
    NUM_NODES, NUM_TENANTS, SPIKE_PROB, DEFAULT_CONFIG,
)
from cluster_manager import ClusterManager, RunningJob, BatchResult

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
    Exclusive tenants each get their own isolated group; shared tenants
    share one group.  Matches the isolation semantics of the real optimizer.
    """
    num_tenants   = int(cfg.get('num_tenants',            NUM_TENANTS))
    num_nodes     = int(cfg.get('total_nodes', cfg.get('num_nodes', NUM_NODES)))
    num_exclusive = int(cfg.get('num_exclusive_tenants',  1))
    horizon       = int(cfg.get('horizon_steps', 50))
    period_steps  = int(cfg.get('period_steps',           4))
    n_periods     = max(1, horizon // period_steps)
    week_number   = interval // horizon if horizon > 0 else 0

    exclusive_ids   = list(range(min(num_exclusive, num_tenants)))
    shared_ids      = list(range(len(exclusive_ids), num_tenants))
    all_machine_ids = list(range(num_nodes))

    def _make_groups(_h: int) -> list:
        groups = [
            {"tenant_ids": [i], "machine_ids": all_machine_ids, "exclusive": True}
            for i in exclusive_ids
        ]
        if shared_ids:
            groups.append({"tenant_ids": shared_ids, "machine_ids": all_machine_ids, "exclusive": False})
        return groups

    intervals_out = [
        {"interval": h, "groups": _make_groups(h)}
        for h in range(n_periods)
    ]

    # tenant_schedule for frontend Tenants panel display
    all_tenant_ids = list(range(num_tenants))
    tenant_schedule = {
        str(t): {str(h): all_machine_ids for h in range(n_periods)}
        for t in all_tenant_ids
    }

    slot_labels = [
        f"{h * period_steps}–{h * period_steps + period_steps - 1}i"
        for h in range(n_periods)
    ]
    pos_in_period = interval % horizon if horizon > 0 else 0
    current_slot  = min(pos_in_period // period_steps, n_periods - 1)

    return {
        "intervals":        intervals_out,
        "interval":         interval,
        "num_slots":        n_periods,
        "period_steps":    period_steps,
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


def run_plan_ahead(
    cfg:            dict,
    interval:       int,
    feedback_wait:  dict | None = None,  # {tenant_id: avg_wait_sec} from realtime
    feedback_vbar:  dict | None = None,  # {node_id: violation_rate} from realtime
    feedback_queue: dict | None = None,  # {tenant_id: queued_job_count} from realtime
) -> dict:
    """
    Run plan-ahead MISOCP (SOCP default).  Attempts Gurobi; falls back to numpy mock.

    Feedback parameters are passed from the live SimulationManager so the
    plan-ahead inflates demand for tenants with high wait times and reduces
    capacity for nodes with high SLA violation rates.

    Returns dict with "intervals" (for ClusterManager) and "tenant_schedule"
    (for frontend display).
    """
    if not _HAS_GUROBI:
        return _mock_plan_ahead(cfg, interval)

    import math

    try:
        num_tenants    = int(cfg.get('num_tenants',          NUM_TENANTS))
        num_nodes      = int(cfg.get('total_nodes', cfg.get('num_nodes', NUM_NODES)))
        n_always_avail = int(cfg.get('always_on_nodes', cfg.get('num_always_available', 3)))
        num_exclusive  = int(cfg.get('num_exclusive_tenants', 1))
        period_steps   = int(cfg.get('period_steps',   4))
        horizon        = int(cfg.get('horizon_steps', 50))
        n_periods      = max(1, horizon // period_steps)
        time_limit     = int(cfg.get('plan_time_limit',    30))
        mip_gap        = float(cfg.get('plan_mip_gap',     0.05))
        use_socp       = bool(int(cfg.get('use_socp',       1)))   # SOCP is default
        sigma_frac     = float(cfg.get('sigma_frac',        0.20))
        epsilon        = float(cfg.get('cantelli_epsilon',  0.10))
        min_mach       = int(cfg.get('min_machines_per_tenant', 2))

        feedback_alpha    = float(cfg.get('feedback_alpha',    0.5))
        feedback_beta     = float(cfg.get('feedback_beta',     0.3))
        feedback_gamma    = float(cfg.get('feedback_gamma',    0.3))
        feedback_wait_ref = float(cfg.get('feedback_wait_ref', 1.0))
        queue_ref         = int(cfg.get('queue_ref',           10))

        plan_capacity_buffer = float(cfg.get('plan_capacity_buffer', 0.0))

        # ── Node capacity (average node GB) ──────────────────────────────────
        mem_min_gb  = float(cfg.get('node_mem_min_gb', 16))
        mem_max_gb  = float(cfg.get('node_mem_max_gb', 64))
        node_cap    = (mem_min_gb + mem_max_gb) / 2.0  # GB

        # ── Demand calibration: force 2+ machines per tenant ─────────────────
        # The SOCP Cantelli buffer consumes kappa*sigma_frac*u per node.
        # A single node can satisfy: u < node_cap / (1 + kappa*sigma_frac).
        # We set usage_min just above that threshold so 2 nodes are always required,
        # and usage_max at 1.35× to firmly land in the 2-node region.
        kappa_val    = math.sqrt((1.0 - epsilon) / epsilon)
        one_node_max = node_cap / (1.0 + kappa_val * sigma_frac)
        usage_min    = round(max(1.0, one_node_max * 1.05), 2)
        usage_max    = round(max(2.0, one_node_max * 1.40), 2)

        P = build_synthetic_data(
            seed                    = 42,
            n_tenants               = num_tenants,
            n_nodes                 = num_nodes,
            n_intervals             = n_periods,
            node_capacity           = node_cap,
            n_always_available      = n_always_avail,
            n_exclusive             = num_exclusive,
            tenant_usage_min        = usage_min,
            tenant_usage_max        = usage_max,
            sigma_frac              = sigma_frac,
            epsilon                 = epsilon,
            feedback_vbar           = feedback_vbar or {},
            feedback_wait           = feedback_wait or {},
            feedback_queue          = feedback_queue or {},
            feedback_alpha          = feedback_alpha,
            feedback_beta           = feedback_beta,
            feedback_gamma          = feedback_gamma,
            feedback_wait_ref       = feedback_wait_ref,
            queue_ref               = queue_ref,
            capacity_buffer_frac    = plan_capacity_buffer,
            min_machines_per_tenant = min_mach,
        )
        # Zero infra cost: always-on machines are pre-paid; model should
        # distribute tenants freely rather than packing into the minimum.
        P['lam'][0] = 0.0
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
            f"{h * period_steps}–{h * period_steps + period_steps - 1}i"
            for h in range(n_periods)
        ]
        pos_in_period = interval % horizon if horizon > 0 else 0
        current_slot  = min(pos_in_period // period_steps, n_periods - 1)

        sigma_val = (vars_['sigma'].X
                     if 'sigma' in vars_ and hasattr(vars_['sigma'], 'X')
                     else 0.0)
        avg_nodes = (
            sum(len(ns) for h_dict in tenant_schedule.values() for ns in h_dict.values())
            / max(1, num_tenants * n_periods)
        )

        return {
            "intervals":              plan_out["intervals"],
            "interval":               interval,
            "num_slots":              n_periods,
            "period_steps":           period_steps,
            "planning_horizon":       horizon,
            "slot_labels":            slot_labels,
            "tenant_schedule":        tenant_schedule,
            "current_slot":           current_slot,
            "exclusive_tenant_count": len(P["T_e"]),
            "total_tenant_count":     num_tenants,
            "summary": {
                "avg_nodes_per_tenant": round(avg_nodes, 2),
                "isolation_score":      round(sigma_val, 4),
                "week_number":          interval // horizon if horizon > 0 else 0,
            },
        }
    except Exception:
        return _mock_plan_ahead(cfg, interval)


# ═══════════════════════════════════════════════════════════════════════════════
# § SIMULATION MANAGER  (config-aware subclass of Realtime's ClusterManager)
# ═══════════════════════════════════════════════════════════════════════════════

class SimulationManager(ClusterManager):
    """
    Thin, config-aware subclass of Realtime's ClusterManager.

    The full scheduling loop (_run_batch, _expire_jobs, _refresh_node_states,
    _get_groups, _bump_wait_for_unplaced, _update_W_t) is inherited directly
    from ClusterManager — no duplication.  Only three methods are overridden
    because they need to read from the runtime sim_config dict:

      __init__   — builds config-aware node topology
      _make_jobs — respects job_arrival_interval; reads job-count range from config
      _start_job — reads spike_prob and lifetime bounds from config

    The sys.modules trick (simulation_config registered as "simulation_data")
    ensures ClusterManager's module-level imports (BATCH_DURATION_SEC = 1,
    generate_nodes, generate_jobs) come from simulation_config automatically.
    """

    def __init__(self, seed: int = 42, sim_config: Optional[dict] = None) -> None:
        cfg = dict(sim_config or DEFAULT_CONFIG)
        self._sim_config  = cfg
        self._num_tenants = int(cfg.get('num_tenants', NUM_TENANTS))
        # Initialise parent with config-aware k_window; no verbose output, no log file.
        super().__init__(
            seed      = seed,
            verbose   = False,
            k_window  = int(cfg.get('k_window', K_WINDOW)),
            log_file  = None,
        )
        # Replace the default-topology nodes created by ClusterManager.__init__
        # with config-aware nodes (total_nodes, mem ranges, cpu ranges).
        self.nodes = generate_nodes(self.rng, config=cfg)
        self._refresh_node_states(record_history=False)

    # ── Config-aware overrides ─────────────────────────────────────────────────

    def _make_jobs(self, batch_id: int) -> list[Job]:
        """Generate jobs only on arrival-interval boundaries; uses config ranges."""
        cfg              = self._sim_config
        arrival_interval = max(1, int(cfg.get('job_arrival_interval', 1)))
        if batch_id % arrival_interval != 0:
            return []
        jobs_min = int(cfg.get('jobs_min_per_round', self._jobs_per_round))
        jobs_max = int(cfg.get('jobs_max_per_round', jobs_min))
        if jobs_min < jobs_max:
            mean     = (jobs_min + jobs_max) / 2.0
            std      = (jobs_max - jobs_min) / 6.0
            num_jobs = int(np.clip(round(self.rng.normal(mean, std)), jobs_min, jobs_max))
        else:
            num_jobs = max(1, jobs_min)
        num_tenants = int(cfg.get('num_tenants', NUM_TENANTS))
        jobs = generate_jobs(batch_id, num_jobs=num_jobs,
                             num_tenants=num_tenants, rng=self.rng, config=cfg)
        for j in jobs:
            j.arrival_timestamp = self.sim_time
        return jobs

    def _start_job(self, job: Job, node_id: int) -> RunningJob:
        """Place job; reads spike probability and lifetime bounds from config."""
        job.scheduling_timestamp = self.sim_time
        cfg        = self._sim_config
        spike_prob = float(cfg.get('spike_prob_pct', SPIKE_PROB * 100)) / 100.0
        spike_frac = sample_spike_fraction(self.rng, spike_prob=spike_prob)
        act_mem_mb = job.pred_mem_mb * (1.0 + spike_frac)
        min_life   = float(cfg.get('min_lifetime_sec', MIN_LIFETIME_SEC))
        max_life   = float(cfg.get('max_lifetime_sec', MAX_LIFETIME_SEC))
        lifetime   = float(self.rng.uniform(min_life, max_life))
        rj = RunningJob(
            job          = job,
            node_id      = node_id,
            act_mem_mb   = act_mem_mb,
            is_spike     = spike_frac > 0.0,
            start_time   = self.sim_time,
            lifetime_sec = lifetime,
        )
        self._running_jobs.append(rj)
        return rj

    def run_step(self, batch_id: int, plan_output: Optional[dict] = None) -> BatchResult:
        """Single scheduling epoch — delegates to ClusterManager._run_batch()."""
        return self._run_batch(batch_id, plan_output)


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
        self._log_file = None
        if int(cfg.get('enable_logging', 0)):
            import json as _json, pathlib as _pl
            log_path = _pl.Path(__file__).parent / 'sim_log.jsonl'
            self._log_file = open(log_path, 'w', encoding='utf-8')
            self._json = _json

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
        plan_ahead_i = int(self.cfg.get('horizon_steps', 50))

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
                "req_mem_mb":  round(cm.scheduling_log[k].get("req_mem_mb", 0.0), 1),
                "req_cpu":     round(cm.scheduling_log[k].get("req_cpu", 0.0), 3),
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

        # Structured log entry (when enable_logging=1)
        if self._log_file is not None:
            log_entry = {
                "interval":           self.interval,
                "jobs_generated":     result.jobs_generated,
                "jobs_placed":        result.jobs_placed,
                "queue_size":         result.queue_size_after,
                "node_violations":    result.node_violations,
                "spike_count":        result.spike_count,
                "overflow_count":     result.physical_overflow_count,
                "jobs_expired":       result.jobs_expired,
                "solver_calls":       result.solver_calls,
                "avg_eff_mem_pct":    round(result.avg_eff_mem_pct,  2),
                "avg_phys_mem_pct":   round(result.avg_phys_mem_pct, 2),
                "avg_act_pct":        round(result.avg_eff_active_pct, 2),
                "nodes_assigned":     result.nodes_assigned,
                "total_nodes_used":   result.total_nodes_used,
                "W_t":                {str(k): round(v, 2) for k, v in self.manager.W_t.items()},
                "running_jobs":       len(self.manager._running_jobs),
            }
            self._log_file.write(self._json.dumps(log_entry) + "\n")
            self._log_file.flush()

        # Fire plan-ahead at configured interval (with live feedback from manager)
        if self.interval > 0 and self.interval % plan_ahead_i == 0:
            self.last_plan_ahead = run_plan_ahead(
                self.cfg, self.interval,
                feedback_wait  = dict(cm.W_t),
                feedback_vbar  = {
                    n.node_id: compute_violation_rate(n.overflow_history, cm._k_window)
                    for n in cm.nodes
                },
                feedback_queue = dict(Counter(j.tenant_id for j in cm.job_queue)),
            )
        elif self.last_plan_ahead is not None:
            # Advance current_slot pointer so the frontend highlights correctly
            horizon       = int(self.last_plan_ahead.get("planning_horizon", plan_ahead_i))
            period_steps = int(self.last_plan_ahead.get("period_steps", 4))
            num_slots     = int(self.last_plan_ahead.get("num_slots", 1))
            pos           = self.interval % horizon if horizon > 0 else 0
            self.last_plan_ahead["current_slot"] = min(
                pos // period_steps, num_slots - 1
            )

    def trigger_plan_ahead(self) -> dict:
        """Run plan-ahead immediately (on-demand from frontend) with live feedback."""
        cm = self.manager
        self.last_plan_ahead = run_plan_ahead(
            self.cfg, self.interval,
            feedback_wait  = dict(cm.W_t),
            feedback_vbar  = {
                n.node_id: compute_violation_rate(n.overflow_history, cm._k_window)
                for n in cm.nodes
            },
            feedback_queue = dict(Counter(j.tenant_id for j in cm.job_queue)),
        )
        return self.last_plan_ahead

    @property
    def sim_totals(self) -> dict:
        n = max(1, self._num_steps)
        q = len(self.manager.job_queue)
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
