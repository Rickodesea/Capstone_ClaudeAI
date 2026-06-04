# Real-Time Optimization Model — Code Explained Simply

> **Files covered:** `realtime_optimizer.py` and `cluster_manager.py`
> Files like `test_model.py` and `simulation_data.py` are not covered here — they are for testing and data setup.

---

## How the Two Files Relate

The real-time model runs every scheduling interval to place pending jobs onto cluster nodes. Two files handle this:

- **`realtime_optimizer.py`** = the brain — one function that solves "which job goes to which node right now"
- **`cluster_manager.py`** = the body — manages everything else: the queue, running jobs, expiring jobs, tracking wait times, and calling the brain once per tenant group per interval

Think of it like an airport:
- `cluster_manager.py` is the air traffic control tower — it tracks all flights, decides which ones are ready to land, and manages the runway state
- `realtime_optimizer.py` is the algorithm that assigns each flight to a specific runway gate

---

## File 1: `realtime_optimizer.py`

This file contains one public function: `solve()`. That's it. Everything else is setup.

The solver is **stateless**: it knows nothing about which tenant group it's solving for, or what the plan-ahead structure looks like. It only sees the jobs and nodes it's given.

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

### `solve(jobs, nodes, W_t, K, time_limit_ms)`

Called once per tenant group per scheduling interval. Takes the current state of the pre-filtered jobs and nodes, builds a small math model, solves it within the time limit, and returns which job goes to which node.

**Parameters:**

| Parameter | What it is |
|---|---|
| `jobs` | Pre-filtered list of pending jobs for this tenant group only |
| `nodes` | Pre-filtered list of machines assigned to this tenant group by plan-ahead |
| `W_t` | Average wait time per tenant over the last K intervals |
| `K` | Rolling window size |
| `time_limit_ms` | Solver wall-clock budget in milliseconds (default 10,000 ms) |

