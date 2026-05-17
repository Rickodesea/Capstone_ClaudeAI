"""
main.py
───────
FastAPI backend for the Cluster Optimization Simulation.

Endpoints:
    GET  /api/state   → current simulation state (JSON)
    POST /api/step    → advance one scheduling epoch, return new state
    POST /api/reset   → restart simulation (applies staged config)
    POST /api/config  → stage config changes (applied on next reset)

Run with:
    cd Cluster_Optimization_Models/Simulation/api
    uvicorn main:app --reload --port 8000
"""

from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from cluster_manager import ClusterManager
from simulation_data import (
    NUM_TENANTS, NUM_NODES,
    compute_available_capacity,
    compute_violation_rate,
)
from plan_ahead_mock import generate_plan_ahead

# ── Default config ─────────────────────────────────────────────────────────────
# All values here are overridable via POST /api/config (applied on next reset).

_SIM_CONFIG: dict = {
    # Cluster topology
    'num_nodes':        5,
    'num_tenants':      3,
    'node_mem_min_gb':  16,    # GB
    'node_mem_max_gb':  64,    # GB
    'node_cpu_min':     8,     # cores
    'node_cpu_max':     64,    # cores
    # Workload
    'jobs_per_round':   20,
    'req_mem_min_mb':   512,
    'req_mem_max_mb':   1024,
    'spike_prob_pct':   10,    # %
    # Model hyper-parameters
    'k_window':         10,
    'min_lifetime_sec': 60,
    'max_lifetime_sec': 600,
    # Plan-ahead
    'plan_ahead_interval': 50,
    'access_period':       4,
}

MEM_HISTORY_SIZE: int = 80


