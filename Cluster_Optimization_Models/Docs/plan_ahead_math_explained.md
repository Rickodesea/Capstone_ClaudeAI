# Plan-Ahead Model — Mathematical Reference

## 1. Overview

The plan-ahead model is a **Mixed-Integer Second-Order Cone Program (MISOCP)** solved
once per planning horizon (every `horizon_steps` simulation intervals). It answers:

> *Which machines should each tenant be assigned to for each planning period,
> given their estimated resource demand, machine pool, and feedback from the previous horizon?*

### What the model decides

1. Which additional machines (beyond the always-available pool) to activate.
2. Which machines to assign **exclusively** to exclusive tenants (fixed for the entire horizon).
3. Which machines to assign to **shared** tenants per period (can change each period).
4. How to **mix** high-usage and low-usage shared tenants on the same machines.

### Output

An ordered list of intervals (total = planning horizon). Each interval has a list of
**tenant groups**. Each group specifies a set of tenant IDs and their assigned machine IDs.
All tenants appear in every interval's group list. The Cluster Manager uses this output to:
- Filter jobs by tenant group before calling the Real-Time model.
- Filter machines by the group's machine list.
- Call the Real-Time model once per group per interval.

### Two solver modes

| Mode | `use_socp` | When used | Capacity constraint |
|------|-----------|-----------|---------------------|
| MISOCP | `True` (**default everywhere**) | All contexts | Cantelli probabilistic bound |
| MILP | `False` (opt-in only) | Simulation toggle | Hard linear bound |

MISOCP is the default in simulation, pipeline, sensitivity analysis, and all tests.
MILP is available as an option but never enabled by default.

---

## 2. Sets

| Symbol | Description |
|--------|-------------|
| **T** | All tenants: T = T_e ∪ T_s |
| **T_e** | Exclusive tenants — dedicated machine assignment, fixed for the entire horizon. Do not share machines with others. |
| **T_s** | Shared tenants — T \ T_e. May share machines with other shared tenants. |
| **M** | All machines in the cluster pool: M = M_a ∪ M_b |
| **M_a** | Always-available machines (|M_a| = A). Always active; no activation cost. |
| **M_b** | Additional machines (M \ M_a). The model decides whether to turn these on. |
| **H** | Planning periods (slots): H = {0, 1, …, |H|−1}. Each period spans `period_steps` intervals. |

---

## 3. Parameters

### Machine parameters

| Symbol | Description |
|--------|-------------|
| C[n] | Resource capacity of machine n (abstract units) |
| π_n | Infrastructure cost per additional machine n ∈ M_b (activation cost) |

### Tenant demand parameters

| Symbol | Description |
|--------|-------------|
| u[i,h] | Expected resource demand of tenant i in period h |
| u_max[i] | Peak demand of exclusive tenant i: max_h u[i,h] |
| σ²[i,h] | Demand variance: σ²[i,h] = (σ_frac × u[i,h])² |
| σ_frac | Uncertainty fraction — std dev is this fraction of mean demand (default 0.20) |

### Uncertainty / safety parameters

| Symbol | Description |
|--------|-------------|
| ε | Tail probability for Cantelli bound (default 0.10 → 90% safety) |
| κ | Cantelli factor: κ = √((1−ε)/ε). For ε=0.10: κ=3.0 |

### Feedback parameters (from previous horizon)

| Symbol | Description |
|--------|-------------|
| v̄_n | Rolling SLA violation rate on machine n from realtime (0 on first run) |
| W̄_i | Average scheduling wait time of tenant i (in intervals) from realtime |
| C_eff[n] | Effective capacity after SLA feedback: C_eff[n] = C[n] × (1 − α·v̄_n) |
| u_fb[i,h] | Feedback-adjusted demand: u_fb[i,h] = u[i,h] × (1 + β·min(2, W̄_i/W̄_ref)) |
| α | Capacity shrinkage coefficient (default 0.5) |
| β | Demand inflation coefficient (default 0.3) |
| W̄_ref | Reference wait time in intervals (default 10 intervals) |

### Scheduling policy parameters

| Symbol | Description |
|--------|-------------|
| m_min | Minimum machines per shared tenant per period (default 2; set via `min_machines_per_tenant`) |

### Objective weights

| Symbol | Description |
|--------|-------------|
| λ_0 | Infrastructure cost weight (minimize activated additional machines) |
| λ_1 | Fairness weight (maximize min demand-satisfaction ratio σ for shared tenants) |
| λ_2 | Mix bonus weight (reward pairing heavy + light shared tenants per machine) |

---

## 4. Decision Variables

