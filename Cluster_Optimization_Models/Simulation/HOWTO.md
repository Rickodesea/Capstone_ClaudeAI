# Simulation — How To Run

## Overview
Interactive visualization of the multi-tenant cluster scheduler running in real time.

The Simulation combines both model layers:
- **Real-Time MILP** (from `Realtime/`) — OR-Tools CBC solver, called once per step to place pending jobs
- **Plan-Ahead mock** — numpy-only approximation of `extract_tenant_access_schedule()` from `PlanAhead/`, produces `TenantAccessSchedule` with the same JSON shape for UI display

The backend is a FastAPI server that drives a React + Recharts frontend. All simulation parameters are configurable from the UI without restarting the backend.

## Requirements
```bash
# Backend
pip install fastapi uvicorn ortools numpy

# Frontend
node >= 18, npm
```

## Start the backend
```bash
cd Simulation/api/
uvicorn main:app --reload --port 8000
```

## Start the frontend
```bash
cd Simulation/frontend/
npm install
npm run dev
```
Open http://localhost:5173

## Run backend tests
```bash
cd Simulation/api/
pytest test_main.py -v
```

## Run frontend tests
```bash
cd Simulation/frontend/
npm test
```

## API endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| GET /api/state | GET | Current simulation state |
| POST /api/step | POST | Advance one scheduling epoch |
| POST /api/reset | POST | Restart simulation (applies staged config) |
| POST /api/config | POST | Stage config changes for next reset |

## Controls
| Control | Description |
|---------|-------------|
| Play / Pause | Auto-advance at configured speed |
| Step | Single epoch advance |
| `N`s delay | Seconds to wait between steps; 0 = run as fast as possible |
| Cap Util / Eff Util | Toggle M_n (physical RAM) vs M_n^cap (effective cap) as denominator |
| Plan Ahead | Re-show TenantAccessSchedule from last plan-ahead run |
| Tenants | Per-tenant panel: authorized nodes (plan) vs active nodes (now) + avg delay |
| More | Config panel — all simulation parameters; changes staged, applied on Reset |
| Reset | Restart simulation, apply staged config |

## Configurable parameters (More panel)

**Topology**
| Parameter | Default | Description |
|-----------|---------|-------------|
| Num Nodes | 5 | Number of cluster nodes |
| Num Tenants | 3 | Number of tenants (max 5) |
| Node RAM Min (GB) | 16 | Smallest node memory |
| Node RAM Max (GB) | 64 | Largest node memory |
| Node CPU Min (cores) | 8 | Fewest CPU cores per node |
| Node CPU Max (cores) | 64 | Most CPU cores per node |

**Workload**
| Parameter | Default | Description |
|-----------|---------|-------------|
| Jobs / Round | 20 | New jobs generated each step |
| Job RAM Min (MB) | 512 | Smallest requested memory per job |
| Job RAM Max (MB) | 1024 | Largest requested memory per job |
| Spike Prob % | 10 | Probability actual usage exceeds predicted |
| Min Lifetime (s) | 60 | Shortest job runtime |
| Max Lifetime (s) | 600 | Longest job runtime |

**Model**
| Parameter | Default | Description |
|-----------|---------|-------------|
| K Window | 10 | Rolling window size for v̄_n^SLA and W̄_t |
| Plan-Ahead Horizon (i) | 50 | Steps between plan-ahead refreshes |
| Access Period (i/slot) | 4 | Steps per plan-ahead time slot |

## Node card metrics
| Field | Description |
|-------|-------------|
| Memory bar | Used / Cap (or Eff) RAM as % |
| Cap. CPU | Node CPU cores |
| Cap. RAM | M_n — physical RAM |
| Eff. RAM | M_n^cap = M_n − OS tax − safety buffer |
| Viols | Count (last K steps) where `used_mb > M_n^cap` (soft schedulable limit) |
| PME | Count (last K steps) where `used_mb > M_n` (physical RAM exceeded) |

## Plan-ahead output
Runs every `plan_ahead_interval` steps. Shows TenantAccessSchedule as a Gantt chart:
- Y-axis: nodes, X-axis: time slots
- Tenant-colored capsules per cell show which tenants are authorized on each node per slot
- Active slot highlighted with ▶ NOW marker
- Mirrors the output shape of `extract_tenant_access_schedule()` from `PlanAhead/`

Replace `Simulation/api/plan_ahead_mock.py` with the real Gurobi call to integrate the full Pipeline.
