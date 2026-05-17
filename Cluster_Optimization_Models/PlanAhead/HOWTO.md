# Plan-Ahead Optimizer — How To Run

## Overview
Partitions cluster nodes among tenants for an upcoming planning period (e.g., one week).
Uses MISOCP (Gurobi WLS). Output feeds the real-time optimizer's tenant access control.

Key output: `TenantAccessSchedule = dict[(tenant_id, time_slot) -> list[node_id]]`

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
- Admitted tenants
- Workload placement (tenant, workload, node, time slot, isolation primitive)
- Active nodes
- DRF fairness sigma
- Total migrations

## Run tests
```bash
cd PlanAhead/
pytest test_plan_ahead.py -v
```

## Run sensitivity analysis
```bash
cd PlanAhead/
python plan_ahead_sensitivity.py
```

## Key output: TenantAccessSchedule
```python
schedule = extract_tenant_access_schedule(vars_, P)
# schedule[(tenant_id, slot)] = [node_id, ...]
# Slice to current slot and pass as tenant_node_access to real-time optimizer
```

## Configuration (`plan_ahead_data.py — build_synthetic_data()`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| n_tenants | 3 | Number of tenants (T) |
| n_workloads_per_tenant | 2 | Workloads per tenant (W_i) |
| n_nodes | 4 | Cluster nodes (N) |
| n_time_slots | 2 | Planning horizon length (H) |
| node_capacity | 10.0 | Node resource capacity |
| sla_eps | 0.05 | Cantelli SLA risk tolerance (ε) |

## Isolation primitives (K)
| ID | Name | Overhead |
|----|------|---------|
| 0 | none | —  |
| 1 | gVisor | +20% |
| 2 | Kata | +5% |

The optimizer selects the isolation primitive per workload based on SLA requirements and co-location compatibility (C3).

## DRF Fairness (C7)
Maximises minimum tenant DRF share σ = min_i (dominant_resource_allocated_i / quota_i).
Prevents any tenant from being starved.

## Constraints
- **C1**: Placement integrity (each workload on exactly one node)
- **C2**: Probabilistic capacity (SOCP/Cantelli) — prevents overcommitment
- **C3**: Isolation primitive selection (co-location compatibility)
- **C4**: Control-plane budgets (etcd, QPS, service mesh)
- **C5**: SLA latency satisfaction
- **C6**: Migration linking and disruption budgets
- **C7**: DRF fairness
