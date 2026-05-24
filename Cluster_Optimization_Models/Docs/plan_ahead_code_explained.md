# Plan-Ahead Model — Code Explained Simply

> **Files covered:** `plan_ahead_data.py` and `plan_ahead_optimizer.py`

---

## How the Two Files Relate

Think of it like baking a cake:

- **`plan_ahead_data.py`** = the ingredients and kitchen setup (parameters and data)
- **`plan_ahead_optimizer.py`** = the recipe and oven (the math model and solver)

You always prepare the ingredients first, then bake.

---

## File 1: `plan_ahead_data.py`

This file does three things:
1. Loads the Gurobi license credentials
2. Provides a function to start Gurobi
3. Generates the data the optimizer needs

---

### Loading Gurobi credentials

```python
def _load_env_file(path): ...
def make_gurobi_env(): ...
```

Gurobi is the solver that does the heavy math. It requires a license stored in a `.env` file (never committed to source control). `_load_env_file` reads the credentials; `make_gurobi_env` starts Gurobi with them. Every solve call needs this environment object.

---

### Generating the data

```python
def build_synthetic_data(
    seed, n_tenants, n_nodes, n_intervals,
    node_capacity, n_always_available, exclusive_frac,
    tenant_usage_min, tenant_usage_max,
    sigma_frac=0.20, epsilon=0.10, ...
)
```

Since we don't have live cluster trace data wired in yet, this function makes up realistic-looking numbers for testing. It returns a single dictionary `P` with everything the optimizer needs.

| What it creates | Key in `P` | What it means |
|---|---|---|
| All tenants | `T` | e.g. `[0, 1, 2, 3]` |
| Exclusive tenants | `T_e` | Randomly chosen (fraction = exclusive_frac), e.g. `[1]` |
| Shared tenants | `T_s` | Everything not exclusive, e.g. `[0, 2, 3]` |
| Heavy shared tenants | `T_s_heavy` | Shared tenants with above-median avg usage |
| Light shared tenants | `T_s_light` | Shared tenants with below-median avg usage |
| All machines | `M` | e.g. `[0, 1, 2, 3, 4]` |
| Always-available machines | `M_a` | First n_always_available nodes, e.g. `[0, 1, 2]` |
| Additional machines | `M_b` | Remaining nodes, e.g. `[3, 4]` |
| Planning intervals | `H` | e.g. `[0, 1, 2]` for a 3-interval horizon |
| Node capacity | `C` | Same for all nodes (e.g. 10.0 capacity units) |
| Effective capacity | `C_eff` | Reduced by SLA feedback: `C[n] × (1 − α × v̄_n)` |
| Infrastructure cost | `pi_n` | Cost to activate additional machine n |
| Usage profiles | `u` | Estimated demand of tenant i in interval h |
| Demand variance | `sigma2` | Uncertainty in u[i,h]; SOCP mode only |
| Safety factor | `kappa` | Size of safety buffer; = √((1−ε)/ε) |
| Objective weights | `lam` | How much to weight infra cost, fairness, mix bonus |

**`u[i,h]`** is the key input: how much resource tenant `i` is expected to consume in interval `h`. In production this comes from a prediction layer. For now it is a random number in `[tenant_usage_min, tenant_usage_max]`.

**`sigma2[i,h]`** and **`kappa`** are only used in SOCP mode. They encode how uncertain the usage prediction is and how large a safety buffer the capacity constraint should reserve.

**Exclusive vs shared split:** `exclusive_frac` (e.g. 0.25) randomly picks that fraction of tenants as "exclusive" (T_e). Exclusive tenants get dedicated machines assigned for the entire horizon — they never share with other tenants. Shared tenants (T_s) are reassigned per interval and may move between machines.

**Heavy vs light classification:** Among shared tenants, those whose average u[i,h] is above the median are tagged "heavy" (T_s_heavy); the rest are "light" (T_s_light). This feeds the mix bonus objective.

---

## File 2: `plan_ahead_optimizer.py`

