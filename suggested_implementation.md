# Suggested Implementation Guide
## Optimal Shared Memory Utilization with Service Level Guarantees in Multi-Tenant Clusters

---

## System Architecture Overview

The framework has four layers that execute in sequence per simulation tick:

```
[Google Cluster Trace v3]
        |
        v
[Data Pipeline] --> cleaned, normalized per-tenant time series
        |
        v
[Prediction Layer]
   LSTM Regressor  --> P95 memory demand per pod/tenant
   RF Classifier   --> job profile class per pod
        |
        v
[Optimization Layer] --> pod-to-node assignment decisions
   Multi-objective ILP / Greedy Heuristic
        |
        v
[Discrete-Event Simulation] --> replays trace, applies assignments,
   SimPy-based             tracks utilization, SLA violations, fairness
        |
        v
[Evaluation and Visualization]
   vs. K8s default baseline and DRF baseline
```

Each layer is independently testable. The prediction layer can be stubbed while the optimizer and simulation are developed, and vice versa.

---

## Phase 1: Data Pipeline

### Source
Google Cluster Trace v3 (publicly available on Google Cloud Storage). Contains approximately 2.6 billion task events from a production Borg cluster over approximately 31 days.

### Key Tables
- `instance_events`: job/task lifecycle events (submit, schedule, evict, finish)
- `instance_usage`: sampled CPU and memory usage per task at 5-minute intervals
- `collection_events`: job-level metadata including priority and tenant identifier (collection_id as proxy)

### Steps
1. Download a subset (first 7 days is sufficient for training) using the BigQuery public dataset or direct GCS download.
2. Join `instance_events` with `instance_usage` on (collection_id, instance_index).
3. Extract per-task features: submission timestamp, declared CPU request, declared memory request, actual peak CPU usage, actual peak memory usage, job duration, priority class, tenant identifier (collection_id).
4. Filter out records with null actual usage (these are jobs that never ran long enough to produce a usage sample).
5. Compute the declared-versus-actual memory ratio per task. This ratio is the core signal the prediction layer exploits.
6. Aggregate to per-tenant time series at 5-minute resolution for LSTM training.
7. Apply Savitzky-Golay smoothing (window=11, polynomial order=3) followed by min-max normalization per tenant, following Kofi (2025).
8. Split: first 21 days for training, days 22-28 for validation, days 29-31 for test/simulation replay.

### Platform
Google Colab (free tier is sufficient for data preprocessing). Use pandas and BigQuery Python client. For the 31-day full trace, Google Cloud free credits are enough since queries are read-only.

### Output Artifacts
- `processed_trace.parquet`: cleaned, joined, feature-engineered table
- `tenant_timeseries.parquet`: per-tenant aggregated time series for LSTM
- `job_features.parquet`: per-job feature matrix for RF classifier
- `scaler_params.json`: min-max scaler parameters per tenant (needed at inference time)

---

## Phase 2: Prediction Layer

### 2.1 LSTM Regressor (Temporal Memory Demand Forecasting)

**Goal:** For each tenant, predict a distribution of memory usage over the next scheduling window (e.g., 5 minutes ahead). From this distribution, extract the P95 value as the safe-overcommitment estimate.

**Why LSTM:** Tenant workload patterns are temporal and exhibit autocorrelation. Kofi (2025) achieved R² = 0.99 on Google Cluster Trace v3 with an LSTM, outperforming SVM and SATCN. The LSTM's gated memory cells capture both short-term spikes and longer recurring patterns.

**Preprocessing steps (from Kofi 2025):**
1. Savitzky-Golay smoothing on raw usage time series (removes noise while preserving spike shapes)
2. Min-max normalization per tenant (scale usage to [0,1])
3. Sliding window construction: input = 12 time steps (60 minutes of history), output = 1 time step ahead

**Model structure (suggested):**
- Input layer: shape (12, num_features) where features = [avg_cpu_usage, avg_mem_usage, num_tasks_running, num_tasks_pending]
- LSTM layer 1: 64 units, return_sequences=True
- Dropout: 0.2
- LSTM layer 2: 32 units
- Dense output: 1 unit (predicted normalized memory usage)

