"""
main.py
───────
FastAPI backend for the cluster scheduler demo.

Endpoints:
    GET  /api/state   → current simulation state (JSON)
    POST /api/step    → advance one scheduling epoch, return new state
    POST /api/reset   → restart simulation from scratch
    POST /api/config  → stage config changes (applied on next reset)

Run with:
    cd demo/api
    uvicorn main:app --reload --port 8000

Prediction stub:
    _predict_job() is called for each new job before it enters the queue.
    Currently wraps simulation_data.simulate_max_mem / simulate_p95_cpu.
    Replace with a real HTTP call to the prediction team's API in Phase 3.
"""

from __future__ import annotations

import os
import sys

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

# ── Config ─────────────────────────────────────────────────────────────────────
PLAN_AHEAD_INTERVAL:     int = 50   # steps between plan-ahead runs (horizon length)
PLAN_AHEAD_ACCESS_PERIOD: int = 4   # intervals per time slot
MEM_HISTORY_SIZE:        int = 80   # rolling window for memory wave graph

# ── Application ────────────────────────────────────────────────────────────────
app = FastAPI(title="Cluster Scheduler Demo API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Simulation state ────────────────────────────────────────────────────────────

class _SimState:
    def __init__(self) -> None:
        self.manager              = ClusterManager(seed=42, verbose=False, log_file=None)
        self.interval:      int   = 0
        self.mem_history:   list  = []
        self.eff_history:   list  = []
        self.placed_history: list = []
        self.recent_placements: list = []
        self.plan_ahead_interval:     int = PLAN_AHEAD_INTERVAL
        self.plan_ahead_access_period: int = PLAN_AHEAD_ACCESS_PERIOD
        self.last_plan_ahead: dict | None = generate_plan_ahead(
            NUM_TENANTS, NUM_NODES, 0,
            plan_ahead_horizon=PLAN_AHEAD_INTERVAL,
            access_period=PLAN_AHEAD_ACCESS_PERIOD,
        )


_state = _SimState()

_pending_config: dict = {}

@app.post("/api/config")
def update_config(body: dict) -> dict:
    """Stage config changes; applied on next POST /api/reset."""
    _pending_config.update(body)
    return {"ok": True, "pending": dict(_pending_config)}


# ── Prediction stub ─────────────────────────────────────────────────────────────

def _predict_job(req_mem_mb: float, req_cpu: float) -> tuple[float, float]:
    """
    Stub: returns (pred_mem_mb, pred_cpu_p95).

    Replace this with an HTTP call to the prediction team's API:
        resp = requests.post(PREDICTION_API_URL, json={...})
        return resp.json()["pred_mem_mb"], resp.json()["pred_cpu_p95"]
    """
    from simulation_data import simulate_max_mem, simulate_p95_cpu
    pred_mem = simulate_max_mem(req_mem_mb)
    pred_cpu = simulate_p95_cpu(req_cpu)
    return pred_mem, pred_cpu


# ── Serialisation ───────────────────────────────────────────────────────────────

def _serialize(state: _SimState) -> dict:
    cm = state.manager

    # Queue: active (oldest-first) + staged (just generated, wait=0)
    all_queued = cm.job_queue + cm._staged_queue
    queue = sorted(
        [
            {
                "job_id":           j.job_id,
                "tenant_id":        j.tenant_id,
                "req_mem_mb":       round(j.req_mem_mb, 1),
                "pred_mem_mb":      round(j.pred_mem_mb, 1),
                "arrival_interval": j.arrival_round,
                "wait_intervals":   max(0, state.interval - j.arrival_round),
                "req_cpu":          round(j.req_cpu, 2),
            }
            for j in all_queued
        ],
        key=lambda x: x["wait_intervals"],
        reverse=True,
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
            "cpu_cores":      n.cpu_cores,
            "violation_rate": round(vrate, 3),
            "viols_count":   sum(n.overflow_history[-cm._k_window:]),   # used_mb > m_cap (soft)
            "pme_count":     sum(n.violation_history[-cm._k_window:]),  # used_mb > capacity_mb (hard)
            "running_jobs":  node_jobs[n.node_id],
        })

    # HUD
    total_jobs    = len(cm.job_queue) + len(cm._staged_queue) + len(cm._running_jobs)
    longest_wait  = max(
        (state.interval - j.arrival_round for j in all_queued), default=0
    )
    avg_mem_pct   = sum(nd["mem_pct"] for nd in nodes) / max(1, len(nodes))

    intervals_to_plan_ahead = (
        state.plan_ahead_interval - (state.interval % state.plan_ahead_interval)
        if state.interval % state.plan_ahead_interval != 0 or state.interval == 0
        else 0
    )

    # Per-tenant: currently running job node IDs and authorized nodes from plan-ahead
    tenant_running: dict[int, list[int]] = {}
    for rj in cm._running_jobs:
        t = rj.job.tenant_id
        if t not in tenant_running:
            tenant_running[t] = []
        if rj.node_id not in tenant_running[t]:
            tenant_running[t].append(rj.node_id)

    tenants_info = []
    for t in range(NUM_TENANTS):
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
        "interval":             state.interval,
        "plan_ahead_interval":  state.plan_ahead_interval,
        "sim_time":             cm.sim_time.isoformat(),
        "queue":                queue,
        "nodes":                nodes,
        "recent_placements":    state.recent_placements,
        "plan_ahead":           state.last_plan_ahead,
        "hud": {
            "total_jobs":               total_jobs,
            "total_tenants":            NUM_TENANTS,
            "total_nodes":              NUM_NODES,
            "mem_utilization_pct":      round(avg_mem_pct, 1),
            "longest_wait_intervals":   longest_wait,
            "intervals_to_plan_ahead":  intervals_to_plan_ahead,
        },
        "mem_history":  state.mem_history[-MEM_HISTORY_SIZE:],
        "eff_history":  state.eff_history[-MEM_HISTORY_SIZE:],
        "placed_history": state.placed_history[-MEM_HISTORY_SIZE:],
        "tenants":      tenants_info,
    }


