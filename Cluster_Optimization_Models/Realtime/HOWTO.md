# Real-Time Optimizer — How To Run

## Overview
Stateless MILP scheduler (OR-Tools CBC/GLOP/SCIP) called once per tenant group per planning interval.

Receives pre-filtered jobs and machines from the Cluster Manager — no knowledge of tenants, plan-ahead structure, or access control. Places the given jobs onto the given nodes to maximize weighted memory placement.

## Requirements
```bash
pip install ortools numpy
```

## Run the simulation
```bash
cd Realtime/
python cluster_manager.py
```

## Run tests
```bash
cd Realtime/
pytest test_cluster_manager.py -v
pytest test_model.py -v
```

## Run sensitivity analysis
```bash
cd Pipeline/
python sensitivity_analysis.py
```
Sweeps interval frequency, machine count, tenant count, and saturation grid.  
Saves CSVs to `Pipeline/sensitivity_data/` and plots to `Pipeline/sensitivity_plots/`.

## Key configuration (`simulation_data.py`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| NUM_NODES | 5 | Cluster size |
| NUM_TENANTS | 3 | Number of tenants |
| JOBS_PER_ROUND | 20 | New jobs per scheduling interval |
| K_WINDOW | 10 | Rolling window for v̄_n^SLA and W̄_t |
| MEM_THRESHOLD_FRAC | 0.10 | Safety buffer (M_n^theta = frac × M_n) |
| MAX_PLACEMENT_RETRIES | 3 | Consecutive zero-placement rounds before giving up |
| SPIKE_PROB | 0.10 | Probability a job's act_mem exceeds pred_mem |
| SPIKE_MAX_FRAC | 0.20 | Max spike above pred_mem |
| MIN_LIFETIME_SEC | 60 | Shortest job lifetime |
| MAX_LIFETIME_SEC | 600 | Longest job lifetime |
| NODE_MEM_MIN_GB | 16 | Smallest node RAM (GB) |
| NODE_MEM_MAX_GB | 64 | Largest node RAM (GB) |

## Node health metrics

| Metric | Definition |
|--------|-----------|
| Viols | Count (last K intervals) where `used_mb > M_n^cap` (exceeded schedulable capacity — soft limit) |
| PME | Count (last K intervals) where `used_mb > M_n` (exceeded physical RAM — hard limit, OOM territory) |
| v̄_n^SLA | SLA violation rate used by optimizer to reduce effective capacity on stressed nodes |
| W̄_t | Per-tenant avg scheduling delay; boosts fairness weight for lagging tenants |

## solve() signature
```python
placements = solve(
    jobs          = group_jobs,   # pre-filtered by Cluster Manager (this group only)
    nodes         = group_nodes,  # pre-filtered by Cluster Manager (this group only)
    W_t           = self.W_t,     # per-tenant average wait times
    K             = k_window,
    time_limit_ms = 10_000,       # wall-clock limit for the solver
)
# Returns: dict[job_id -> node_id | None]
```

The solver has no knowledge of which tenant group these jobs belong to or what the plan-ahead structure looks like. The Cluster Manager handles all that filtering before calling `solve()`.

## Constraints (`optimizer_google_or.py`)
- **C1**: Each job placed on at most one node
- **C2**: Predicted memory fits within M_n^eff (effective remaining capacity)
- **C3**: Binary domain for x[j,n]
- **C4**: CPU demand fits within node CPU cores (enforced as variable upper bound, not a constraint row)

## Cluster Manager per-group loop
```python
for group in plan_output["intervals"][h]["groups"]:
    group_jobs  = [j for j in queue if j.tenant_id in group["tenant_ids"]]
    group_nodes = [n for n in nodes if n.node_id  in group["machine_ids"]]
    placements  = solve(group_jobs, group_nodes, W_t, K, time_limit_ms)
    # unplaced jobs get a wait bump; placed jobs are removed from queue
```

## Unplaced job wait bump
If the solver cannot place a job (returns `None` for that job_id), the job remains in the queue and its tenant's rolling wait-time window receives a `batch_duration_sec` bump. This causes W̄_t to rise for that tenant, increasing the fairness weight (ω_delay) on subsequent solver calls.

## Prediction interface stub
`simulate_max_mem()` and `simulate_p95_cpu()` in `simulation_data.py` simulate the prediction team's models.
Replace with real HTTP calls when available:
```python
def _predict_job(req_mem_mb, req_cpu):
    resp = requests.post(PREDICTION_API_URL, json={...})
    return resp.json()["pred_mem_mb"], resp.json()["pred_cpu_p95"]
```
