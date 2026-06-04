# Plan-Ahead MISOCP Instance Run Guide

> Note: the instance export (`pa_instance_export.py`) uses the full single-shot MISOCP
> (no-iterative / Gurobi). This is intentional — the instances are meant to demonstrate
> the scale limitations that motivate the decomposition heuristic approach.

## What you are getting

Two Gurobi model files exported from the Plan-Ahead MISOCP at the two critical scale points observed in our computational time analysis:

| File | Tenants (T) | Machines (N) | Variables | Our result |
|---|---|---|---|---|
| `pa_T256_N256.lp` | 256 | 256 | ~477k | Solved to OPT in **756 s** |
| `pa_T256_N512.lp` | 256 | 512 | ~1.06M | **OOM crash** (Gurobi ran out of memory) |

These instances use exactly the same model formulation and parameters as our timing analysis (`computational_time_analysis.py`).

---

## File locations

After running `python Pipeline/pa_instance_export.py`, all files are in:

```
Cluster_Optimization_Models/Pipeline/timing_data/instances/
  pa_T256_N256.lp
  pa_T256_N256_params.json
  pa_T256_N512.lp
  pa_T256_N512_params.json
  README.txt
```

---

## How to generate the files

```bash
cd Cluster_Optimization_Models/Pipeline/
python pa_instance_export.py
```

This builds both Gurobi models and writes `.lp` files (1–2 minutes to build the large models).

---

## How to run in Python

```python
import gurobipy as gp

# Your Gurobi credentials (WLS or local license)
env = gp.Env(params={
    "WLSACCESSID": "<your-access-id>",
    "WLSSECRET":   "<your-secret>",
    "LICENSEID":   <your-license-id>,
})

# Load and solve
model = gp.read("pa_T256_N256.lp", env=env)
model.Params.TimeLimit = 900     # 15-minute cap (match our analysis)
model.Params.MIPGap    = 0.01    # 1% gap
model.Params.Threads   = 8       # adjust to your machine

model.optimize()

print(f"Status  : {model.Status}")
print(f"ObjVal  : {model.ObjVal  if model.SolCount > 0 else 'no solution'}")
print(f"MIPGap  : {model.MIPGap  if model.SolCount > 0 else '—'}")
print(f"Runtime : {model.Runtime:.1f} s")
```

---

## How to run from the command line (Gurobi shell)

```bash
gurobi_cl TimeLimit=900 MIPGap=0.01 pa_T256_N256.lp
```

---

## Model structure summary

The MISOCP is a multi-period tenant-to-machine allocation model:

- **Planning horizon**: P = 4 periods (e.g. 6-hour slots over 24 hours)
- **Tenants**: T = 256 (mix of exclusive and shared tenants)
- **Machines**: N = 256 (or 512) — some always-on, some activated by model
- **Key variables**: `e[i,n,h]`, `y[i,n,h]`, `f[i,n,h]`, `z_on[n]`, `sigma`, `mix[n,h]`
- **Objective**: Minimize infrastructure cost, maximize fairness (σ), reward heavy+light tenant co-location (mix bonus)
- **Cantelli constraint**: Probabilistic capacity guarantee (SOCP cone) — this is what makes it a MISOCP

The variable count grows as **O(T × N × P)**:
- T=256, N=256, P=4 → ~477k variables → solvable (756 s)
- T=256, N=512, P=4 → ~1.06M variables → OOM on our machine

---

## Parameter JSON

Each `.json` file contains a summary of the instance: tenant set, machine set, planning periods, node capacity, demand bounds, and the first 10 `u[i,h]` demand values. The full model is self-contained in the `.lp` file.

---

## Expected behavior on different machines

| RAM | T=256, N=256 | T=256, N=512 |
|---|---|---|
| 16 GB | Should solve (756 s in our run) | Likely OOM |
| 32 GB | Should solve faster | May solve with TLim |
| 64 GB | Should solve faster | Should solve |

The OOM behavior confirms that **Benders decomposition** or **column generation** is needed to scale the plan-ahead model beyond ~500k variables on commodity hardware.
