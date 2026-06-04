# Cluster Optimization Models

Multi-tenant cluster scheduling system built for cloud environments. Combines a periodic plan-ahead optimizer with a real-time job placer to maximize resource utilization while enforcing per-tenant fairness, SLA guarantees, and isolation requirements.

---

## Repository Structure

```
Cluster_Optimization_Models/
├── INFO.md                  ← you are here
│
├── Realtime/                ← Real-time MILP scheduler (OR-Tools / HiGHS)
│   ├── HOWTO.md
│   ├── realtime_optimizer.py     core solver — single-shot MILP per scheduling round
│   ├── optimizer_iterative.py    iterative wrapper — batch MILP loop (default)
│   ├── solver_backends.py        pluggable backends: CBC, SCIP, GUROBI, HIGHS
│   ├── cluster_manager.py        orchestrates queue, expiry, node state
│   ├── simulation_data.py        data generation, node/job factories
│   ├── sensitivity_analysis.py   parameter sweep (iterative RT by default)
│   └── test_*.py                 pytest suite
│
├── PlanAhead/               ← Periodic MISOCP planner (Gurobi WLS)
│   ├── HOWTO.md
│   ├── plan_ahead_optimizer.py   MISOCP model build + solve (Gurobi)
│   ├── plan_ahead_iterative.py   iterative greedy wrapper — no Gurobi needed (default)
│   ├── plan_ahead_data.py        synthetic data generation + Gurobi env
│   ├── sensitivity_analysis.py   MISOCP sensitivity sweeps + iterative comparison
│   ├── plan_ahead_sensitivity.py parametric MISOCP sweeps (epsilon, fairness, etc.)
│   ├── plan_ahead.tex            LaTeX formulation
│   └── test_plan_ahead.py        pytest suite
│
├── Pipeline/                ← End-to-end integration of both models
│   ├── HOWTO.md
│   ├── interface.py              runs all three layers in sequence
│   ├── sensitivity_analysis.py   pipeline sweep (iterative RT by default)
│   ├── large_scale_sensitivity.py large-scale grid sweep (iterative RT by default)
│   └── pipeline_configs.py       Simple / Medium / High sample configs
│
├── Simulation/              ← Interactive visualization + agnostic CLI runner
│   ├── HOWTO.md
│   ├── sim_runner.py             solver-agnostic CLI — compare RT regular vs iterative
│   ├── api/                      FastAPI backend (real-time solver + plan-ahead)
│   └── frontend/                 React + Recharts UI
│
├── Prediction/              ← Prediction layer integration
│   ├── prediction_api.py         FastAPI + direct Python wrapper for prediction outputs
│   ├── borg_configuration.py     Borg dataset constants (9 tenants, normalized capacity)
│   └── Docs/                     Prediction layer documentation
│
└── Docs/                    ← Plain-language explanations of the models
    ├── plan_ahead_math_explained.md   math formulation in plain English
    ├── plan_ahead_code_explained.md   code walkthrough for PlanAhead/
    ├── real_time_code_explained.md    code walkthrough for Realtime/
    └── pa_iterative_explanation.md    explanation of the iterative PA variant
```

---

## The Two Models

### Real-Time Scheduler (`Realtime/`)
Runs every scheduling epoch (~60 seconds). Solves a **MILP** (Mixed-Integer Linear Program) to assign pending jobs to cluster nodes. Two execution modes:

- **Iterative (default):** `optimizer_iterative.py` loops through batches of BATCH_JOBS × BATCH_NODES, calling `realtime_optimizer.py` per batch. Stays tractable at any scale — unplaced jobs remain pending for the next round.
- **Single-shot:** `realtime_optimizer.py` solves the full J×N MILP in one call. Optimal within the time limit but grows exponentially with J and N.

Key ideas:
- Each node has a physical RAM ceiling (M_n) and a softer schedulable ceiling (M_n^cap = M_n − OS tax − safety buffer)
- The optimizer tracks per-tenant average wait time (W̄_t) and per-node violation rate (v̄_n^SLA), using both to weight placement decisions fairly
- Backend is selectable: CBC (default), SCIP, HiGHS, or Gurobi — same MILP model, different solver

### Plan-Ahead Optimizer (`PlanAhead/`)
Runs periodically (e.g., once per week). Produces a tenant-to-node assignment schedule for an upcoming planning horizon. Two execution modes:

- **Iterative (default):** `plan_ahead_iterative.py` uses greedy first-fit decreasing (FFD) in a sliding window of 8 tenants × 64 nodes. No Gurobi needed. Scales to any number of tenants by processing them in batches.
- **Full MISOCP:** `plan_ahead_optimizer.py` solves a single Gurobi MISOCP (Mixed-Integer Second-Order Cone Program). Globally optimal but limited to ~T=256, N=256 on 16 GB RAM before OOM.

Key ideas (both modes):
- Probabilistic capacity constraint (Cantelli bound, MISOCP mode only) ensures no node is overcommitted even accounting for demand variance
- DRF fairness objective prevents tenant starvation
- Output is `TenantAccessSchedule = dict[(tenant_id, slot) → list[node_id]]`, consumed by the real-time scheduler

---

## How the Models Connect

```
PlanAhead (iterative or MISOCP)  →  TenantAccessSchedule
                                              ↓
                              Real-time solver (iterative or single-shot)
                                              ↓
                                     Job → Node placement
```

The `Pipeline/interface.py` script runs this full chain end-to-end.
The `Simulation/sim_runner.py` script runs agnostic comparisons between solver modes.

---

## Quick Start

**Run the real-time model (iterative, default — no Gurobi needed):**
```bash
cd Realtime/
pip install ortools numpy highspy
python optimizer_iterative.py          # interactive test
python cluster_manager.py              # full simulation
```

**Run the plan-ahead model (iterative, default — no Gurobi needed):**
```bash
cd PlanAhead/
pip install numpy
python plan_ahead_iterative.py         # iterative greedy allocation
```

**Load Google Borg dataset configuration in the dashboard:**
In the dashboard, click `More > Load` to stage Borg dataset parameters (9 tenants,
normalized capacity, Gurobi solver). Then click Reset to apply.

**Run the plan-ahead model (full MISOCP — requires Gurobi WLS license):**
```bash
cd PlanAhead/
pip install gurobipy numpy
# create .env with Gurobi credentials
python plan_ahead_optimizer.py
```

**Compare RT solvers side-by-side:**
```bash
cd Simulation/
python sim_runner.py --compare                    # iterative vs regular, 20 batches
python sim_runner.py --compare --batches 50 --pa mock
```

**Run the full pipeline:**
```bash
cd Pipeline/
pip install gurobipy ortools numpy
python interface.py          # Sample 1 — Simple
```

**Run sensitivity analysis (iterative by default):**
```bash
cd Realtime/
python sensitivity_analysis.py                    # iterative RT (default)
python sensitivity_analysis.py --no-iterative     # single-shot baseline
```

**Run the interactive simulation:**
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
| `Docs/real_time_code_explained.md` | Code walkthrough for `realtime_optimizer.py` and `cluster_manager.py` |
| `Docs/pa_iterative_explanation.md` | Explanation of the iterative plan-ahead variant and its sliding-window algorithm |
| `Realtime/HOWTO.md` | How to run tests, sensitivity analysis, solver modes, and configuration reference |
| `PlanAhead/HOWTO.md` | How to run iterative and MISOCP variants, Gurobi credentials, configuration reference |
| `Pipeline/HOWTO.md` | How to run the end-to-end pipeline with different sample configs |
| `Simulation/HOWTO.md` | How to run the interactive visualization, sim_runner.py CLI, and UI control reference |
