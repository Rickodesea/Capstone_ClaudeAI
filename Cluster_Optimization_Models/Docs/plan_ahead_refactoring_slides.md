---
marp: true
theme: default
paginate: true
math: katex
style: |
  section {
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: 22px;
    padding: 40px 60px;
  }
  section.lead {
    text-align: center;
    justify-content: center;
  }
  section.lead h1 { font-size: 44px; }
  section.lead h2 { font-size: 28px; color: #555; }
  h1 { color: #1a3a5c; border-bottom: 2px solid #1a3a5c; padding-bottom: 6px; }
  h2 { color: #2a5a8c; }
  h3 { color: #3a7aac; }
  table { font-size: 18px; border-collapse: collapse; width: 100%; }
  th { background: #1a3a5c; color: white; padding: 8px 12px; }
  td { padding: 6px 12px; border-bottom: 1px solid #ddd; }
  tr:nth-child(even) { background: #f0f4f8; }
  code { background: #f0f4f8; padding: 2px 6px; border-radius: 3px; font-size: 18px; }
  blockquote { border-left: 4px solid #2a5a8c; margin: 10px 0; padding: 6px 18px; background: #f0f6ff; color: #333; }
  .tag-green { background: #e6f4ea; color: #1e7e34; padding: 2px 10px; border-radius: 12px; font-size: 17px; }
  .tag-red   { background: #fce8e6; color: #c62828; padding: 2px 10px; border-radius: 12px; font-size: 17px; }
  .tag-blue  { background: #e8f0fe; color: #1a56db; padding: 2px 10px; border-radius: 12px; font-size: 17px; }
  ul { line-height: 1.7; }
  li { margin-bottom: 4px; }
---

<!-- _class: lead -->

# Plan-Ahead Model Refactoring
## Justification, Design Decisions & Optimization Layer Status

**Alrick Grandison** — Capstone Project, Spring 2026

---

## Agenda

**Part 1 — Plan-Ahead Refactoring**
1. What the original model was
2. Problems discovered during simulation
3. Change 1: Workloads → Tenant Usage Profiles
4. Change 2: Hard Access → Priority Hints
5. Change 3: SOCP uncertainty input redesigned
6. What was removed (migration, isolation, McCormick)
7. Complexity comparison — model remains MISOCP

**Part 2 — Optimization Layer Status**

8. System architecture
9. Plan-Ahead: current state
10. Real-Time: current state
11. Pipeline: how the layers connect
12. Simulation: current state
13. What's next

---

<!-- _class: lead -->

# Part 1
## Plan-Ahead Model Refactoring

---

## The Original Model

The original plan-ahead was a **Mixed-Integer Second-Order Cone Program (MISOCP)** with:

| Component | Description |
|---|---|
| **Scheduling unit** | Individual workloads $x_{i,j,n,t} \in \{0,1\}$ — per-job placement |
| **Capacity constraint** | Cantelli SOCP on per-workload covariance $\Sigma_r$ (Cholesky decomposition) |
| **Access control** | Hard block: $x_{j,n} = 0$ if $n \notin A_{t(j)}$ |
| **Isolation** | gVisor / Kata selection $w_{i,j,k,t}$ with McCormick linearization |
| **Migration** | Explicit migration indicators $m_{i,j,n,t}$ across periods |
| **Fairness** | DRF max-min on declared demand $d_{i,j,r}$ |

> The model was mathematically complete on paper, but its inputs required data that **cannot be known at planning time**.

---

## Problems Found During Simulation

Three categories of failure emerged from simulation testing:

**1. Infeasible input requirements**
The SOCP cone required a Cholesky decomposition of $\Sigma_r$ — the joint covariance matrix of *all individual workloads* across the horizon. This is unknowable in advance.

**2. Feasibility failures → hard access control**
With $x_{j,n} = 0$ outside the plan-ahead set, jobs were blocked from idle nodes while their "allowed" nodes were saturated.
- Little's Law: blocking a job from 4 of 5 nodes multiplies expected wait time proportionally
- Simulation confirmed: queue backlogs persisted even with idle capacity available

**3. Over-engineered variables**
Migration and isolation-primitive variables each scale as $O(T \cdot W \cdot N \cdot K \cdot H)$. They cannot be meaningfully set at planning granularity — these are real-time decisions.

> **Key insight:** the original design modelled the *wrong level of detail* for a plan-ahead horizon.

---

## Change 1: Workloads → Tenant Usage Profiles

### What changed

| | Original | Refactored |
|---|---|---|
| **Planning unit** | $x_{i,j,n,t} \in \{0,1\}$ per workload | $u_{i,h} \geq 0$ — aggregate usage profile |
| **What must be predicted** | Which specific jobs tenant $i$ will submit | How much resource tenant $i$ will use in total |
| **Predictability** | Very low — depends on user behaviour | High — aggregate demand is forecastable |
| **Variable count** | $O(T \cdot W \cdot N \cdot H)$ | $O(T \cdot N \cdot H)$ |

### Why aggregate demand is forecastable

Google cluster traces v3 show aggregate per-tenant resource consumption is **predictable at 15-min to 4-hour granularity** using standard time-series methods (ARIMA, LSTM). Individual job arrivals are not.

$$u_{i,h} = \text{total expected capacity usage of tenant } i \text{ in period } h$$

This is the input the prediction team produces — a clean, well-defined interface.

---

## Change 2: Hard Access Control → Priority Hints

### The problem with hard blocks

The original C5 blocked jobs outside the plan-ahead set:
$$x_{j,n} = 0 \quad \text{if } n \notin A_{t(j)}$$

**Two failure modes:**
- **Feasibility fragility** — stale plan + node failure = job unnecessarily rejected
- **Queue starvation** — blocked tenant waits indefinitely while other nodes sit idle

### The fix: priority boost in the real-time objective

$$b_{t(j),\,n} = \begin{cases} 2.0 & n \in \text{priority\_set}[t(j)] \\ 1.0 & \text{otherwise} \end{cases}$$

- Jobs are **never unnecessarily blocked**
- Plan-ahead guidance is **respected when capacity allows**
- System **degrades gracefully** when the plan is stale

> No node is off-limits. The plan-ahead *guides* placements; it does not *dictate* them.

---

## Change 3: SOCP Uncertainty Input Redesigned

### Original SOCP cone (C2)

$$\underbrace{\sum_{i,j,k} \eta_{k,r} \cdot \mu_{i,j,r} \cdot \xi_{i,j,n,k,t}}_{\text{mean load}} + \kappa_n \cdot \underbrace{\|\mathbf{L}_r \cdot \boldsymbol{\xi}_{n,r,t}\|_2}_{\text{SOCP cone}} \leq C_{n,r} \cdot z_{n,t}$$

Requires: full workload covariance matrix $\Sigma_r$, Cholesky factor $\mathbf{L}_r$ — **unknowable at planning time**.

### Refactored SOCP cone (C1a + C1b)

$$\sum_{i} f_{i,n,h} + \kappa \cdot t_{n,h} \leq C_n \cdot z_{n,h} \qquad \textbf{(C1a — linear)}$$

$$\sum_{i} \sigma^2_{i,h} \cdot y_{i,n,h} \leq t_{n,h}^2 \qquad \textbf{(C1b — cone)}$$

where $\sigma^2_{i,h} = (\texttt{sigma\_frac} \times u_{i,h})^2$ — variance proportional to usage profile.

**Same probabilistic guarantee:** $P[\text{actual usage} \leq C_n \cdot z_{n,h}] \geq 1 - \varepsilon$

Required input: just `sigma_frac` (one operator scalar) + $u_{i,h}$ (from prediction team).

---

## The Model Remains MISOCP — Why This Matters

> **The refactored model is still a Mixed-Integer Second-Order Cone Program.**

The cone is preserved. What changed is the *source of uncertainty* driving it.

| | Original | Refactored |
|---|---|---|
| **Cone expression** | $\|\mathbf{L}_r \cdot \boldsymbol{\xi}_{n,r,t}\|_2 \leq \texttt{soc\_aux}$ | $\sum_i \sigma^2_{i,h} \cdot y_{i,n,h} \leq t_{n,h}^2$ |
| **Uncertainty source** | Per-workload covariance $\Sigma_r$ | Per-tenant usage variance $\sigma^2_{i,h}$ |
| **Data required** | Joint workload distribution (unknowable) | `sigma_frac` × $u_{i,h}$ (estimable from traces) |
| **Probabilistic guarantee** | $P[\text{usage} \leq C] \geq 1-\varepsilon$ | same |

The Cantelli safety factor $\kappa = \sqrt{(1-\varepsilon)/\varepsilon}$ applies in both cases.

**Simulation-speed MILP mode** (`use_socp=False`): drops C1b and $t_{n,h}$. Used only when the plan-ahead fires every 50 simulation steps. **This is not the primary formulation** — it does not change the model's identity.

---

## What Was Removed and Why

**Isolation primitives** $(w_{i,j,k,t},\; \xi_{i,j,n,k,t})$
Choosing gVisor vs Kata containers is a **runtime decision** made by the real-time scheduler at placement time — not knowable at planning granularity. Removing these eliminates McCormick linearizations and C3 / C4 control-plane budget constraints.

**Migration variables** $(m_{i,j,n,t})$
In a priority-hint model, the plan-ahead does not place individual jobs. Migration is a **real-time concept** — the notion of "workload $j$ moves from node $n$ to $n'$ in period $h$" has no meaning when the plan only signals priority. Removing these eliminates C6 migration linking and disruption budget constraints.

**DRF fairness on declared demand** $(d_{i,j,r},\; s_i)$
Per-job declared demand $d_{i,j,r}$ is not available at planning time. Fairness is now expressed over aggregate allocation vs aggregate demand (C4), which is computable.

**Overall effect on constraint structure:**

| Original | Refactored |
|---|---|
| C1, C1b, C1c, McCormick, C2 SOCP, C3 isolation, C4 control-plane, C5 latency, C6 migration, C7 DRF | C1a (linear capacity), C1b (cone), C2 (priority link), C3 (demand), C4 (fairness), C5 (node activation) |

---

## Complexity Comparison

| Aspect | Original MISOCP | Refactored MISOCP |
|---|---|---|
| Binary variables | $O(TWNH + TNH + NH + T)$ | $O(TNH + NH + T)$ |
| Continuous variables | $O(TWNKH + TWH + NH)$ | $O(TNH + NH + 1)$ |
| Cone constraints | Yes — Cholesky of $\Sigma_r$ per $(n, r, t)$ | Yes — per-tenant variance per $(n, h)$ |
| Isolation variables | $O(TWNKH)$ binary (McCormick) | None |
| Migration variables | $O(TWNH)$ binary | None |
| External data required | $\Sigma_r$, $d_{i,j,r}$, $N_{i,t}$, $\eta_{k,r}$, $\gamma_{i,j}$, … | $u_{i,h}$, `sigma_frac` |

> **The model is simpler to solve but not simpler in class.** MISOCP complexity is retained through the Cantelli cone — it is just driven by practically obtainable inputs.

---

<!-- _class: lead -->

# Part 2
## Optimization Layer — Current Status

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     PREDICTION LAYER  (teammates)               │
│   time-series forecast → u[i,h] per tenant per planning period  │
└──────────────────────────────┬──────────────────────────────────┘
                               │  u[i,h]  (plug-in interface ready)
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│              PLAN-AHEAD MODEL  (MISOCP — Gurobi)                 │
│   Solved once per planning horizon                               │
│   Output: TenantAccessSchedule — priority hints y[i,n,h]        │
└──────────────────────────────┬───────────────────────────────────┘
                               │  TenantAccessSchedule
                               │  (dict[(tenant_id, period) → [node_ids]])
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│              REAL-TIME MODEL  (MILP — OR-Tools / CBC)            │
│   Solved every scheduling epoch                                  │
│   Input: job queue, node states, priority boosts from plan-ahead │
│   Output: placement decisions x[j,n]                            │
└──────────────────────────────┬───────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│              SIMULATION  (FastAPI + React)                       │
│   Interactive step-by-step execution with live dashboard         │
└──────────────────────────────────────────────────────────────────┘
```

---

## Plan-Ahead Model — Current State ✓

**Status: Implemented and tested**

The MISOCP is built in Gurobi (`plan_ahead_optimizer.py`) and solves correctly.

**Variables**
- $f_{i,n,h} \geq 0$ — allocation of node $n$ capacity to tenant $i$ in period $h$
- $y_{i,n,h} \in \{0,1\}$ — priority assignment (primary output)
- $z_{n,h} \in \{0,1\}$ — node activation
- $t_{n,h} \geq 0$ — Cantelli slack (SOCP mode only)
- $a_i \in \{0,1\}$ — tenant admission
- $\sigma \in [0,1]$ — fairness auxiliary

**Constraints:** C1a (capacity), C1b (cone), C2 (priority link), C3 (demand), C4 (fairness), C5 (node activation)

**Output:** `TenantAccessSchedule` — compact dict of priority hints, consumed by the real-time model

**Two modes:**
- `use_socp=True` (default) — full MISOCP with probabilistic guarantee
- `use_socp=False` — plain MILP, simulation-speed fallback

**Sensitivity analysis** and synthetic data generation complete (`plan_ahead_data.py`, `plan_ahead_sensitivity.py`)

---

## Real-Time Model — Current State ✓

**Status: Implemented and tested**

Solved every scheduling epoch using OR-Tools / CBC (`optimizer_google_or.py`).

**Decision variable:** $x_{j,n} \in \{0,1\}$ — job $j$ placed on node $n$

**Objective:**
$$\max \sum_{j \in J} \sum_{n \in N} \omega_{\text{delay},\, t(j)} \cdot \hat{P}_j^{\text{mem}} \cdot u_n^{\text{mem}} \cdot \sigma_n^{\text{consolid}} \cdot b_{t(j),n} \cdot x_{jn}$$

where $b_{t(j),n} = 2.0$ if $n \in$ plan-ahead priority set, else $1.0$.

**Constraints:**
- C1: at most one node per job
- C2: memory capacity $\leq M_n^{\text{eff}} = M_n^{\text{cap}} - U_n - \bar{v}_n \cdot M_n^{\text{cap}}$
- C4: CPU fitment (pre-filter)
- C5: priority boost (soft — not hard block)

**Feedback loops active:**
- SLA violation rate $\bar{v}_n$ — shrinks effective capacity on struggling nodes
- Tenant delay weight $\omega_{\text{delay},t}$ — boosts priority for waiting tenants

---

## Pipeline — How the Layers Connect ✓

**Status: Implemented** (`Pipeline/interface.py`)

```
1. build_synthetic_data()
   → generates u[i,h]  ← PLACEHOLDER for prediction team's output

2. Plan-Ahead MISOCP (Gurobi)
   → solves over horizon H
   → outputs TenantAccessSchedule: dict[(i, h) → [node_ids]]

3. schedule_to_leases()
   → compresses contiguous same-node periods into TenantLease objects
   → cleaner interface for period tracking

4. filter_active_access(schedule, current_period)
   → slices TenantAccessSchedule to the current planning period
   → returns dict[tenant_id → [node_ids]]  (the active priority hints)

5. Real-Time Model (OR-Tools)
   → receives active priority hints → applies PRIORITY_BOOST = 2.0
   → places jobs from queue onto nodes
   → returns placement results + updated node states
```

**Plug-in point for prediction team:**
Replace the call to `build_synthetic_data()` with the prediction model output.
The interface contract is: `u[i,h]` as a `dict[(tenant_id: int, period: int) → float]`.

---

## Simulation — Current State ✓

**Status: Implemented and running** (`Simulation/` — FastAPI backend + React frontend)

**What it does:**
- Manages a live job queue with per-tenant arrival rates and random job lifetimes
- Runs the real-time MILP every step (configurable step size)
- Fires the plan-ahead MISOCP every `plan_ahead_interval` steps (default: 50)
- Tracks per-node memory usage, SLA violation rates, tenant wait times
- Exposes a REST API (`GET /api/state`, `POST /api/step`, `POST /api/reset`, `POST /api/config`)

**Dashboard shows (live):**
- Node utilization over time
- Queue depth per tenant
- Placed vs rejected jobs per batch
- Plan-ahead priority assignments (which tenant → which nodes)
- SLA violation rate per node

**Simulation mechanics:**
- Per-job lifetimes replace old fractional memory release — accurate per-job lifecycle
- Actual memory = predicted memory × $(1 + \text{spike})$ — tests robustness to demand spikes
- Simulated clock advances by `BATCH_DURATION_SEC` per step

---

## What's Next

**Immediate: Plug in prediction model output**

The optimization layer is ready for real $u_{i,h}$ values. The interface contract is defined:

```python
# Replace this:
P = build_synthetic_data()   # synthetic u[i,h]

# With this:
P['u'] = prediction_model.forecast(tenants, horizon)  # dict[(i,h) → float]
```

The prediction team delivers a `dict[(tenant_id, period) → total_resource_usage_float]`. No other changes required in the optimization layer.

---

## Future Work / Research Directions

**Heuristic acceleration for the real-time model**

The real-time MILP (OR-Tools / CBC) solves a bin-packing-style problem every epoch. For small instances (≤ 20 jobs, 5 nodes) it is fast. For larger instances:

- **Greedy first-fit decreasing (FFD)** — $O(J \log J)$, well-studied for bin packing. Likely sufficient for most practical cluster sizes. Does not use the plan-ahead priority boost natively but can be adapted.
- **LP relaxation + rounding** (OR-Tools GLOP mode) — faster, slight quality loss
- **Learning-based heuristics** — policy trained on historical placement decisions; out of scope for capstone timeline

> **Capstone scope note:** Heuristic acceleration is exploratory. A working exact MILP is already in place. If time permits we will benchmark FFD vs CBC; otherwise this becomes **future work** beyond the capstone.

**Other future directions:**
- Replace synthetic $u_{i,h}$ with live Google trace replay
- Tune Cantelli $\varepsilon$ per-tenant (risk-aware SLA contracts)
- Multi-resource extension: CPU + memory jointly in the plan-ahead cone

---

<!-- _class: lead -->

# Summary

| Layer | Status | Next step |
|---|---|---|
| Plan-Ahead (MISOCP) | ✅ Implemented & tested | Swap synthetic $u_{i,h}$ for prediction output |
| Real-Time (MILP) | ✅ Implemented & tested | — |
| Pipeline | ✅ End-to-end working | — |
| Simulation | ✅ Running with dashboard | — |
| Prediction integration | ⏳ Waiting on teammates | Define handoff format |
| Heuristic RT solver | 🔬 Exploratory | Future work |

> The optimization layer is **complete and functional** as a standalone system. The primary open integration point is the prediction model output $u_{i,h}$.

---

<!-- _class: lead -->

# Questions?

**Alrick Grandison** — Capstone Project, Spring 2026

*Cluster_Optimization_Models / Docs / plan_ahead_refactoring_slides.md*
