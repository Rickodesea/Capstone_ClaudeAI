"""
main.py  (Simulation)
─────────────────────
FastAPI backend for the interactive cluster scheduler simulation.

Endpoints:
    GET  /api/state   → current simulation state (JSON)
    POST /api/step    → advance one scheduling epoch, return new state
    POST /api/reset   → restart simulation (applies staged config)
    POST /api/config  → stage config changes (applied on next reset)

Run with:
    cd Cluster_Optimization_Models/Simulation/api
    uvicorn main:app --reload --port 8000

Load order (critical):
    1. sys.path set to include this directory
    2. simulation_config imported (DEFAULT_CONFIG, load_config)
    3. interface imported → registers simulation_config as "simulation_data"
       in sys.modules (class identity for Realtime compat), then adds
       Realtime/ to sys.path and imports the MILP solver + dataclasses
"""

from __future__ import annotations
import os, sys

# ── Step 1: Ensure this directory is first on sys.path ─────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# ── Step 2: Import simulation_config (all config, data helpers, and defaults)
from simulation_config import DEFAULT_CONFIG, load_config   # noqa: E402

# ── Step 3: Import interface (adds Realtime/ to sys.path) ──────────────────
from interface import SimulationState                   # noqa: E402

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


# ═══════════════════════════════════════════════════════════════════════════════
# § CONFIG  (all values overridable via POST /api/config, applied on reset)
# ═══════════════════════════════════════════════════════════════════════════════

_SIM_CONFIG: dict = dict(DEFAULT_CONFIG)

MEM_HISTORY_SIZE: int = 80


# ═══════════════════════════════════════════════════════════════════════════════
# § APPLICATION
# ═══════════════════════════════════════════════════════════════════════════════

