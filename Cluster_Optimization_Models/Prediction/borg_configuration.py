"""
Prediction/borg_configuration.py
──────────────────────────────────
Configuration constants derived from the Google Borg cluster dataset used by the
prediction team. These values override the synthetic defaults in simulation_config.py
when the dashboard "Load Borg Config" button is pressed, or when --use-borg-config
is passed to sim_runner.py.

Source: Google Cluster Usage Traces v3 (a subset downloaded by the prediction team)
  https://github.com/google/cluster-data/blob/master/ClusterData2019.md

How to use
──────────
  from prediction.borg_configuration import BORG_CONFIG
  # Then merge into DEFAULT_CONFIG before creating ClusterManager / SimulationManager

Prediction API integration
──────────────────────────
  When use_prediction_api is enabled (set via the dashboard or borg config),
  the simulation calls prediction_api.predict_realtime() and predict_workload()
  instead of synthesising job and workload data.
"""

from __future__ import annotations

# ── Dataset characteristics ────────────────────────────────────────────────────

# Number of unique tenants (collections) in the prediction team's dataset subset.
# Source: prediction_api.py health_check() — plan_ahead_df unique tenant_id count.
NUM_TENANTS: int = 9

# Planning periods from the prediction team's data.
# period_index values: {0, 6, 12, 18} → 6-hour slots over a 24-hour horizon.
PLAN_PERIOD_HOURS: int  = 6     # width of each planning slot (hours)
N_PLAN_PERIODS:   int  = 4     # number of planning periods (0, 6, 12, 18)
HORIZON_HOURS:    int  = 24    # total planning horizon (hours)

# ── Resource units ─────────────────────────────────────────────────────────────
# Google cluster traces normalise all resources to fractions of machine capacity
# (0 to 1 scale). The prediction team's u[i,h] and pred_mem_mb values use this
# same normalised scale.
#
# NODE_CAPACITY is set to 1.0 so that the plan-ahead MISOCP constraint
#   Σᵢ f[i,n,h] ≤ C[n] = 1.0
# works directly with the prediction API's u values (which are fractions 0..1).
#
# To use the default synthetic model (NODE_CAPACITY=10.0), scale u by 10 first.
PA_NODE_CAPACITY: float = 1.0  # normalised: 1.0 = full machine capacity

# ── Cluster topology (representative estimates) ────────────────────────────────
# These are synthetic estimates based on the Borg cluster paper (Verma et al. 2015).
# Exact node counts are not released in the public trace; use these as reasonable
# proxies for a small cluster cell.
NUM_NODES:    int   = 50        # representative cluster size for simulation
ALWAYS_ON:    int   = 40        # always-available machines (M_a)
N_EXCLUSIVE:  int   = 2         # exclusive tenants (≈ 22% of 9 tenants)

# Node RAM range (GB). Borg cells use heterogeneous machines; we approximate with
# a representative range from the cluster paper.
NODE_MEM_MIN_GB: float = 32.0
NODE_MEM_MAX_GB: float = 128.0

# CPU cores per node
NODE_CPU_MIN: float = 8.0
NODE_CPU_MAX: float = 64.0

# ── Workload parameters ────────────────────────────────────────────────────────
# pred_mem_mb from the prediction API is in normalised units (fraction of machine
# RAM). A value of 0.001 = 0.1% of machine RAM. For a 64 GB machine this is ~65 MB.
# The values are already calibrated for use with the MILP's memory constraints.
# If your simulation uses absolute MB, you need to multiply by machine RAM.

# ── Realtime solver (Gurobi preferred for speed) ──────────────────────────────
RT_SOLVER: str  = "GUROBI"
RT_ITERATIVE:   bool = True
RT_BATCH_JOBS:  int  = 32
RT_BATCH_NODES: int  = 32
RT_TIME_LIMIT_MS: int = 10_000


# ── Simulation config override dict ────────────────────────────────────────────
# Passed to POST /api/config or merged into simulation_config.DEFAULT_CONFIG.
# Values here override the defaults in simulation_config.py on Reset.
BORG_CONFIG: dict = {
    # Topology
    "num_tenants":            NUM_TENANTS,
    "total_nodes":            NUM_NODES,
    "always_on_nodes":        ALWAYS_ON,
    "num_exclusive_tenants":  N_EXCLUSIVE,
    "node_mem_min_gb":        NODE_MEM_MIN_GB,
    "node_mem_max_gb":        NODE_MEM_MAX_GB,
    "node_cpu_min":           NODE_CPU_MIN,
    "node_cpu_max":           NODE_CPU_MAX,
    # Plan-ahead
    "horizon_steps":          N_PLAN_PERIODS * 6,   # 24 steps (1 per hour, then PA reruns)
    "period_steps":           6,                     # 6-step slots matching 6-hour periods
    # Realtime solver
    "realtime_solver":        RT_SOLVER,
    "rt_iterative":           int(RT_ITERATIVE),
    "rt_batch_jobs":          RT_BATCH_JOBS,
    "rt_batch_nodes":         RT_BATCH_NODES,
    "realtime_time_limit_ms": RT_TIME_LIMIT_MS,
    # Prediction API integration
    "use_prediction_api":     1,
}