This file does three things:
1. Builds the math model in Gurobi
2. Solves it and prints results
3. Extracts the output (which tenants go to which machines each interval)

---

### `build_model(P, env, use_socp=True)`

This is the core function. It takes the data dictionary `P` and the Gurobi environment and builds the entire optimization model.

#### Two modes: MILP and MISOCP

The `use_socp` flag controls which capacity constraint is used:

| Mode | `use_socp` | Capacity constraint | Speed |
|---|---|---|---|
| MILP | `False` | Plain linear: total allocation ≤ capacity | Fast |
| MISOCP | `True` | Cantelli: allocation + safety buffer ≤ capacity, cone constraint on buffer | Slower but safer |

**MILP is the default for the simulation** (it runs every 50 steps, so speed matters).
**MISOCP is the default for sample 2 and 3 in the pipeline** — the extra solve time is acceptable, and the probabilistic capacity guarantee is worth having.

---

#### Decision variables

```python
e[i,n]        # 1 if exclusive tenant i is assigned to machine n (entire horizon)
z_on[n]       # 1 if additional machine n is activated
y[i,n,h]      # 1 if shared tenant i uses machine n in interval h
f[i,n,h]      # fraction of machine n's capacity allocated to shared tenant i in h
sigma         # fairness score: minimum demand-satisfaction ratio across all tenants
t[n,h]        # (SOCP mode only) safety buffer for machine n in interval h
has_heavy[n,h]  # 1 if any heavy tenant uses machine n in interval h
has_light[n,h]  # 1 if any light tenant uses machine n in interval h
mix[n,h]      # 1 if machine n has both heavy AND light tenants in interval h
```

Compared to older versions, there is no `a[i]` (admission) variable — **every tenant must be assigned machines every interval**. Admission is not optional.

---

#### Constraints (in order)

**C_aa — Always-available machines are always on**
```
z_on[n] = 1    for all n ∈ M_a
```
M_a machines are fixed on for every interval. Only M_b machines have a variable activation.

**C_act — Additional machines need activation**
```
z_on[n] ∈ {0, 1}    for all n ∈ M_b
```
The model decides which additional machines to turn on based on cost vs capacity needs.

**C_excl1 — Each exclusive tenant gets exactly one machine**
```
Σ_n e[i,n] = 1    for all i ∈ T_e
```

**C_excl2 — Exclusive machines are exclusive (no two exclusive tenants share)**
```
Σ_{i∈T_e} e[i,n] ≤ 1    for all n ∈ M
```

**C_excl_cap — Exclusive tenant's demand must fit on their assigned machine**
```
u_max[i] × e[i,n] ≤ C[n]    for all i ∈ T_e, n ∈ M
```

**C_sep — Exclusive and shared tenants cannot share a machine in any interval**
```
Σ_{i∈T_e} e[i,n] + y[j,n,h] ≤ 1    for all j ∈ T_s, n ∈ M, h ∈ H
```
This is the key separation constraint. If an exclusive tenant is on machine n (`e[i,n]=1`), no shared tenant can be on the same machine in any interval.

**C_share — Each shared tenant uses at most one machine per interval**
```
Σ_n y[i,n,h] ≤ 1    for all i ∈ T_s, h ∈ H
```

**C1a / C1b — Capacity constraint**

In MILP mode (C1a only):
```
Σ_{i∈T_s} f[i,n,h] ≤ C_eff[n] × z_on[n]    for all n ∈ M, h ∈ H
```

In MISOCP mode (C1a + C1b):
```
C1a: Σ f[i,n,h] + κ·t[n,h] ≤ C_eff[n] × z_on[n]
C1b: t[n,h]² ≥ Σ_{i∈T_s} sigma2[i,h] × y[i,n,h]
```
C1b is a second-order cone constraint. It forces the safety buffer `t[n,h]` to grow with the uncertainty of the tenants assigned to that machine. The combination guarantees: even if actual usage is higher than predicted, the machine stays within capacity at least (1−ε) of the time (90% with ε=0.10).

