# Real-Time Optimization Model — Code Explained Simply

> **Files covered:** `optimizer_google_or.py` and `cluster_manager.py`
> Files like `test_model.py` and `simulation_data.py` are not covered here — they are for testing and data setup.

---

## How the Two Files Relate

The real-time model runs every ~60 seconds to place pending jobs. Two files handle this:

- **`optimizer_google_or.py`** = the brain — one function that solves "which job goes to which node right now"
- **`cluster_manager.py`** = the body — manages everything else: the queue, running jobs, expiring jobs, tracking wait times, and calling the brain repeatedly

Think of it like an airport:
- `cluster_manager.py` is the air traffic control tower — it tracks all flights, decides which ones are ready to land, and manages the runway state
- `optimizer_google_or.py` is the algorithm that assigns each flight to a specific runway gate

---

## File 1: `optimizer_google_or.py`

This file contains one public function: `solve()`. That's it. Everything else is setup.

---

### Solver choice

```python
SOLVER_ID = "CBC"
```

This selects which math solver to use. We're using OR-Tools (Google's free optimization library) instead of Gurobi (which is expensive and used only for the plan-ahead model). Three options:

- **CBC** — exact MILP solver, always available, used by default
- **GLOP** — LP relaxation (treats binary variables as real numbers between 0 and 1, then rounds), faster for huge problems
- **SCIP** — another exact MILP solver, sometimes faster than CBC on large problems

---

### `solve(jobs, nodes, W_t, K, tenant_node_access)`

This is called once per scheduling round. It takes the current state of everything, builds a tiny math model, solves it in milliseconds, and returns which job goes to which node.

**Parameters:**

| Parameter | What it is |
|---|---|
| `jobs` | The list of pending jobs waiting to be placed |
| `nodes` | The current state of all cluster nodes (how full each one is) |
| `W_t` | Average wait time per tenant over the last K rounds |
| `K` | Rolling window size |
| `tenant_node_access` | Output from the plan-ahead model — which nodes each tenant is allowed to use |

