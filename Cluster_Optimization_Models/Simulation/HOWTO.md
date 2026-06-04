# Simulation — How To Run

## Overview
Two ways to run the simulation:

1. **`sim_runner.py` — solver-agnostic CLI** (no frontend needed). Runs the full ClusterManager scheduling loop with either the regular or iterative RT solver, prints aggregate stats, and optionally compares both solvers side-by-side. Iterative is the default.

2. **Interactive browser visualization** — FastAPI backend + React frontend. Step through scheduling intervals in real time, configure all parameters from the UI, see live node/tenant metrics.

---

## Quick Start: sim_runner.py

```bash
cd Simulation/
pip install ortools numpy

python sim_runner.py                            # iterative RT, no PA, 20 batches
python sim_runner.py --rt no-iterative          # single-shot MILP baseline
python sim_runner.py --compare                  # iterative vs regular side-by-side
python sim_runner.py --compare --pa mock --batches 30 --quiet
python sim_runner.py --rt iterative --rt-batch-jobs 16 --rt-batch-nodes 16
python sim_runner.py --compare --csv results.csv    # saves regular.csv + iterative.csv
```

### All flags

| Flag | Default | Description |
|------|---------|-------------|
| `--rt {regular,iterative}` | iterative | RT solver mode |
| `--pa {none,mock,gurobi}` | none | Plan-ahead grouping mode |
| `--batches N` | 20 | Scheduling intervals |
| `--seed N` | 42 | RNG seed — same seed = identical job arrivals |
| `--solver SOLVER` | CBC | Integer backend: CBC, SCIP, HIGHS, GUROBI |
| `--rt-batch-jobs N` | 32 | Jobs per sub-MILP (iterative RT only) |
| `--rt-batch-nodes N` | 32 | Nodes per sub-MILP (iterative RT only) |
| `--time-limit MS` | 10000 | Per-call solver wall-clock limit ms |
| `--jobs-per-round N` | — | Override JOBS_PER_ROUND from simulation_data |
| `--csv PATH` | — | Save per-batch stats to CSV |
| `--quiet` | — | Suppress ClusterManager per-batch output |
| `--compare` | — | Run both RT modes, print side-by-side table |

### PA modes

| Mode | Description |
|------|-------------|
| `none` | All tenants compete for all nodes — no grouping (fastest) |
| `mock` | Deterministic round-robin groups — no Gurobi required |
| `gurobi` | Full MISOCP plan-ahead — falls back to mock if Gurobi unavailable |

### How injection works
`sim_runner.py` patches `cluster_manager.solve` with the chosen RT solver function before each run and restores the original in a `finally` block. No existing model code is modified or copied.

---

## Interactive Browser Simulation

### Overview
Combines both model layers:
- **Real-Time MILP** (from `Realtime/`) — OR-Tools CBC solver, called once per tenant group per step
- **Plan-Ahead** — Gurobi MILP/MISOCP if available; falls back to a numpy mock that produces the same output format

The backend is a FastAPI server driving a React + Tailwind frontend. All simulation parameters are configurable from the UI without restarting the backend.

### Requirements
```bash
# Backend
pip install fastapi uvicorn ortools numpy

# Frontend
node >= 18, npm
```

Gurobi WLS license in `PlanAhead/.env` is optional — the mock plan-ahead runs without it.

### Start the backend
```bash
cd Simulation/api/
uvicorn main:app --reload --port 8000
```

### Start the frontend
```bash
cd Simulation/frontend/
npm install
npm run dev
```
Open http://localhost:5173

The Simulation combines both model layers:
- **Real-Time MILP** (from `Realtime/`) — OR-Tools CBC solver, called once per tenant group per step
- **Plan-Ahead** — Gurobi MILP/MISOCP if available; falls back to a numpy mock that produces the same output format

The backend is a FastAPI server that drives a React + Tailwind frontend. All simulation parameters are configurable from the UI without restarting the backend. Changes stage immediately and apply on Reset.

## Requirements
```bash
# Backend
pip install fastapi uvicorn ortools numpy

# Frontend
node >= 18, npm
```

Gurobi WLS license in `PlanAhead/.env` is optional — the mock plan-ahead runs without it.

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

## API endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| GET /api/state | GET | Current simulation state |
| POST /api/step | POST | Advance one scheduling epoch |
| POST /api/reset | POST | Restart simulation (applies staged config) |
| POST /api/config | POST | Stage config changes for next reset |
| POST /api/plan_ahead | POST | Run plan-ahead immediately |

