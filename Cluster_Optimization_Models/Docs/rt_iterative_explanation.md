# Real-Time Iterative Variant — Explanation

## File: `Realtime/optimizer_iterative.py`

This is the **default real-time solver** in the simulation. It is an iterative wrapper that breaks one large job-to-node assignment MILP into a sequence of small sub-MILPs — the base model in `realtime_optimizer.py` remains unchanged and is still used directly for small problems and as the single-shot baseline.

The iterative variant is used by default in `Simulation/sim_runner.py` (`--rt iterative`) and is benchmarked in `Pipeline/computational_time_analysis_iterative.py`.

---

## Why iterative instead of one large MILP?

The single-shot MILP has J × N binary variables. At production scale this explodes: at J=1024, N=1024 that is ~1.05M binaries, and even Gurobi cannot close the optimality gap inside the 5-minute cap (it returns a feasible 100% placement but runs the clock out). CBC is far worse.

The iterative approach solves the **same** placement problem by looping over small sub-MILPs instead of one large MIP. Each sub-MILP is solved to integer optimality, so this is a **decomposition**, not a relaxation — the mathematical formulation is unchanged; the iterative layer only avoids presenting the full problem to the solver at once.

---

## Core idea: batch decomposition over jobs and nodes

Instead of solving all J jobs × N nodes in one MILP, the algorithm processes jobs in batches:

- **Batch size**: up to `BATCH_JOBS` jobs × `BATCH_NODES` nodes per sub-MILP (default 16 × 16)
- **Unplaced pool**: all jobs not yet placed, sorted largest-memory-first (FFD)
- **Available nodes**: nodes with remaining capacity, re-sorted each pass

Each iteration solves one sub-MILP and updates state:

```
Iteration k:
  1. SELECT     — top BATCH_NODES nodes by remaining capacity
                  + top BATCH_JOBS unplaced jobs (largest pred_mem first)
  2. SOLVE      — call realtime_optimizer.solve() on that sub-problem
  3. RECORD     — apply placements, add placed memory to node usage,
                  remove placed jobs from the unplaced pool
  4. EVICT-N    — retire nodes with < FULL_THRESH (5%) remaining capacity,
                  but only while spare nodes still exist
  5. STALL-GUARD — if an entire batch places nothing, stop (remaining jobs stay queued)
```

---

## Job and node selection (step 1)

**First-Fit Decreasing (FFD):** unplaced jobs are sorted by predicted memory, largest first, so big jobs are placed before the cluster fills with small ones and squeezes them out. Nodes are sorted by current remaining capacity (descending) and the top `BATCH_NODES` are offered to the sub-MILP, each rebuilt as a `NodeState` copy reflecting in-call usage.

---

## Sub-MILP solve (step 2)

```python
sub_result = realtime_optimizer.solve(
    batch_jobs, batch_nodes_updated, W_t, K,
    per_batch_ms, solver_id=solver_id,
)
```

The sub-problem is the **identical** MILP from `realtime_optimizer.py` (same objective, same C1–C4 constraints) applied to a small slice. Because each call has only `BATCH_JOBS × BATCH_NODES` variables, it stays tractable and is solved to integer optimality.

---

## Node eviction (step 4)

A node is **retired** when its remaining capacity drops below `FULL_THRESH × total capacity` (5%). Eviction only happens while spare nodes remain, so the solver never runs out of targets. Retired nodes are dropped from the available list for the rest of the call.

---

## Stall recovery (step 5)

If a whole batch places **zero** jobs, the loop breaks and the remaining jobs are returned as `None`. The Cluster Manager re-queues them for the next scheduling interval (their accumulated wait time raises their priority weight next round). This matches the single-shot solver's contract exactly — it is a drop-in replacement.

> **Important — false stalls from the time budget.** The loop hands each sub-MILP a slice of the total budget: `per_batch_ms = max(500, time_limit_ms // n_batches)`. If that slice is too small for a *large* sub-MILP (a 32 × 32 or 64 × 64 batch) to return any integer solution, the batch places nothing and trips the stall guard — even though the cluster has ample capacity. This is a **budget artifact, not a capacity limit**: with an adequate per-sub-MILP budget every batch size places 100% of jobs. The practical fix is to keep the batch small (e.g. 8 × 8), so each sub-MILP solves comfortably inside its slice. See `Pipeline/computational_time_analysis_iterative.py`.

---

## Choosing the batch size

From the iterative timing analysis (`Pipeline/computational_time_analysis_iterative.py`, batch configs 8×8, 16×16, 32×32, 64×64):

- Given an adequate per-sub-MILP budget, **every batch size places 100%** of jobs.
- Solve time rises sharply with batch size, because each sub-MILP is exponentially harder.
- **Jobs, not nodes, are the bottleneck**: for a fixed batch size, solve time is roughly flat as the node count grows — it tracks the number of jobs (hence iterations), not machines.
- Under Gurobi, **16 × 16 is the fastest robust setting** (it balances few enough sub-solves against each staying trivial), so it is the module default `BATCH_JOBS = BATCH_NODES = 16`. 8 × 8 is essentially equivalent; 32 × 32 and 64 × 64 are 10–20× slower.

---

## Solver backend

`optimizer_iterative.py` exposes a module-level `SOLVER_ID = "CBC"`, but the sub-MILP backend is selectable per call. The simulation and the timing analysis now pass **Gurobi**, which is the production default (`sim_runner.py --solver` defaults to `GUROBI`). CBC, SCIP, and HiGHS remain available when no Gurobi license is present. Note that with Gurobi the per-solve startup overhead can dominate when there are many tiny sub-MILPs, so the speed gap between batch sizes is smaller than under CBC.

---

## Trade-offs vs single-shot MILP

| Property | Single-shot MILP | Iterative batch-MILP |
|---|---|---|
| Optimality | Global optimum (when it finishes) | Per-batch optimal; greedy across batches |
| Scalability | Caps out / OOM at ~1M variables | Scales — each sub-MILP is bounded |
| Placement rate | 100% only if the solve completes | 100% with an adequate per-batch budget |
| Consolidation | Maximized globally (fewer active nodes) | Slightly lower — fills each batch greedily |
| Speed | Minutes at scale | Seconds at scale |
| Formulation | Exact MILP | Same exact MILP, applied in slices |

---

## How to run

```bash
cd Cluster_Optimization_Models/Realtime/

# Standalone test: 16 jobs, 64 nodes, default batch, compare vs single-shot
python optimizer_iterative.py --compare

# Custom batch size and solver
python optimizer_iterative.py --jobs 1024 --nodes 1024 --batch-jobs 8 --batch-nodes 8 --solver GUROBI
```

In the simulation:

```bash
cd Cluster_Optimization_Models/Simulation/
python sim_runner.py --rt iterative --rt-batch-jobs 8 --rt-batch-nodes 8 --solver GUROBI
```

---

## Connection to the full model

This is a **decomposition-based matheuristic** (Maniezzo, Boschetti & Stuetzle, 2021): an exact MILP solver applied repeatedly to tractable sub-problems inside a heuristic master loop. It is *inspired by* the divide-and-conquer principle behind Dantzig-Wolfe, Lagrangian, and Benders decomposition, but it is **not** an implementation of any of those formal methods — there is no master/sub-problem dual exchange and no convergence proof. It trades a global optimality guarantee for the ability to place 100% of jobs in seconds at any scale. The natural exact successor is column generation (branch-and-price), where each "column" is a feasible bundle of jobs on one node — see the report's future-work section.
