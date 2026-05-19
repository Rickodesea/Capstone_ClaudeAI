# Plan-Ahead Model Refactoring: Justification and Arguments

*Alrick Grandison — Capstone Project, Spring 2026*

---

## Executive Summary

During the development of an interactive cluster scheduling simulation, a number of
practical issues were encountered that exposed fundamental limitations in the original
plan-ahead model design.  These findings did not invalidate the original theory —
rather, they clarified the gap between the theoretical formulation and what a real
cluster scheduler can actually know and enforce.  The changes described below grew
directly out of that empirical process.

The original plan-ahead model was a Mixed-Integer Second-Order Cone Program (MISOCP)
that scheduled individual workloads, enforced probabilistic capacity constraints, and
used hard tenant-node access control.  Simulation testing revealed that these design
choices caused feasibility failures, queue starvation, and data requirements that
cannot be met in practice.  This document justifies replacing it with a simpler
Mixed-Integer Linear Program (MILP) that:

1. Operates on **tenant usage profiles** rather than individual workloads.
2. Produces **priority hints** rather than hard access constraints.
3. Eliminates SOCP terms, isolation primitives, and migration variables.

---

## 1. Why Remove Individual Workloads

### The Problem

The original model included variables $x_{i,j,n,t}$ for individual workload $j$
of tenant $i$ on node $n$ at time $t$.  This requires the prediction layer to
forecast which **specific workloads** a tenant will submit during a future
planning horizon.

**This is not knowable in advance.**  A tenant's job submission pattern at
time $h$ depends on user behaviour, data pipelines, and business conditions
that cannot be reliably predicted at horizon-planning time.  The workload list
is only known when jobs arrive at the scheduler — not hours or days in advance.

### The Fix

Replace workloads with **tenant usage profiles** $u_{i,h}$: the estimated
*total* resource consumption of tenant $i$ during period $h$.  This is
aggregated demand, which is significantly more predictable than individual job
arrival patterns.  Literature on datacenter resource forecasting (e.g., Google
cluster usage traces v3) consistently shows that aggregate tenant demand is
forecastable using time-series methods at 15-minute to 4-hour granularity,
while individual job-level prediction is much less reliable.

Removing per-workload variables reduces the variable count from
$O(\lvert T \rvert \cdot W \cdot \lvert N \rvert \cdot \lvert H \rvert)$ to
$O(\lvert T \rvert \cdot \lvert N \rvert \cdot \lvert H \rvert)$, making the
MILP faster to solve.

---

## 2. Why Remove Explicit Exclusivity

### The Problem

The original model included an "exclusivity" parameter (`tenant_exclusivity_pct`)
that governed whether a tenant received dedicated time slots, and C3
isolation-primitive constraints that selected gVisor/Kata containers.

**Modelling exclusivity as an input parameter is conceptually backwards.**
Exclusivity is an *emergent property* of the scheduling solution — it occurs
naturally when a tenant's demand is large enough to fill a node, or when
multiple high-demand tenants are admitted.  Explicitly designing for it at
the planning stage overrides the optimizer's freedom to pack tenants efficiently.

Furthermore, forcing explicit exclusivity at planning time does not account
for the real-time queue dynamics: even a "dedicated" node may receive
opportunistic placements from other tenants if capacity is available.

### The Fix

Remove the exclusivity parameter and isolation-primitive variables.  The MILP
finds the optimal allocation $f_{i,n,h}$ and priority indicators $y_{i,n,h}$
subject to capacity and demand constraints.  If a single tenant's demand fills
a node, that node will naturally be assigned exclusively to them — no explicit
constraint required.

---

## 3. Why Change C5 from Hard Constraint to Soft Priority

### The Problem

The original C5 constraint was:

$$x_{j,n} = 0 \quad \text{if } n \notin A_{t(j)}$$

This **blocked** any job from tenant $t(j)$ from being placed on nodes outside
the plan-ahead access set.  This creates two serious problems:

**3a. Feasibility fragility.** If a node fails, spikes in load prevent
placements, or the plan-ahead was solved with stale data, legitimate jobs may
be unnecessarily rejected.  A hard block has no fallback.

**3b. Queue starvation.** In the real-time model, unprioritised tenants wait
indefinitely if their "allowed" nodes are saturated, even when other nodes
have capacity.  This violates the fairness guarantee that every admitted job
should eventually be placed.

### The Evidence

