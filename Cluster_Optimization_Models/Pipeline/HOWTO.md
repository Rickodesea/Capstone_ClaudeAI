# Pipeline — How To Run

## Overview
End-to-end integration of all three model layers:

1. **Synthesis** — `build_synthetic_data()` generates tenant demand profiles u[i,h], classifies tenants as exclusive (T_e) or shared (T_s), and partitions machines into always-available (M_a) and additional (M_b).

2. **Plan-Ahead MILP/MISOCP** (Gurobi) — solves over the planning horizon H. Assigns exclusive tenants to dedicated machines for the full horizon; assigns shared tenants to machines per interval. Output: ordered list of intervals, each with tenant groups and their assigned machines.

3. **Real-Time MILP** (OR-Tools) — called once per tenant group per interval by the Cluster Manager. Receives pre-filtered jobs and machines; no knowledge of plan-ahead structure or tenant classifications.

## Requirements
```bash
pip install gurobipy ortools numpy
```
You also need a valid Gurobi WLS license in `PlanAhead/.env`.

## Run the pipeline
```bash
cd Pipeline/
python interface.py          # Sample 1 — Simple (default)
python interface.py 2        # Sample 2 — Medium
python interface.py 3        # Sample 3 — High
```

## Run sensitivity analysis
```bash
cd Pipeline/
python sensitivity_analysis.py
```
Sweeps interval frequency, machine count, tenant count, and a saturation grid.  
Saves CSVs to `Pipeline/sensitivity_data/` and plots to `Pipeline/sensitivity_plots/`.

## Sample configurations

| Sample | Tenants | Nodes | Intervals | Always-On | Excl% | Jobs/Group | Solver |
|--------|---------|-------|-----------|-----------|-------|-----------|--------|
| 1 — Simple | 4 | 5 | 2 | 3 | 25% | 8 | CBC / MILP |
| 2 — Medium | 5 | 7 | 3 | 4 | 20% | 12 | CBC / MISOCP |
| 3 — High | 8 | 10 | 4 | 6 | 25% | 20 | GLOP / MISOCP |

See `pipeline_configs.py` for all tunable parameters.

## Output (per run)
```
LAYER 1  Synthesis  [Simple]
  Tenants:           4  total
    Exclusive T_e:   [1]  (fixed machine assignment, entire horizon)
    Shared    T_s:   [0, 2, 3]  (per-interval assignment)
  Machines:          5  total
    Always-available M_a: [0, 1, 2]
    Additional       M_b: [3, 4]  (model decides which to activate)
  Intervals (horizon): [0, 1]

LAYER 2  Plan-Ahead MILP  (Gurobi)
  Status:          OPTIMAL
  Objective:       0.3842
  Fairness sigma:  0.8210
  MIP gap:         0.00%

LAYER 3  Plan-Ahead Output  (interval groups)
  Interval 0:
    [EXCL] tenants=[1]        machines=[2, 4]
    [SHRD] tenants=[0, 2, 3]  machines=[0, 1, 3]
  Interval 1:
    [EXCL] tenants=[1]        machines=[2, 4]
    [SHRD] tenants=[0, 2, 3]  machines=[0, 1]

LAYER 4+5  Real-Time Scheduling  (interval h=0)
  [EXCL] group 0  tenants=[1]  machines=[2, 4]
          jobs=2  placed=2  unplaced=0
          machine   2:   1 jobs  total_pred_mem=512 MB
  [SHRD] group 1  tenants=[0, 2, 3]  machines=[0, 1, 3]
          jobs=6  placed=5  unplaced=1
          ...
```

## Key data structures

### plan_output (from extract_plan_output)
```python
plan_output = {
    "intervals": [
        {
            "interval": 0,
            "groups": [
                {"tenant_ids": [1],       "machine_ids": [2, 4],    "exclusive": True},
                {"tenant_ids": [0, 2, 3], "machine_ids": [0, 1, 3], "exclusive": False},
            ]
        },
        {
            "interval": 1,
            "groups": [
                {"tenant_ids": [1],       "machine_ids": [2, 4],    "exclusive": True},
                {"tenant_ids": [0, 2, 3], "machine_ids": [0, 1],    "exclusive": False},
            ]
        },
    ]
}
```

### Per-group real-time loop
```python
for group in interval_dict["groups"]:
    jobs  = _make_realtime_jobs(h, group["tenant_ids"], n_jobs, rng)
    nodes = _make_realtime_nodes(group["machine_ids"])
    placements = rt_module.solve(jobs=jobs, nodes=nodes, W_t={}, time_limit_ms=10_000)
```

## Path setup
`interface.py` inserts `Realtime/` and `PlanAhead/` into `sys.path` at runtime, so it imports directly from those folders. No install required.
