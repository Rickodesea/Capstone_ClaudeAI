# Pipeline — How To Run

## Overview
End-to-end integration of all three model layers:

1. **Synthesis / Prediction** — `build_synthetic_data()` generates resource demand, mean usage, and covariance matching Google cluster-usage traces v3.
2. **Plan-Ahead MISOCP** (Gurobi) — solves the periodic scheduling problem over the planning horizon. Decides which tenants are admitted and which nodes each tenant is authorized to use per time slot. Output: `TenantAccessSchedule`.
3. **Real-Time MILP** (OR-Tools) — one call per scheduling round. Receives `TenantAccessSchedule` from the plan-ahead and enforces constraint C5 (tenant access control).

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

## Sample configurations

| Sample | Tenants | Nodes | Slots | Jobs/Slot | Solver | Notes |
|--------|---------|-------|-------|-----------|--------|-------|
| 1 — Simple | 3 | 4 | 2 | 8 | CBC | Gurobi solves to optimality in < 1 s |
| 2 — Medium | 3 | 5 | 3 | 12 | CBC | ~120 s Gurobi budget |
| 3 — High | 3 | 5 | 4 | 20 | GLOP | LP relaxation keeps real-time step fast |

See `pipeline_configs.py` for all tunable parameters.

## Output (per run)
```
LAYER 1  Synthesis / prediction
  Tenants: 3  Nodes: 4  Time slots: 2  Workloads: 6 total

LAYER 2  Plan-ahead MISOCP  (Gurobi)
  Status:       OPTIMAL
  Objective:    0.4823
  Fairness sigma: 0.9210
  Admitted (3): [0, 1, 2]

LAYER 3  TenantAccessSchedule
  tenant=0  t=0  nodes=[2, 3]
  tenant=1  t=0  nodes=[0, 1]
  ...

LAYER 4+5  Real-time scheduling  (slot t=0  solver=CBC)
  Placed: 8/8
  node 0:   3 jobs  tenants=[1]  total_pred_mem=1,024 MB
  ...
```

## Key data structures

### TenantLease
Represents a contiguous block of time slots where a tenant has the same authorized node set:
```python
@dataclass
class TenantLease:
    tenant_id:        int
    authorised_nodes: list[int]
    start_slot:       int
    end_slot:         int    # exclusive — [start_slot, end_slot)
```

### filter_active_access
Slices the full `TenantAccessSchedule` to a single time slot for the real-time solver:
```python
access = filter_active_access(schedule, time_t=1)
# Returns dict[tenant_id -> list[node_id]]
placements = solve(jobs=jobs, nodes=nodes, W_t={}, tenant_node_access=access)
```

## Path setup
`interface.py` inserts `Realtime/` and `PlanAhead/` into `sys.path` at runtime, so it imports directly from those folders. No install required.
