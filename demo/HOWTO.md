# Demo — How To Run

## Overview
Interactive visualization of the multi-tenant cluster scheduler.

- **Backend**: FastAPI + OR-Tools MILP optimizer
- **Frontend**: React + Vite + Tailwind + Framer Motion + Recharts
- **Plan-Ahead**: Mock of `extract_tenant_access_schedule()` (numpy-only, same JSON shape)

## Requirements
```bash
# Backend
pip install fastapi uvicorn ortools numpy

# Frontend
node >= 18, npm
```

## Start the backend
```bash
cd demo/api/
uvicorn main:app --reload --port 8000
```

## Start the frontend
```bash
cd demo/frontend/
npm install
npm run dev
```
Open http://localhost:5173

## Run backend tests
```bash
cd demo/api/
pytest test_main.py -v
```

## Run frontend tests
```bash
cd demo/frontend/
npm test
```

## API endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| GET /api/state | GET | Current simulation state |
| POST /api/step | POST | Advance one scheduling epoch |
| POST /api/reset | POST | Reset (applies staged config) |
| POST /api/config | POST | Stage config changes |

## Controls
| Control | Description |
|---------|-------------|
| Play / Pause | Auto-advance at configured speed |
| Step | Single epoch advance |
| `N`s ×1/×4/×10 | Base interval × multiplier |
| Cap Util / Eff Util | Toggle M_n vs M_n^cap denominator for utilization % |
| Plan Ahead | Re-show TenantAccessSchedule from last plan-ahead run |
| Tenants | Per-tenant panel: authorized nodes (plan) vs active nodes (now) + avg delay |
| More | Config panel — changes staged, applied on Reset |
| Reset | Restart simulation, apply staged config |

## Node card metrics
| Field | Description |
|-------|-------------|
| Memory Utilization | Used / Cap (or Eff) RAM as % |
| Cap. CPU | Node CPU cores |
| Cap. RAM | M_n — physical RAM |
| Eff. RAM | M_n^cap = M_n − OS_tax − M_theta |
| Ovrflw | Count (last K) where used_mb > M_n^cap (soft limit) |
| Viols | Count (last K) where used_mb > M_n (hard physical limit) |

## Plan-ahead output
Runs every PLAN_AHEAD_INTERVAL steps. Shows TenantAccessSchedule:
- Planning horizon divided into NUM_SLOTS time periods
- Each tenant is assigned a subset of nodes per slot
- Active slot highlighted (▶)
- Mirrors `extract_tenant_access_schedule()` from `plan_ahead_optimizer.py`

Replace `demo/api/plan_ahead_mock.py` with the real Gurobi call in Phase 3.

## Prediction stub
`_predict_job()` in `main.py` is the interface for the prediction team's API.
Currently uses `simulate_max_mem()` and `simulate_p95_cpu()` from `simulation_data.py`.