**C2 — Demand satisfaction (all tenants, every interval)**
```
Σ_n f[i,n,h] ≥ u[i,h]    for all i ∈ T_s, h ∈ H
```
Every shared tenant's demand must be fully covered every interval. (Exclusive tenants are handled by C_excl_cap.)

**C3 — Node activation**
```
z_on[n] ≥ y[i,n,h]    for all i ∈ T_s, n ∈ M, h ∈ H
```
A machine must be on if any shared tenant is assigned to it.

**C4 — Fairness**
```
sigma ≤ (Σ_{n,h} f[i,n,h]) / (Σ_h u[i,h])    for all i ∈ T_s
```
`sigma` is the minimum demand-satisfaction fraction across all shared tenants. The optimizer is pushed to maximize it via the objective.

**Mix constraints — Linearized AND for co-location bonus**
```
mix[n,h] ≤ has_heavy[n,h]
mix[n,h] ≤ has_light[n,h]
mix[n,h] ≥ has_heavy[n,h] + has_light[n,h] − 1
```
`mix[n,h] = 1` if and only if machine n has both a heavy tenant AND a light tenant in interval h. This is a standard linearization of an AND gate using three inequality constraints instead of a nonlinear product.

---

#### Objective

```python
obj = lam[0] * infra_cost  -  lam[1] * sigma  -  lam[2] * mix_total
```

Three competing goals:
- `infra_cost = Σ_{n∈M_b} pi_n·z_on[n]` — use fewer additional machines (cost efficiency)
- `sigma` — maximize the minimum tenant satisfaction (fairness)
- `mix_total = Σ_{n,h} mix[n,h]` — maximize co-location of heavy and light tenants (diversity bonus)

The λ weights (set in `build_synthetic_data`) control the balance between these goals.

---

### `solve_and_report(model, vars_, P)`

```python
model.Params.TimeLimit = 120    # stop after 2 minutes
model.Params.MIPGap    = 0.01   # stop if within 1% of optimal
model.optimize()
```

After solving, reads each variable's `.X` attribute (the numeric value Gurobi found) and prints a summary.

---

### `extract_plan_output(vars_, P)`

Reads the solved variables and constructs the output format consumed by the Cluster Manager and Pipeline:

```python
plan_output = {
    "intervals": [
        {
            "interval": 0,
            "groups": [
                # Exclusive tenants group (one per exclusive tenant)
                {"tenant_ids": [1], "machine_ids": [2, 4], "exclusive": True},
                # Shared tenants group (all shared tenants for this interval)
                {"tenant_ids": [0, 2, 3], "machine_ids": [0, 1], "exclusive": False},
            ]
        },
        {
            "interval": 1,
            "groups": [
                {"tenant_ids": [1], "machine_ids": [2, 4], "exclusive": True},
                {"tenant_ids": [0, 2, 3], "machine_ids": [0, 3], "exclusive": False},
            ]
        },
    ]
}
```

Key points:
- **Exclusive assignments are the same every interval** (because `e[i,n]` has no h subscript)
- **Shared assignments can change between intervals** (because `y[i,n,h]` varies with h)
- Every tenant appears in exactly one group in every interval — no tenant is ever unassigned

The Cluster Manager receives this dict and calls `solve()` once per group per interval, pre-filtering jobs and nodes to only what that group is assigned.

---

## How It All Runs (standalone)

```python
P   = build_synthetic_data(...)        # Step 1: prepare data
env = make_gurobi_env()                # Step 2: start Gurobi
m, vars_ = build_model(P, env)         # Step 3: build MILP or MISOCP
solve_and_report(m, vars_, P)          # Step 4: solve and print
plan = extract_plan_output(vars_, P)   # Step 5: extract interval groups
```

Five lines. When called from the simulation, `use_socp=False` is typically passed to step 3 so the model runs as a plain MILP for speed (default config: plan fires every 50 steps).
