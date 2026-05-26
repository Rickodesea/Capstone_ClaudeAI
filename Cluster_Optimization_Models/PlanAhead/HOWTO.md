# Plan-Ahead Optimizer — How To Run

## Overview
Periodic cluster planning model (MILP or MISOCP) using Gurobi WLS.

Divides the tenant pool into **exclusive tenants** (dedicated machines for the full horizon) and **shared tenants** (per-interval machine assignments).  
Activates additional machines from a secondary pool to meet demand.  
Optimizes infrastructure cost, fairness (σ), and workload diversity (mix bonus).

**Output:** Ordered list of planning intervals, each with one or more tenant groups — every tenant is assigned machines every interval.

## Requirements
```bash
pip install gurobipy numpy
```

## Gurobi credentials
Create `PlanAhead/.env`:
```
WLSACCESSID=your-access-id
WLSSECRET=your-secret
LICENSEID=your-license-id
```
Never commit `.env`.

## Run the optimizer
```bash
cd PlanAhead/
python plan_ahead_optimizer.py
```
Prints:
- Exclusive tenant machine assignments (fixed for entire horizon)
- Per-interval shared tenant group assignments
- Active nodes (always-on + additional activated)
- Fairness σ and mix bonus score
- MIP gap and objective value

## Run tests
```bash
cd PlanAhead/
pytest test_plan_ahead.py -v
```

## Key output: extract_plan_output()
```python
plan = extract_plan_output(vars_, P)
# plan["intervals"] = list of interval dicts
# Each interval dict:
# {
#   "interval": h,
#   "groups": [
#     {"tenant_ids": [0, 2], "machine_ids": [0, 1, 3], "exclusive": False},
#     {"tenant_ids": [1],    "machine_ids": [2, 4],    "exclusive": True},
#   ]
# }
```
The Cluster Manager receives this dict and schedules one real-time solver call per group per interval.

## Configuration (`plan_ahead_data.py — build_synthetic_data()`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| n_tenants | 4 | Total tenants (T = T_e ∪ T_s) |
| n_nodes | 5 | Total machines (M = M_a ∪ M_b) |
| n_intervals | 2 | Planning horizon length (number of intervals H) |
| n_always_available | 3 | \|M_a\| — always-on machines; rest are additional (M_b) |
| n_exclusive | 1 | Number of tenants randomly tagged exclusive (T_e); clamped to [0, n_tenants] |
| node_capacity | 10.0 | C[n] — resource capacity per machine (uniform) |
| tenant_usage_min | 0.8 | Lower bound for u[i,h] (capacity units) |
| tenant_usage_max | 6.0 | Upper bound for u[i,h] (capacity units) |
| sigma_frac | 0.20 | Demand uncertainty fraction (SOCP mode) |
| epsilon | 0.10 | Cantelli tail probability ε (SOCP mode; ε=0.10 → 90% guarantee) |

## Machine pool
| Set | Description |
|-----|-------------|
| M_a | Always-available machines — on for every interval, no activation cost |
| M_b | Additional machines — model decides which to activate via z_on[n] ∈ {0,1} |

## Tenant classification
| Set | Description |
|-----|-------------|
| T_e | Exclusive tenants — assigned dedicated machines for the full horizon (e[i,n] binary) |
| T_s | Shared tenants — per-interval machine assignments (y[i,n,h] binary) |

## Model modes
| Mode | use_socp | Capacity constraint | Speed |
|------|----------|-------------------|-------|
| MILP | False | Plain linear: Σ f ≤ C·z_on | Fast |
| MISOCP | True | Cantelli cone: Σ f + κ·t ≤ C·z_on, t²≥Σσ²·y | Slower, probabilistically safe |

## Constraints (summary)
- **C_aa**: Always-available machines always active
- **C_act**: Additional machine activation (z_on[n])
- **C_excl1/2**: Exclusive tenant dedicated machine assignment (one machine, full horizon)
- **C_excl_cap**: Exclusive machines have capacity for their tenant
- **C_sep**: Exclusive and shared tenants cannot share the same machine
- **C_share**: Shared tenants assigned at most one machine per interval
- **C1a/C1b**: Capacity constraint (linear or Cantelli cone)
- **C2**: Demand satisfaction — every tenant gets their u[i,h] covered every interval
- **C3**: Node activation — a machine is on if any tenant uses it
- **C4**: Fairness — σ ≤ min allocation ratio across tenants
- **Mix**: Linearized AND constraint rewarding heavy+light tenant co-location

## Objective
```
Minimize: λ₀·infra_cost  −  λ₁·σ  −  λ₂·mix_total
```
- `infra_cost` = sum of pi_n·z_on[n] for additional machines
- `σ` = minimum tenant demand-satisfaction ratio (fairness)
- `mix_total` = count of (node, interval) pairs with both heavy and light tenants
