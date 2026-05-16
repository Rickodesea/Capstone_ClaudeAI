# Cluster Scheduler Demo — Session TODO

> Persistent tracker. Update status each session. Format: `[ ]` not started · `[~]` in progress · `[x]` done.

## Context

Full-stack interactive demo of the cluster scheduling system. Python backend (FastAPI) wraps the
real optimization code; React frontend visualizes the live simulation.

**Key files to re-read at session start:**
| File | Purpose |
|------|---------|
| `optimization/cluster_manager.py` | Real-time MILP orchestration — the engine |
| `optimization/optimizer_google_or.py` | OR-Tools MILP solver (constraints C1–C5) |
| `optimization/simulation_data.py` | Config constants, Job/NodeState dataclasses, generators |
| `PlanAheadModel/plan_ahead_optimizer.py` | MISOCP plan-ahead (Gurobi) |
| `real_time_optimization_model_source_of_truth.txt` | Math reference for real-time model |
| `PlanAheadModel/plan_ahead_source_of_truth.txt` | Math reference for plan-ahead model |
| `demo/api/main.py` | FastAPI entry point — step/state/reset endpoints |
| `demo/api/plan_ahead_mock.py` | Mock plan-ahead (no Gurobi needed for demo) |
| `demo/frontend/src/App.tsx` | Main React app — simulation loop, layout |
| `demo/frontend/src/types.ts` | All TypeScript interfaces matching API response |

---

## Phase 1 — MVP (Session 2026-05-15)

### Backend (demo/api/)
- [x] Copy `simulation_data.py` from `optimization/`
- [x] Copy `optimizer_google_or.py` from `optimization/`
- [x] Copy `cluster_manager.py` from `optimization/` (remove `__main__` block)
- [x] Create `plan_ahead_mock.py` — generates heatmap data without Gurobi
- [x] Create `main.py` — FastAPI with `/api/state`, `/api/step`, `/api/reset`
- [x] Create `requirements.txt`

### Frontend (demo/frontend/)
- [x] Vite + React + TypeScript scaffold
- [x] Tailwind CSS + Framer Motion + Recharts
- [x] `types.ts` — SimState, NodeInfo, QueuedJob, PlanAheadResult interfaces
- [x] `api.ts` — fetch wrappers for all 3 endpoints
- [x] `App.tsx` — simulation loop (play/pause/speed/reset), layout
- [x] `HUD.tsx` — upper-right: jobs, tenants, nodes, mem%, next plan-ahead
- [x] `JobQueue.tsx` — animated queue panel with oldest-job timer
- [x] `NodeGrid.tsx` — node cards with memory bars + running job pills
- [x] `MemoryWave.tsx` — Recharts area chart with rolling history
- [x] `PlanAheadOverlay.tsx` — slide-in panel with heatmap + tenant-node table

---

## Phase 2 — Visual Polish (Next Session)

- [ ] Color-coded job pills per tenant in NodeGrid
- [ ] Animated job-to-node "flight" (job leaves queue, lands on node card)
- [ ] Node violation pulse (red glow when violation_rate > 0.3)
- [ ] Speed slider: 1x / 5x / 10x / 50x presets
- [ ] Queue filter by tenant
- [ ] Tooltip on node memory bar (used MB / cap MB)
- [ ] Smooth memory bar width transition (CSS `transition: width 0.5s`)

---

## Phase 3 — Prediction Integration

- [ ] Replace `plan_ahead_mock.py` with real Gurobi plan-ahead call
  - Requires Gurobi WLS license in `demo/api/.env`
  - Wire `plan_ahead_data.py` + `plan_ahead_optimizer.py` from `PlanAheadModel/`
- [ ] Replace `simulate_max_mem` / `simulate_p95_cpu` with real LSTM API calls
  - Prediction team API endpoint: TBD (see `Team_Alignment_Prediction_Pipeline.md`)
  - Interface already stubbed in `demo/api/main.py` → `_predict_job()`
- [ ] Add tenant-node access control (plan-ahead `A_t_i` → feed into optimizer C5)

---

## Phase 4 — Demo Hardening

- [ ] Docker Compose: `api` service + `frontend` service
- [ ] Hot-reload on param changes (NUM_NODES, NUM_TENANTS sliders in UI)
- [ ] Export simulation log as CSV
- [ ] README with screenshots and run instructions

---

## Architecture Notes

```
demo/
├── api/                          # Python backend (FastAPI)
│   ├── main.py                   # POST /api/step · GET /api/state · POST /api/reset
│   ├── simulation_data.py        # Copied from optimization/ — constants + dataclasses
│   ├── optimizer_google_or.py    # Copied from optimization/ — OR-Tools MILP
│   ├── cluster_manager.py        # Copied from optimization/ — orchestration
│   ├── plan_ahead_mock.py        # Mock plan-ahead (replace with Gurobi in Phase 3)
│   └── requirements.txt
└── frontend/                     # React + TypeScript (Vite)
    ├── src/
    │   ├── App.tsx               # Root: simulation loop, layout grid
    │   ├── types.ts              # TS interfaces for all API responses
    │   ├── api.ts                # Fetch wrappers
    │   └── components/
    │       ├── HUD.tsx           # Top-right overlay HUD
    │       ├── JobQueue.tsx      # Left panel: animated queue
    │       ├── NodeGrid.tsx      # Right panel: node cards + job pills
    │       ├── MemoryWave.tsx    # Top: rolling memory area chart
    │       └── PlanAheadOverlay.tsx  # Slide-in plan-ahead heatmap
    └── package.json
```

## Key Constants (change in simulation_data.py to tune demo)

| Constant | Default | Effect |
|----------|---------|--------|
| `NUM_NODES` | 5 | Cluster size |
| `NUM_TENANTS` | 3 | Number of tenants |
| `JOBS_PER_ROUND` | 20 | New jobs per interval |
| `MIN_LIFETIME_SEC` | 60 | Shortest job (simulated seconds) |
| `MAX_LIFETIME_SEC` | 600 | Longest job (simulated seconds) |
| `SPIKE_PROB` | 0.10 | Fraction of jobs that spike memory |

## API Contract (POST /api/step response shape)

```json
{
  "interval": 42,
  "plan_ahead_interval": 50,
  "sim_time": "2026-05-15T10:00:00+00:00",
  "queue": [{ "job_id": "r3_j1", "tenant_id": 0, "req_mem_mb": 512, "pred_mem_mb": 480, "wait_intervals": 5 }],
  "nodes": [{ "node_id": 0, "capacity_mb": 16384, "used_mb": 8200, "mem_pct": 50.0, "eff_pct": 61.2, "violation_rate": 0.0, "running_jobs": [] }],
  "recent_placements": [{ "job_id": "r3_j1", "tenant_id": 0, "node_id": 2, "pred_mem_mb": 480 }],
  "plan_ahead": null,
  "hud": { "total_jobs": 37, "total_tenants": 3, "total_nodes": 5, "mem_utilization_pct": 48.3, "longest_wait_intervals": 7 },
  "mem_history": [44.1, 45.2, 46.8, 48.3]
}
```
