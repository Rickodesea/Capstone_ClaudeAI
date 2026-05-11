# Team Alignment: Prediction Pipeline, Optimization Models, and Research Grounding

**Date:** 2026-05-11  
**Refined by:** Claude Code  
**Audience:** Full team + Instructor  
**Purpose:** Architectural alignment reference — what the prediction layer must produce for each optimization model, at what grain, using what training targets, and from what data

---

## Executive Summary

This document helps clear certain confusion around the prediction to optimization pipeline.

**Five key findings:**

1. **Tasks in a collection are copies of the same binary.** They are expected to behave nearly identically. This makes collection-level aggregation a valid and cleaner prediction strategy: predict "expected peak memory and P90 CPU for any task from collection X" rather than per-instance temporal sequences. The collection is the natural grain for prediction feeding into both optimization models. The tasks in a collection are also the `jobs` that the optimization model optimizes for.

A collection lets a tenant describe parallel computation cleanly: "run 50 identical workers on different partitions of my dataset." All 50 workers execute the same binary with the same resource spec; the cluster assigns them to 50 different machines automatically. The tenant declares the work once, the cluster scales it to N machines. This is the standard MapReduce/batch pattern. From the tenant's perspective it is one "job" (one logical task), even though the cluster executes it as N individual container instances.

2. **The correct training targets are `avg(max_mem per task)` and `avg(p90_cpu per task)`, aggregated at the collection level.** The optimizer consumes peak values (P_mem_j = worst-case peak memory per task), and your training target must match. Using avg_mem underestimates peak demand and will cause OOM kills.

3. **Two separate data extractions are needed.** The first (Dataset 1) aggregates 5-min measurement windows into per-task lifetimes, then further aggregates tasks into per-collection rows. This is the training data for the memory prediction model. The second (Dataset 2) generates temporal snapshots — for each tenant, at each time interval (e.g. hourly), how many collections were active. This drives N_it for the plan-ahead model. See Part 15 for the full ETL specification and SQL pseudocode.

4. **Two distinct pipelines are needed:**
   - **Pipeline 1 (for Real-Time Optimizer):** Collection-level LSTM or RF model. **Inputs:** `[req_cpu, req_mem, tenant_id, collection_id, scheduling_class, priority, num_tasks]`. **Targets (outputs):** `avg(max_mem per task)` → P_mem_j and `avg(p90_cpu per task)` → P_cpu_j. The targets are never inputs — using them as inputs would be data leakage.
   - **Pipeline 2 (for Plan-Ahead Optimizer):** Two-part pipeline. (a) Predict `mu_ijrt` (expected mean resource usage per workload per future slot) and `N_it` (expected active workload count per tenant per future slot) — both require a model because they vary by time of day. (b) Compute `Sigma_r` (cross-workload covariance) and `d_ijr` (declared demand) once from trace history — these are static properties of the workload population and do not vary by slot.


---

## Part 1: Team Terminology and Scheduling Basics

There was an earlier misunderstanding of what a job is. There are 2 places jobs are used in the cloud ecosystem, a tennant requests a "job" but a schedular places a "job". But what a schedular calls a job is actually a task and what a tennant calls a job is a collection. So let us clear up our terminology and apply them going forward.

A tenant submits a **workload** — a collection of identical tasks. The cluster decomposes this into individual **jobs** (tasks/pods), each placed on a separate machine. All jobs in a workload run the same binary with the same declared resource spec. Because they are identical copies, any one job is expected to behave the same as any other job from the same workload. This makes the **workload (collection) the natural prediction grain**: train on workload-level statistics and the prediction applies to every individual job from that workload.


In both optimization models: **"job" = one task/pod; "workload" = one collection.** Resource requests (`req_cpu`, `req_mem`) are specified per task in both Kubernetes (`spec.template.spec.containers.resources`) and the Google trace (`InstanceEvents.resource_request`). All tasks in a workload share the same declared request.


Use this table when communicating across the team to avoid confusion:

| Our term | Borg equivalent | Kubernetes equivalent | Notes |
|---|---|---|---|
| Job | Task | Pod | One container; one scheduling unit. This is what the real-time optimizer places. |
| Workload (collection) | Collection / Job | Job resource | What the tenant submits: N jobs sharing the same binary and resource spec |
| Tenant | User | Namespace | Owner of one or more workloads |
| Node | Machine | Node | Physical compute host |
| Planning slot | Alloc period | — | 4-hour block in the plan-ahead model |
| Scheduling epoch | Task scheduling cycle | Scheduling cycle | Every ~60 seconds for the real-time model |


### Mapping Our Terms to the Google Cluster Trace v3 Schema

| Our concept | Our term | Google Trace table | Google Trace field / key |
|---|---|---|---|
| One scheduling unit | Job | `InstanceEvents` | `(collection_id, instance_index)` |
| Multi-job submission | Workload (collection) | `CollectionEvents` | `collection_id` |
| User/owner | Tenant | `CollectionEvents` | `user` (hashed) |
| Physical machine | Node | `MachineEvents` | `machine_id` |
| Declared CPU per job | `d_ijr` (CPU) | `InstanceEvents` | `resource_request.cpus` |
| Declared memory per job | `d_ijr` (MEM) | `InstanceEvents` | `resource_request.memory` |
| Average CPU usage (5-min window) | `avg_cpu` | `InstanceUsage` | `average_usage.cpus` |
| Average memory usage (5-min window) | `avg_mem` | `InstanceUsage` | `average_usage.memory` |
| Peak memory in window | `max_mem` | `InstanceUsage` | `maximum_usage.memory` |
| P90 CPU in window | `p90_cpu` | `InstanceUsage` | `tail_cpu_usage_distribution[4]` |
| Predicted peak memory (optimizer input) | `P_mem_j` | `InstanceUsage` | derived from `maximum_usage.memory` |
| Mean workload usage for planning | `mu_ijr` | `InstanceUsage` | `average_usage` aggregated to 4h |
| Expected job count per slot | `N_it` | `CollectionEvents` | SUBMIT event count per tenant per 4h bucket |
| Node capacity | `C_nr` | `MachineEvents` | `capacity.cpus` / `capacity.memory` |