**Returns:** A dictionary mapping `job_id → node_id` (or `None` if the job couldn't be placed)

Returns `{job_id: None for all jobs}` immediately if `jobs` or `nodes` is empty.

---

**Step 1 — Start the solver**

```python
solver = pywraplp.Solver.CreateSolver(SOLVER_ID)
solver.set_time_limit(time_limit_ms)
```

Create a fresh OR-Tools solver instance. The time limit prevents the solver from hanging on very large inputs, which is important for the simulation where the UI must stay responsive.

---

**Step 2 — Compute node quantities**

For each node, before solving, we calculate several values from the current state:

```python
v_bar[n] = compute_violation_rate(n.overflow_history, K)
```
**v̄_n^SLA** — Fraction of the last K intervals where this node's memory usage exceeded its capacity ceiling. If a node keeps overflowing, this rises toward 1.0 and the node effectively gets blocked from receiving new jobs.

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
**M_eff_n** — The effective capacity offered to new jobs, shrunk by the SLA violation rate. Formula: `max(0, M_avail × (1 - v̄))`. If the node has been violating a lot lately (v̄ = 0.8), it only offers 20% of its remaining space to new jobs.

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

**u_mem** (ω_utilize): How full the node is memory-wise, mapped to [1, 2]. A fully packed node scores 2 — the solver is rewarded more for placing on busy nodes, which consolidates workloads rather than spreading them thin.

**w_node** (σ_consolid): A fixed bias. Node 0 gets the highest number, the last node gets 1. This steers the solver to fill lower-indexed nodes first, keeping the cluster compact.

> **Think of a parking lot:** You always want to fill up the row closest to the entrance first, then the next row, rather than scattering cars everywhere.

---

**Step 5 — Create decision variables**

```python
for j in jobs:
    for n in nodes:
        cpu_fits = j.pred_cpu_p95 <= n.cpu_cores   # C4
        ub = 1 if cpu_fits else 0
        x[j.job_id, n.node_id] = solver.IntVar(0, ub, name)
```

For every (job, node) pair, create a binary variable x. C4 (CPU fitment) is enforced upfront by setting the upper bound of the variable to 0 when the job doesn't fit. A variable with upper bound 0 can never be 1 — it's permanently blocked. This is more efficient than adding constraint rows.

Note: there is no access-control check here. The Cluster Manager ensures that only authorized jobs and nodes for this group are passed in — the solver doesn't need to know about plan-ahead structure.

---

**Step 6 — Set the objective**

```python
for j in jobs:
    w = omega[j.tenant_id]
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

The solver picks the combination of placements that maximizes this sum. The weights guide it toward:
- Prioritizing long-waiting tenants (delay weight)
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

Unscheduled jobs (all x = 0) are returned as `None` — they stay in the queue for the next interval.

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

**`BatchResult`** — Statistics for one scheduling interval (one ~60 second round).

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

### `ClusterManager.run(num_batches, plan_output=None)`

The main loop. Runs `num_batches` scheduling intervals and returns a `SimulationResult`.

`plan_output` is the full plan-ahead dict (with the `"intervals"` key). If `None`, falls back to one group with all tenants on all machines.

---

### `ClusterManager._run_batch(batch_id, plan_output)`

This is the heart of the simulation. Here's what happens in each interval, in order:

**Step 1 — Advance the simulated clock**
```python
self.sim_time += timedelta(seconds=BATCH_DURATION_SEC)
```
Each interval represents 60 seconds of simulated time moving forward.

**Step 2 — Expire finished jobs**
```python
expired_count = self._expire_jobs()
```
Remove any running jobs whose lifetime has passed. When a job is removed, its memory is automatically freed — because node memory is always recomputed from the running jobs list, not tracked separately.

**Step 3 — Recompute node states and record SLA history**
```python
node_violations_start = self._refresh_node_states(record_history=True)
```
For every node: sum up the memory of all still-running jobs to get current usage (U_n). Record whether the node is currently in violation (usage > M_cap). This feeds into the rolling v̄ calculation next interval.

**Step 4 — Generate new jobs**
```python
new_jobs = self._make_jobs(batch_id)
self.job_queue.extend(new_jobs)
```
New jobs are stamped with the current simulated time as their `arrival_timestamp` and added to the queue. Old unplaced jobs from previous intervals are still in the queue too.

**Step 5 — Per-group scheduling loop**

For each tenant group in the current plan interval:

```python
groups = self._get_groups(plan_output, batch_id)
for group in groups:
    group_jobs  = [j for j in queue if j.tenant_id in group["tenant_ids"]]
    group_nodes = [n for n in nodes  if n.node_id  in group["machine_ids"]]
    placements  = solve(group_jobs, group_nodes, W_t, K, time_limit_ms)
    
    placed   = [j for j in group_jobs if placements[j.job_id] is not None]
    unplaced = [j for j in group_jobs if placements[j.job_id] is None]
    self._bump_wait_for_unplaced(unplaced)
```

Each group's jobs and machines are pre-filtered before calling the solver. The solver sees only the slice relevant to this group and has no access-control logic internally.

**Step 6 — Wait bump for unplaced jobs**

```python
def _bump_wait_for_unplaced(self, unplaced_jobs):
    for j in unplaced_jobs:
        self._tenant_wait_times[j.tenant_id].append(BATCH_DURATION_SEC)
    self._update_W_t()
```

If the solver cannot place a job this interval, the tenant's rolling wait-time window gets a `BATCH_DURATION_SEC` bump. This raises W̄_t for that tenant, which increases ω_delay on the next call — the solver will try harder to place their jobs.

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

Called repeatedly — before each group solver call within an interval.

```python
used = {n.node_id: sum(rj.act_mem_mb for rj in self._running_jobs if rj.node_id == n.node_id)}
for n in self.nodes:
    n.used_mb = used[n.node_id]
    in_violation = n.used_mb > m_cap
    if record_history:
        n.overflow_history.append(in_violation)
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

After each group of placements (or bumps), recompute the average wait time for each tenant. Each tenant has a rolling deque of their last K wait times. The optimizer uses this on the next call to compute the delay weights (omega).

---

## How It All Fits Together

```
ClusterManager.run()
  └── _run_batch() × num_batches
        ├── Expire old jobs (_expire_jobs)
        ├── Refresh node states (_refresh_node_states)
        ├── Generate new jobs (_make_jobs)
        └── Per-group loop:
              ├── Filter jobs + nodes for this group
              ├── solve() ← realtime_optimizer.py
              ├── _start_job() for each placed job
              ├── _bump_wait_for_unplaced() for unplaced
              └── _update_W_t()
```

The real-time model is deliberately simpler than the plan-ahead model. It's a linear program (LP/MILP) not a second-order cone program. It solves in milliseconds instead of minutes. The complexity is in the feedback loops — the way v̄ and ω_delay are continuously updated so the system self-corrects over time.
