# Cluster Optimization Models

Multi-tenant cluster scheduling system built for cloud environments. Combines a periodic plan-ahead optimizer with a real-time job placer to maximize resource utilization while enforcing per-tenant fairness, SLA guarantees, and isolation requirements.

---

## Repository Structure

```
Cluster_Optimization_Models/
├── INFO.md                  ← you are here
│
├── Realtime/                ← Real-time MILP scheduler (OR-Tools)
│   ├── HOWTO.md
│   ├── optimizer_google_or.py    core solver — one call per scheduling round
│   ├── cluster_manager.py        orchestrates queue, expiry, node state
│   ├── simulation_data.py        data generation, node/job factories
│   ├── sensitivity_analysis.py   parameter sweep over K, spike_prob, etc.
│   └── test_*.py                 pytest suite
│
├── PlanAhead/               ← Periodic MISOCP planner (Gurobi WLS)
│   ├── HOWTO.md
│   ├── plan_ahead_optimizer.py   MISOCP model build + solve
│   ├── plan_ahead_data.py        synthetic data generation + Gurobi env
│   ├── plan_ahead_sensitivity.py sensitivity sweeps
│   ├── plan_ahead.tex            LaTeX formulation
│   └── test_plan_ahead.py        pytest suite
│
├── Pipeline/                ← End-to-end integration of both models
│   ├── HOWTO.md
│   ├── interface.py              runs all three layers in sequence
│   └── pipeline_configs.py       Simple / Medium / High sample configs
│
├── Simulation/              ← Interactive browser-based visualization
│   ├── HOWTO.md
│   ├── api/                      FastAPI backend (real-time solver + mock plan-ahead)
│   └── frontend/                 React + Recharts UI
│
└── Docs/                    ← Plain-language explanations of the models
    ├── plan_ahead_math_explained.md   math formulation in plain English
    ├── plan_ahead_code_explained.md   code walkthrough for PlanAhead/
    └── real_time_code_explained.md    code walkthrough for Realtime/
```

---

## The Two Models

### Real-Time Scheduler (`Realtime/`)
Runs every scheduling epoch (~60 seconds). Solves a **MILP** (Mixed-Integer Linear Program) using OR-Tools CBC to assign pending jobs to cluster nodes.

Key ideas:
- Each node has a physical RAM ceiling (M_n) and a softer schedulable ceiling (M_n^cap = M_n − OS tax − safety buffer)
- The optimizer tracks per-tenant average wait time (W̄_t) and per-node violation rate (v̄_n^SLA), using both to weight placement decisions fairly
- Constraint C5 enforces plan-ahead access control: a tenant can only place jobs on nodes it was authorized for in the current plan-ahead slot

### Plan-Ahead Optimizer (`PlanAhead/`)
Runs periodically (e.g., once per week). Solves a **MISOCP** (Mixed-Integer Second-Order Cone Program) using Gurobi to partition cluster nodes among tenants for an upcoming planning horizon.

Key ideas:
- Probabilistic capacity constraint (C2, Cantelli bound) ensures no node is overcommitted even accounting for demand variance
- Each workload is assigned an isolation primitive (none / gVisor / Kata) balancing overhead cost against co-location compatibility
- DRF fairness objective (C7) prevents tenant starvation
- Output is `TenantAccessSchedule = dict[(tenant_id, slot) → list[node_id]]`, consumed by the real-time C5 constraint

---

## How the Models Connect

```
PlanAhead optimizer  →  TenantAccessSchedule
                                 ↓
                      Real-time solver (C5 constraint)
                                 ↓
                         Job → Node placement
```

The `Pipeline/interface.py` script runs this full chain end-to-end with three sample configurations.

---

## Quick Start

**Run the real-time model standalone** (no Gurobi needed):
```bash
cd Realtime/
pip install ortools numpy
python cluster_manager.py
```

**Run the plan-ahead model** (requires Gurobi WLS license):
```bash
cd PlanAhead/
pip install gurobipy numpy
# create .env with Gurobi credentials
python plan_ahead_optimizer.py
```

**Run the full pipeline**:
```bash
cd Pipeline/
pip install gurobipy ortools numpy
python interface.py          # Sample 1 — Simple
```

**Run the interactive simulation**:
```bash
# See Simulation/HOWTO.md for full instructions
cd Simulation/api/ && uvicorn main:app --reload --port 8000
cd Simulation/frontend/ && npm install && npm run dev
```

---

## Documentation

| File | Contents |
|------|----------|
| `Docs/plan_ahead_math_explained.md` | Full math formulation in plain English — sets, variables, constraints, objective |
| `Docs/plan_ahead_code_explained.md` | Code walkthrough for `plan_ahead_data.py` and `plan_ahead_optimizer.py` |
| `Docs/real_time_code_explained.md` | Code walkthrough for `optimizer_google_or.py` and `cluster_manager.py` |
| `Realtime/HOWTO.md` | How to run tests, sensitivity analysis, and configuration reference |
| `PlanAhead/HOWTO.md` | How to run tests, Gurobi credentials, and configuration reference |
| `Pipeline/HOWTO.md` | How to run the end-to-end pipeline with different sample configs |
| `Simulation/HOWTO.md` | How to run the interactive visualization and UI control reference |