| Variable | Domain | Description |
|----------|--------|-------------|
| **e[i,n]** | {0,1} | 1 iff exclusive tenant i ∈ T_e is assigned to machine n. Fixed for entire horizon. |
| **z_on[n]** | {0,1} | 1 iff additional machine n ∈ M_b is activated at all. |
| **z[n,h]** | {0,1} | 1 iff machine n is active in period h. |
| **y[i,n,h]** | {0,1} | 1 iff shared tenant i ∈ T_s is assigned to machine n in period h. |
| **f[i,n,h]** | ≥ 0 | Capacity allocation of machine n to shared tenant i in period h. |
| **σ** | [0,1] | Minimum demand-satisfaction ratio across all shared tenants (fairness). |
| **t[n,h]** | ≥ 0 | Cantelli slack — upper bound on weighted demand std dev (SOCP mode only). |
| **has_heavy[n,h]** | {0,1} | 1 iff machine n has ≥1 heavy-demand shared tenant in period h. |
| **has_light[n,h]** | {0,1} | 1 iff machine n has ≥1 light-demand shared tenant in period h. |
| **mix[n,h]** | {0,1} | 1 iff machine n has both heavy and light shared tenants in period h. |

---

## 5. Constraints

### Machine Activation

**C_aa (Always Available)**
Always-available machines are always active — no decision needed:
```
z[n,h] = 1    ∀ n ∈ M_a, h ∈ H
```

**C_act (Additional Machine Activation)**
Additional machines are active in interval h only if switched on:
```
z[n,h] ≤ z_on[n]    ∀ n ∈ M_b, h ∈ H
```

**C_zact (Machine Active if Assigned)**
A machine must be active if any shared tenant is assigned to it:
```
z[n,h] ≥ y[i,n,h]    ∀ i ∈ T_s, n ∈ M, h ∈ H
```
Exclusive-machine activation is implied by always-available or additional-machine activation.

---

### Exclusive Tenant Constraints

**C_excl1 (At Most One Exclusive Per Machine)**
Each machine can be exclusively held by at most one exclusive tenant:
```
Σ_{i ∈ T_e} e[i,n] ≤ 1    ∀ n ∈ M
```

**C_excl2 (Each Exclusive Must Be Assigned)**
Every exclusive tenant must receive at least one machine:
```
Σ_{n ∈ M} e[i,n] ≥ 1    ∀ i ∈ T_e
```

**C_excl_cap (Exclusive Capacity Coverage)**
The machines assigned exclusively to tenant i must together provide enough capacity for its peak demand:
```
Σ_{n ∈ M} e[i,n] × C[n] ≥ u_max[i]    ∀ i ∈ T_e
```

**C_sep (Exclusive-Shared Separation)**
A machine assigned exclusively to any exclusive tenant cannot be used by shared tenants in any interval:
```
Σ_{i ∈ T_e} e[i,n] + y[j,n,h] ≤ 1    ∀ j ∈ T_s, n ∈ M, h ∈ H
```

---

### Shared Tenant Constraints

**C_share (Each Shared Tenant Must Be Assigned Minimum Machines Per Period)**
Every shared tenant must receive at least `m_min` machines every period.
Default `m_min = 2` (configurable via `min_machines_per_tenant`).
Setting m_min ≥ 2 provides resilience when one assigned machine spikes or fails:
```
Σ_{n ∈ M} y[i,n,h] ≥ m_min    ∀ i ∈ T_s, h ∈ H
```

**C1a (Capacity with Safety Buffer)**
Total allocation plus κ times the Cantelli slack must fit in machine capacity:
```
Σ_{i ∈ T_s} f[i,n,h] + κ · t[n,h] ≤ C_eff[n] · z[n,h]    ∀ n ∈ M, h ∈ H
```
In MILP mode: same without κ · t[n,h] term.

**C1b (Cantelli Cone — SOCP only)**
The slack t[n,h] must cover the weighted demand standard deviation:
```
Σ_{i ∈ T_s} σ²[i,h] · y[i,n,h] ≤ t[n,h]²    ∀ n ∈ M, h ∈ H
```
Together, C1a + C1b guarantee: P[actual total shared demand ≤ C_eff[n]] ≥ 1 − ε.

**C2 (Priority Link)**
A shared tenant can only receive allocation on a machine it is assigned to:
```
f[i,n,h] ≤ C[n] · y[i,n,h]    ∀ i ∈ T_s, n ∈ M, h ∈ H
```

**C3 (Demand Satisfaction)**
Each shared tenant must receive at least its feedback-adjusted demand:
```
Σ_{n ∈ M} f[i,n,h] ≥ u_fb[i,h]    ∀ i ∈ T_s, h ∈ H
```

**C4 (Fairness — Min Demand-Satisfaction Ratio)**
σ is the minimum fraction of total demand satisfied across all shared tenants:
```
σ ≤ (Σ_{n,h} f[i,n,h]) / (Σ_h u_fb[i,h])    ∀ i ∈ T_s
```

---

### Mix Bonus Constraints

Heavy tenants: T_s_heavy = {i ∈ T_s : avg_h u[i,h] ≥ median over T_s}
Light tenants: T_s_light = T_s \ T_s_heavy