app = FastAPI(title="Cluster Scheduler Simulation API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

_state = SimulationState(load_config(_SIM_CONFIG))


# ═══════════════════════════════════════════════════════════════════════════════
# § SERIALISATION
# ═══════════════════════════════════════════════════════════════════════════════

def _serialize(state: SimulationState, include_plan_ahead: bool = True) -> dict:
    from simulation_config import compute_available_capacity, compute_violation_rate

    cm          = state.manager
    cfg         = state.cfg
    num_tenants = int(cfg.get('num_tenants', 3))
    plan_ahead_i = int(cfg.get('plan_ahead_interval', 50))

    # ── Queue (active + staged) ─────────────────────────────────────────────
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

    # ── Running jobs indexed by node ────────────────────────────────────────
    node_jobs: dict[int, list[dict]] = {n.node_id: [] for n in cm.nodes}
    for rj in cm._running_jobs:
        node_jobs[rj.node_id].append({
            "job_id":     rj.job.job_id,
            "tenant_id":  rj.job.tenant_id,
            "act_mem_mb": round(rj.act_mem_mb, 1),
            "is_spike":   rj.is_spike,
        })

    # ── Nodes ────────────────────────────────────────────────────────────────
    nodes = []
    for n in cm.nodes:
        m_cap = compute_available_capacity(n)
        vrate = compute_violation_rate(n.overflow_history, cm._k_window)
        nodes.append({
            "node_id":        n.node_id,
            "capacity_mb":    n.capacity_mb,
            "os_tax_mb":      n.os_tax_mb,
            "used_mb":        round(n.used_mb, 1),
            "m_cap":          round(m_cap, 1),
            "mem_pct":        round((n.used_mb / n.capacity_mb) * 100, 1),
            "eff_pct":        round((n.used_mb / max(1.0, m_cap)) * 100, 1),
            "cpu_cores":      n.cpu_cores,
            "violation_rate": round(vrate, 3),
            # viols_count = rolling K-window (for isHot detection on node card)
            # viols_total = cumulative all-time SLA breach count (displayed on node card)
            "viols_count":    sum(n.overflow_history[-cm._k_window:]),
            "viols_total":    sum(n.overflow_history),
            "ovrflw_count":   sum(n.violation_history[-cm._k_window:]),
            "running_jobs":   node_jobs[n.node_id],
        })

    # ── HUD ──────────────────────────────────────────────────────────────────
    total_jobs   = len(cm.job_queue) + len(cm._staged_queue) + len(cm._running_jobs)
    longest_wait = max(
        (state.interval - j.arrival_round for j in all_queued), default=0
    )
    avg_cap_pct  = sum(nd["mem_pct"] for nd in nodes) / max(1, len(nodes))
    avg_eff_pct  = sum(nd["eff_pct"] for nd in nodes) / max(1, len(nodes))
    active_nds   = [nd for nd in nodes if nd["used_mb"] > 0]
    avg_eff_act  = (sum(nd["eff_pct"] for nd in active_nds) / len(active_nds)) if active_nds else 0.0

    intervals_to_plan_ahead = (
        plan_ahead_i - (state.interval % plan_ahead_i)
        if state.interval % plan_ahead_i != 0 or state.interval == 0
        else 0
    )

    # ── Per-tenant info ──────────────────────────────────────────────────────
    tenant_running: dict[int, list[int]] = {}
    tenant_running_count: dict[int, int] = {}
    for rj in cm._running_jobs:
        t_id = rj.job.tenant_id
        if t_id not in tenant_running:
            tenant_running[t_id] = []
        if rj.node_id not in tenant_running[t_id]:
            tenant_running[t_id].append(rj.node_id)
        tenant_running_count[t_id] = tenant_running_count.get(t_id, 0) + 1

    tenant_queued_count: dict[int, int] = {}
    for j in all_queued:
        tenant_queued_count[j.tenant_id] = tenant_queued_count.get(j.tenant_id, 0) + 1

    pa          = state.last_plan_ahead
    cur_slot    = str(pa.get("current_slot", 0)) if pa else "0"
    ts_sched    = pa.get("tenant_schedule", {}) if pa else {}

    tenants_info = []
    for t in range(num_tenants):
        auth = ts_sched.get(str(t), {}).get(cur_slot, [])
        tenants_info.append({
            "tenant_id":          t,
            "avg_wait_sec":       round(cm.W_t.get(t, 0.0), 2),
            "active_node_ids":    sorted(tenant_running.get(t, [])),
            "authorized_nodes":   auth,
            "running_jobs_count": tenant_running_count.get(t, 0),
            "queued_jobs_count":  tenant_queued_count.get(t, 0),
        })

    # ── Batch stats (last step result) ───────────────────────────────────────
    br = state.last_batch_result
    batch_stats = None
    if br is not None:
        batch_stats = {
            "batch_id":               br.batch_id,
            "jobs_generated":         br.jobs_generated,
            "jobs_placed":            br.jobs_placed,
            "queue_size_after":       br.queue_size_after,
            "solver_calls":           br.solver_calls,
            "consecutive_failures":   br.consecutive_failures,
            "node_violations":        br.node_violations,
            "spike_count":            br.spike_count,
            "physical_overflow_count": br.physical_overflow_count,
            "jobs_expired":           br.jobs_expired,
            "nodes_assigned":         br.nodes_assigned,
            "total_nodes_used":       br.total_nodes_used,
            "avg_eff_mem_pct":        round(br.avg_eff_mem_pct,    1),
            "avg_phys_mem_pct":       round(br.avg_phys_mem_pct,   1),
            "avg_eff_active_pct":     round(br.avg_eff_active_pct, 1),
        }

    return {
        "interval":            state.interval,
        "plan_ahead_interval": plan_ahead_i,
        "sim_time":            cm.sim_time.isoformat(),
        "queue":               queue,
        "nodes":               nodes,
        "recent_placements":   state.recent_placements,
        "plan_ahead":          state.last_plan_ahead if include_plan_ahead else None,
        "hud": {
            "total_jobs":              total_jobs,
            "total_tenants":           num_tenants,
            "total_nodes":             int(cfg.get('num_nodes', 5)),
            "mem_utilization_pct":        round(avg_cap_pct, 1),
            "eff_utilization_pct":        round(avg_eff_pct, 1),
            "eff_active_utilization_pct": round(avg_eff_act, 1),
            "longest_wait_intervals":     longest_wait,
            "intervals_to_plan_ahead":    intervals_to_plan_ahead,
        },
        "mem_history":         state.mem_history[-MEM_HISTORY_SIZE:],
        "eff_history":         state.eff_history[-MEM_HISTORY_SIZE:],
        "eff_active_history":  state.eff_active_history[-MEM_HISTORY_SIZE:],
        "placed_history":      state.placed_history[-MEM_HISTORY_SIZE:],
        "tenants":        tenants_info,
        "sim_config":     dict(cfg),
        "batch_stats":    batch_stats,
        "sim_totals":     state.sim_totals,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# § ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/api/config")
def update_config(body: dict) -> dict:
    """Stage config changes; applied on next POST /api/reset."""
    _SIM_CONFIG.update({k: v for k, v in body.items() if k in _SIM_CONFIG})
    return {"ok": True, "pending": dict(_SIM_CONFIG)}


@app.get("/api/state")
def get_state() -> dict:
    return _serialize(_state)


@app.post("/api/step")
def step() -> dict:
    _state.step()

    # On plan-ahead boundary, include the new plan in the response
    plan_ahead_i = int(_state.cfg.get('plan_ahead_interval', 50))
    fired_plan_ahead = (
        _state.interval > 0 and _state.interval % plan_ahead_i == 0
    )
    response = _serialize(_state, include_plan_ahead=True)
    # Only send plan_ahead payload on the step it fires (avoids redundant data)
    if not fired_plan_ahead:
        response["plan_ahead"] = None
    return response


@app.post("/api/plan_ahead")
def trigger_plan_ahead() -> dict:
    """Run plan-ahead immediately and return the result."""
    return _state.trigger_plan_ahead()


@app.post("/api/reset")
def reset() -> dict:
    global _state
    cfg    = load_config(_SIM_CONFIG)
    _state = SimulationState(cfg)
    return _serialize(_state)
