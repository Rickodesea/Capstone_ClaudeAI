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
    n_tenants, n_nodes, n_time_slots,
    node_capacity, tenant_usage_min, tenant_usage_max,
    sigma_frac=0.20, epsilon=0.10,
    ...
)
```

Since we don't have live cluster trace data wired in yet, this function makes up realistic-looking numbers for testing. It returns a single dictionary `P` with everything the optimizer needs.

| What it creates | Key in `P` | What it means |
|---|---|---|
| Tenant list | `T` | e.g. `[0, 1, 2]` |
| Node list | `N` | e.g. `[0, 1, 2, 3]` |
| Planning periods | `H` | e.g. `[0, 1]` for a 2-period horizon |
| Node capacity | `C` | Same for all nodes (e.g. 10.0 capacity units) |
| Infrastructure cost | `pi_n` | Cost to activate node n per period |
| Contract revenue | `pi_bar` | Revenue gained by admitting tenant i |
| Operational cost | `v_op` | Cost to serve tenant i |
| Usage profiles | `u` | Estimated total demand of tenant i in period h |
| Objective weights | `lam` | How much to weight cost, revenue, fairness |
| Demand variance | `sigma2` | Uncertainty in u[i,h]; used by SOCP mode only |
| Safety factor | `kappa` | Size of the safety buffer; = √((1−ε)/ε) |

**`u[i,h]`** is the key input: how much resource tenant `i` is expected to consume in period `h`. In production this comes from a prediction layer (e.g. time-series forecast of historical usage). For now it is a random number in `[tenant_usage_min, tenant_usage_max]`.

**`sigma2[i,h]`** and **`kappa`** are only used in SOCP mode. They encode how uncertain the usage prediction is and how large a safety buffer the capacity constraint should reserve. See File 2 for details.

---

## File 2: `plan_ahead_optimizer.py`

This file does three things:
1. Builds the math model in Gurobi
2. Solves it and prints results
3. Extracts the output (which nodes each tenant is prioritised on)

---

### `build_model(P, env, use_socp=True)`

This is the core function. It takes the data dictionary `P` and the Gurobi environment and builds the entire optimization model.

#### Two modes: MILP and SOCP

The `use_socp` flag controls which capacity constraint is used:

| Mode | `use_socp` | Capacity constraint | Speed |
|---|---|---|---|
| MILP | `False` | Plain linear: total allocation ≤ capacity | Fast |
| MISOCP | `True` | Cantelli: allocation + safety buffer ≤ capacity, with cone constraint on buffer | Slower but safer |

**MILP is the default for the simulation** (it runs every 50 steps, so speed matters).
**SOCP is the default when running the plan-ahead model by itself** — since it only runs once, the extra solve time is fine, and the probabilistic capacity guarantee is worth having.

#### Step 1 — Decision variables

```python
f[i,n,h]  # how much of node n's capacity to allocate to tenant i in period h
y[i,n,h]  # 1 if tenant i is a priority on node n in period h, else 0
z[n,h]    # 1 if node n is switched on in period h, else 0
a[i]      # 1 if tenant i is admitted for this planning horizon, else 0
sigma     # fairness score: minimum demand-satisfaction ratio across all admitted tenants
t[n,h]    # (SOCP mode only) safety buffer variable for node n in period h
```

#### Step 2 — C1: Capacity constraint

**MILP mode** — straightforward:
```
total allocation on node n in period h  ≤  node capacity × (is node on?)
```

**SOCP mode** — two-part Cantelli constraint:
```
C1a: total allocation + safety buffer  ≤  node capacity × (is node on?)
C1b: safety buffer²  ≥  sum of (demand variance × is tenant assigned here?)
```
C1b is a quadratic (second-order cone) constraint — it forces the safety buffer `t[n,h]` to grow with the uncertainty of the tenants assigned to that node. The combination guarantees: even if actual usage is higher than predicted, the node stays within capacity at least 90% of the time (with default ε=0.10).

#### Step 3 — C2: Priority link

```
f[i,n,h]  ≤  node_capacity × y[i,n,h]
```

If `y[i,n,h] = 0`, no capacity can be allocated to tenant i on node n. This links the continuous allocation variable to the binary priority indicator.

#### Step 4 — C3: Demand satisfaction

```
total allocation for tenant i in period h  ≥  u[i,h] × (is tenant admitted?)
```

Admitted tenants must receive at least their predicted demand. Rejected tenants (a[i]=0) have this turned off.

#### Step 5 — C4: Fairness

```
sigma  ≤  (total allocation for tenant i across all periods) / (total demand)  +  (1 - a[i])
```

`sigma` is the minimum demand-satisfaction fraction across all admitted tenants. The optimizer is pushed to maximise it (via the objective). The `(1 - a[i])` term deactivates this constraint for rejected tenants.

#### Step 6 — C5: Node activation

```
z[n,h]  ≥  y[i,n,h]
```

A node must be marked active if any tenant is assigned to it.

#### Step 7 — Objective

```
Minimise:  λ₀ × infrastructure cost  −  λ₁ × admission revenue  −  λ₂ × fairness
```

Three competing goals: use fewer nodes, admit more tenants, treat all admitted tenants fairly. The λ weights (set in `build_synthetic_data`) control the balance.

---

### `solve_and_report(model, vars_, P)`

```python
model.Params.TimeLimit = 300    # stop after 5 minutes
model.Params.MIPGap    = 0.01   # stop if within 1% of optimal
model.optimize()
```

After solving, it reads each variable's `.X` attribute (the numeric value Gurobi found) and prints a summary: which tenants were admitted, which nodes they were assigned to, and the final fairness score.

---

### `extract_tenant_access_schedule(vars_, P)`

```python
for i in T:
    for h in H:
        nodes = [n for n in N if y[i, n, h].X > 0.5]
        schedule[(i, h)] = nodes
```

Reads the solved `y` variables and collects, for each (tenant, period), which nodes have `y=1`. This is the **priority hint** handed to the real-time scheduler.

The output looks like:
```python
{
    (0, 0): [2, 3],   # tenant 0, period 0 → prioritised on nodes 2 and 3
    (0, 1): [2],      # tenant 0, period 1 → prioritised on node 2
    (1, 0): [0, 1],   # tenant 1, period 0 → prioritised on nodes 0 and 1
}
```

The real-time scheduler receives this and applies a `PRIORITY_BOOST` multiplier to the objective coefficient for jobs that would land on a priority node. No node is blocked — it is a preference, not a rule.

---

## How It All Runs (standalone)

```python
P   = build_synthetic_data()           # Step 1: prepare data (with sigma2/kappa)
env = make_gurobi_env()                # Step 2: start Gurobi
m, vars_ = build_model(P, env)         # Step 3: build MISOCP (use_socp=True by default)
solve_and_report(m, vars_, P)          # Step 4: solve and print
schedule = extract_tenant_access_schedule(vars_, P)  # Step 5: extract priority hints
```

Five lines. When called from the simulation, `use_socp=False` is passed to step 3 so the model runs as a plain MILP for speed.