**Returns:** A dictionary mapping `job_id → node_id` (or `None` if the job couldn't be placed)

---

**Step 1 — Start the solver**

```python
solver = pywraplp.Solver.CreateSolver(SOLVER_ID)
solver.set_time_limit(10_000)   # 10 seconds max
```

Create a fresh OR-Tools solver instance. The 10-second time limit prevents the solver from hanging on very large inputs.

---

**Step 2 — Compute node quantities**

For each node, before solving, we calculate several values from the current state:

```python
v_bar[n] = compute_violation_rate(n.violation_history, K)
```
**vbar_n** — Fraction of the last K rounds where this node's memory usage exceeded its capacity ceiling. If a node keeps overflowing, this rises toward 1.0 and the node effectively gets blocked from receiving new jobs.

```python
m_cap[n] = compute_available_capacity(n)
```
**M_cap_n** — Node capacity after subtracting OS overhead and the safety buffer. This is the real ceiling for tenant jobs. Formula: `M_n - tax - threshold`.

```python
r_avail[n] = compute_remaining_avail(n, m_cap[n])
```
**M_avail_n** — How much memory is still free right now. Formula: `M_cap_n - current_usage`.

```python
R[n] = compute_remaining_eff(r_avail[n], v_bar[n])
```
**M_eff_n** — The effective capacity offered to new jobs, shrunk by the SLA violation rate. Formula: `max(0, M_avail × (1 - vbar))`. If the node has been violating a lot lately (vbar = 0.8), it only offers 20% of its remaining space to new jobs.

> **Think of it like a hotel:** If a hotel has been over-booking rooms and getting complaints (violations), it starts blocking out more rooms as a buffer to prevent future complaints.

---

**Step 3 — Compute tenant delay weights**

```python
omega = compute_omega({t: W_t.get(t, 0.0) for t in all_tenants})
```

For each tenant, compute how much priority boost they should get. The formula:

```
omega_t = 1 + max(0, (W_t - W_average) / max(1, W_average))
```

- If tenant's average wait = cluster average → omega = 1 (no boost)
- If tenant's average wait > cluster average → omega > 1 (boost, solver prefers their jobs)

> **Think of it like a food order queue:** If your order has been waiting longer than everyone else's, the kitchen bumps it up.

---

**Step 4 — Compute node weights**

```python
u_mem[n] = compute_utilization_weight(n)   # memory utilization ∈ [1,2]
w_node[n] = compute_node_weight(n.node_id, len(nodes))  # consolidation bias ∈ [1, |N|]
```

**u_mem** (omega_utilize): How full the node is memory-wise, mapped to [1, 2]. A fully packed node scores 2 — the solver is rewarded more for placing on busy nodes, which consolidates workloads rather than spreading them thin.

**w_node** (sigma_consolid): A fixed bias. Node 0 gets the highest number, the last node gets 1. This steers the solver to fill lower-indexed nodes first, keeping the cluster compact.

> **Think of a parking lot:** You always want to fill up the row closest to the entrance first, then the next row, rather than scattering cars everywhere.

---

**Step 5 — Create decision variables**

```python
for j in jobs:
    for n in nodes:
        cpu_fits  = j.pred_cpu_p95 <= n.cpu_cores        # C4
        has_access = n.node_id in tenant_node_access.get(j.tenant_id, [])  # C5
        ub = 1 if (cpu_fits and has_access) else 0
        x[j.job_id, n.node_id] = solver.IntVar(0, ub, name)
```

For every (job, node) pair, create a binary variable x. Critically, instead of adding C4 and C5 as separate constraint rows, we enforce them upfront by setting the upper bound of the variable to 0 when the job either:
- Doesn't fit the node's CPU (C4), or
- The tenant isn't authorized for that node by the plan-ahead model (C5)

A variable with upper bound 0 can never be 1 — it's permanently blocked. This is more efficient than adding constraint rows.

---

**Step 6 — Set the objective**

```python
for j in jobs:
    w = omega[j.tenant_id]        # delay weight
    for n in nodes:
        obj.SetCoefficient(
            x[j.job_id, n.node_id],
            w * j.pred_mem_mb * u_mem[n.node_id] * w_node[n.node_id]
        )
obj.SetMaximization()
```

The objective: maximize the total "weighted memory placed". For each (job, node) pair, the coefficient is:

```
delay_weight × predicted_memory × utilization_weight × consolidation_weight
```

The solver picks the combination of placements that maximises this sum. The weights guide it toward:
- Prioritising long-waiting tenants (delay weight)
- Placing on busier nodes (utilization weight)
- Filling lower-indexed nodes first (consolidation weight)

---

**Step 7 — Add constraints**

```python
# C1: each job goes to at most one node
ct = solver.Constraint(0.0, 1.0, f"c1_{j.job_id}")
for n in nodes:
    ct.SetCoefficient(x[j.job_id, n.node_id], 1.0)
```

The sum of all x for one job must be ≤ 1. A job can be placed on exactly one node, or left unscheduled (sum = 0) if no node has room.

```python
# C2: memory capacity per node
ct = solver.Constraint(0.0, R[n.node_id], f"c2_{n.node_id}")
for j in jobs:
    ct.SetCoefficient(x[j.job_id, n.node_id], j.pred_mem_mb)
```

For each node, the total predicted memory of placed jobs must be ≤ M_eff_n (the effective remaining capacity).

---

**Step 8 — Solve and return**

```python
status = solver.Solve()
```

OR-Tools runs CBC/GLOP/SCIP internally. We check that the result is OPTIMAL or FEASIBLE. Then we read each x variable's value — if it's > 0.5, that job is placed on that node.

Unscheduled jobs (all x = 0) are returned as `None` — they stay in the queue for the next round.

---

## File 2: `cluster_manager.py`

This file manages the simulation loop and everything around the optimizer. It is the "glue" that connects jobs, nodes, the optimizer, and time.

---

### Key data structures

**`RunningJob`** — A placed job that is currently executing on a node.

| Field | What it is |
|---|---|
| `job` | The original job object |
| `node_id` | Which node it's running on |
| `act_mem_mb` | Actual memory consumed (may be higher than predicted if a spike occurred) |
| `is_spike` | True if actual memory > predicted memory |
| `lifetime_sec` | How long this job will run before it finishes |

The `end_time` property computes `start_time + lifetime` — when the job should be removed.

**`BatchResult`** — Statistics for one scheduling epoch (one ~60 second round).

**`SimulationResult`** — Aggregate statistics across the entire simulation run.

---

### `ClusterManager.__init__`

Sets up everything needed before the simulation starts:

- Creates nodes (`generate_nodes`)
- Creates an empty job queue
- Creates an empty running jobs list
- Initializes fairness tracking (W_t per tenant)
- Starts a simulated clock
- Pre-loads nodes with some already-running jobs to simulate a partially occupied cluster at startup

---

### `ClusterManager.run(num_batches)`

The main loop. Runs `num_batches` scheduling epochs and returns a `SimulationResult`.

Each batch calls `_run_batch(batch_id)` and collects the result.

---

### `ClusterManager._run_batch(batch_id)`

This is the heart of the simulation. Here's what happens in each batch, in order:

**Step 1 — Advance the simulated clock**
```python
self.sim_time += timedelta(seconds=BATCH_DURATION_SEC)
```
Each batch represents 60 seconds of simulated time moving forward.

**Step 2 — Expire finished jobs**
```python
expired_count = self._expire_jobs()
```
Remove any running jobs whose lifetime has passed. When a job is removed, its memory is automatically freed — because node memory is always recomputed from the running jobs list, not tracked separately.

**Step 3 — Recompute node states and record SLA history**
```python
node_violations_start = self._refresh_node_states(record_history=True)
```
For every node: sum up the memory of all still-running jobs to get current usage (U_n). Then record whether the node is currently in violation (usage > M_cap). This feeds into the rolling vbar calculation next round.

**Step 4 — Generate new jobs**
```python
new_jobs = self._make_jobs(batch_id)
self.job_queue.extend(new_jobs)
```
New jobs are stamped with the current simulated time as their `arrival_timestamp` and added to the queue. Old unplaced jobs from previous batches are still in the queue too.

**Step 5 — The scheduling loop**

This is a while loop that keeps calling the optimizer until either:
- The queue is empty, or
- The solver returns zero placements `MAX_PLACEMENT_RETRIES` times in a row (nodes are full)

```python
while self.job_queue:
    if consecutive_failures >= MAX_PLACEMENT_RETRIES:
        break
    
    self._refresh_node_states(record_history=False)
    
    queue_slice = sorted(self.job_queue, key=lambda j: j.arrival_round)[:MAX_JOBS_PER_SOLVE]
    
    placements = solve(jobs=queue_slice, nodes=self.nodes, W_t=self.W_t, K=...)
```

Why a loop instead of one call? Because a single solver call can only handle `MAX_JOBS_PER_SOLVE` jobs at once (to avoid the solver taking too long with thousands of binary variables). After each successful batch of placements, the node states are updated and the solver is called again with the remaining queue.

For each placed job, the manager:
1. Calls `_start_job()` to record it as running with actual memory and a random lifetime
2. Logs the placement with timestamps
3. Updates W_t (fairness weights) so the next solver call has fresh priority information

---

### `_start_job(job, node_id)`

When a job is placed:
1. Records `scheduling_timestamp = sim_time`
2. Randomly decides if this job will "spike" (use more memory than predicted). About 10% of jobs spike, using up to 20% more memory than predicted.
3. Assigns a random lifetime between MIN_LIFETIME_SEC and MAX_LIFETIME_SEC
4. Creates a RunningJob and adds it to `_running_jobs`

The spike simulates real-world unpredictability — prediction models are good but not perfect.

---

### `_refresh_node_states(record_history)`

Called repeatedly — before each solver call within a batch.

```python
used = self._compute_node_used_mb()  # sum act_mem_mb of all running jobs per node
for n in self.nodes:
    n.used_mb = used[n.node_id]
    in_violation = n.used_mb > m_cap
    if record_history:
        n.violation_history.append(in_violation)
```

Node memory usage is always recomputed from scratch by summing all running jobs. This means it's always accurate — there's no drift from accumulated rounding errors or missed updates.

---

### `_update_W_t()`

```python
self.W_t = {
    t: sum(ws) / len(ws)
    for t, ws in self._tenant_wait_times.items()
}
```

After each group of placements, recompute the average wait time for each tenant. Each tenant has a rolling deque of their last K wait times. The optimizer uses this on the next call to compute the delay weights (omega).

---

## How It All Fits Together

```
ClusterManager.run()
  └── _run_batch() × num_batches
        ├── Expire old jobs (_expire_jobs)
        ├── Refresh node states (_refresh_node_states)
        ├── Generate new jobs (_make_jobs)
        └── Scheduling loop:
              ├── solve() ← optimizer_google_or.py
              ├── _start_job() for each placed job
              └── _update_W_t()
```

The real-time model is deliberately simpler than the plan-ahead model. It's a linear program (LP/MILP) not a second-order cone program. It solves in milliseconds instead of minutes. The complexity is in the feedback loops — the way vbar and omega_delay are continuously updated so the system self-corrects over time.