# ── Routes ──────────────────────────────────────────────────────────────────────

@app.get("/api/state")
def get_state() -> dict:
    return _serialize(_state)


@app.post("/api/step")
def step() -> dict:
    cm = _state.manager

    # Snapshot scheduling_log before the batch runs
    before_keys = set(cm.scheduling_log.keys())

    # Advance one scheduling epoch
    result = cm._run_batch(_state.interval)
    _state.interval += 1

    # Capture newly placed jobs for the animation layer
    after_keys = set(cm.scheduling_log.keys())
    _state.recent_placements = [
        {
            "job_id":     cm.scheduling_log[k]["job_id"],
            "tenant_id":  cm.scheduling_log[k]["tenant_id"],
            "node_id":    cm.scheduling_log[k]["node_id"],
            "pred_mem_mb": round(cm.scheduling_log[k]["pred_mem_mb"], 1),
        }
        for k in (after_keys - before_keys)
    ]

    # Rolling history for wave charts
    _state.mem_history.append(round(result.avg_phys_mem_pct, 2))
    _state.eff_history.append(round(result.avg_eff_mem_pct, 2))
    _state.placed_history.append(result.jobs_placed)

    # Plan-ahead trigger — only fire at the interval boundary
    new_plan_ahead = None
    if _state.interval > 0 and _state.interval % _state.plan_ahead_interval == 0:
        new_plan_ahead = generate_plan_ahead(
            NUM_TENANTS, NUM_NODES, _state.interval,
            plan_ahead_horizon=_state.plan_ahead_interval,
            access_period=_state.plan_ahead_access_period,
        )
        _state.last_plan_ahead = new_plan_ahead

    # Override plan_ahead in response: only non-null when newly triggered this step
    response = _serialize(_state)
    response["plan_ahead"] = new_plan_ahead
    return response


@app.post("/api/reset")
def reset() -> dict:
    global PLAN_AHEAD_INTERVAL, PLAN_AHEAD_ACCESS_PERIOD

    # Apply staged config changes
    jobs_per_round = _pending_config.get("jobs_per_round", None)
    k_window       = _pending_config.get("k_window", None)

    if "plan_ahead_interval" in _pending_config:
        PLAN_AHEAD_INTERVAL = int(_pending_config["plan_ahead_interval"])
    if "access_period" in _pending_config:
        PLAN_AHEAD_ACCESS_PERIOD = int(_pending_config["access_period"])

    _state.manager = ClusterManager(
        seed=42, verbose=False, log_file=None,
        jobs_per_round=jobs_per_round,
        k_window=k_window,
    )
    _state.interval              = 0
    _state.mem_history           = []
    _state.eff_history           = []
    _state.placed_history        = []
    _state.recent_placements     = []
    _state.plan_ahead_interval   = PLAN_AHEAD_INTERVAL
    _state.plan_ahead_access_period = PLAN_AHEAD_ACCESS_PERIOD
    _state.last_plan_ahead       = generate_plan_ahead(
        NUM_TENANTS, NUM_NODES, 0,
        plan_ahead_horizon=PLAN_AHEAD_INTERVAL,
        access_period=PLAN_AHEAD_ACCESS_PERIOD,
    )
    return _serialize(_state)
