# Real-Time Optimizer — How To Run

## Overview
Multi-tenant cluster scheduler using MILP (OR-Tools CBC solver).
Places incoming jobs onto cluster nodes while respecting memory capacity, CPU constraints, and per-tenant fairness.

## Requirements
```bash
pip install ortools numpy
```

## Run the simulation
```bash
cd optimization/
python cluster_manager.py
```

## Run tests
```bash
cd optimization/
pytest test_cluster_manager.py -v
pytest test_model.py -v
```

## Run sensitivity analysis
```bash
cd optimization/
python sensitivity_analysis.py
```
Sweeps key parameters and reports placement rate, overflow rate, and queue size.

## Key configuration (`simulation_data.py`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| NUM_NODES | 5 | Cluster size |
| NUM_TENANTS | 3 | Number of tenants |
| JOBS_PER_ROUND | 20 | New jobs per scheduling epoch |
| K_WINDOW | 10 | Rolling window for v̄_n^SLA and W̄_t |
| MEM_THRESHOLD_FRAC | 0.10 | Safety buffer (M_n^theta = frac × M_n) |
| SPIKE_PROB | 0.10 | Probability a job's act_mem exceeds pred_mem |
| SPIKE_MAX_FRAC | 0.20 | Max spike above pred_mem |
| MIN_LIFETIME_SEC | 60 | Shortest job lifetime |
| MAX_LIFETIME_SEC | 600 | Longest job lifetime |
| NODE_MEM_MIN_MB | 16384 | Smallest node RAM (16 GB) |
| NODE_MEM_MAX_MB | 65536 | Largest node RAM (64 GB) |

## Node health metrics

| Metric | Definition |
|--------|-----------|
| Ovrflw | Count (last K intervals) where `used_mb > M_n^cap` (exceeded schedulable capacity — soft limit) |
| Viols | Count (last K intervals) where `used_mb > M_n` (exceeded physical RAM — hard limit, OOM territory) |
| v̄_n^SLA | Overflow rate used by optimizer to reduce effective capacity on stressed nodes |
| W̄_t | Per-tenant avg scheduling delay; boosts fairness weight for lagging tenants |

## Prediction interface stub
`simulate_max_mem()` and `simulate_p95_cpu()` in `simulation_data.py` simulate the prediction team's models.
Replace with real HTTP calls when available:
```python
def _predict_job(req_mem_mb, req_cpu):
    resp = requests.post(PREDICTION_API_URL, json={...})
    return resp.json()["pred_mem_mb"], resp.json()["pred_cpu_p95"]
```

## Constraints (optimizer_google_or.py)
- **C1**: Each job placed on exactly one node
- **C2**: Predicted memory fits within M_n^eff (effective remaining capacity)
- **C3**: Binary domain for x_{jn}
- **C4**: CPU demand fits within node CPU cores
- **C5**: Tenant node access control (from plan-ahead schedule)
