# Plan-Ahead Model — Code Explained Simply

> **Files covered:** `plan_ahead_data.py` and `plan_ahead_optimizer.py`
> Files like `test_plan_ahead.py` and `plan_ahead_sensitivity.py` are not covered here — they are for testing and analysis, not the core model.

---

## How the Two Files Relate

Think of it like baking a cake:

- **`plan_ahead_data.py`** = the ingredients and kitchen setup (all the numbers and parameters)
- **`plan_ahead_optimizer.py`** = the recipe and oven (the actual math model and solver)

You always prepare ingredients first, then bake.

---

## File 1: `plan_ahead_data.py`

This file does three things:
1. Loads the Gurobi license credentials
2. Provides helper math functions
3. Generates synthetic (fake) data for testing

---

### Loading Gurobi credentials

```python
def _load_env_file(path):
    ...
```

Gurobi is the solver software that does the heavy math. It requires a license. This function reads a `.env` file that contains secret license keys (like a password file) and loads them into the program's environment so Gurobi can verify them.

The `.env` file is never shared publicly — that's why it's in `.gitignore`.

```python
def make_gurobi_env():
    ...
```

This creates the actual Gurobi "environment" — basically starting up the solver with the credentials. Every time we want to solve a model, we pass this environment in.

---

### The Cantelli helper

```python
def kappa(eps):
    return sqrt((1 - eps) / eps)
```

This is the safety buffer formula from the math model. It takes an epsilon (risk tolerance) and returns the kappa value used in the probabilistic capacity constraint.

- `kappa(0.05)` → 4.36 (95% confidence, large buffer)
- `kappa(0.10)` → 3.00 (90% confidence, smaller buffer)

You call this whenever you need to know "how big should my safety margin be?"

---

### Index helper

```python
def wl_index(all_wl, i, j):
    return all_wl.index((i, j))
```

The covariance matrix Sigma is a 2D table where rows and columns represent workloads. This function converts a workload (tenant i, workload j) into its row/column number in that table. It's just a lookup.

---

### Generating synthetic data

```python
def build_synthetic_data(...):
    ...
    return dict(T=T, Wi=Wi, N=N, ...)
```

Since we don't have real Google trace data wired in yet, this function makes up fake but realistic-looking numbers for everything the optimizer needs. It returns one big Python dictionary where every key is a parameter name and every value is the actual data.

Here is what it creates (matching the math model):

| What it creates | Parameter name | What it is |
|---|---|---|
| List of tenants | `T` | e.g. [0, 1, 2] |
| Workloads per tenant | `Wi` | e.g. {0: [0,1], 1: [0,1], 2: [0,1]} |
| Nodes | `N` | e.g. [0, 1, 2, 3] |
| Resource types | `R` | [0=CPU, 1=MEM] |
| Time slots | `H` | [0, 1] for a 2-slot horizon |
| Declared resource demand | `d` | Random values between 0.5 and 2.0 |
| Mean predicted usage | `mu` | Similar to d but with ±10% noise |
| Covariance matrix | `Sigma` | A random positive-semidefinite matrix |
| Node capacity | `C` | All nodes same capacity (e.g. 10.0) |
| SLA risk tolerance | `eps_i` | 0.05 for all tenants |
| Isolation overhead | `eta` | gVisor +20%, Kata +5% |
| Job count forecast | `N_it` | Constant = n_workloads_per_tenant |

The `coll_id` field assigns each workload a collection ID — this is for the prediction layer to look up the right historical average for that workload type.

---

## File 2: `plan_ahead_optimizer.py`

This file does three things:
1. Builds the math model in Gurobi
2. Solves it and prints results
3. Extracts the output (authorized node sets)

---

### `build_model(P, env)`

This is the main function. It takes the parameter dictionary P (from `build_synthetic_data`) and the Gurobi environment, and constructs the entire optimization model.

**Step 1 — Create decision variables**

```python
x = m.addVars([...], vtype=GRB.BINARY, name="x")
```

For every combination of (tenant, workload, node, time slot), Gurobi creates a binary variable. This is x_ijnt from the math. There are also variables for y, z, w, m_mig, a, e, eps_eff, xi, zeta, and sigma — all the decision variables from the model.

