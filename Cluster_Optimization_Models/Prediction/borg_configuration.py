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
# Exact node counts are not released in the public trace. The Borg cluster paper
# (Verma et al. 2015) suggests an estimate of around 50 machines for a cell, but we
# chose smaller values because we are working with only a subset of the tenants.
NUM_NODES:    int   = 15        # representative cluster size for simulation
ALWAYS_ON:    int   =  5        # always-available machines (M_a)
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


# ── Borg dataset statistics (from the prediction team) ─────────────────────────
# Mean + standard deviation per metric over the 9-tenant subset of the raw Google
# cluster-usage traces v3. Memory and CPU are normalized to fractions of the
# largest machine (Google's native format).
#
# Two reported values were out of range and were adjusted (originals kept for
# traceability):
#   • job_cpu_std = 11.90 is impossible for a normalized [0, 1] metric (most likely
#     un-normalized cores, or driven by a few outliers). It is capped so that
#     mean + 3·std stays within one machine (≤ 1.0).
#   • tenant_demand_per_period (19.44 ± 48.02) is a sum of normalized per-row usage
#     and far exceeds a single machine's capacity. It is kept for reference only
#     and is NOT used as a capacity bound. If it is ever fed to the plan-ahead
#     model as u[i,h] with PA_NODE_CAPACITY = 1.0, it must be rescaled first.
_CPU_MEAN    = 0.123887
_CPU_STD_RAW = 11.903688

BORG_STATS: dict = {
    "num_tenants":                     NUM_TENANTS,
    "job_mem_mean":                    0.000549,   # normalized (fraction of largest machine RAM)
    "job_mem_std":                     0.00309,
    "job_cpu_mean":                    _CPU_MEAN,   # normalized (fraction of largest machine CPU)
    "job_cpu_std":                     round(min(_CPU_STD_RAW, (1.0 - _CPU_MEAN) / 3.0), 4),  # 11.90 -> 0.2920
    "job_cpu_std_reported":            _CPU_STD_RAW,  # original team value, retained
    "jobs_per_tenant_per_minute_mean": 1.8655,
    "jobs_per_tenant_per_minute_std":  0.6338,
    "job_lifetime_sec_mean":           89.0,
    "job_lifetime_sec_std":            341.07,
    "spike_prob_pct":                  2.45,
    "overcommit_ratio_mean":           0.0887,
    "overcommit_ratio_std":            0.1955,
    "tenant_demand_per_period_mean":   19.444506,   # see note: reference only, not a capacity bound
    "tenant_demand_per_period_std":    48.020514,
}


def _range(mean: float, std: float, lo_floor: float, hi_cap: float) -> tuple[float, float]:
    """Build a [min, max] config range as mean ± 3·std, clamped to [lo_floor, hi_cap]."""
    lo = max(lo_floor, mean - 3.0 * std)
    hi = min(hi_cap,   mean + 3.0 * std)
    return (round(lo, 4), round(hi, 4))


# Scale normalized job memory to MB against the largest machine in the pool.
_LARGEST_MACHINE_MB = NODE_MEM_MAX_GB * 1024.0
_mem_lo_n, _mem_hi_n = _range(BORG_STATS["job_mem_mean"], BORG_STATS["job_mem_std"], 0.0, 1.0)
REQ_MEM_MIN_MB: float = max(32.0, round(_mem_lo_n * _LARGEST_MACHINE_MB, 1))
REQ_MEM_MAX_MB: float = round(_mem_hi_n * _LARGEST_MACHINE_MB, 1)

# Jobs per scheduling interval (~60 s) = per-tenant rate × tenant count.
# Aggregate std scales by sqrt(n) assuming tenants arrive independently.
_JOBS_MEAN = BORG_STATS["jobs_per_tenant_per_minute_mean"] * NUM_TENANTS
_JOBS_STD  = BORG_STATS["jobs_per_tenant_per_minute_std"]  * (NUM_TENANTS ** 0.5)
JOBS_PER_ROUND:     int = int(round(_JOBS_MEAN))
JOBS_MIN_PER_ROUND: int = int(round(max(0.0, _JOBS_MEAN - 3.0 * _JOBS_STD)))
JOBS_MAX_PER_ROUND: int = int(round(_JOBS_MEAN + 3.0 * _JOBS_STD))

# Job lifetime range (s), floored at 1 s and capped at the 1800 s trace limit.
LIFETIME_MIN_SEC, LIFETIME_MAX_SEC = _range(
    BORG_STATS["job_lifetime_sec_mean"], BORG_STATS["job_lifetime_sec_std"], 1.0, 1800.0)

# Overcommit ratio = mean(actual usage) / request -> usage lower-bound fraction.
REQUEST_PER_BORG: float = round(BORG_STATS["overcommit_ratio_mean"], 3)

SPIKE_PROB_PCT_BORG: float = BORG_STATS["spike_prob_pct"]

# Note: job CPU range is intentionally NOT mapped into the simulation config. The
# trace CPU is collection-level and normalized to a full machine (mean ≈ 0.124 of
# a machine), which is not comparable to the simulation's per-job core requests
# (0.25–1.0 cores). The default req_cpu range is left in place.


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
    # Workload (derived from BORG_STATS — see derivation above)
    "jobs_per_round":         JOBS_PER_ROUND,
    "jobs_min_per_round":     JOBS_MIN_PER_ROUND,
    "jobs_max_per_round":     JOBS_MAX_PER_ROUND,
    "req_mem_min_mb":         REQ_MEM_MIN_MB,
    "req_mem_max_mb":         REQ_MEM_MAX_MB,
    "spike_prob_pct":         SPIKE_PROB_PCT_BORG,
    "min_lifetime_sec":       LIFETIME_MIN_SEC,
    "max_lifetime_sec":       LIFETIME_MAX_SEC,
    "request_per":            REQUEST_PER_BORG,
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
