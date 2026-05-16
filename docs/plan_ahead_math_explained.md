# Plan-Ahead Model — Math Explained Simply

> **Who this is for:** Anyone on the team who wants to understand what the math is actually doing. No prior optimization knowledge needed.

---

## The Big Picture

Imagine you manage a hotel with several buildings (nodes). Every week, a bunch of group bookings come in (tenants), and each group wants multiple rooms (workloads/jobs). You need to plan for the whole week at once — who stays in which building, on which days — before the week even starts.

That is exactly what the Plan-Ahead model does. It looks at the full planning horizon (e.g. a week split into 4-hour blocks) and decides in advance which buildings each group is allowed to use. The real-time scheduler then uses those decisions like a rulebook every 60 seconds.

---

## Part 1: The Players (Sets)

These are just labels for the groups of things we're dealing with.

| Symbol | Plain English |
|---|---|
| **T** | The list of all tenants (companies renting the cluster) |
| **W_i** | The list of workloads (jobs) that belong to tenant i |
| **N** | The list of all cluster nodes (physical machines) |
| **R** | The two resource types we track: CPU and Memory |
| **H** | The planning slots — e.g. 42 slots for a 7-day week in 4-hour blocks |
| **Q** | Quality of service classes: "guaranteed" or "burstable" |
| **K** | The isolation options: bare container, gVisor, or Kata VM |

---

## Part 2: The Choices (Decision Variables)

These are the things the model is allowed to choose. Think of these as blank boxes the optimizer fills in.

**x_ijnt = 1 or 0**
> "Should workload j of tenant i be placed on node n during time slot t?"
> 1 = yes, 0 = no.
> This is the main decision — who goes where and when.

**y_int = 1 or 0**
> "Does tenant i have at least one workload on node n at time t?"
> The model fills this in automatically based on x. We need it to track which tenants share a node.

**z_nt = 1 or 0**
> "Is node n switched on at time t?"
> If no workloads are placed on a node, it can be turned off to save energy and money.

**w_ijkt = 1 or 0**
> "Should workload j of tenant i use isolation method k at time t?"
> Each workload must pick exactly one isolation level (bare, gVisor, or Kata).

**m_mig_ijnt = 1 or 0**
> "Does workload j of tenant i move to node n at time t (migration)?"
> Moving jobs between nodes is expensive — we track it so we can penalise it.

**a_i = 1 or 0**
> "Is tenant i admitted to the cluster this week?"
> If rejected, none of that tenant's workloads are placed anywhere.

**e_ijt ≥ 0**
> "How much did workload j of tenant i go over its latency limit at time t?"
> We want this to be 0 always, but if it can't be, we at least measure by how much we missed.

**sigma_fair**
> "What is the minimum satisfaction fraction across all tenants?"
> This is a fairness score between 0 and 1. We want to maximise it.

---

## Part 3: The Numbers We Know Upfront (Parameters)

These are fixed facts the optimizer receives before it starts solving.

### Resource numbers
- **d_ijr** — How much resource (CPU or memory) the workload *claims* it needs. Declared by the tenant at submission.
- **mu_ijr** — The *expected actual* resource usage based on historical data. Usually lower than the declared amount.
- **Sigma_r** — The covariance matrix. Tells us how different workloads' resource usage moves together. If workload A and workload B always spike at the same time, that's captured here.
- **C_nr** — How much resource (CPU or memory) node n actually has.
- **N_it** — How many workloads we expect tenant i to have active during slot t.

### Money and risk numbers
- **p_ij** — How expensive it is if workload j misses its SLA (higher = bigger penalty).
- **pi_n** — How much it costs to keep node n running per time slot.
- **pi_bar_i** — How much revenue we earn by admitting tenant i.
- **eps_i** — How much risk tenant i tolerates. If eps_i = 0.05, the cluster must ensure capacity is not exceeded more than 5% of the time.

### Isolation numbers
- **eta_kr** — How much extra resource isolation method k uses. gVisor uses 20% more; Kata uses 5% more.
- **rho_kkp** — How much interference still leaks between workloads even after isolation is applied.
- **tau_iip** — The maximum interference tenant i is willing to tolerate from tenant i'.