**Training:**
- Loss: Mean Squared Error (MSE)
- Optimizer: Adam, learning rate 0.001
- Epochs: 50 with early stopping (patience=5 on validation MSE)
- Batch size: 32

**P95 Derivation:**
The LSTM predicts a point estimate. To get a distribution, use Monte Carlo Dropout: run the model in inference mode with dropout enabled 100 times per input window. The result is 100 predictions for each time step. Take the 95th percentile of this set as the P95 estimate. This is a lightweight uncertainty quantification method that does not require retraining.

**Alternatively (simpler):** Train the LSTM normally, then compute the residuals on the validation set. Fit a Gaussian to the residuals. At inference time, P95 = point_prediction + 1.645 * residual_std.

**Evaluation metrics:** R², MAE, MAPE. Target: MAPE < 5% following Doukha and Ez-zahout (2025).

**Platform:** Google Colab with GPU runtime (T4 GPU). Training should complete in under 30 minutes per tenant model on 21 days of data.

**People needed:** 1 person. LSTM implementation is straightforward with Keras. Main effort is data preprocessing and P95 derivation.

**Python libraries:** TensorFlow 2.x / Keras, numpy, pandas, scikit-learn (for scaler), scipy (for Savitzky-Golay).

---

### 2.2 Random Forest Classifier (Job Profile Classification)

**Goal:** Assign each incoming job to a resource consumption profile class before it executes. These classes inform the optimizer which memory constraint tier applies to that job.

**Why RF:** Doukha and Ez-zahout (2025) showed RF achieves MAPE of 2.65% versus 17.43% for LSTM on static point predictions. For classification of job types (a static, non-temporal task), RF provides higher accuracy and is interpretable (feature importances show which job characteristics matter most).

**Class definitions (suggested 3-class scheme):**
- Class 0 (Low): actual peak memory < 30% of declared request
- Class 1 (Medium): actual peak memory 30–70% of declared request
- Class 2 (High): actual peak memory > 70% of declared request

These thresholds should be calibrated from the training split of the trace.