---

## Part 2: What Each Optimization Model Needs from Prediction

The two optimization models have **different inputs, different time scales, and different prediction requirements**.

### 2.1 Real-Time Optimization Model — Input Requirements

**When it runs:** Every scheduling epoch (~60 seconds)  
**What it does:** Decides which pending job (task) goes to which node right now

| Parameter | Description | Required value | Source in trace |
|---|---|---|---|
| **P_mem_j** | Peak memory for one job | `avg(max_mem per task)` from collection-level model | `InstanceUsage.maximum_usage.memory` |
| **P_cpu_j** | P90 CPU for one job | `avg(p90_cpu per task)` from collection-level model | `InstanceUsage.tail_cpu_usage_distribution[4]` |

P_mem_j is the worst-case peak memory any one job from the workload will use. P_cpu_j is the P90 CPU for one job. Both are predicted at the workload (collection) level and applied to each individual arriving job from that workload.

**How used in the optimizer:** Constraint C2 checks that the sum of predicted peak usage across placed jobs fits within node capacity with a probabilistic safety buffer. Using peak values (not average) allows safe overcommitment.

### 2.2 Plan-Ahead Model — Input Requirements

**When it runs:** Once per planning horizon (e.g., daily, weekly, etc)  
**What it does:** Reserves node sets for each tenant over a multi-slot horizon

| Parameter | Description | Required value | Time scale | Source |
|---|---|---|---|---|
| **mu_ijrt** | Expected mean resource usage of workload (i,j) for resource r at future slot t | Predicted mean CPU / memory per workload per future slot | Per slot — **predicted** | Historical average per `(collection, hour, day_of_week)` bucket |
| **N_it** | Expected number of active workloads for tenant i at slot t | Predicted active collection count | Per slot — **predicted** | Temporal submission-count model |
| **Sigma_r** | Covariance matrix of resource r across all workloads | Empirical cross-workload covariance | Static — computed once | Descriptive stats from trace |
| **d_ijr** | Declared resource demand | Direct from trace | Static per workload | `InstanceEvents.resource_request` |

**mu_ijrt and N_it are slot-specific estimates** because the plan-ahead model solves over future time slots, and both values change depending on what time of day / day of week each slot falls on. The prediction approach is simple: compute the historical average grouped by `(hour_of_day, day_of_week)` from the trace — a descriptive analysis over time buckets. A single global average would treat all future slots identically, which is wrong. Simple regression over time features improves estimates for sparse buckets; no complex ML (LSTM etc.) is needed here.

**Sigma_r is computed once** — it captures how different workload types co-vary in resource usage (e.g., workloads A and B both spike at the same time). This is a structural property of the workload population, not time-varying in a meaningful way. One covariance matrix per resource type, estimated from historical trace, is sufficient.