---

## Part 4: The Safety Rule — Probabilistic Capacity (SOCP)

This is the most important and most mathematical constraint. Here is what it means in plain English.

**The problem:** We don't know exactly how much memory each workload will use at runtime. We only have an average (mu) and some idea of how much the actual usage varies (Sigma). If we just plan for the average, sometimes reality will exceed our plan and jobs will crash.

**The solution:** Add a safety buffer on top of the average. How big the buffer is depends on how confident we want to be.

The Cantelli formula gives us the buffer size:

```
kappa = sqrt( (1 - eps) / eps )
```

- If eps = 0.05 (5% risk allowed) → kappa ≈ 4.36 (big buffer, very safe)
- If eps = 0.10 (10% risk allowed) → kappa ≈ 3.00 (smaller buffer, slightly less safe)

Think of it like packing your suitcase. If you're flying to a cold country and you're 95% sure you'll need a jacket, you bring a big jacket. If you're only 90% sure, maybe you bring a lighter one.

**The full constraint for each node, resource, and time slot:**

```
(Average memory used by all workloads on the node)
+ (Safety buffer based on kappa and Sigma)
≤ (Node capacity × whether node is on)
```

The safety buffer is computed using the Cholesky decomposition of Sigma. Think of it this way: Sigma tells us how much and in what directions usage could vary. The Cholesky decomposition is just a way to break that uncertainty into a shape that math can work with — like decomposing a shadow into its x and y components.

This constraint is what makes the model a **SOCP** (Second-Order Cone Program). The buffer term forms a cone shape in math, which modern solvers like Gurobi handle efficiently.

---

## Part 5: The Trick for Binary Products (McCormick Linearizations)

We have a problem. Two of our constraints need to multiply two binary (0/1) variables together. For example, we need to know: "is this workload placed on this node AND using this isolation method?"

Multiplying variables in optimization creates a non-linear problem, which is much harder to solve. The fix is called a McCormick linearization. Instead of multiplying x × w directly, we create a new helper variable (xi) and add three rules that force xi to behave exactly like x × w:

```
xi ≤ x          (can't be 1 if x is 0)
xi ≤ w          (can't be 1 if w is 0)
xi ≥ x + w - 1  (must be 1 if both x and w are 1)
```

We do this twice:
- **xi_ijnkt** = x_ijnt × w_ijkt (workload placed AND using this isolation method)
- **zeta_...** = x_ijnt × x_i'j'nt (two workloads placed on the same node = co-located)

---

## Part 6: The Rules (Constraints)

### C1 — Every admitted workload must be placed somewhere

If a tenant is admitted (a_i = 1), then each of their workloads must be placed on exactly one node. Not zero, not two — exactly one.

> "If you book a room, you must be assigned a room. Not zero, not two rooms."

### C1b — Track which tenants are on which nodes

y_int = 1 whenever any workload of tenant i is on node n at time t. This helps us figure out which tenants are sharing a node (needed for the safety buffer calculation).

### C1c — Data residency rules

Some workloads are legally required to run only on specific nodes (e.g. data residency laws). This constraint enforces that. We simply block those x variables upfront.

### C2 — Memory must not exceed capacity (the SOCP constraint)

Already explained in Part 4. This is the probabilistic capacity rule.

### C3 — Isolation primitive rules

**C3a:** Every admitted workload must pick exactly one isolation method (bare, gVisor, or Kata). Not zero, not two.

**C3b:** Some tenants are required by compliance to use at minimum a certain isolation level (e.g. a healthcare tenant must use gVisor or better). This blocks the weaker options for those tenants.

**C3c:** If two workloads from conflicting tenants are co-located on the same node, they must use sufficiently strong isolation methods. If their interference tolerance tau is exceeded, they can't share a node without the right isolation.

### C4 — Control plane limits (cluster admin resources)

The cluster's management system (etcd, API server, services) also has limited capacity. These constraints ensure we don't overload the management layer:

- etcd write budget — how many configuration updates per slot
- API server QPS — how many requests per second
- Service count — how many active services
- Migration churn — how many jobs can move in a single slot

> Think of these as the hotel's front desk capacity. Too many check-ins at once = chaos.

### C5 — Latency SLA (response time guarantee)

Each workload has a latency target (e.g. "respond in under 50ms"). The model checks whether the predicted latency — based on base latency + co-location interference + isolation overhead — stays within that target. The slack variable e_ijt absorbs any excess.

The formula is:

```
base latency
+ interference from co-located workloads
+ overhead from isolation method
- slack (e_ijt)
≤ latency target + BIG_M × (1 - a_i)
```

The BIG_M trick: if a_i = 0 (tenant rejected), the right side becomes huge, so the constraint is automatically satisfied. This is a standard technique to "turn off" a constraint for rejected tenants.

### C6 — Migration rules

**C6a:** At the very first time slot, no workload has been anywhere before, so no migrations are possible.

**C6b:** A migration is triggered when a workload moves to a node it wasn't on in the previous slot.

**C6c:** Each tenant has a disruption budget — a maximum number of migrations they can have per slot. Moving jobs is expensive and disruptive, so this limits it.

### C7 — Fairness (DRF)

DRF stands for Dominant Resource Fairness. The idea: each tenant has a "dominant resource" — the resource they're most constrained by (either CPU or memory, whichever ratio is tighter relative to their quota). We compute a satisfaction fraction s_i for each tenant based on how much of their dominant resource they actually got.

sigma_fair is the minimum satisfaction fraction across all admitted tenants. We maximise it to make the outcome as fair as possible.

> It's like dividing a pizza: instead of everyone getting the same number of slices, everyone gets a fair share proportional to how hungry they are.

---

## Part 7: The Goal (Objective Function)

The optimizer minimises this score (lower is better):

```
lam_0 × (infrastructure cost)
+ lam_1 × (SLA penalty for latency violations)
- lam_2 × (revenue from admitted tenants)
- lam_3 × (fairness score)
+ lam_4 × (cost of isolation methods)
+ lam_5 × (cost of migrations)
```

The minus signs on revenue and fairness mean we want MORE of those — so the model earns by admitting tenants and being fair, and spends by using nodes, missing SLAs, using expensive isolation, and moving jobs around.

The lambda weights (lam_0 through lam_5) let the operator decide which things matter most. Default values:

| Weight | Value | Meaning |
|---|---|---|
| lam_0 | 1.0 | Infrastructure cost matters |
| lam_1 | 10.0 | SLA violations are very costly |
| lam_2 | 1.0 | Revenue matters |
| lam_3 | 5.0 | Fairness matters a lot |
| lam_4 | 0.5 | Isolation overhead is minor |
| lam_5 | 2.0 | Migrations are moderately penalised |

---

## Part 8: What Comes Out (Output)

After solving, the model produces one thing for each tenant and each time slot:

**A_t_i = the set of nodes tenant i is allowed to use during slot t**

Example:
```
tenant 0, slot 0 → nodes [2, 3, 4]
tenant 0, slot 1 → nodes [2, 3]
tenant 1, slot 0 → nodes [0, 1]
tenant 1, slot 1 → nodes [0, 1, 4]
```

This becomes the rulebook the real-time scheduler follows every 60 seconds. When a job arrives from tenant 0 during slot 0, the real-time model only considers nodes 2, 3, and 4.

---

## Summary

| Step | What happens |
|---|---|
| Before solving | Prediction layer provides mu, Sigma, N_it. Operator provides node capacities, pricing, SLA targets. |
| Solving | Gurobi finds the best combination of x, w, a, z, m_mig variables |
| Output | A_t_i — authorized node sets for each tenant per time slot |
| Runtime | Real-time model enforces A_t_i as a hard constraint every 60 seconds |

The model is a **MISOCP** — Mixed-Integer Second-Order Cone Program. "Mixed-integer" because some variables are binary (0/1) and some are continuous. "Second-order cone" because of the probabilistic capacity buffer. Gurobi solves it with a time limit of 300 seconds and a 1% optimality gap.
