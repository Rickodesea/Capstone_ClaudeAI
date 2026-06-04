# Prediction Team — Required Model Inputs

---

## Explanation by Claude Code

The optimizer has its own internal concept of "period" and "horizon" that is independent of the Google trace's native 5-minute measurement intervals. The optimizer's **horizon** is configured as **24 hours (1 day)** — this is the full window it plans machine assignments over. The **period** is **6 hours** — the horizon is divided into 4 equal planning slots (h = 0 h, 6 h, 12 h, 18 h from the start of the day), and the optimizer can reassign tenants to different machines at each slot boundary. These values are fixed configuration choices in the optimizer; they have nothing to do with the 5-minute row granularity in the Google trace. Your EDA's "period_h" refers to the trace's native 5-minute measurement window — that is not the optimizer's period. Internally, your models can forecast at whatever resolution suits them (5-minute LSTM steps, hourly rollups, etc.), but the final output delivered to the optimizer must be one aggregate number per tenant per 6-hour slot: the total predicted memory demand that tenant will consume across all its running jobs during that 6-hour window. The optimizer does not see individual job-level inputs for the plan-ahead model; it only sees tenant-level totals per period.

---

**Data source:** Google cluster-usage traces v3.
**Job identity:** `collection_id` only. Ignore `task_id` — all predictions are at the job (collection) level.

---

## Model 1: Real-Time Scheduler

The real-time model runs every scheduling interval (~60 s) to place pending jobs onto machines.
It needs two numbers per job at the moment the job is submitted.

| Field | Type | What to predict |
|---|---|---|
| `pred_mem_mb` | float, MB | **Peak memory** the job will use over its entire lifetime. Use the observed max across all tasks in the collection. Lifetime is capped at **30 minutes** (Google trace limit). |
| `pred_cpu_p95` | float, cores | **P90 CPU peak** across all tasks in the collection over the same 30-minute window. (Field is named `pred_cpu_p95` in code; target P90 from the trace data.) |

Both fields must be available before the job enters the queue — the scheduler uses them immediately for placement and capacity checks.

**Fallbacks (synthesize — data may not always be available):**
- New `collection_id` (never seen before): use the **per-tenant median** of `pred_mem_mb` and `pred_cpu_p95` across past collections for that tenant.
- New tenant (no history): use the **global cluster median** across all tenants.

---

## Model 2: Plan-Ahead Scheduler

The plan-ahead model runs once per planning horizon (~every 20 intervals) to assign tenants to machines for the next set of planning periods. It needs aggregate demand estimates per tenant per planning period — not per job.

A **planning period** is **6 hours**. The full planning horizon is **24 hours (1 day)**, divided into 4 periods at h = 0 h, 6 h, 12 h, and 18 h.

| Field | Type | What to predict |
|---|---|---|
| `u[i, h]` | float, MB | **Total memory demand** for tenant `i` in planning period `h`. Sum of predicted peak memory across all jobs expected to be running for tenant `i` during period `h`. |
| `sigma2[i, h]` | float, MB² | **Variance of that demand estimate.** Used by the SOCP capacity safety cone. If you can't compute it from data, default to `(0.20 × u[i,h])²` — this assumes a 20% standard deviation, which the model treats as a conservative safety margin. |

**Fallbacks:**
- New tenant: use global median demand scaled to the tenant's declared job count.
- Sparse history for a known tenant: use their historical median `u` and default `sigma2`.

**Note on `sigma2`:** The model uses the Cantelli inequality to compute a safety buffer proportional to `sqrt(sigma2[i,h])`. Underestimating variance means tighter packing with higher overflow risk; overestimating means wasted capacity. The 20% default is calibrated for the current cluster config — adjust if your estimates are tighter or noisier.

---

## Delivery Format

For real-time, produce predictions **per collection_id** at submission time (online inference is fine).

For plan-ahead, produce predictions **per (tenant_id, period_index)** ahead of each horizon run — the system will call your endpoint before solving. A batch call covering all tenants for the next H periods is preferred.

If fields cannot be predicted for a specific job or tenant, return the appropriate fallback value described above rather than null — the models have no internal fallback logic.

---

## Descriptive Statistics for Borg Simulation Config

The `Prediction/borg_configuration.py` file contains constants used when the simulation dashboard
loads the Borg dataset configuration. Some values are currently estimated; the ones marked below
can be replaced with real descriptive statistics from your dataset. Please run these calculations
from your EDA and share the results so the optimization team can update `borg_configuration.py`.

These replace synthetic defaults in the simulation — they do **not** need to be learned/predicted
by your models, just computed once as summary statistics from the downloaded trace data.

### From the real-time scheduler dataset (`real_time_scheduler_input.csv`)

| Value needed | How to calculate | Config key |
|---|---|---|
| Average memory per job (MB) | `real_time_df['pred_mem_mb'].mean()` — convert to MB if normalized | `req_mem_min_mb`, `req_mem_max_mb` |
| Memory range per job | `real_time_df.groupby('tenant_id')['pred_mem_mb'].agg(['min','max'])` | Informs job RAM range per tenant |
| Average CPU per job | `real_time_df['pred_cpu_p95'].mean()` | `req_cpu_min`, `req_cpu_max` |
| Avg jobs per tenant per interval | Count collections per tenant over a 60-second window in the trace | `jobs_min_per_round`, `jobs_max_per_round` |
| Unique tenant count | `real_time_df['tenant_id'].nunique()` | `num_tenants` (expected: 9) |

### From the plan-ahead scheduler dataset (`plan_ahead_scheduler_input.csv`)

| Value needed | How to calculate | Config key |
|---|---|---|
| Demand range per tenant per period | `plan_ahead_df.groupby('tenant_id')['u'].agg(['min','max'])` | Informs `tenant_usage_min`, `tenant_usage_max` in PA model |
| Demand variance range | `plan_ahead_df['sigma2'].describe()` | Confirms sigma_frac in Cantelli constraint |
| Unique tenants | `plan_ahead_df['tenant_id'].nunique()` | `num_tenants` (expected: 9) |

### Machine/node counts (from the Google trace or your download)

| Value needed | Source | Config key |
|---|---|---|
| Number of machines in your cluster subset | From the trace `machine_events` table: count distinct `machine_id` values in your subset | `total_nodes` (currently estimated at 50) |
| Typical machine RAM (normalized 0–1) | From `machine_attributes` or `resource_request` table: max memory across machines | `node_mem_min_gb`, `node_mem_max_gb` after denormalization |

> **Note on normalization:** Google cluster traces store all resources as fractions of machine
> capacity (0 to 1). If your `pred_mem_mb` values are in this normalized scale, set
> `PA_NODE_CAPACITY = 1.0` in `borg_configuration.py` (already done). Multiply by the
> actual machine RAM to get real MB values for the real-time model.

### What to send back

A short table or JSON block with these values is enough. The optimization team will update
`Prediction/borg_configuration.py` accordingly. Example format:

```json
{
  "num_tenants": 9,
  "total_machines_in_subset": 120,
  "avg_jobs_per_tenant_per_minute": 2.3,
  "pred_mem_mb_range": [0.0003, 0.95],
  "pred_cpu_p95_range": [0.0005, 0.80],
  "u_range_per_tenant_per_period": [0.001, 0.28],
  "sigma2_range": [7e-9, 4e-6]
}
```
