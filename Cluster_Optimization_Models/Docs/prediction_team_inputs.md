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