## Controls
| Control | Description |
|---------|-------------|
| Play / Pause | Auto-advance at configured speed |
| Step | Single epoch advance |
| `N`s delay | Seconds between steps; 0 = run as fast as possible |
| Cap Util / Eff Util / Act Util | Toggle memory utilization denominator |
| Plan Ahead | Re-show last plan-ahead output (or trigger a fresh run) |
| Tenants | Per-tenant panel: priority nodes (plan) vs active nodes (now) + avg delay |
| More | Last-batch stats and glossary |
| Settings (⚙) | Config panel — all simulation parameters; changes staged, apply on Reset |
| Reset | Restart simulation, apply staged config |

## Configurable parameters (Settings panel)

**Topology**
| Parameter | Default | Description |
|-----------|---------|-------------|
| Num Nodes | 5 | Total machines in pool (M = M_a ∪ M_b) |
| Num Tenants | 3 | Total tenants (no max — only > 0 required) |
| Node RAM Min (GB) | 16 | Smallest node memory |
| Node RAM Max (GB) | 64 | Largest node memory |
| Node CPU Min (cores) | 8 | Fewest CPU cores per node |
| Node CPU Max (cores) | 64 | Most CPU cores per node |

**Workload**
| Parameter | Default | Description |
|-----------|---------|-------------|
| Jobs Min / Round | 5 | Min jobs generated each step |
| Jobs Max / Round | 20 | Max jobs generated each step |
| Job RAM Min (MB) | 512 | Smallest requested memory per job |
| Job RAM Max (MB) | 1024 | Largest requested memory per job |
| Job CPU Min (cores) | 0.25 | Min CPU request per job |
| Job CPU Max (cores) | 4.0 | Max CPU request per job |
| Spike Prob % | 10 | Probability actual usage exceeds predicted |
| Min Lifetime (s) | 60 | Shortest job runtime |
| Max Lifetime (s) | 600 | Longest job runtime |

**Scheduler**
| Parameter | Default | Description |
|-----------|---------|-------------|
| Batch Duration (s) | 60 | Simulated seconds per interval |
| K Window | 10 | Rolling window size for v̄_n^SLA and W̄_t |
| Safety Buffer | 0.10 | M_n^theta = frac × M_n (memory reserved as safety margin) |

**Plan-Ahead**
| Parameter | Default | Description |
|-----------|---------|-------------|
| Horizon (intervals) | 50 | Steps between plan-ahead refreshes |
| Period Width (intervals) | 4 | Steps per plan period within the horizon |
| Usage Min (cap units) | 0.8 | Lower bound for u[i,h] (demand profiles) |
| Usage Max (cap units) | 6.0 | Upper bound for u[i,h] |
| Gurobi Time Limit (s) | 30 | Wall-clock budget per plan solve |
| Gurobi MIP Gap | 0.05 | Relative optimality gap target |
| Capacity Model | MILP | MILP (fast) or SOCP (Cantelli probabilistic capacity) |

## Per-group scheduling (each step)
Each step, the simulator retrieves the current plan-ahead interval's groups and calls the real-time solver once per group:
```
for group in plan["intervals"][h % len(intervals)]["groups"]:
    solve(group_jobs, group_nodes, W_t, time_limit_ms=realtime_time_limit_ms)
```
Jobs that the solver cannot place remain in the queue and their tenant's W̄_t is bumped by `batch_duration_sec`, increasing priority in the next step.

## Node card metrics
| Field | Description |
|-------|-------------|
| Memory bar | Used / Cap (or Eff) RAM as % |
| Cap. CPU | Node CPU cores |
| Cap. RAM | M_n — physical RAM |
| Eff. RAM | M_n^cap = M_n − OS tax − safety buffer |
| Viols | Count (last K steps) where `used_mb > M_n^cap` (soft schedulable limit exceeded) |
| PME | Count (last K steps) where `used_mb > M_n` (physical RAM exceeded) |

## Plan-ahead output
Runs every `plan_ahead_interval` steps (or on demand via the Plan Ahead button). Shows as a Gantt chart:
- Y-axis: nodes, X-axis: plan periods
- Tenant-colored capsules per cell show which tenants are prioritized on each node per period
- Active period highlighted with ▶ NOW marker
- When Gurobi is available, uses full MILP/MISOCP optimization; otherwise falls back to mock (all tenants on all nodes)
