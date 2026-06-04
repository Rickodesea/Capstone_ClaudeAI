# Plan-Ahead Optimizer — How To Run

## Overview
Periodic cluster planning model. Two execution modes:

- **Iterative (default):** `plan_ahead_iterative.py` — greedy first-fit decreasing (FFD) in a sliding window of 8 tenants × 64 nodes. **No Gurobi needed.** Scales to any number of tenants. Recommended for development, testing, and large-scale deployments.
- **Full MISOCP:** `plan_ahead_optimizer.py` — Gurobi MISOCP. Globally optimal with probabilistic capacity guarantees (Cantelli bound). Limited to ~T=256, N=256 on 16 GB RAM. Requires Gurobi WLS license.

Both modes output the same `TenantAccessSchedule` format consumed by the real-time scheduler.

## Requirements

**Iterative (no Gurobi needed):**
```bash
pip install numpy
```

**Full MISOCP (requires Gurobi license):**
```bash
pip install gurobipy numpy
```

## Gurobi credentials (MISOCP mode only)
Create `PlanAhead/.env`:
```
WLSACCESSID=your-access-id
WLSSECRET=your-secret
LICENSEID=your-license-id
```
Never commit `.env`.

## Run the iterative optimizer (default — no Gurobi needed)
```bash
cd PlanAhead/
python plan_ahead_iterative.py                       # default: 128 tenants
python plan_ahead_iterative.py --tenants 512         # scale up
python plan_ahead_iterative.py --tenants 64 --seed 7 --csv output.csv
```
Prints iteration log: active tenants, active nodes, demand placed, completions, elapsed time.

## Run the full MISOCP optimizer
```bash
cd PlanAhead/
python plan_ahead_optimizer.py                       # default params from plan_ahead_data.py
python plan_ahead_optimizer.py --tenants 8 --nodes 16 --periods 2
python plan_ahead_optimizer.py --tenants 32 --nodes 64 --mip-gap 0.05 --time-limit 60
```
Prints:
- Exclusive tenant machine assignments (fixed for entire horizon)
- Per-interval shared tenant group assignments
- Active nodes, fairness σ, mix bonus score, MIP gap

## Run tests
```bash
cd PlanAhead/
pytest test_plan_ahead.py -v
```

## Run sensitivity analysis
```bash
cd PlanAhead/
python sensitivity_analysis.py              # MISOCP scale sweep + iterative comparison (default)
python sensitivity_analysis.py --no-iterative   # MISOCP only

python plan_ahead_sensitivity.py            # parametric sweeps: epsilon, fairness, MIP gap
python plan_ahead_sensitivity.py --no-iterative   # suppress iterative note
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
| n_exclusive | 1 | Number of tenants randomly tagged exclusive (T_e) |
| node_capacity | 10.0 | C[n] — resource capacity per machine (uniform) |
| tenant_usage_min | 0.8 | Lower bound for u[i,h] (capacity units) |
| tenant_usage_max | 6.0 | Upper bound for u[i,h] (capacity units) |
| sigma_frac | 0.20 | Demand uncertainty fraction (SOCP mode) |
| epsilon | 0.10 | Cantelli tail probability ε (SOCP mode; ε=0.10 → 90% guarantee) |

## Iterative variant configuration (`plan_ahead_iterative.py`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| UNIT_TENANTS | 8 | Active tenant window size per iteration |
| UNIT_NODES | 64 | Active node pool size per iteration |
| TOTAL_TENANTS | 128 | Default total tenant population to place |
| N_PERIODS | 4 | Planning horizon length (number of periods) |
| NODE_CAPACITY | 10.0 | Capacity per node per period |
| EVICT_THRESH | 0.05 | Evict node when remaining < 5% of NODE_CAPACITY |

## Machine pool (MISOCP mode)
| Set | Description |
|-----|-------------|
| M_a | Always-available machines — on for every interval, no activation cost |
| M_b | Additional machines — model decides which to activate via z_on[n] ∈ {0,1} |

## Model modes (MISOCP)
| Mode | use_socp | Capacity constraint | Speed |
|------|----------|-------------------|-------|
| MILP | False | Plain linear: Σ f ≤ C·z_on | Fast |
| MISOCP | True | Cantelli cone: Σ f + κ·t ≤ C·z_on, t²≥Σσ²·y | Slower, probabilistically safe |

## Objective (MISOCP)
```
Minimize: λ₀·infra_cost  −  λ₁·σ  −  λ₂·mix_total
```
- `infra_cost` = sum of pi_n·z_on[n] for additional machines
- `σ` = minimum tenant demand-satisfaction ratio (fairness)
- `mix_total` = count of (node, interval) pairs with both heavy and light tenants