Queuing theory (Little's Law) tells us that blocking a job from 80% of
available servers while they sit idle increases expected wait time
dramatically.  In our simulation with 5 nodes and the default 10%
exclusivity, a blocked tenant sees 4/5 nodes become inaccessible — their
average wait grows proportionally.

Simulation runs confirmed this directly: with hard access control enabled,
tenants whose "allowed" nodes were temporarily saturated accumulated queue
backlogs even while other nodes sat idle.  Switching to a priority-boost
model eliminated these backlogs without sacrificing the planning signal.

### The Fix

Replace the hard block with a **priority boost** in the real-time objective:

$$b_{t(j),n} = \begin{cases}
  2.0 & \text{if } n \in \text{priority\_set}[t(j)] \\
  1.0 & \text{otherwise}
\end{cases}$$

This makes plan-ahead-endorsed placements twice as attractive in the MILP
objective, guiding the solver toward the planned allocation, but still
allowing placements elsewhere when needed.  The result:

- Jobs are **never unnecessarily blocked**.
- Plan-ahead guidance is **respected when capacity allows**.
- The system **degrades gracefully** when the plan is stale or capacity changes.

---

## 4. How the Capacity Constraint Changed

### Original Form

The original C2 was a Cantelli probabilistic constraint built from the **covariance
matrix $\Sigma$ of individual workload memory demand**:

$$\mu_n + \kappa(\epsilon) \cdot \|\mathbf{L} \cdot \boldsymbol{\xi}\|_2 \leq C_n \cdot z_{n,t}$$

This required knowing the joint distribution of all workloads' memory usage — a
Cholesky decomposition $\mathbf{L}$ of $\Sigma$ — and introduced second-order cone
variables tied to those workloads.

### New Form

Since individual workloads are no longer modelled (Section 1), the constraint is now
expressed at the **tenant usage profile** level.  The uncertainty in how much tenant
$i$ will consume in period $h$ is modelled as:

$$\sigma^2_{i,h} = (\text{sigma\_frac} \times u_{i,h})^2$$

Larger predicted usage → proportionally larger uncertainty.  The capacity constraint
is split into two parts:

$$\sum_{i} f_{i,n,h} + \kappa \cdot t_{n,h} \leq C_n \cdot z_{n,h}
\qquad \text{(C1a: allocation + safety buffer ≤ capacity)}$$

$$\sum_{i} \sigma^2_{i,h} \cdot y_{i,n,h} \leq t_{n,h}^2
\qquad \text{(C1b: buffer must cover combined uncertainty)}$$

$t_{n,h}$ is a continuous variable the solver sizes automatically.  Together the two
constraints guarantee that the node stays within capacity at least **90% of the time**
even if actual demand exceeds the prediction ($\varepsilon = 0.10$, $\kappa = 3.0$).

### What Changed and Why

| | Original | New |
|---|---|---|
| Uncertainty source | Per-workload covariance $\Sigma$ | Per-tenant usage variance $\sigma^2_{i,h}$ |
| Data required | Joint workload distribution (unknowable in advance) | `sigma_frac` × usage profile (estimable from traces) |
| Cone variable | Tied to Cholesky of $\Sigma$ | Single slack $t_{n,h}$ per node per period |

The probabilistic guarantee is the same in kind; the inputs are simpler and
practically obtainable.

### MILP Option for Simulation Speed

A plain MILP mode (`use_socp=False`) was added alongside the SOCP for one reason:
the simulation fires the plan-ahead model every 50 steps.  The SOCP adds cone
constraints that increase solve time, which is noticeable at interactive simulation
speeds.

The MILP mode drops the safety buffer entirely and uses the plain linear capacity
constraint.  It produces the same priority assignments in the normal case; it simply
does not carry the probabilistic capacity guarantee.

The SOCP remains the correct and default formulation when running the plan-ahead
model on its own, since it runs only once and speed is not a constraint.

---

## 5. Why Remove Migration Variables

### The Problem

The original C6 tracked migrations $m_{i,j,n,t}$ across planning periods.
This added $O(\lvert T \rvert \cdot W \cdot \lvert N \rvert \cdot \lvert H \rvert)$
binary variables and corresponding constraints.

**In a priority-hint model, migration is a real-time concept.**  The
plan-ahead does not place individual jobs; it only signals priority.
The real-time scheduler may place a job on any node and the concept of
"migrating from node $n$ in period $h-1$ to node $n'$ in period $h$" has
no meaning at the planning level.

---

## 6. Summary of Model Complexity

| Aspect | Original MISOCP | New MILP |
|--------|----------------|---------|
| Binary variables | $O(TW N H + TNH + NH + T)$ | $O(TNH + NH + T)$ |
| Continuous variables | $O(TW N K H + T W H + NH)$ | $O(TNH + 1)$ |
| Constraint classes | C1 placement, C1b indicator, C1c compliance, McCormick, C2 SOCP, C3 isolation, C4 control-plane, C5 latency, C6 migration, C7 DRF | C1 capacity, C2 priority link, C3 demand, C4 fairness, C5 node activation |
| Cone constraints | Yes (SOCP) | None (pure MILP) |
| External data required | Workload covariance $\Sigma$, isolation parameters, migration costs | Tenant usage profile $u_{i,h}$ |

---

## 7. Conclusion

The refactored model is:

- **Conceptually correct**: it forecasts what can be known (aggregate demand),
  not what cannot (individual job arrivals).
- **Mathematically simpler**: pure MILP, no SOCP, fewer variables.
- **More robust**: priority hints degrade gracefully; no hard blocks.
- **Practically implementable**: $u_{i,h}$ can be generated from cluster traces
  using standard time-series forecasting; the prediction team has a clear,
  well-defined input to produce.

The plan-ahead output feeds the real-time model as a **soft guidance signal**,
respecting the principle that operational decisions (actual job placement) should
remain at the real-time layer where full system state is visible.
