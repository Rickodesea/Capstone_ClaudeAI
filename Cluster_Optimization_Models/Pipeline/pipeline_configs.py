"""
pipeline/pipeline_configs.py
──────────────────────────────
Complexity configurations for the end-to-end pipeline demo.

Select a sample when running interface.py:
    python interface.py          # default: Sample 1 (simple)
    python interface.py 2        # Sample 2 (medium)
    python interface.py 3        # Sample 3 (high)

Configuration fields
─────────────────────
name                : display label
n_tenants           : |T| — tenants in the plan-ahead model
n_nodes             : |N| — cluster nodes
n_time_slots        : |H| — planning periods (horizon ÷ access_period)
n_jobs_per_slot     : real-time jobs submitted per plan-ahead period
plan_time_limit     : Gurobi wall-clock limit in seconds
plan_mip_gap        : Gurobi relative optimality gap threshold
realtime_solver     : OR-Tools solver ID — "CBC" (exact MILP) or
                      "GLOP" (LP relaxation + rounding, faster for
                      large instances)
seed                : random seed for reproducibility
node_capacity       : C[n] — resource capacity per node (uniform)
tenant_usage_min    : lower bound for u[i,h] (capacity units) — placeholder
tenant_usage_max    : upper bound for u[i,h] (capacity units) — placeholder

Complexity notes
─────────────────
Sample 1 — Simple
  3T × 4N × 2H.  ~24 binary vars in the MILP.
  Gurobi solves to optimality in < 1 second.
  OR-Tools CBC handles 8 jobs/slot instantly.

Sample 2 — Medium
  3T × 5N × 3H.  ~45 binary vars.
  Gurobi solves to optimality in < 10 seconds.
  CBC still handles 12 real-time jobs/slot in a few seconds.

Sample 3 — High
  5T × 8N × 4H.  ~160 binary vars.
  Gurobi solves quickly; real-time uses GLOP for speed with 20 jobs/slot.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PipelineConfig:
    name:             str
    n_tenants:        int
    n_nodes:          int
    n_time_slots:     int
    n_jobs_per_slot:  int
    plan_time_limit:  int    # seconds
    plan_mip_gap:     float
    realtime_solver:  str    # "CBC" | "GLOP"
    seed:             int
    node_capacity:    float  # C[n] — resource capacity per node
    tenant_usage_min: float  # lower bound for u[i,h] (placeholder for prediction)
    tenant_usage_max: float  # upper bound for u[i,h] (placeholder for prediction)


SAMPLE_1 = PipelineConfig(
    name             = "Simple",
    n_tenants        = 3,
    n_nodes          = 4,
    n_time_slots     = 2,
    n_jobs_per_slot  = 8,
    plan_time_limit  = 120,
    plan_mip_gap     = 0.01,
    realtime_solver  = "CBC",
    seed             = 42,
    node_capacity    = 10.0,
    tenant_usage_min = 0.8,
    tenant_usage_max = 6.0,
)

SAMPLE_2 = PipelineConfig(
    name             = "Medium",
    n_tenants        = 3,
    n_nodes          = 5,
    n_time_slots     = 3,
    n_jobs_per_slot  = 12,
    plan_time_limit  = 120,
    plan_mip_gap     = 0.01,
    realtime_solver  = "CBC",
    seed             = 42,
    node_capacity    = 10.0,
    tenant_usage_min = 0.8,
    tenant_usage_max = 6.0,
)

SAMPLE_3 = PipelineConfig(
    name             = "High",
    n_tenants        = 5,
    n_nodes          = 8,
    n_time_slots     = 4,
    n_jobs_per_slot  = 20,
    plan_time_limit  = 120,
    plan_mip_gap     = 0.01,
    realtime_solver  = "GLOP",   # LP relaxation — stays fast with 20 jobs
    seed             = 42,
    node_capacity    = 10.0,
    tenant_usage_min = 0.8,
    tenant_usage_max = 6.0,
)

SAMPLES: dict[int, PipelineConfig] = {
    1: SAMPLE_1,
    2: SAMPLE_2,
    3: SAMPLE_3,
}
