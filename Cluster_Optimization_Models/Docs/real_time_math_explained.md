# Real-Time Model — Mathematical Reference

## 1. Overview

The Real-Time model is a **Mixed-Integer Linear Program (MILP)** solved by the Cluster
Manager once per tenant group per planning interval. It answers:

> *Given a set of pending jobs and a set of available machines, which job goes on which machine?*

The model is **stateless and timeless** — it has no concept of time, intervals, or job
lifetimes. It simply receives a job list and a machine list, solves the placement problem,
and returns an assignment. All lifecycle management (lifetimes, wait times, interval
tracking) is handled by the Cluster Manager.

### How the Cluster Manager calls it

Each simulation interval:
1. Cluster Manager reads the current interval's group list from the Plan-Ahead output.
2. For each group (tenant_ids, machine_ids):
   - Filters the job queue to jobs belonging to those tenants.
   - Filters the node list to those machines.
   - Calls `solve(group_jobs, group_machines, W_t, K)`.
   - Places returned assignments; unplaced jobs return to queue (wait time += 1 interval).
3. Moves to the next group.

The Real-Time model may be called multiple times per interval (once per group).

---

## 2. Sets

| Symbol | Description |
|--------|-------------|
| **J** | Pending jobs passed to this solve call (filtered to one tenant group) |
| **N** | Available machines for this solve call (filtered to one machine group) |

Note: J and N are already filtered by the Cluster Manager. The model does not know which
tenants own the jobs or which plan-ahead group these machines belong to.

---

## 3. Decision Variable

| Variable | Domain | Description |
|----------|--------|-------------|
| x[j,n] | {0,1} | 1 iff job j is placed on machine n |

---

## 4. Derived Node Quantities (computed before each solve)

| Symbol | Formula | Description |
|--------|---------|-------------|
| v̄_n | fraction of last K intervals with U_n > M_n^cap | Rolling SLA violation rate |
| M_n^cap | M_n − M_n^tax − M_n^θ | Schedulable capacity (after OS overhead and safety buffer) |
| M_n^avail | M_n^cap − U_n | Remaining available capacity |
| M_n^eff | max(0, M_n^avail × (1 − v̄_n)) | Effective capacity offered to new jobs (C2 right-hand side) |
| u_n^mem | 1 + clamp(U_n / M_n^cap, [0,1]) | Utilization weight ∈ [1, 2] |
| σ_n^consolid | |N| − n | Fixed consolidation bias (lower-indexed machines preferred) |

Where:
- M_n: physical RAM of machine n
- M_n^tax: OS/kubelet overhead (fixed fraction of M_n)
- M_n^θ: safety threshold buffer (threshold_frac × M_n)
- U_n: current memory usage (sum of act_mem_mb of all running jobs on n)

The SLA feedback loop: v̄_n > 0 shrinks M_n^eff, reducing how many new jobs can land on
a struggling machine. As stress subsides, v̄_n falls and capacity recovers.

---

## 5. Tenant Fairness Weight

| Symbol | Formula | Description |
|--------|---------|-------------|
| W̄_t | rolling K-window average of scheduling wait times for tenant t | Per-tenant average delay |
| W̄ | Σ_t W̄_t / |tenants| | Cluster-wide average delay |
| ω_t | 1 + max(0, (W̄_t − W̄) / max(1, W̄)) | Per-tenant delay weight ∈ [1, ∞) |

Tenants whose average wait exceeds the cluster mean get ω_t > 1 — their jobs contribute
more to the objective, so the solver naturally prefers placing them first. Fairness is a
side effect of weighted maximisation, not a hard constraint.

---

## 6. Objective

```
Maximize Z = Σ_{j ∈ J} Σ_{n ∈ N}  ω_{t(j)} · P̂_j^mem · u_n^mem · σ_n^consolid · x[j,n]
```

Where `t(j)` is the tenant of job j.

**Weight interpretation:**
- `ω_{t(j)}`: fairness — boosts jobs whose tenant has been waiting longer
- `P̂_j^mem`: predicted memory — larger jobs contribute more to utilization
- `u_n^mem`: utilization packing — prefers filling already-busy machines
- `σ_n^consolid`: consolidation — breaks ties toward lower-indexed machines

---

## 7. Constraints

**C1 (One Machine Per Job)**
Each job is placed on at most one machine:
```
Σ_{n ∈ N} x[j,n] ≤ 1    ∀ j ∈ J
```

**C2 (Machine Memory Capacity)**
Total predicted memory of all placed jobs on a machine must not exceed effective capacity:
```
Σ_{j ∈ J} P̂_j^mem · x[j,n] ≤ M_n^eff    ∀ n ∈ N
```

**C3 (Binary Domain)**
```
x[j,n] ∈ {0,1}    ∀ j ∈ J, n ∈ N
```

**C4 (CPU Fitment)**
A job cannot be placed on a machine whose CPU cores are less than the job's P95 CPU peak:
```
x[j,n] = 0    if P̂_j^CPU > C_n    ∀ j ∈ J, n ∈ N
```
Implemented by fixing the variable's upper bound to 0 before the solve.

Note: C5 (plan-ahead access control) has been **removed**. The Cluster Manager handles
machine filtering before calling this model — the model receives only the machines that are
authorized for this group's tenants.

---

## 8. Solver

| Setting | Value |
|---------|-------|
| Solver | OR-Tools CBC (exact MILP); SCIP fallback |
| Simulation time limit | 2 seconds (configurable) |
| Standalone time limit | 10 seconds |
| LP relaxation mode | GLOP (for large instances, rounds fractional solution) |

---

## 9. Return Value

```python
placements: dict[job_id → node_id | None]
```

- `node_id` (int): job was placed on this machine.
- `None`: job was not placed (returned to queue by Cluster Manager; wait time += 1 interval).

Unplaced jobs accumulate wait time, which raises their tenant's W̄_t, which raises ω_t in
the next call — a natural feedback loop that prevents starvation.