# ── Application ────────────────────────────────────────────────────────────────
app = FastAPI(title="Cluster Scheduler Simulation API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


# ── Simulation state ────────────────────────────────────────────────────────────

class _SimState:
    def __init__(self, cfg: dict) -> None:
        self.cfg = dict(cfg)
        self.manager = ClusterManager(seed=42, verbose=False, log_file=None,
                                      sim_config=self.cfg)
        self.interval:      int  = 0
        self.mem_history:   list = []
        self.eff_history:   list = []
        self.placed_history: list = []
        self.recent_placements: list = []
        self.last_plan_ahead: dict | None = generate_plan_ahead(
            self.cfg['num_tenants'], self.cfg['num_nodes'], 0,
            plan_ahead_horizon=self.cfg['plan_ahead_interval'],
            access_period=self.cfg['access_period'],
        )


_state = _SimState(_SIM_CONFIG)


@app.post("/api/config")
def update_config(body: dict) -> dict:
    """Stage config changes; applied on next POST /api/reset."""
    _SIM_CONFIG.update({k: v for k, v in body.items() if k in _SIM_CONFIG})
    return {"ok": True, "pending": dict(_SIM_CONFIG)}


# ── Serialisation ───────────────────────────────────────────────────────────────

def _serialize(state: _SimState) -> dict:
    cm            = state.manager
    cfg           = state.cfg
    num_tenants   = int(cfg.get('num_tenants', NUM_TENANTS))
    plan_ahead_i  = int(cfg.get('plan_ahead_interval', 50))

    # Queue
    all_queued = cm.job_queue + cm._staged_queue
    queue = sorted(
        [{
            "job_id":           j.job_id,
            "tenant_id":        j.tenant_id,
            "req_mem_mb":       round(j.req_mem_mb, 1),
            "pred_mem_mb":      round(j.pred_mem_mb, 1),
            "arrival_interval": j.arrival_round,
            "wait_intervals":   max(0, state.interval - j.arrival_round),
            "req_cpu":          round(j.req_cpu, 2),
        } for j in all_queued],
        key=lambda x: x["wait_intervals"], reverse=True,
    )

    # Running jobs indexed by node
    node_jobs: dict[int, list[dict]] = {n.node_id: [] for n in cm.nodes}
    for rj in cm._running_jobs:
        node_jobs[rj.node_id].append({
            "job_id":     rj.job.job_id,
            "tenant_id":  rj.job.tenant_id,
            "act_mem_mb": round(rj.act_mem_mb, 1),
            "is_spike":   rj.is_spike,
        })

    # Nodes
    nodes = []
    for n in cm.nodes:
        m_cap = compute_available_capacity(n)
        vrate = compute_violation_rate(n.overflow_history, cm._k_window)
        nodes.append({
            "node_id":       n.node_id,
            "capacity_mb":   n.capacity_mb,
            "os_tax_mb":     n.os_tax_mb,
            "used_mb":       round(n.used_mb, 1),
            "m_cap":         round(m_cap, 1),
            "mem_pct":       round((n.used_mb / n.capacity_mb) * 100, 1),
            "eff_pct":       round((n.used_mb / max(1.0, m_cap)) * 100, 1),
            "cpu_cores":     n.cpu_cores,
            "violation_rate": round(vrate, 3),
            "viols_count":   sum(n.overflow_history[-cm._k_window:]),
            "pme_count":     sum(n.violation_history[-cm._k_window:]),
            "running_jobs":  node_jobs[n.node_id],
        })

    # HUD
    total_jobs   = len(cm.job_queue) + len(cm._staged_queue) + len(cm._running_jobs)
    longest_wait = max((state.interval - j.arrival_round for j in all_queued), default=0)
    avg_cap_pct  = sum(nd["mem_pct"] for nd in nodes) / max(1, len(nodes))
    avg_eff_pct  = sum(nd["eff_pct"] for nd in nodes) / max(1, len(nodes))

    intervals_to_plan_ahead = (
        plan_ahead_i - (state.interval % plan_ahead_i)
        if state.interval % plan_ahead_i != 0 or state.interval == 0
        else 0
    )

    # Per-tenant info
    tenant_running: dict[int, list[int]] = {}
    for rj in cm._running_jobs:
        t = rj.job.tenant_id
        if t not in tenant_running:
            tenant_running[t] = []
        if rj.node_id not in tenant_running[t]:
            tenant_running[t].append(rj.node_id)

    tenants_info = []
    for t in range(num_tenants):
        slot = str(state.last_plan_ahead.get("current_slot", 0)) if state.last_plan_ahead else "0"
        ts   = state.last_plan_ahead.get("tenant_schedule", {}) if state.last_plan_ahead else {}
        auth = ts.get(str(t), {}).get(slot, [])
        tenants_info.append({
            "tenant_id":        t,
            "avg_wait_sec":     round(cm.W_t.get(t, 0.0), 2),
            "active_node_ids":  sorted(tenant_running.get(t, [])),
            "authorized_nodes": auth,
        })

    return {
        "interval":            state.interval,
        "plan_ahead_interval": plan_ahead_i,
        "sim_time":            cm.sim_time.isoformat(),
        "queue":               queue,
        "nodes":               nodes,
        "recent_placements":   state.recent_placements,
        "plan_ahead":          state.last_plan_ahead,
        "hud": {
            "total_jobs":              total_jobs,
            "total_tenants":           num_tenants,
            "total_nodes":             int(cfg.get('num_nodes', NUM_NODES)),
            "mem_utilization_pct":     round(avg_cap_pct, 1),
            "eff_utilization_pct":     round(avg_eff_pct, 1),
            "longest_wait_intervals":  longest_wait,
            "intervals_to_plan_ahead": intervals_to_plan_ahead,
        },
        "mem_history":   state.mem_history[-MEM_HISTORY_SIZE:],
        "eff_history":   state.eff_history[-MEM_HISTORY_SIZE:],
        "placed_history": state.placed_history[-MEM_HISTORY_SIZE:],
        "tenants":       tenants_info,
        "sim_config":    dict(cfg),
    }


# ── Routes ──────────────────────────────────────────────────────────────────────

@app.get("/api/state")
def get_state() -> dict:
    return _serialize(_state)


@app.post("/api/step")
def step() -> dict:
    cm = _state.manager
    before_keys = set(cm.scheduling_log.keys())

    result = cm._run_batch(_state.interval)
    _state.interval += 1

    after_keys = set(cm.scheduling_log.keys())
    _state.recent_placements = [
        {
            "job_id":      cm.scheduling_log[k]["job_id"],
            "tenant_id":   cm.scheduling_log[k]["tenant_id"],
            "node_id":     cm.scheduling_log[k]["node_id"],
            "pred_mem_mb": round(cm.scheduling_log[k]["pred_mem_mb"], 1),
        }
        for k in (after_keys - before_keys)
    ]

    _state.mem_history.append(round(result.avg_phys_mem_pct, 2))
    _state.eff_history.append(round(result.avg_eff_mem_pct, 2))
    _state.placed_history.append(result.jobs_placed)

    new_plan_ahead = None
    cfg = _state.cfg
    plan_ahead_i = int(cfg.get('plan_ahead_interval', 50))
    if _state.interval > 0 and _state.interval % plan_ahead_i == 0:
        new_plan_ahead = generate_plan_ahead(
            int(cfg.get('num_tenants', NUM_TENANTS)),
            int(cfg.get('num_nodes', NUM_NODES)),
            _state.interval,
            plan_ahead_horizon=plan_ahead_i,
            access_period=int(cfg.get('access_period', 4)),
        )
        _state.last_plan_ahead = new_plan_ahead

    response = _serialize(_state)
    response["plan_ahead"] = new_plan_ahead
    return response


@app.post("/api/reset")
def reset() -> dict:
    global _state
    _state = _SimState(_SIM_CONFIG)
    return _serialize(_state)