`wl_pairs` is a list of every unique pair of workloads. We only keep ordered pairs (so we don't double-count) — these are used for the co-location variables (zeta).

**Step 2 — Add C1 (Placement integrity)**

```python
m.addConstr(quicksum(x[i,j,n,t] for n in N) == a[i])
```

For each workload of each admitted tenant at each time slot: the sum of x across all nodes must equal a_i. Since a_i is 0 or 1, this means: if admitted, exactly one node must be chosen.

**Step 3 — Add McCormick linearizations**

```python
m.addConstr(xi[i,j,n,k,t] <= x[i,j,n,t])
m.addConstr(xi[i,j,n,k,t] <= w[i,j,k,t])
m.addConstr(xi[i,j,n,k,t] >= x[i,j,n,t] + w[i,j,k,t] - 1)
```

For every xi variable (placement × isolation), these three lines force xi = x × w. The same pattern is repeated for zeta (placement × placement).

**Step 4 — Add C2 (Probabilistic capacity — the SOCP)**

This is the most complex part of the code. Here's what it does step by step:

```python
chol[r] = np.linalg.cholesky(Sigma[r] + 1e-6 * eye)
```

First, compute the Cholesky decomposition of the covariance matrix. The `+ 1e-6 * eye` is a small regularization added to prevent numerical issues when the matrix is nearly singular (nearly flat). If Cholesky fails (which shouldn't happen with this fix), it falls back to the identity matrix.

```python
kap_node[n] = kappa(min(eps_i for tenants with access to n))
```

For each node, compute the kappa safety factor using the strictest (most risk-averse) tenant that has access to that node. This ensures we never plan too aggressively for tenants who require high confidence.

```python
mean_load = quicksum(eta[k,r] * mu[i,j,r] * xi[i,j,n,k,t] ...)
```

The mean load on a node: sum up the predicted average usage of every workload placed there, multiplied by the isolation overhead (eta). We use xi (not x) because we need to know both placement AND which isolation method is used.

```python
soc_comps = []
for s in range(n_wl):
    comp = m.addVar(lb=-GRB.INFINITY)
    m.addConstr(comp == quicksum(L_mat[s,q] * xi[...] for q in range(n_wl)))
    soc_comps.append(comp)
m.addQConstr(sum(c*c for c in soc_comps) <= soc_aux * soc_aux)
```

This constructs the SOCP safety buffer term: `||chol_r × xi_vec||_2`. Each `comp` variable is one component of the result of multiplying the Cholesky matrix by the placement vector. Then `addQConstr` says "the sum of squares of all components must be ≤ soc_aux²" — that is the definition of an L2 norm (Euclidean length).

```python
m.addConstr(mean_load + kap_node[n] * soc_aux <= C[n,r] * z[n,t])
```

Finally, the full constraint: mean load + (kappa × safety buffer) ≤ node capacity × (is node on?). If z[n,t] = 0 (node off), the right side is 0 and no workload can be placed there.

**Step 5 — Add C3, C4, C5, C6, C7**

These follow naturally from the math and are more straightforward:

- **C3a:** `sum(w[i,j,k,t] for k in K) == a[i]` — exactly one isolation method
- **C3b:** `w[i,j,k,t] = 0 if k < k_min[i]` — compliance floor
- **C3c:** interference check using zeta and w together
- **C4a-d:** control plane budget sums ≤ budget limits
- **C5:** latency formula ≤ SLA target + BIG_M slack
- **C6a-c:** migration detection and budget
- **C7:** DRF fairness — sigma ≤ s_i for all admitted tenants

**Step 6 — Set the objective**

```python
obj = (
    lam[0] * infra_cost
  + lam[1] * sla_penalty
  - lam[2] * admission_revenue
  - lam[3] * sigma
  + lam[4] * isolation_cost
  + lam[5] * migration_cost
)
m.setObjective(obj, GRB.MINIMIZE)
```

Six terms, six weights. Minus signs on revenue and fairness because we want to maximise those (minimizing a negative = maximizing).

---

### `solve_and_report(model, vars_, P)`

```python
model.Params.TimeLimit = 300
model.Params.MIPGap    = 0.01
model.optimize()
```

- `TimeLimit = 300` — stop after 5 minutes even if not perfectly optimal
- `MIPGap = 0.01` — stop if within 1% of the theoretical best solution

After solving, it reads the `.X` attribute of each variable (`.X` gives the numeric value Gurobi found) and prints a human-readable summary.

---

### `extract_tenant_access_schedule(vars_, P)`

```python
for i in T:
    for t in H:
        nodes = [n for n in N if sum(x[i,j,n,t].X for j in Wi[i]) >= 0.5]
        schedule[(i, t)] = nodes
```

After solving, this function reads all the x variables and collects, for each (tenant, time slot), which nodes had at least one of that tenant's workloads placed on them. That becomes the authorized node set A_t_i.

The output looks like:
```python
{
    (0, 0): [2, 3, 4],   # tenant 0, slot 0 → nodes 2, 3, 4
    (0, 1): [2, 3],      # tenant 0, slot 1 → nodes 2, 3
    (1, 0): [0, 1],      # tenant 1, slot 0 → nodes 0, 1
}
```

This dictionary is handed to the real-time optimizer as `tenant_node_access`.

---

## How It All Runs

```python
P   = build_synthetic_data()      # Step 1: prepare data
env = make_gurobi_env()           # Step 2: start Gurobi
m, vars_ = build_model(P, env)    # Step 3: build the math model
solve_and_report(m, vars_, P)     # Step 4: solve and print
schedule = extract_tenant_access_schedule(vars_, P)  # Step 5: extract output
```

Five lines. Everything else in the codebase is either supporting these steps or testing them.