```
has_heavy[n,h] ≥ y[i,n,h]    ∀ i ∈ T_s_heavy, n ∈ M, h ∈ H
has_light[n,h] ≥ y[j,n,h]    ∀ j ∈ T_s_light, n ∈ M, h ∈ H
mix[n,h] ≤ has_heavy[n,h]    ∀ n, h
mix[n,h] ≤ has_light[n,h]    ∀ n, h
mix[n,h] ≥ has_heavy[n,h] + has_light[n,h] − 1    ∀ n, h
```
mix[n,h] = 1 iff machine n has at least one heavy AND at least one light shared tenant in period h.

---

## 6. Objective

```
Minimize: λ_0 · infra_cost − λ_1 · σ − λ_2 · mix_total

where:
  infra_cost = Σ_{n ∈ M_b} π_n · z_on[n]     (cost of activating additional machines)
  mix_total  = Σ_{n ∈ M, h ∈ H} mix[n,h]     (total machine-interval pairs with both heavy + light)
```

Three competing goals:
1. **Minimize infrastructure** — activate as few additional machines as possible.
2. **Maximize fairness (σ)** — every shared tenant gets its fair share of demand.
3. **Maximize mixing** — encourage heavy and light tenants to share machines.

---

## 7. Uncertainty Model (Cantelli)

**Why Cantelli?**  
We do not know the true distribution of tenant demand — only mean u[i,h] and variance σ²[i,h].
Cantelli's inequality is *distribution-free*: it provides a one-sided safety bound using only mean and variance.

**Model:**
- Tenant i's actual demand in interval h on machine n is a random variable D_{i,n,h}.
- D_{i,n,h} has mean f[i,n,h] (planned allocation) and variance σ²[i,h] · y[i,n,h] (uncertainty active only when assigned).
- Total demand on machine n: D_n,h = Σ_i D_{i,n,h}.

**Cantelli bound applied to the sum:**
```
P[ D_n,h > μ + κ·σ_total ] ≤ 1/(1+κ²) = ε

where: μ = Σ_i f[i,n,h]   (mean total),  σ_total² = Σ_i σ²[i,h]·y[i,n,h]
```

**C1a + C1b together enforce:**
```
μ + κ·σ_total ≤ C_eff[n]·z[n,h]
→ P[ D_n,h > C_eff[n]·z[n,h] ] ≤ ε
→ P[ D_n,h ≤ C_eff[n]·z[n,h] ] ≥ 1 − ε
```

For ε=0.10, κ=3.0: machines overflow at most 10% of the time under the worst-case demand distribution.

---

## 8. Feedback Integration

After the first planning horizon, the Real-Time model reports:

- **v̄_n**: rolling SLA violation rate per machine (fraction of intervals where demand > capacity)
- **W̄_i**: average scheduling wait time per tenant (seconds)

These feed back into the plan-ahead parameters:

```
C_eff[n] = C[n] × (1 − α · v̄_n)
u_fb[i,h] = u[i,h] × (1 + β · min(2, W̄_i / W̄_ref))
```

- High violation rate → conservative capacity → fewer tenants packed per machine.
- High wait time → inflated demand → model assigns more machines for that tenant.
- The `min(2, …)` cap prevents extreme demand inflation from making the MISOCP infeasible.

Default: **α = 0.5, β = 0.3, W̄_ref = 10 intervals** (calibrated to simulated time-steps,
not wall-clock seconds; at W̄_i = 10 intervals the scale factor is 1.30×).

---

## 9. Output Format

```
PlanAheadOutput = {
    "periods": [
        {
            "period": 0,
            "groups": [
                {
                    "tenant_ids": [2],           # exclusive tenant (single-tenant group)
                    "machine_ids": [0, 1],        # their machines (same every period)
                    "exclusive": True
                },
                {
                    "tenant_ids": [0],            # shared tenant
                    "machine_ids": [2, 4],        # their machines this period
                    "exclusive": False
                },
                {
                    "tenant_ids": [1, 3],         # shared tenants grouped together
                    "machine_ids": [3],
                    "exclusive": False
                },
            ]
        },
        {
            "period": 1,
            "groups": [...]      # exclusive tenant groups unchanged; shared may differ
        },
        ...
    ]
}
```

**Rules:**
- All tenants appear in every period.
- Exclusive tenant groups have the same machine_ids across all periods (entire horizon).
- Shared tenant groups may change machine_ids between periods.
- The Cluster Manager loops through groups in order and calls the Real-Time model once per group per interval within that period.

---

## 10. Connection to Real-Time Model

The Cluster Manager uses the plan-ahead output as follows each scheduling interval:

1. Determine the current period index `h = (simulation_interval // period_steps) % |H|`.
2. Get the group list for period h.
3. For each group in order (the Real-Time model is called **multiple times per interval** — once per group):
   - Filter the job queue to jobs whose `tenant_id ∈ group.tenant_ids`.
   - Filter the machine list to machines whose `node_id ∈ group.machine_ids`.
   - Call the Real-Time model: `placements = solve(group_jobs, group_machines, W_t, K)`.
   - Place returned assignments; unplaced jobs return to the queue with wait bumped by 1 interval.
4. Advance to the next interval.

The Real-Time model has **no knowledge** of tenants, plan-ahead, or machine assignments.
It receives a filtered job list and machine list and returns the best placement given capacity.
