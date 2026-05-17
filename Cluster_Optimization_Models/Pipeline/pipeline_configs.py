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
name                    : display label
n_tenants               : |T| — tenants in the plan-ahead model
n_workloads_per_tenant  : |W_i| — workloads per tenant
n_nodes                 : |N| — cluster nodes
n_time_slots            : |H| — planning horizon length
n_jobs_per_slot         : real-time jobs submitted per plan-ahead slot
plan_time_limit         : Gurobi wall-clock limit in seconds
plan_mip_gap            : Gurobi relative optimality gap threshold
realtime_solver         : OR-Tools solver ID — "CBC" (exact MILP) or
                          "GLOP" (LP relaxation + rounding, faster for
                          large instances)
seed                    : random seed for reproducibility

Complexity notes
─────────────────
Sample 1 — Simple
  3T × 4N × 2H × 2W/T.  ~200 binary vars in the MISOCP.
  Gurobi solves to optimality in < 1 second.
  OR-Tools CBC handles 8 jobs/slot instantly.

Sample 2 — Medium
  8T × 10N × 3H × 2W/T.  ~8 k binary vars in the MISOCP (xi + zeta dominate).
  Gurobi typically reaches 5 % gap within 60–120 s (academic license).
  CBC still handles 20 real-time jobs/slot in a few seconds.

Sample 3 — High
  15T × 20N × 4H × 2W/T.  ~60 k binary vars.  Gurobi will likely hit the
  time limit and report a feasible (suboptimal) solution.  Real-time uses
  GLOP (LP relaxation) to stay fast with 50 jobs/slot.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PipelineConfig:
    name:                   str
    n_tenants:              int
    n_workloads_per_tenant: int
    n_nodes:                int
    n_time_slots:           int
    n_jobs_per_slot:        int
    plan_time_limit:        int    # seconds
    plan_mip_gap:           float
    realtime_solver:        str    # "CBC" | "GLOP"
    seed:                   int
    node_capacity:          float  # C[n,r] — resource capacity per node
    sla_eps:                float  # eps_i — SLA risk tolerance (lower = stricter)
    # kappa at eps=0.05 ~ 4.36; at eps=0.10 ~ 3.00; at eps=0.15 ~ 2.38
    # Larger instances need higher eps (softer SOCP) to stay feasible


SAMPLE_1 = PipelineConfig(
    name                   = "Simple",
    n_tenants              = 3,
    n_workloads_per_tenant = 2,
    n_nodes                = 4,
    n_time_slots           = 2,
    n_jobs_per_slot        = 8,
    plan_time_limit        = 120,
    plan_mip_gap           = 0.01,
    realtime_solver        = "CBC",
    seed                   = 42,
    node_capacity          = 10.0,
    sla_eps                = 0.05,   # kappa ~ 4.36
)

SAMPLE_2 = PipelineConfig(
    name                   = "Medium",
    n_tenants              = 3,
    n_workloads_per_tenant = 3,    # more workloads per tenant (was 2)
    n_nodes                = 5,
    n_time_slots           = 3,
    n_jobs_per_slot        = 12,
    plan_time_limit        = 120,
    plan_mip_gap           = 0.01,
    realtime_solver        = "CBC",
    seed                   = 42,
    node_capacity          = 10.0,
    sla_eps                = 0.05,
)

SAMPLE_3 = PipelineConfig(
    name                   = "High",
    n_tenants              = 3,
    n_workloads_per_tenant = 3,    # same as medium
    n_nodes                = 5,
    n_time_slots           = 4,
    n_jobs_per_slot        = 20,
    plan_time_limit        = 120,
    plan_mip_gap           = 0.01,
    realtime_solver        = "GLOP",   # LP relaxation — stays fast with 20 jobs
    seed                   = 42,
    node_capacity          = 10.0,
    sla_eps                = 0.05,
)

SAMPLES: dict[int, PipelineConfig] = {
    1: SAMPLE_1,
    2: SAMPLE_2,
    3: SAMPLE_3,
}
