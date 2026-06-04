# Plan-Ahead Iterative Variant — Explanation

## File: `PlanAhead/plan_ahead_iterative.py`

This is the **default plan-ahead solver**. It is an iterative wrapper that avoids the memory and scale limitations of the full MISOCP — the base MISOCP model in `plan_ahead_optimizer.py` remains unchanged and available for smaller problems or offline planning.

The iterative variant is used by default in `Simulation/sim_runner.py` (`--pa mock`) and can be run standalone from `PlanAhead/` or invoked from the sensitivity analysis with `--iterative` (the default).

---

## Why iterative instead of one large MISOCP?

The full MISOCP crashes at T=256, N=512 (~1.06M variables, OOM) and requires Gurobi.
The iterative approach solves the same placement problem by running many small greedy passes instead of one large MIP — no solver license required, scales to any number of tenants.

The math does not change — the iterative variant only adds a layer that avoids the scalability limitations of the single-shot formulation.

---

## Core idea: sliding window over tenants and nodes

Instead of solving all T tenants × N machines in one MILP, the algorithm processes tenants in a rolling window:

- **Active window**: 8 tenants × 64 nodes (chosen because T=8, N=64 solves in ~2 s in full MISOCP)
- **Tenant pool**: all tenants waiting to be placed
- **Node pool**: a pool of nodes with remaining capacity

Each iteration allocates as much tenant demand as possible onto the current active nodes, then evicts and replaces:

```
Iteration k:
  1. ALLOCATE   — greedy first-fit decreasing over active tenants × active nodes
  2. EVICT-T    — tenants whose full demand is satisfied → complete; pull next from queue
  3. EVICT-N    — nodes with < 5% remaining capacity → retired; provision fresh node
  4. STALL-GUARD — if 3 consecutive low-progress iterations → force-evict worst node
```

---

## Allocation logic (step 1)

**Greedy First-Fit Decreasing (FFD):**

```
Sort tenants by total remaining demand (largest first)
For each tenant i:
  For each period h:
    Find node n* = argmax remaining_capacity[n, h]
    Allocate  min(remaining_demand[i,h], remaining_capacity[n*,h])
    Update remaining_capacity[n*, h] -= allocation
    Update tenant.placed[h]         += allocation
```

This is a polynomial-time heuristic — no MIP solve per iteration. Complexity per iteration: O(T × N × H).

---

## Tenant eviction (step 2)

A tenant is **complete** when `remaining_demand[i,h] < 1e-6` for all periods h.

On eviction: the tenant is moved to the `completed` list. A new tenant from the queue replaces it in the active window, preserving window size.

---

## Node eviction (step 3)

A node is **full** when `max(remaining_capacity[h] for all h) < 5% × NODE_CAPACITY`.

On eviction: a fresh node (full capacity = 10.0 per period) is provisioned. The node is retired permanently (we do not revisit it).

The total number of nodes used tracks physical infrastructure cost — the iterative method may use more nodes than the full MISOCP because it cannot globally optimize which nodes to use.

---

## Stall recovery (step 4)

If fewer than `MIN_PROGRESS = 1e-4` capacity-units are placed in an iteration, a stall is detected. After 3 consecutive stalls, the node with the least remaining capacity (most consumed, but not yet full) is forcibly evicted and replaced with a fresh node. This breaks deadlock when all active nodes are partially used but the remaining tenant demands don't fit in any single node's remaining slice.

---

## Why 8 tenants × 64 nodes?

From the computational timing analysis:
- T=8, N=64: **2.03 s** total (build + solve) in the full MISOCP
- T=8, N=256: **31 s** — build time dominates (30.5 s)
- T=8, N=1024: **43 s**

At 8 tenants × 64 nodes, each full-MISOCP solve is fast. This is the sweet spot for an iterative unit that stays responsive.

---

## Trade-offs vs full MISOCP

| Property | Full MISOCP | Iterative |
|---|---|---|
| Optimality | Global optimum (MIP guarantee) | Greedy heuristic — no guarantee |
| Scalability | OOM at ~500k variables | Scales to any T (bounded iterations) |
| Infrastructure cost | Minimized by objective | Not minimized — may over-provision |
| Fairness (σ) | Explicitly maximized | Not modeled |
| Mix bonus | Explicitly rewarded | Not modeled |
| Speed per iteration | Scales with T×N×P | O(T×N×H) — very fast |
| Cantelli constraint | Enforced probabilistically | Not enforced (deterministic caps only) |

---

## How to run

```bash
cd Cluster_Optimization_Models/PlanAhead/

# Default: 128 tenants, seed=42
python plan_ahead_iterative.py

# Custom: 1000 tenants, save CSV
python plan_ahead_iterative.py --tenants 1000 --csv

# Different seed
python plan_ahead_iterative.py --tenants 256 --seed 7 --csv
```

Output CSV is written to `Pipeline/timing_data/pa_iterative_results.csv`.

---

## What the output shows

```
Iter  Active T  Active N   Placed  Cmpl  Evict  New
   1         8        64   5.2341     0      0    0
   2         8        64   3.1204     1      2    2
   3         7        64   2.8811     0      1    1
  ...
```

- **Placed**: capacity-units allocated this iteration (sum over all tenants × periods)
- **Cmpl**: tenants that completed this iteration
- **Evict**: nodes retired this iteration
- **New**: fresh nodes provisioned

---

## Connection to full model

This experiment explores whether a **greedy decomposition** of the plan-ahead problem is practically viable when the full MISOCP is too large to solve. The results inform whether:

1. The iterative approach converges (all tenants placed)
2. The node count (infrastructure cost) is competitive with the MISOCP solution
3. The approach is fast enough for online re-planning (e.g. every 6 hours)

If convergence is reliable and node overhead is acceptable, the iterative method could serve as a **fallback** when the full MISOCP hits memory limits.
