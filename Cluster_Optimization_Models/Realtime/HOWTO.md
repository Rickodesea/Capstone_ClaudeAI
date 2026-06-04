# Real-Time Optimizer — How To Run

## Overview
Stateless MILP scheduler called once per tenant group per planning interval. Two execution modes:

- **Iterative (default):** `optimizer_iterative.py` wraps `realtime_optimizer.py` — loops through batches of up to BATCH_JOBS × BATCH_NODES, solving a smaller MILP per batch. Stays tractable at any scale; unplaced jobs carry forward to the next round.
- **Single-shot:** `realtime_optimizer.py` directly — solves the full J×N MILP in one call. Globally optimal within the time limit but exponential in J×N.

Both modes share the same return contract: `dict[job_id -> node_id | None]`. The Cluster Manager calls whichever is injected — no knowledge of which mode is active.

## Requirements
```bash
pip install ortools numpy
pip install highspy    # optional: adds HIGHS backend
```

## Run the simulation (iterative default)
```bash
cd Realtime/
python cluster_manager.py
```

## Run the iterative solver interactively
```bash
cd Realtime/
python optimizer_iterative.py                          # default: J=16, N=64
python optimizer_iterative.py --jobs 64 --nodes 256   # custom scale
python optimizer_iterative.py --compare                # compare vs single-shot
python optimizer_iterative.py --batch-jobs 16 --batch-nodes 16 --solver SCIP
```

## Run tests
```bash
cd Realtime/
pytest test_cluster_manager.py -v
pytest test_model.py -v
```

## Run sensitivity analysis
```bash
cd Realtime/
python sensitivity_analysis.py                           # iterative RT (default)
python sensitivity_analysis.py --no-iterative            # single-shot MILP baseline
python sensitivity_analysis.py --batches 20 --seed 99
python sensitivity_analysis.py --rt-batch-jobs 16 --rt-batch-nodes 16
```
Sweeps K (violation window) × jobs_per_round (arrival load).
Saves CSV to `sensitivity_results.csv` and plots to `sensitivity_plots/`.

## Key configuration (`simulation_data.py`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| NUM_NODES | 5 | Cluster size |
| NUM_TENANTS | 3 | Number of tenants |
| JOBS_PER_ROUND | 20 | New jobs per scheduling interval |
| K_WINDOW | 10 | Rolling window for v̄_n^SLA and W̄_t |
| MEM_THRESHOLD_FRAC | 0.10 | Safety buffer (M_n^theta = frac × M_n) |
| SPIKE_PROB | 0.10 | Probability a job's act_mem exceeds pred_mem |
| SPIKE_MAX_FRAC | 0.20 | Max spike above pred_mem |
| MIN_LIFETIME_SEC | 60 | Shortest job lifetime |
| MAX_LIFETIME_SEC | 600 | Longest job lifetime |
| NODE_MEM_MIN_GB | 16 | Smallest node RAM (GB) |
| NODE_MEM_MAX_GB | 64 | Largest node RAM (GB) |

## Iterative solver configuration (`optimizer_iterative.py`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| BATCH_JOBS | 32 | Max jobs per sub-MILP call |
| BATCH_NODES | 32 | Max nodes per sub-MILP call |
| FULL_THRESH | 0.05 | Evict node when remaining < 5% capacity |
| SOLVER_ID | "CBC" | Backend: CBC, SCIP, HIGHS, GUROBI |

## Available backends (`solver_backends.py`)

| SOLVER_ID | Requires | Notes |
|-----------|----------|-------|
| GUROBI | gurobipy + license | **Default** — fastest for large problems; set as default because it consistently outperforms open-source alternatives at scale |
| CBC | ortools | Exact MILP, always available without a license |
| SCIP | ortools | Exact MILP, sometimes faster than CBC on structured problems |
| HIGHS | highspy | Exact MILP, open-source — `pip install highspy` |

## Node health metrics

| Metric | Definition |
|--------|-----------|
| Viols | Count (last K intervals) where `used_mb > M_n^cap` (exceeded schedulable capacity — soft limit) |
| PME | Count (last K intervals) where `used_mb > M_n` (exceeded physical RAM — hard limit) |
| v̄_n^SLA | SLA violation rate — reduces effective capacity on stressed nodes |
| W̄_t | Per-tenant avg scheduling delay — boosts fairness weight for lagging tenants |

## solve() signatures
```python
# realtime_optimizer.solve() — single-shot MILP
placements = solve(
    jobs          = group_jobs,
    nodes         = group_nodes,
    W_t           = self.W_t,
    K             = k_window,
    time_limit_ms = 10_000,
    solver_id     = None,     # per-call override; None = use module SOLVER_ID
)

# optimizer_iterative.solve() — batch MILP loop (calls realtime_optimizer per batch)
placements = solve(
    jobs          = group_jobs,
    nodes         = group_nodes,
    W_t           = self.W_t,
    K             = k_window,
    time_limit_ms = 10_000,   # total budget split across batches
    batch_jobs    = 32,
    batch_nodes   = 32,
    solver_id     = "CBC",
)
# Both return: dict[job_id -> node_id | None]
```

## Constraints (`realtime_optimizer.py`)
- **C1**: Each job placed on at most one node
- **C2**: Predicted memory fits within M_n^eff (effective remaining capacity)
- **C3**: Binary domain for x[j,n]
- **C4**: CPU demand fits within node CPU cores (enforced as variable upper bound)

## Cluster Manager per-group loop
```python
for group in plan_output["intervals"][h]["groups"]:
    group_jobs  = [j for j in queue if j.tenant_id in group["tenant_ids"]]
    group_nodes = [n for n in nodes if n.node_id  in group["machine_ids"]]
    placements  = solve(group_jobs, group_nodes, W_t, K, time_limit_ms)
    # unplaced jobs get a wait bump; placed jobs are removed from queue
```

## Unplaced job wait bump
When the solver returns `None` for a job, the job stays in the queue and its tenant's rolling wait-time window receives a `batch_duration_sec` bump. This raises W̄_t for that tenant, increasing the fairness weight (ω_delay) on subsequent solve calls.

## Prediction interface stub
`simulate_max_mem()` and `simulate_p95_cpu()` in `simulation_data.py` simulate the prediction team's models.
Replace with real HTTP calls when available:
```python
def _predict_job(req_mem_mb, req_cpu):
    resp = requests.post(PREDICTION_API_URL, json={...})
    return resp.json()["pred_mem_mb"], resp.json()["pred_cpu_p95"]
```
