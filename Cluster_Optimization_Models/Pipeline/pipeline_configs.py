"""
pipeline/pipeline_configs.py
──────────────────────────────
Complexity configurations for the end-to-end pipeline demo.

Select a sample when running interface.py:
    python interface.py          # default: Sample 1 (simple)
    python interface.py 2        # Sample 2 (medium)
    python interface.py 3        # Sample 3 (high)

New in this version
────────────────────
  • n_always_available  : number of always-on machines (rest are additional)
  • n_exclusive         : exact number of tenants tagged exclusive (validated ≤ n_tenants)
  • n_intervals         : planning horizon length (replaces n_time_slots)
  • use_socp            : True = MISOCP (Cantelli cone); False = MILP
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PipelineConfig:
    name:                str
    n_tenants:           int
    n_nodes:             int
    n_intervals:         int     # planning horizon length (number of intervals)
    n_always_available:  int     # |M_a| — always-on machines; rest are additional
    n_exclusive:         int     # number of exclusive tenants (must be ≤ n_tenants)
    n_jobs_per_slot:     int     # real-time jobs generated per interval
    plan_time_limit:     int     # Gurobi wall-clock limit (seconds)
    plan_mip_gap:        float   # Gurobi relative optimality gap
    realtime_solver:     str     # "CBC" | "GLOP"
    use_socp:            bool    # True = Cantelli MISOCP, False = plain MILP
    seed:                int
    node_capacity:       float   # C[n] per machine (uniform)
    tenant_usage_min:    float
    tenant_usage_max:    float


SAMPLE_1 = PipelineConfig(
    name                = "Simple",
    n_tenants           = 4,
    n_nodes             = 5,
    n_intervals         = 2,
    n_always_available  = 3,
    n_exclusive         = 1,    # 1 of 4 tenants exclusive
    n_jobs_per_slot     = 8,
    plan_time_limit     = 120,
    plan_mip_gap        = 0.01,
    realtime_solver     = "CBC",
    use_socp            = True,
    seed                = 42,
    node_capacity       = 10.0,
    tenant_usage_min    = 0.8,
    tenant_usage_max    = 6.0,
)

SAMPLE_2 = PipelineConfig(
    name                = "Medium",
    n_tenants           = 5,
    n_nodes             = 7,
    n_intervals         = 3,
    n_always_available  = 4,
    n_exclusive         = 1,    # 1 of 5 tenants exclusive
    n_jobs_per_slot     = 12,
    plan_time_limit     = 120,
    plan_mip_gap        = 0.01,
    realtime_solver     = "CBC",
    use_socp            = True,
    seed                = 42,
    node_capacity       = 10.0,
    tenant_usage_min    = 0.8,
    tenant_usage_max    = 6.0,
)

SAMPLE_3 = PipelineConfig(
    name                = "High",
    n_tenants           = 8,
    n_nodes             = 10,
    n_intervals         = 4,
    n_always_available  = 6,
    n_exclusive         = 2,    # 2 of 8 tenants exclusive
    n_jobs_per_slot     = 20,
    plan_time_limit     = 120,
    plan_mip_gap        = 0.01,
    realtime_solver     = "GLOP",
    use_socp            = True,
    seed                = 42,
    node_capacity       = 10.0,
    tenant_usage_min    = 0.8,
    tenant_usage_max    = 6.0,
)

SAMPLES: dict[int, PipelineConfig] = {
    1: SAMPLE_1,
    2: SAMPLE_2,
    3: SAMPLE_3,
}