**Features per job:**
- Declared memory request
- Declared CPU request
- Job priority class (from trace)
- Tenant historical average memory ratio (mean actual/declared for that tenant over last 7 days)
- Tenant historical std dev of memory ratio
- Time of day (hour) when job was submitted
- Job duration estimate (from historical average for that tenant's job type)

**Training:**
- 200 estimators, max_depth=10, class_weight='balanced' (to handle class imbalance)
- 5-fold cross-validation on training split
- Evaluation: per-class precision, recall, F1. MAPE on predicted vs actual memory usage.

**Integration with optimizer:**
The class label maps to a memory multiplier applied to the declared request when the LSTM P95 is not yet available for a new tenant. Class 0 jobs get multiplier 0.3, Class 1 get 0.55, Class 2 get 0.85.

**Platform:** Google Colab (CPU only, no GPU needed). Training completes in seconds to minutes.

**People needed:** 1 person (can be the same person as LSTM if comfortable, or a different person who focuses on feature engineering).

**Python libraries:** scikit-learn, pandas, numpy, matplotlib (for feature importance plots).

---

### 2.3 Prediction Layer Output Contract

The prediction layer exposes a single interface to the optimizer. For each pod j arriving at time t, it returns:

```
{
  "pod_id": j,
  "tenant_id": k,
  "p95_memory_mb": float,       # P95 predicted memory from LSTM (or RF-based estimate)
  "p95_cpu_cores": float,       # P95 predicted CPU (same method)
  "job_class": int,             # 0, 1, or 2 from RF classifier
  "prediction_source": str      # "lstm" | "rf_stub" | "declared_discount"
}
```

The `prediction_source` field enables the simulation to flag which predictions came from the full model versus a stub, useful for ablation analysis.

---

## Phase 3: What to Do When the Prediction Model is Not Available Yet

During early development, the optimizer and simulation can be built and tested independently using a stub prediction layer. Two stub modes:

**Stub Mode A: Declared Discount**
P95 memory = declared_memory_request * 0.60

This reflects the well-documented ~40% over-declaration gap in production clusters (Chaudhari, 2025). It is directionally correct even if not calibrated to the specific trace.

**Stub Mode B: RF Class-Based Multiplier**
If the RF classifier is trained but the LSTM is not yet ready:
- Class 0: P95 = declared * 0.30
- Class 1: P95 = declared * 0.55
- Class 2: P95 = declared * 0.85

**Implementation approach:**
Write a `PredictionInterface` abstract class with a `predict(pod_metadata) -> PredictionResult` method. Implement a `StubPredictor` and a `LivePredictor` that both conform to this interface. The simulation takes a `PredictionInterface` instance at initialization. Swap stubs for live models without changing simulation code.

---

## Phase 4: Optimization Layer

### Mathematical Formulation

---

#### Sets and Indices

Let N = {1, 2, ..., n} be the set of cluster nodes.
Let J = {1, 2, ..., m} be the set of pods in the current scheduling batch.
Let K = {1, 2, ..., k} be the set of tenants.
Let J_k ⊆ J be the set of pods belonging to tenant k.

---

#### Decision Variables

x_{j,i} ∈ {0, 1}     for all j ∈ J, i ∈ N

x_{j,i} = 1 if pod j is assigned to node i in this scheduling round; 0 otherwise.

---

#### Parameters (inputs from prediction layer and simulation state)

m̂_j   : P95 predicted memory demand for pod j (in MB), from prediction layer

ĉ_j   : P95 predicted CPU demand for pod j (in cores)

M_i   : total available memory capacity of node i (in MB)

C_i   : total available CPU capacity of node i (in cores)

α     : memory overcommitment factor (e.g., α = 1.2 means nodes can be assigned up to 120% of their declared memory capacity before triggering SLA concern). Tunable.

sub_j : submission timestamp of pod j

t_now : current simulation time step

D_k   : current running average scheduling delay for tenant k, tracked across all prior batches. Initialized to 0.

D̄     : cluster-wide mean scheduling delay = (1/K) Σ_k D_k

w₁, w₂, w₃ : non-negative scalar weights for the three objectives. Must sum to 1. Default: w₁ = 0.5, w₂ = 0.3, w₃ = 0.2. These are configurable parameters for the simulation experiments.

---

#### Objective Function

The framework maximizes a weighted composite score:

    Maximize F = w₁ · U_mem(x) - w₂ · V_sla(x) - w₃ · F_fair(x)

where the three terms are defined as follows.

---

**Term 1: Memory Utilization U_mem(x)**

    U_mem(x) = (1/n) · Σ_{i ∈ N} [ Σ_{j ∈ J} x_{j,i} · m̂_j ] / M_i

This is the average fraction of each node's memory that is consumed by the assigned pods. Higher is better. Bounded in [0, α] given the overcommitment constraint.

---

**Term 2: SLA Violation Penalty V_sla(x)**

Define node i as overloaded under assignment x if:

    Σ_{j ∈ J} x_{j,i} · m̂_j > M_i

The SLA violation score for this batch is the fraction of nodes that are overloaded:

    V_sla(x) = (1/n) · Σ_{i ∈ N} 𝟙[ Σ_{j ∈ J} x_{j,i} · m̂_j > M_i ]

where 𝟙[·] is the indicator function (1 if the condition holds, 0 otherwise).

Note: 𝟙[·] is non-linear and makes this a mixed-integer problem. For the ILP solver, linearize using a big-M formulation. Introduce binary slack variables s_i ∈ {0,1}:

    Σ_{j ∈ J} x_{j,i} · m̂_j ≤ M_i + M_i · s_i     for all i ∈ N
    V_sla(x) = (1/n) · Σ_{i ∈ N} s_i

Cumulative SLA tracking: across the simulation, maintain V_sla_total = (total overloaded node-time-steps) / (total node-time-steps). This is the uptime SLA metric reported in results.

---

**Term 3: Fairness Penalty F_fair(x)**

After this batch is assigned, the projected scheduling delay for each pod j is:

    delay_j(x) = (t_now - sub_j) · Σ_{i ∈ N} x_{j,i}    if assigned (delay measured at assignment)
               = (t_now - sub_j) + Δt                      if unassigned (delay increases by one tick Δt)

For each tenant k, the updated average delay after this batch:

    D_k(x) = [ D_k · |prev_assigned_k| + Σ_{j ∈ J_k, assigned} delay_j(x) ] / |assigned_k_total|

where |prev_assigned_k| is the count of historically assigned pods for tenant k.

The updated cluster mean:

    D̄(x) = (1/K) · Σ_k D_k(x)

Fairness penalty (variance of per-tenant average delay):

    F_fair(x) = (1/K) · Σ_{k ∈ K} ( D_k(x) - D̄(x) )²

Note: F_fair depends on D_k(x) which is not linear in x. For the ILP formulation, approximate D_k(x) by treating delays as known constants from prior state and updating them after the solve. This is a sequential approximation: solve the ILP with w₃ penalizing assignments that would increase delay for already-disadvantaged tenants, then update D_k state.

A practical linear approximation for F_fair during solving:
Assign a priority score p_k to each tenant based on current relative delay:

    p_k = D_k / D̄    (ratio of tenant delay to cluster mean)

A tenant with p_k > 1 is disadvantaged. Add to the objective a bonus for assigning pods from high-p_k tenants:

    F_fair_approx(x) = (1/|J|) · Σ_{j ∈ J} Σ_{i ∈ N} x_{j,i} · p_{tenant(j)}

This is linear in x and maximizes scheduling of delayed tenants first, approximating the variance minimization goal.

---

#### Constraints

**C1: Each pod assigned to at most one node:**

    Σ_{i ∈ N} x_{j,i} ≤ 1     for all j ∈ J

(≤ 1 rather than = 1 allows pods to remain unscheduled in the current batch if no node fits)

**C2: CPU hard constraint per node:**

    Σ_{j ∈ J} x_{j,i} · ĉ_j ≤ C_i     for all i ∈ N

CPU is a hard constraint. No overcommitment on CPU.

**C3: Memory overcommitment bound (soft upper limit):**

    Σ_{j ∈ J} x_{j,i} · m̂_j ≤ α · M_i     for all i ∈ N

This prevents extreme overloading. The SLA violation slack variable s_i tracks overloading within [M_i, α · M_i]. Pods that would require assigning beyond α · M_i are deferred to the next batch.

**C4: Binary constraint:**

    x_{j,i} ∈ {0, 1}     for all j ∈ J, i ∈ N

---

#### Complete ILP Formulation (Linearized)

    Maximize:
        w₁ · (1/n) · Σ_i [ Σ_j x_{j,i} · m̂_j ] / M_i
      - w₂ · (1/n) · Σ_i s_i
      + w₃ · (1/|J|) · Σ_j Σ_i x_{j,i} · p_{tenant(j)}

    Subject to:
        Σ_i x_{j,i} ≤ 1                                      for all j ∈ J
        Σ_j x_{j,i} · ĉ_j ≤ C_i                             for all i ∈ N
        Σ_j x_{j,i} · m̂_j ≤ α · M_i                         for all i ∈ N
        Σ_j x_{j,i} · m̂_j ≤ M_i + M_i · s_i                for all i ∈ N
        x_{j,i} ∈ {0, 1}                                     for all j, i
        s_i ∈ {0, 1}                                          for all i ∈ N

---

#### Solving the ILP

**Recommended solver:** PuLP with CBC backend (free, Python-native).

For batches up to ~200 pods and ~20 nodes, CBC solves this in under 1 second per batch. Larger batches (500+ pods) may require the greedy heuristic.

**OR-Tools CP-SAT** (Google) is a stronger alternative for larger instances and has native Python bindings.

---

#### Greedy Heuristic (for large batches or time-constrained simulation)

When the ILP takes too long, use this priority-based greedy:

Step 1: Sort pods in descending order of tenant priority score p_{tenant(j)}. Within the same tenant, sort by submission time (oldest first, FIFO).

Step 2: For each pod j in sorted order:
    a. Score each node i:
           score(i,j) = w₁ · (mem_after_assign / M_i)
                      - w₂ · (1 if mem_after_assign > M_i else 0)
       where mem_after_assign = current_assigned_memory(i) + m̂_j

    b. Filter to nodes where:
           current_assigned_cpu(i) + ĉ_j ≤ C_i           (CPU constraint)
           current_assigned_memory(i) + m̂_j ≤ α · M_i    (overcommitment bound)

    c. If at least one node passes the filter, assign j to the highest-score node.
       If no node passes, defer pod j to the next batch.

Step 3: Update running per-node assigned memory and CPU.

Step 4: After all pods processed, update per-tenant average delay D_k.

---

#### State Variables Tracked Across Batches

- `node_memory_used[i]`: total P95 memory currently assigned to node i (across all live pods)
- `node_cpu_used[i]`: total P95 CPU currently assigned to node i
- `tenant_avg_delay[k]`: running average scheduling delay in seconds for tenant k
- `tenant_pods_scheduled[k]`: count of pods successfully scheduled for tenant k
- `sla_violation_ticks[i]`: count of simulation ticks where node i was in overloaded state
- `total_ticks`: total simulation ticks elapsed

---

#### Derived Metrics at Evaluation Time

**Memory utilization:**
    U = (1 / (n · T)) · Σ_i Σ_t [ node_memory_used[i,t] / M_i ]

**SLA compliance rate:**
    SLA_rate = 1 - [ Σ_i sla_violation_ticks[i] ] / [ n · total_ticks ]

**Fairness (variance of per-tenant average delay):**
    F_variance = (1/K) · Σ_k ( tenant_avg_delay[k] - mean(tenant_avg_delay) )²

Lower F_variance = more fair.

**Prediction MAPE:**
    MAPE = (1/|J|) · Σ_j | (actual_peak_j - m̂_j) / actual_peak_j | · 100

---

## Phase 5: Simulation Design

### Framework
Use SimPy (Python discrete-event simulation library). SimPy models time-advancing events with generators and a shared environment clock.

### Simulation State
- Cluster: n nodes, each with M_i memory and C_i CPU
- Event queue: pod submission events loaded from trace, ordered by timestamp
- Pod lifecycle: SUBMITTED → PENDING → SCHEDULED → RUNNING → COMPLETE (or EVICTED if OOM)
- Scheduler trigger: fires every Δt seconds of simulated time (e.g., Δt = 300 seconds = 5 minutes)

### Simulation Loop
1. Advance simulation clock to next scheduler tick.
2. Collect all pods in PENDING state.
3. Call prediction layer to get P95 estimates for each pending pod.
4. Run optimizer (ILP or greedy) on current pending pods and current node state.
5. Apply assignments: move assigned pods to RUNNING, update node resource tracking.
6. Check SLA: for each node, if total assigned P95 memory > M_i, increment sla_violation_ticks[i].
7. Process completions: pods that have passed their expected duration are removed; node resources freed.
8. Log metrics: utilization, per-tenant delay, SLA state.
9. Advance to next tick.

### Baselines
Run three configurations of the same simulation loop:

**Baseline A (Default K8s):**
Prediction layer replaced with declared values (no discount). Optimizer replaced with first-fit: assign pod to the first node where declared_memory + current_used ≤ M_i. No fairness consideration.

**Baseline B (DRF):**
Prediction layer replaced with declared values. Optimizer uses dominant resource fairness: allocate to tenant with lowest dominant share. No memory overcommitment.

**Framework (Proposed):**
Full prediction layer + ILP/greedy optimizer with three-objective function.

### Synthetic Workload Scenarios
In addition to trace replay, generate two synthetic scenarios:

Scenario 1 (Memory spike storm): all tenants submit large memory jobs simultaneously. Tests SLA violation handling and fairness under contention.

Scenario 2 (Tenant monopoly): one tenant submits 10x the normal job volume. Tests whether the fairness objective prevents crowding out of smaller tenants.

Synthetic jobs are parameterized by: declared_memory, actual_peak_memory (sampled from log-normal with mean = 0.6 * declared), duration, tenant_id, submission_time.

---

## Phase 6: Testing Strategy

### Unit Tests
- P95 calculation: given mock LSTM output distribution, verify P95 is the correct percentile
- SLA indicator: given node memory state and pod assignment, verify overload detection
- Fairness computation: given per-tenant delay arrays, verify variance calculation
- ILP solver: given a 3-pod, 2-node toy problem with known optimal solution, verify solver returns it
- Greedy heuristic: same toy problem, verify greedy returns a valid (if not necessarily optimal) solution

### Integration Tests
- Stub predictor → optimizer → simulation: run 100 ticks on synthetic data, verify no crashes and metrics are in plausible ranges
- Live predictor → optimizer: verify output contract shape matches what optimizer expects

### Validation
- LSTM: plot actual vs predicted memory on held-out test days; compute MAPE
- RF classifier: confusion matrix on held-out test set; per-class F1
- Simulation: on Baseline A, verify average memory utilization is in the 40-60% range documented by Chaudhari (2025) and Delimitrou & Kozyrakis (2014) — this confirms the simulation is correctly reproducing the known problem state before applying the framework

### Ablation Studies
Run four simulation configurations and compare:
1. Full framework (prediction + fairness + SLA)
2. Prediction only, no fairness objective (w₃ = 0)
3. Fairness only, no prediction (stub predictor)
4. Baseline A (default K8s)

The difference between configurations 3 and 4 isolates the prediction contribution. The difference between 2 and 1 isolates the fairness contribution.

---

## Phase 7: How to Wire Prediction Outputs Into the Optimizer

The prediction layer runs as a preprocessing step before each scheduling tick. The contract is:

Input to prediction layer:
- List of pending pods: [(pod_id, tenant_id, declared_memory_mb, declared_cpu_cores, submit_time, tenant_recent_usage_series), ...]

Output from prediction layer (one record per pod):
- [(pod_id, p95_memory_mb, p95_cpu_cores, job_class, prediction_source), ...]

This list is passed directly into the optimizer as the parameters m̂_j and ĉ_j. The optimizer does not need to know whether values came from LSTM, RF stub, or declared discount — the prediction_source flag is only used for post-hoc analysis.

For tenant_recent_usage_series: the simulation maintains a rolling 12-step usage buffer per tenant (updated every tick from pod completions and running pod usage samples). This buffer is passed to the LSTM at each inference call.

---

## People Allocation Summary

| Task | Effort | Suggested Assignee Count |
|---|---|---|
| Data pipeline (download, clean, feature engineer) | Medium | 1 person |
| LSTM regressor (training, P95 derivation, evaluation) | High | 1 person |
| RF classifier (training, feature engineering, evaluation) | Low-Medium | 1 person (can share with data pipeline) |
| Optimization formulation + ILP/greedy implementation | High | 1 person |
| SimPy simulation framework + baseline implementations | High | 1 person |
| Integration, testing, evaluation, visualization | Medium | All members |

For a 5-person team: assign data pipeline + RF to one person, LSTM to one person, optimization to one person, simulation to one person, integration/evaluation/report to the fifth person who also supports others as needed.

---

## Suggested Python Stack

| Component | Library |
|---|---|
| Data pipeline | pandas, pyarrow, google-cloud-bigquery |
| LSTM | TensorFlow 2.x / Keras |
| RF classifier | scikit-learn |
| Savitzky-Golay | scipy.signal.savgol_filter |
| ILP solver | PuLP (CBC backend) |
| Backup solver | OR-Tools (ortools Python package) |
| Simulation | SimPy |
| Visualization | matplotlib, seaborn, plotly |
| Testing | pytest |
| Platform (training) | Google Colab (GPU runtime) |
| Platform (simulation) | Local Python or Colab CPU |