**d_ijr** comes from historical trace data — use the average resource request for that collection from the most recent available window (e.g., last week's submissions). No prediction model needed.

In the real world, large cloud providers offer **capacity reservations** (Google Committed Use Discounts, AWS Reserved Instances, Azure Capacity Reservations) where tenants commit to a resource level in exchange for lower pricing. If a tenant has made such a commitment, that declared level takes priority over the historical average and is used directly as d_ijr. Fine-grained per-collection, per-hour forecasts from tenants do not happen in practice — reservations are coarse (vCPU blocks, not per-workload). For our model: use the tenant's reservation as d_ijr if one exists; otherwise fall back to the historical average from the trace.

### 2.3 Summary Table: Optimizer Inputs vs Prediction Source

| Optimizer | Parameter | Meaning | How to produce |
|---|---|---|---|
| **Real-Time** | P_mem_j | Peak memory per job | `avg(max_mem per task)` — collection-level LSTM or RF |
| **Real-Time** | P_cpu_j | P90 CPU per job | `avg(p90_cpu per task)` — collection-level LSTM or RF (optional; see note) |
| **Plan-Ahead** | mu_ijrt | Mean usage per workload per **future** slot t | Historical average per `(collection, hour, day_of_week)` bucket |
| **Plan-Ahead** | N_it | Active workload count per tenant per slot | Historical average per `(tenant, hour, day_of_week)` bucket — team may also use regression or LSTM |
| **Plan-Ahead** | Sigma_r | Cross-workload covariance | Empirical sample covariance — computed once from trace, static |
| **Both** | d_ijr | Declared demand per job | Historical average `resource_request` per collection from trace (e.g., last week) |

---

## Part 5: Prediction Team's Current Approach

The team has done solid work! The following are specific issues to address.

### Issue 1: Training targets — the team's targets are correct, address the leakage concern

The team predicts `max_mem` and `p90_cpu` — these are the correct targets. `avg(max_mem per task)` → P_mem_j and `avg(p90_cpu per task)` → P_cpu_j are exactly what the optimizer needs.

The leakage concern the team raised is a misunderstanding. Using `max_mem` and `p90_cpu` as **targets (outputs)** is correct supervised learning — that is what the model is trying to predict. Data leakage would be using them as **input features** (feeding the answer into the model). As long as `max_mem` and `p90_cpu` appear only on the output side and never as input features, there is no leakage.

**Inputs (features):** `req_cpu`, `req_mem`, `tenant_id`, `collection_id`, `scheduling_class`, `priority`, `num_tasks`
**Targets (outputs):** `avg(max_mem per task)` → P_mem_j, `avg(p90_cpu per task)` → P_cpu_j

### Issue 2: The prediction model needs to train at the collection (workload) level

Since all tasks in a workload run the same binary with the same resource spec, collection-level prediction is the right grain. Aggregate per-task statistics across a collection's tasks first, then train on the aggregated row. This avoids compounding errors from predicting per-instance and then aggregating, which is what the instructor flagged.

### Issue 4: A separate pipeline is needed for the Plan-Ahead Model

The collection-level model for the real-time optimizer serves the real-time MILP. The plan-ahead model needs three additional inputs from a separate prediction pipeline:

- **mu_ijrt** (predicted mean resource usage per workload per future slot): use a historical average per `(collection_id, hour_of_day, day_of_week)` bucket. Simple model, but it is a model — the average updates as new data arrives, and it produces a slot-specific estimate rather than one global value. Cold start: fall back to the collection's overall running average.
- **N_it** (predicted active workload count per tenant per future slot): same temporal model as Dataset 2 — see Part 15.
- **Sigma_r** (cross-workload covariance): static property, computed once from historical trace. No prediction model needed — this is the one exception where a descriptive statistic is correct.

### A 3rd fallback model for unknown tenants or collections

The two main models assume the tenant and collection are known from historical data. For new tenants or new collection types with no history, a fallback model is needed. This model trains on aggregate statistics with no tenant or collection ID — purely on resource request features (req_cpu, req_mem, scheduling_class, priority) → avg peak memory.

ETL for the fallback model: same Dataset 1 aggregation, but drop `tenant_id` and `collection_id` before training. The model learns the general relationship: given these declared resource specs, how much memory does a task typically peak at, across all tenants.

At inference time:
```python
def realtime_predict(tenant_id, collection_id, req_mem, ...):
    if tenant_id is known AND collection_id is known:
        return specific_model.predict(tenant_id, collection_id, req_mem, ...)
    else:
        return fallback_model.predict(req_mem, ...)  # no IDs — general estimate
```

**Can the real-time optimizer pass collection_id?** In Kubernetes, every pod carries the Job name as a label (`job-name`). If the optimizer reads pod labels at scheduling time, it can pass `collection_id` to the prediction layer and enable collection-specific prediction. This is a useful model improvement: add `collection_id` to the job metadata that the optimizer passes to the prediction function. It does not require changes to the optimizer's LP formulation — only to the data passed at the call site. Add this as a future improvement.

---

## Part 7: Pipeline Overview — Information Flow

```
┌─────────────────────────────────────────────────────────┐
│                    DATA SOURCE                          │
│  Google Cluster Trace v3 — instance_usage,             │
│  instance_events, collection_events                     │
│  (5-min windows, Cell A, filtered down samples )       │
│  (no long running jobs)                                 │
└────────────────────────┬────────────────────────────────┘
                         │
             ┌───────────┴────────────┐
             │                        │
             ▼                        ▼
┌────────────────────┐    ┌──────────────────────────────┐
│  COLLECTION-LEVEL  │    │  PLAN-AHEAD PREDICTION        │
│  RF/LSTM Model     │    │  Pipeline                     │
│  (one row per      │    │                               │
│  workload)         │    │  Per-slot (hist avg / regr):  │
│                    │    │  - mu_ijrt ← hist avg │
│  IN: req_cpu,      │    │    (coll, hour, day_of_week)  │
│      req_mem,      │    │  - N_it ← avg by              │
│      tenant,       │    │    (tenant, hour, day_of_week)│
│      sched_class,  │    │                               │
│      num_tasks     │    │  Cold start → coll avg / d_ijr│
│                    │    │                               │
│  OUT per job:      │    │  STATIC (computed once):      │
│  - P_mem_j         │    │  - Sigma_r (covariance)       │
│  - P_cpu_j         │    │  - d_ijr (declared demand)    │
└─────────┬──────────┘    └──────────────┬───────────────┘
          │                              │
          │                              │
          ▼                              ▼
┌─────────────────────┐    ┌─────────────────────────────┐
│  REAL-TIME          │    │  PLAN-AHEAD MODEL            │
│  OPTIMIZATION       │◄───┤  (runs once per planning     │
│  MODEL              │    │  horizon: daily or weekly)   │
│  (runs every ~60s)  │    │                              │
│                     │    │  Outputs:                    │
│  Inputs per pod:    │    │  A_t_i — authorized node set │
│  P_mem_j, P_cpu_j   │    │  per tenant per time slot    │
│  omega_delay_t (monitor) │                              │
│  U_mem_n (monitor)  │    └─────────────┬───────────────┘
│  A_t (plan-ahead)   │                  │
│                     │◄─────────────────┘
│  Output: x_jn       │  (A_t constrains which nodes
│  (pod-to-node       │   each tenant's pods can use)
│  assignment)        │
└─────────────────────┘
          │
          ▼
┌─────────────────────┐
│  CLUSTER MONITOR    │
│  Tracks: U_mem_n,   │
│  vbar_n, Wbar_t     │
│  Feeds back to      │
│  optimizer next     │
│  round              │
└─────────────────────┘
```

---

## Part 10: What the Prediction Team Needs to Know

### Core model: predict peak memory per job

1. **Change training target from `avg_mem` to `avg(max_mem per task)`, aggregated at the collection level**
   - `avg(max_mem across tasks of the collection)` = expected peak for any one task from that collection
   - `max_mem` is `InstanceUsage.maximum_usage.memory` — it is a measured field, not a future value, so using it as a target is not leakage
   - The team correctly excluded it as an input feature; it should instead be the output label

2. **Consider dropping the CPU prediction model entirely**
   - CPU is compressible (kernel throttles it; jobs don't get killed). The CPU constraint in the real-time optimizer almost never binds at 7% median utilization.
   - Suggestion: use `req_cpu` directly as P_cpu_j (the declared request is already a conservative ceiling).
   - This eliminates a second prediction model and lets the team focus effort on memory, which is what actually causes failures.
   - If the team wants to keep CPU prediction, the correct target is `avg(p90_cpu per task)` (not avg_cpu). But this is optional given how weak the CPU constraint is.

3. **Move from instance-level LSTM to collection-level model**
   - Aggregate at the collection level before training (see Part 15 for the full ETL)
   - LSTM or RF are both viable at collection grain — the team is already evaluating RF, which is appropriate
   - Collection-level removes the temporal sequence complexity (5-min windows) and gives one clean row per collection to train on

### Plan-Ahead Model inputs — two predicted, two static

The plan-ahead model solves over future time slots. Any parameter that varies by time of day needs to be **predicted** for each future slot, not just plugged in as a historical average.

**Slot-specific values — historical average (simple model):**

- **mu_ijrt** (expected mean resource usage for workload (i,j) for resource r at slot t): use a historical average per `(collection_id, hour_of_day, day_of_week)` bucket — technically a model, just a simple one. As new trace data arrives, the average updates. A single global average across all slots would treat 9am and 2am identically — wrong. The time-bucket grouping is what makes it slot-specific and therefore a valid prediction for each future slot. Cold start (collection never seen): fall back to the collection's overall running average, or to `d_ijr` if no history exists at all.

- **N_it** (expected active collections per tenant per slot): compute `avg(active_count)` grouped by `(tenant_id, hour_of_day, day_of_week)` from Dataset 2 as a baseline. The prediction team may use simple regression or a more advanced model (e.g., LSTM) if they choose — the baseline lookup is the minimum viable approach. See `predict_N_it_for_slot()` for the 4-hour aggregation helper.

**Static — computed once from trace:**

- **Sigma_r** (cross-workload covariance): captures structural correlations between workload types, not time-varying in a meaningful way. Compute the empirical sample covariance matrix once from historical per-workload usage aggregates.

- **d_ijr** (declared resource demand): the tenant's stated resource request, immutable. Read directly from `InstanceEvents.resource_request`. No prediction needed.

### Cold-Start Fallbacks — Unknown Tenant or Unknown Collection

When a new tenant requests admission and has no trace history, or when a collection has never been seen before, the plan-ahead model still needs values for all four parameters. Use the fallbacks below in order of preference (most specific → least specific):

| Parameter | Unknown collection, known tenant | Unknown tenant entirely |
|---|---|---|
| **mu_ijrt** | Average mu across all collections of that tenant per `(hour, day_of_week)` | Global average mu across all tenants and collections per `(hour, day_of_week)`. If no temporal data at all: use `d_ijr` (declared request) as a proxy |
| **N_it** | N/A — N_it is per tenant, not per collection | **Dataset 4** — cross-tenant average active-collection count per `(hour, day_of_week)` |
| **Sigma_r** | Global average covariance across all known collections. If that is unavailable: diagonal matrix `σ² · I` using the global variance `σ²` estimated from all collections (assumes workloads are independent — conservative) | Same as unknown collection — use global average or `σ² · I` |
| **d_ijr** | Global average `resource_request` per `(scheduling_class, priority)` — the tenant must still declare a request but if pre-admission estimation is needed, stratify by job type | Same — global average per `(scheduling_class, priority)` |

> **Note:** d_ijr is always declared by the tenant at submission time, so a true "unknown" case only arises during pre-admission capacity estimation. At actual admission, the tenant provides it directly.

### Parameters the prediction team does NOT need to produce

The following plan-ahead parameters come from the operator configuration, not from prediction:

- `pi_n`, `pi_bar_i`, `v_op_i` — pricing (operator-defined)
- `L_iq` — SLA latency targets (from SLA contracts)
- `eta_kr`, `rho_kkp`, `tau_iip` — isolation overhead and interference (from benchmarks)
- `gamma_ij`, `Delta_i` — migration policy (operator policy)
- `lam_s` — objective weights (operator priority)



## Part 15: Data Extraction Strategy (ETL)

> **Disclaimer:** All SQL below is pseudocode. Field names follow the BigQuery schema from Google Cluster Trace v3 but may vary. The team should verify column names against the actual schema before running queries. The SQL is meant to communicate the intent of each ETL step — not to be copy-pasted verbatim.

---

### Pre-ETL: Scope Reduction

Before extracting any features, reduce the dataset to a manageable size. This keeps BigQuery costs low and keeps the training set focused on the batch workload pattern the prediction model needs to learn.

**Step 1 — Sample T tenants.** Don't train on all users. Pick T users (e.g., T = 30) stratified by activity level — include light, medium, and heavy users so the model generalises across tenant profiles.

**Step 2 — Sample W collections per tenant.** For each sampled tenant, pick W collections at random (e.g., W = 50 per tenant). This gives T × W rows in the final training set.

**Step 3 — Filter out long-running (service) collections.** Collections where no task ever completes (no FINISH, KILL, or EVICT event exists in the trace window) are always-on services, not batch jobs. They have no meaningful "peak memory" for a finite job run. Exclude them.

```sql
-- Pre-ETL: identify eligible collections and sampled scope

-- 3a. Collections that have at least one completed task in the trace window
eligible_collections AS (
  SELECT DISTINCT collection_id
  FROM InstanceEvents
  WHERE type IN ('FINISH', 'KILL', 'EVICT')
),

-- 3b. Tenants with enough submission history to be worth sampling
active_tenants AS (
  SELECT user AS tenant_id
  FROM CollectionEvents
  WHERE type = 'SUBMIT'
  GROUP BY user
  HAVING COUNT(DISTINCT collection_id) >= 10
),

-- 3c. Random sample of T tenants from active pool
sampled_tenants AS (
  SELECT tenant_id
  FROM active_tenants
  ORDER BY RAND()
  LIMIT /* T, e.g. 30 */
),

-- 3d. For each sampled tenant, pick W collections at random
sampled_collections AS (
  SELECT
    ce.collection_id,
    ce.user AS tenant_id,
    ROW_NUMBER() OVER (PARTITION BY ce.user ORDER BY RAND()) AS rn
  FROM CollectionEvents ce
  WHERE ce.type = 'SUBMIT'
    AND ce.user IN (SELECT tenant_id FROM sampled_tenants)
    AND ce.collection_id IN (SELECT collection_id FROM eligible_collections)
  QUALIFY rn <= /* W, e.g. 50 */
)
```

---

### Dataset 1 — Collection-Level Resource Features

**Purpose:** Train the memory prediction model. One row per collection. Values represent expected behavior of any one task from that collection (all tasks are identical copies of the same binary).

**Why collection-level?** All tasks in a collection share the same binary and the same `resource_request`. Their actual resource usage follows the same distribution. Aggregating to collection level reduces noise and produces one clean training example per workload type, rather than thousands of correlated per-task rows.

```sql
-- Full Dataset 1 query (continues from sampled_collections above)

-- Step 1: Match SCHEDULE event (task start) to its terminal event (task end)
tasks_complete AS (
  SELECT
    ie_sched.collection_id,
    ie_sched.instance_index,
    ie_sched.user                          AS tenant_id,
    ie_sched.scheduling_class,
    ie_sched.priority,
    ie_sched.resource_request_cpus         AS req_cpu,     -- NCU
    ie_sched.resource_request_memory       AS req_mem,     -- NCU
    ie_sched.time                          AS start_time,
    ie_end.time                            AS end_time,
    (ie_end.time - ie_sched.time) / 1e6   AS lifetime_seconds
  FROM InstanceEvents ie_sched
  JOIN InstanceEvents ie_end
    ON  ie_end.collection_id  = ie_sched.collection_id
    AND ie_end.instance_index = ie_sched.instance_index
    AND ie_end.type           = 'FINISH'  -- suggested: FINISH only (see note below)
  WHERE ie_sched.type = 'SCHEDULE'
    AND ie_sched.collection_id IN (SELECT collection_id FROM sampled_collections)
),

-- Step 2: For each task, aggregate across all 5-min measurement windows
-- maximum_usage_memory across windows → the true peak for that task's entire run
-- APPROX_QUANTILES over average_usage_cpus → P90 CPU behaviour across the run
task_usage AS (
  SELECT
    u.collection_id,
    u.instance_index,
    MAX(u.maximum_usage_memory)                              AS task_peak_mem,
    APPROX_QUANTILES(u.average_usage_cpus, 100)[OFFSET(90)] AS task_p90_cpu
  FROM InstanceUsage u
  INNER JOIN tasks_complete t
    ON  u.collection_id  = t.collection_id
    AND u.instance_index = t.instance_index
  GROUP BY u.collection_id, u.instance_index
),

-- Step 3: One row per task with all features
task_features AS (
  SELECT
    t.collection_id,
    t.tenant_id,
    t.scheduling_class,
    t.priority,
    t.req_cpu,
    t.req_mem,
    t.lifetime_seconds,
    u.task_peak_mem,
    u.task_p90_cpu
  FROM tasks_complete t
  JOIN task_usage u USING (collection_id, instance_index)
),

-- Step 4: Aggregate tasks → one row per collection
-- req_cpu and req_mem are constant across tasks in a collection (ANY_VALUE is safe)
-- avg(task_peak_mem) = expected peak memory for any one task → this is P_mem_j
-- avg(task_p90_cpu) = expected P90 CPU for any one task → this is P_cpu_j
collection_features AS (
  SELECT
    collection_id,
    tenant_id,
    ANY_VALUE(scheduling_class)        AS scheduling_class,
    ANY_VALUE(priority)                AS priority,
    ANY_VALUE(req_cpu)                 AS req_cpu_per_task,
    ANY_VALUE(req_mem)                 AS req_mem_per_task,
    COUNT(*)                           AS num_tasks,
    AVG(task_peak_mem)                 AS avg_peak_mem_per_task,   -- ← P_mem_j target
    AVG(task_p90_cpu)                  AS avg_p90_cpu_per_task     -- ← P_cpu_j target (optional)
  FROM task_features
  GROUP BY collection_id, tenant_id
)

SELECT * FROM collection_features
ORDER BY tenant_id, collection_id
```

**Output schema (one row per collection):**

| Field | Role |
|---|---|
| `collection_id`, `tenant_id` | Identifiers — used for model dispatch, not as numeric features |
| `scheduling_class`, `priority` | Input features |
| `req_cpu_per_task`, `req_mem_per_task` | Input features (declared demand, same for all tasks) |
| `num_tasks` | Input feature (collection size) |
| `avg_peak_mem_per_task` | **Training target — P_mem_j** |
| `avg_p90_cpu_per_task` | Training target — P_cpu_j (keep only if CPU prediction is pursued) |

> **Note on terminal event filter:** The SQL above uses `FINISH` only (suggested). Using `FINISH` gives the cleanest training target for peak memory prediction because the job ran its full course and the observed peak is representative of a normal run. Including `KILL` introduces downward bias — the job was cut short before reaching its natural peak. Including `EVICT` introduces upward noise — the cluster may have evicted the job precisely because it was consuming abnormally high resources. If more training data is needed, adding `KILL` is a reasonable compromise. Avoid `EVICT` for peak memory targets.

---

### Dataset 2 — Temporal Active-Collection Counts (for Plan-Ahead N_it)

**Purpose:** Estimate N_it — the expected number of active collections per tenant per planning slot. Along with mu_ijrt, this is one of two plan-ahead inputs that require a prediction model (both vary by time of day). Sigma_r and d_ijr are the static quantities that do not need prediction.

**Training granularity: start at 1 hour if data size permits; increase if needed (see granularity note below).** The prediction team must record the granularity they trained at and share it with the optimization team — the plan-ahead model needs this value to scale N_it correctly for its slot duration.

The prediction layer must expose a helper that accepts both the requested slot duration and the training granularity, and returns the correctly scaled value:

```python
def predict_N_it_for_slot(model, tenant_id, slot_start_hour, day_of_week,
                           slot_hours: int,
                           training_granularity_hours: int) -> int:
    """Return N_it scaled to slot_hours given a model trained at training_granularity_hours.

    Both teams must agree on training_granularity_hours — share it as a config value.

    slot_hours > training_granularity_hours  →  sum multiple predictions
      e.g. trained at 1h, slot = 4h: call model 4 times and sum

    slot_hours < training_granularity_hours  →  divide single prediction by ratio
      e.g. trained at 12h, slot = 4h: one model call, divide result by 3

    slot_hours == training_granularity_hours →  use model output directly
    """
    if slot_hours >= training_granularity_hours:
        # How many training windows fit in one plan-ahead slot?
        n_windows = slot_hours // training_granularity_hours
        total = 0
        for w in range(n_windows):
            offset_h = w * training_granularity_hours
            hour = (slot_start_hour + offset_h) % 24
            dow  = (day_of_week + (slot_start_hour + offset_h) // 24) % 7
            total += model.predict(tenant_id=tenant_id,
                                   hour_of_day=hour,
                                   day_of_week=dow)
        return max(1, round(total))
    else:
        # Training window is wider than the slot — divide
        ratio = training_granularity_hours / slot_hours   # e.g. 12h / 4h = 3
        raw = model.predict(tenant_id=tenant_id,
                            hour_of_day=slot_start_hour % 24,
                            day_of_week=day_of_week)
        return max(1, round(raw / ratio))
```

**Communication contract:** `training_granularity_hours` is a shared configuration value. The prediction team sets it when they decide their ETL granularity; the optimization team reads it to call `predict_N_it_for_slot()` correctly. Both teams must use the same value — store it in a shared config file or pass it explicitly at the interface boundary.

**What "active" means:** A collection is active at time T if at least one of its tasks has been scheduled and has not yet received a terminal event (FINISH, KILL, or EVICT). We snapshot this count at 1-hour intervals.

> **Granularity note:** The SQL below uses 1-hour slots (`I_us = 3600000000`). If the resulting dataset is too large to download, increase the slot duration — 2 hours, 4 hours, 1 day, 2 days, etc. Use whatever granularity keeps the data manageable. Record the granularity used and share it with the optimization team.
>
> The plan-ahead slot duration and the training granularity do not need to match exactly — the prediction can be rescaled:
> - **Plan-ahead slot > training granularity** → sum predictions across slots (e.g. trained at 1h, plan uses 4h: sum 4 × N_it(1h)). Already handled by `predict_N_it_for_slot()`.
> - **Plan-ahead slot = training granularity** → use prediction directly.
> - **Plan-ahead slot < training granularity** → divide by the ratio (e.g. trained at 4h, plan uses 1h: N_it(1h) = N_it(4h) / 4). This assumes collections are uniformly distributed within the training bucket — a simplification, but reasonable when finer data is unavailable.

```sql
-- Dataset 2: active collection count per tenant per 1-hour slot
-- (Train at 1-hour granularity; aggregate N predictions at inference time for a wider slot)
-- If dataset too large, increase I_us: 2h=7200000000, 4h=14400000000, 1d=86400000000

WITH constants AS (
  SELECT 3600000000 AS I_us   -- 1 hour in microseconds (adjust if dataset too large)
),

-- For each task, determine which planning slots it was alive during
-- A task is alive in slot s if: start_time < (s+1)*I and end_time > s*I
task_slots AS (
  SELECT
    ie_sched.collection_id,
    ie_sched.user                               AS tenant_id,
    ie_sched.time                               AS start_us,
    COALESCE(ie_end.time, /* trace_end */ 0)    AS end_us,
    -- Slot index = floor(start_us / I_us); task may span multiple slots
    DIV(ie_sched.time, (SELECT I_us FROM constants)) AS first_slot,
    DIV(COALESCE(ie_end.time, ie_sched.time),
        (SELECT I_us FROM constants))            AS last_slot
  FROM InstanceEvents ie_sched
  LEFT JOIN InstanceEvents ie_end
    ON  ie_end.collection_id  = ie_sched.collection_id
    AND ie_end.instance_index = ie_sched.instance_index
    AND ie_end.type           IN ('FINISH', 'KILL', 'EVICT')
  WHERE ie_sched.type = 'SCHEDULE'
    AND ie_sched.user IN (SELECT tenant_id FROM sampled_tenants)
),

-- For each (tenant, slot), count distinct active collections
n_it_raw AS (
  SELECT
    tenant_id,
    slot_index,
    COUNT(DISTINCT collection_id) AS active_collections
  FROM task_slots
  -- Expand: collection is active in every slot between first_slot and last_slot
  CROSS JOIN UNNEST(GENERATE_ARRAY(first_slot, last_slot)) AS slot_index
  GROUP BY tenant_id, slot_index
)

SELECT
  tenant_id,
  slot_index,
  active_collections                           AS N_it,
  EXTRACT(DAYOFWEEK FROM
    TIMESTAMP_MICROS(slot_index *
      (SELECT I_us FROM constants)))           AS day_of_week,
  EXTRACT(HOUR FROM
    TIMESTAMP_MICROS(slot_index *
      (SELECT I_us FROM constants)))           AS hour_of_day
FROM n_it_raw
WHERE tenant_id IN (SELECT tenant_id FROM sampled_tenants)
ORDER BY tenant_id, slot_index
```

**Output schema (one row per tenant × planning slot):**

| Field | Use |
|---|---|
| `tenant_id` | Tenant identifier |
| `slot_index` | Planning slot number (0 = first 4h of trace) |
| `N_it` | Number of active collections in that slot — the value the model predicts |
| `day_of_week`, `hour_of_day` | Input features for temporal pattern model |

**Model for N_it:** Baseline is a historical average per `(tenant, day_of_week, hour_of_day)` bucket from Dataset 2. The prediction team may choose to use simple regression, LSTM, or any other approach — the baseline lookup is the minimum viable starting point. The output is a count at the training granularity; use `predict_N_it_for_slot(slot_hours, training_granularity_hours)` to rescale to any plan-ahead slot width at inference time.

---

### Dataset 3 — Fallback Model Training Data (no tenant or collection IDs)

**Purpose:** Train the fallback predictor used when a job arrives from an unknown tenant or a new collection that has no history. The fallback model receives only generic request metadata and must still produce a reasonable peak-memory estimate.

Since there are no identifiers, this dataset is simply task_features (from Dataset 1 pre-processing) without the `collection_id` and `tenant_id` columns.

```sql
-- Dataset 3: fallback model (tenant-agnostic)
SELECT
  scheduling_class,
  priority,
  req_cpu,
  req_mem,
  task_peak_mem       -- ← target
FROM task_features
-- No tenant_id, no collection_id: model learns general req→usage relationship
```

**Model:** Random Forest on `[req_cpu, req_mem, scheduling_class, priority]` → `task_peak_mem`. No LSTM needed (no sequence, no identifiers). This is the simplest possible model and serves as the cold-start fallback.

---

### Dataset 4 — Temporal Fallback for N_it (no tenant ID)

**Purpose:** Provide a baseline N_it estimate for a new tenant with no submission history. Without this, the plan-ahead model has no workload-count estimate for an unknown tenant and either rejects them at admission or requires a hardcoded default. Dataset 4 gives a data-driven fallback: the average active-collection count per `(hour_of_day, day_of_week)` bucket across all sampled tenants.

This is the temporal analogue of Dataset 3 — strip the identifier and average across the population.

```sql
-- Dataset 4: tenant-agnostic N_it fallback
-- Reuses n_it_raw from Dataset 2 (active collection count per tenant per 1-hour slot)

SELECT
  day_of_week,
  hour_of_day,
  AVG(N_it)    AS avg_active_collections,   -- ← fallback N_it for unknown tenant
  STDDEV(N_it) AS stddev_active_collections  -- optional: uncertainty bound
FROM n_it_raw
-- No tenant_id grouping: average across all sampled tenants
GROUP BY day_of_week, hour_of_day
ORDER BY day_of_week, hour_of_day
```

**Output schema (one row per hour × day):**

| Field | Use |
|---|---|
| `day_of_week`, `hour_of_day` | Time bucket — matched to each future planning slot |
| `avg_active_collections` | Fallback N_it when tenant has no history |
| `stddev_active_collections` | Optional: signals how much variance to expect across tenants |

**Usage in the prediction layer:**
```python
def predict_N_it(model, tenant_id, hour_of_day, day_of_week):
    if tenant_id is known and has sufficient history:
        return tenant_model.predict(tenant_id, hour_of_day, day_of_week)
    else:
        return fallback_lookup(hour_of_day, day_of_week)  # Dataset 4 average
```

**Note on the cross-tenant average:** If sampled tenants are unbalanced (a few heavy users dominate), the mean will be skewed upward. Consider using the median or segmenting by tenant activity tier as a refinement. Mean is sufficient as a first pass.

---

### Train / Test Split

Do **not** use a 1% random sample of the full dataset as a test set. Random sampling from across the full time window can put data from the same collection into both train and test, making the model look better than it will be in deployment.

**Recommended approach — time-based split:**

```
Trace window: day 0 → day 28

  Train: day 0 → day 22  (≈ 80 %)
  Test:  day 23 → day 28 (≈ 20 %)
```

Train on older data, evaluate on newer data. This simulates real deployment: the model learns from historical patterns and is evaluated on a future it has not seen. Any collection that appears only in the test window is effectively a cold-start case — the fallback model handles it.

**Alternative — collection-based split:**

Assign each collection_id to either train (80%) or test (20%) at random before extracting features. This tests how well the model generalises to entirely new collections (the more conservative evaluation).

Either split is acceptable. Time-based split is preferred because it directly mirrors the deployment scenario.

---

### Evaluation: Compare Against Existing Baseline

The prediction team presented an LSTM baseline on May 11 (`prediction_team_slides_LSTM_baseline_May11.pptx`). After implementing the collection-level pipeline described in this document, run both approaches on the **same test set** and compare:

| Metric | Baseline (instance-level LSTM) | This approach (collection-level RF/LSTM) |
|---|---|---|
| Memory RMSE | — | — |
| Memory R² | — | — |
| % OOM-safe predictions (peak_mem_predicted ≥ actual peak) | — | — |
| Inference latency (ms per job) | — | — |

**Suggested output:** Plot predicted vs actual peak memory for both models on the test set (scatter plot, one point per collection). The collection-level model should have lower variance because the training target is already an average, not a single noisy instance observation.

If the baseline LSTM achieves better R² and fewer OOM-risk predictions, that result is also valid — document it and explain why (e.g., the LSTM captures temporal correlations within a job's task sequence that the collection-level aggregation loses). Science is the goal, not confirming this document's recommendations.

---
## Prediction Team: What You Need to Build

This is the explicit, concise version of everything the prediction team needs to deliver. Read this section first.

### Required outputs — Real-Time Optimizer

When a new task (pod) arrives at the scheduler, the real-time model calls the prediction layer:

```
predict(tenant_id, collection_id, req_cpu, req_mem, scheduling_class, priority)
   → (P_mem_j, P_cpu_j)
```

| Output | Meaning | How to produce |
|---|---|---|
| `P_mem_j` | Expected peak memory for this task | `avg(max_mem per task)` from collection-level model, in NCU |
| `P_cpu_j` | Expected P90 CPU for this task | `avg(p90_cpu per task)` from collection-level model, in NCU |

**Model type:** LSTM (primary) or Random Forest on collection-level features. For batch jobs with no temporal sequence needed at inference time, RF can serve as a fast baseline or fallback.



### Data to download (four queries — see Part 15)

1. **Dataset 1** (collection-level resource features): trains the memory prediction model → P_mem_j.
2. **Dataset 2** (temporal active-collection counts): trains the N_it predictor for known tenants.
3. **Dataset 3** (fallback, no IDs): trains the cold-start predictor for unknown tenant/collection → P_mem_j fallback.
4. **Dataset 4** (temporal fallback, no tenant ID): provides N_it baseline for unknown tenants → plan-ahead fallback.

Run the Pre-ETL sampling CTEs first; Datasets 1–3 reference the same `sampled_collections` scope. Dataset 4 is derived from Dataset 2's `n_it_raw` CTE — no additional data download needed, just an extra aggregation.

---

## Pipeline Flow Chart — Inputs and Outputs Across All Stages

```
╔══════════════════════════════════════════════════════════════════════════════════╗
║  DATA SOURCE                                                                    ║
║  Google Cluster Trace v3  (instance_usage · instance_events · collection_events)║
║  Cell A · sample · batch tiers (priority ≤ 119) · 5-min windows               ║
╚══════════════════════════════════════════════════════════════════════════════════╝
       │                                          │
       │  per-workload aggregated stats            │  per-tenant temporal aggregates
       │  (Dataset 1: collection-level)            │  (Dataset 2: 4-hour buckets)
       ▼                                          ▼
╔══════════════════════════════════════════════════════════════════════════════════╗
║  PREDICTION LAYER                                                               ║
║                                                                                 ║
║  ┌──────────────────────────────┐   ┌───────────────────────────────────────┐  ║
║  │ [1] COLLECTION-LEVEL MODEL   │   │ [2] PLAN-AHEAD PREDICTION PIPELINE    │  ║
║  │     RF/LSTM                  │   │     per tenant · 1-hour grain         │  ║
║  │     (one row per workload)   │   │                                       │  ║
║  │                              │   │  PREDICTED per future slot:           │  ║
║  │  IN:  req_cpu, req_mem,      │   │  - mu_ijrt ────────────────────────►  ║
║  │       tenant_id,             │   │    (mean usage; model inputs:         │  ║
║  │       scheduling_class,      │   │     coll_id, hour, day_of_week)       │  ║
║  │       priority, num_tasks    │   │  - N_it ───────────────────────────►  ║
║  │                              │   │    (workload count per slot)          │  ║
║  │  OUT per job:                │   │                                       │  ║
║  │  - P_mem_j ──► Real-Time     │   │  STATIC (computed once):              ║
║  │  - P_cpu_j ──► Real-Time     │   │  - Sigma_r ─────────────────────────► ║
║  └──────────────────────────────┘   │  - d_ijr (from trace) ──────────────► ║
║                                     └───────────────────────────────────────┘  ║
╚══════════════════════════════════════════════════════════════════════════════════╝
       │                                          │
       │  P_mem_j, P_cpu_j                        │  mu_ijrt, N_it (predicted)
       │  (per pending job)                        │  Sigma_r, d_ijr (static)
       │                                          ▼
       │                        ╔════════════════════════════════════╗
       │                        ║  PLAN-AHEAD MODEL (MISOCP)         ║
       │                        ║  runs once per planning horizon     ║
       │                        ║                                    ║
       │                        ║  ALSO IN (operator config):        ║
       │                        ║    C_nr  (node capacities)         ║
       │                        ║    Q_quota_ir (tenant quotas)      ║
       │                        ║    eps_i (SLA risk tolerance)      ║
       │                        ║    L_iq  (latency SLA targets)     ║
       │                        ║    pi_n, pi_bar_i (pricing)        ║
       │                        ║    isolation & migration params    ║
       │                        ║                                    ║
       │                        ║  OUT:  A_t_i                       ║
       │                        ║  (authorized node set per tenant   ║
       │                        ║   per time slot)                   ║
       │                        ╚══════════════╤═════════════════════╝
       │                                       │  A_t_i
       ▼                                       ▼
╔══════════════════════════════════════════════════════════════════════════════════╗
║  REAL-TIME MODEL (MILP)                                                         ║
║  runs every ~60 seconds                                                         ║
║                                                                                 ║
║  IN from prediction:   P_mem_j, P_cpu_j (per pending job)                      ║
║  IN from plan-ahead:   A_t_i (authorized node sets for current time slot)       ║
║  IN from monitor:      U_mem_n (current memory usage per node)                  ║
║                        vbar_n  (SLA violation rate per node, K-window)          ║
║                        omega_delay_t (fairness weight per tenant, K-window)     ║
║                                                                                 ║
║  OUT:  x_jn  — job-to-node assignment matrix (placement decision)               ║
╚══════════════════════════════════════════════════════════════════════════════════╝
       │
       │  x_jn  (placement decisions executed by cluster)
       ▼
╔══════════════════════════════════════════════════════════════════════════════════╗
║  CLUSTER MONITOR (feedback loop)                                                ║
║  Observes runtime outcomes → updates U_mem_n, vbar_n, omega_delay_t            ║
║  Feeds updated values back into Real-Time Model on the next round               ║
╚══════════════════════════════════════════════════════════════════════════════════╝
```

**Key:** The Collection-Level Model [1] produces P_mem_j and P_cpu_j for the real-time optimizer. The Plan-Ahead Prediction Pipeline [2] produces four inputs for the plan-ahead MISOCP: mu_ijrt and N_it are **predicted** per future slot (both require a model); Sigma_r and d_ijr are **computed once** from historical trace (static). The Real-Time MILP uses both pipelines' outputs every ~60 seconds.
