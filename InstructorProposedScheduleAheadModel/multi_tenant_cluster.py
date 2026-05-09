"""
Multi-Tenant Cluster Optimization — MISOCP
Implements the full model from full_model.docx using Gurobi.
"""

import numpy as np
import gurobipy as gp
from gurobipy import GRB

params = {
          "WLSACCESSID":"fc17fa3a-ef7f-41d2-b95c-20c3b221a483",
          "WLSSECRET":"6bee54d1-5c9f-4f12-9d64-0c7b16e0dd52",
          "LICENSEID":2804943
          }
env = gp.Env(params = params)

#create the model within
model = gp.Model(env = env)

# ---------------------------------------------------------------------------
# 1.  SYNTHETIC DATA  (replace with real inputs)
# ---------------------------------------------------------------------------

def build_synthetic_data():
    params = {
        "WLSACCESSID": "fc17fa3a-ef7f-41d2-b95c-20c3b221a483",
        "WLSSECRET": "6bee54d1-5c9f-4f12-9d64-0c7b16e0dd52",
        "LICENSEID": 2804943
    }
    env = gp.Env(params=params)

    # create the model within
    #model = gp.Model(env=env)

    """Return a parameter dict for a small illustrative instance."""
    rng = np.random.default_rng(42)

    T  = list(range(3))          # tenants
    Wi = {i: list(range(2)) for i in T}   # 2 workloads per tenant
    N  = list(range(4))          # nodes
    R  = list(range(2))          # resources: 0=CPU, 1=MEM
    H  = list(range(2))          # time periods
    Q  = list(range(2))          # QoS classes: 0=guaranteed, 1=burstable
    K  = list(range(3))          # primitives: 0=none, 1=gVisor, 2=Kata

    # --- resource / demand ---
    d    = {(i,j,r): rng.uniform(0.5, 2.0)   for i in T for j in Wi[i] for r in R}
    mu   = {(i,j,r): d[i,j,r] * rng.uniform(0.9, 1.1) for i in T for j in Wi[i] for r in R}

    # empirical covariance matrix (per resource): shape (|T|*max|Wi|) x (|T|*max|Wi|)
    all_wl = [(i,j) for i in T for j in Wi[i]]
    n_wl   = len(all_wl)
    Sigma  = {}
    for r in R:
        A = rng.standard_normal((n_wl, n_wl))
        Sigma[r] = (A @ A.T) / n_wl    # PSD

    C = {(n, r): 10.0 for n in N for r in R}   # node capacities
    Q_quota = {(i, r): 5.0 for i in T for r in R}
    alpha_oc = {r: 1.5 for r in R}             # overcommit ratio

    # --- pricing / risk ---
    p      = {(i,j): float(i + j + 1)           for i in T for j in Wi[i]}
    pi_n   = {n: 1.0                             for n in N}
    pi_bar = {i: 3.0                             for i in T}
    v_op   = {i: 0.5                             for i in T}
    eps_i  = {i: 0.05                            for i in T}   # SLA risk tolerance

    # --- isolation primitives ---
    # eta[k,r]: resource overhead multiplier
    eta = {(0,r): 1.00 for r in R}
    eta.update({(1,r): 1.20 for r in R})   # gVisor
    eta.update({(2,r): 1.05 for r in R})   # Kata

    c_k = {0: 0.0, 1: 0.3, 2: 0.2}        # operational cost premium

    # rho[k,k']: residual interference
    rho = {
        (0,0): 0.80, (0,1): 0.40, (0,2): 0.30,
        (1,0): 0.40, (1,1): 0.15, (1,2): 0.10,
        (2,0): 0.30, (2,1): 0.10, (2,2): 0.10,
    }
    tau = {(i, ip): 0.35 for i in T for ip in T}   # interference tolerance

    k_min = {i: 0 for i in T}              # minimum primitive level per tenant
    k_min[2] = 1                           # tenant 2 requires at least gVisor

    # --- control-plane ---
    omega_etcd = {(i,k): float(10 * (k+1)) for i in T for k in K}
    omega_qps  = {(i,k): float(5  * (k+1)) for i in T for k in K}
    omega_svc  = {(i,k): float(2  * (k+1)) for i in T for k in K}
    B_etcd     = 200.0
    B_qps      = 100.0
    B_svc      = 50.0
    delta_qps  = 2.0
    B_qps_churn = 20.0

    # --- SLA / migration ---
    # q(i,j): QoS class assignment
    q_assign = {(i,j): 0 for i in T for j in Wi[i]}   # all guaranteed
    L = {(i, q_): 50.0 for i in T for q_ in Q}         # latency targets (ms)
    gamma_ij = {(i,j): 1.0 for i in T for j in Wi[i]}  # migration cost
    Delta_i  = {i: 2      for i in T}                   # disruption budget

    # N_allowed[i,j]: feasible nodes for workload j of tenant i
    N_allowed = {(i,j): list(N) for i in T for j in Wi[i]}
    # compliance example: workload (2,1) restricted to nodes {2,3}
    N_allowed[(2,1)] = [2, 3]

    # latency model coefficients
    l0    = {(i,j): 10.0 for i in T for j in Wi[i]}
    alpha_lat = {(i,j,ip,jp): 2.0
                 for i in T for j in Wi[i]
                 for ip in T for jp in Wi[ip]
                 if (i,j) != (ip,jp)}
    beta_lat  = {(i,j,k): [0.0, 5.0, 2.0][k]
                 for i in T for j in Wi[i] for k in K}

    # objective weights
    lam = {1: 10.0, 2: 1.0, 3: 5.0, 4: 0.5, 5: 2.0}

    return dict(
        T=T, Wi=Wi, N=N, R=R, H=H, Q=Q, K=K,
        all_wl=all_wl, n_wl=n_wl,
        d=d, mu=mu, Sigma=Sigma,
        C=C, Q_quota=Q_quota, alpha_oc=alpha_oc,
        p=p, pi_n=pi_n, pi_bar=pi_bar, v_op=v_op, eps_i=eps_i,
        eta=eta, c_k=c_k, rho=rho, tau=tau, k_min=k_min,
        omega_etcd=omega_etcd, omega_qps=omega_qps, omega_svc=omega_svc,
        B_etcd=B_etcd, B_qps=B_qps, B_svc=B_svc,
        delta_qps=delta_qps, B_qps_churn=B_qps_churn,
        q_assign=q_assign, L=L, gamma_ij=gamma_ij, Delta_i=Delta_i,
        N_allowed=N_allowed,
        l0=l0, alpha_lat=alpha_lat, beta_lat=beta_lat,
        lam=lam,
    )


# ---------------------------------------------------------------------------
# 2.  KAPPA — one-sided Chebyshev (Cantelli) quantile
# ---------------------------------------------------------------------------

def kappa(eps: float) -> float:
    """Worst-case quantile factor from Cantelli's inequality: sqrt((1-eps)/eps)."""
    return np.sqrt((1.0 - eps) / eps)


# ---------------------------------------------------------------------------
# 3.  INDEX HELPERS
# ---------------------------------------------------------------------------

def wl_index(all_wl, i, j):
    """Row index of workload (i,j) in the flattened covariance matrix."""
    return all_wl.index((i, j))


# ---------------------------------------------------------------------------
# 4.  BUILD MODEL
# ---------------------------------------------------------------------------

def build_model(P):
    T, Wi, N, R, H, K = P['T'], P['Wi'], P['N'], P['R'], P['H'], P['K']
    all_wl = P['all_wl']

    m = gp.Model("MultiTenantCluster", env=env)

    # -----------------------------------------------------------------------
    # 4.1  Decision variables
    # -----------------------------------------------------------------------

    # x[i,j,n,t] — placement
    x = m.addVars(
        [(i,j,n,t) for i in T for j in Wi[i] for n in N for t in H],
        vtype=GRB.BINARY, name="x"
    )

    # y[i,n,t] — tenant-on-node indicator
    y = m.addVars(
        [(i,n,t) for i in T for n in N for t in H],
        vtype=GRB.BINARY, name="y"
    )

    # z[n,t] — node active
    z = m.addVars(
        [(n,t) for n in N for t in H],
        vtype=GRB.BINARY, name="z"
    )

    # w[i,j,k,t] — primitive selection
    w = m.addVars(
        [(i,j,k,t) for i in T for j in Wi[i] for k in K for t in H],
        vtype=GRB.BINARY, name="w"
    )

    # m_mig[i,j,n,t] — migration indicator
    m_mig = m.addVars(
        [(i,j,n,t) for i in T for j in Wi[i] for n in N for t in H],
        vtype=GRB.BINARY, name="m_mig"
    )

    # a[i] — admission
    a = m.addVars(T, vtype=GRB.BINARY, name="a")

    # e[i,j,t] — SLA violation slack
    e = m.addVars(
        [(i,j,t) for i in T for j in Wi[i] for t in H],
        lb=0.0, name="e"
    )

    # eps_eff[n,t] — effective per-node risk level (continuous in [0,1])
    eps_eff = m.addVars(
        [(n,t) for n in N for t in H],
        lb=0.0, ub=1.0, name="eps_eff"
    )

    # xi[i,j,n,k,t] — linearization of x * w
    xi = m.addVars(
        [(i,j,n,k,t) for i in T for j in Wi[i] for n in N for k in K for t in H],
        vtype=GRB.BINARY, name="xi"
    )

    # zeta[i,j,ip,jp,n,t] — linearization of x_{ij} * x_{i'j'} on same node
    wl_pairs = [(i,j,ip,jp)
                for i in T for j in Wi[i]
                for ip in T for jp in Wi[ip]
                if (i,j) < (ip,jp)]   # avoid duplicates
    zeta = m.addVars(
        [(i,j,ip,jp,n,t) for (i,j,ip,jp) in wl_pairs for n in N for t in H],
        vtype=GRB.BINARY, name="zeta"
    )

    # sigma — fairness auxiliary (min satisfaction)
    sigma = m.addVar(lb=0.0, ub=1.0, name="sigma")

    m.update()

    # -----------------------------------------------------------------------
    # 4.2  CONSTRAINT 5.1 — Placement integrity
    # -----------------------------------------------------------------------

    # Each workload placed on at most one node, only if admitted
    for i in T:
        for j in Wi[i]:
            for t in H:
                m.addConstr(
                    gp.quicksum(x[i,j,n,t] for n in N) <= a[i],
                    name=f"place_integrity_{i}_{j}_{t}"
                )

    # Tenant-on-node indicator
    for i in T:
        for j in Wi[i]:
            for n in N:
                for t in H:
                    m.addConstr(
                        y[i,n,t] >= x[i,j,n,t],
                        name=f"y_link_{i}_{j}_{n}_{t}"
                    )

    # -----------------------------------------------------------------------
    # 4.3  CONSTRAINT 5.1 — Compliance / data residency (variable fixing)
    # -----------------------------------------------------------------------

    for i in T:
        for j in Wi[i]:
            for n in N:
                if n not in P['N_allowed'][(i,j)]:
                    for t in H:
                        m.addConstr(x[i,j,n,t] == 0,
                                    name=f"residency_{i}_{j}_{n}_{t}")

    # -----------------------------------------------------------------------
    # 4.4  McCormick linearization of xi = x * w  and  zeta = x * x
    # -----------------------------------------------------------------------

    for i in T:
        for j in Wi[i]:
            for n in N:
                for k in K:
                    for t in H:
                        m.addConstr(xi[i,j,n,k,t] <= x[i,j,n,t])
                        m.addConstr(xi[i,j,n,k,t] <= w[i,j,k,t])
                        m.addConstr(xi[i,j,n,k,t] >= x[i,j,n,t] + w[i,j,k,t] - 1)

    for (i,j,ip,jp) in wl_pairs:
        for n in N:
            for t in H:
                m.addConstr(zeta[i,j,ip,jp,n,t] <= x[i,j,n,t])
                m.addConstr(zeta[i,j,ip,jp,n,t] <= x[ip,jp,n,t])
                m.addConstr(zeta[i,j,ip,jp,n,t] >= x[i,j,n,t] + x[ip,jp,n,t] - 1)

    # -----------------------------------------------------------------------
    # 4.5  CONSTRAINT 5.2 — Probabilistic capacity (SOCP / Cantelli)
    #
    # For each node n, resource r, time t:
    #
    #   sum_{i,j,k} eta[k,r] * mu[i,j,r] * xi[i,j,n,k,t]
    #   + kappa(eps_eff[n,t]) * ||Sigma_r^{1/2} * xi_vec_{n,t}||_2
    #   <= C[n,r] * z[n,t]
    #
    # We pre-compute Sigma^{1/2} per resource and model the norm as an SOCP.
    # eps_eff is approximated with the strictest admitted tenant on each node.
    # -----------------------------------------------------------------------

    # Precompute Cholesky factors
    chol = {}
    for r in R:
        try:
            chol[r] = np.linalg.cholesky(P['Sigma'][r] + 1e-6 * np.eye(P['n_wl']))
        except np.linalg.LinAlgError:
            chol[r] = np.eye(P['n_wl'])

    # Effective per-node risk: lower-bound eps_eff by every admitted tenant's eps_i
    # eps_eff[n,t] <= eps_i[i] + (1 - y[i,n,t]) * 1   =>  enforced when y=1
    # Equivalently: eps_eff[n,t] >= eps_i[i] * y[i,n,t]  (lower bound on strictness)
    # We use the linearised "min" via big-M on y:
    #   eps_eff[n,t] <= eps_i[i] + (1 - y[i,n,t])   (M=1 since eps in [0,1])
    for n in N:
        for t in H:
            for i in T:
                m.addConstr(
                    eps_eff[n,t] <= P['eps_i'][i] + (1 - y[i,n,t]),
                    name=f"eps_eff_{n}_{t}_{i}"
                )

    # SOCP capacity constraints
    # Because eps_eff is a variable, kappa(eps_eff) is nonlinear.
    # We linearise by discretising eps_i values: for each distinct eps level,
    # introduce a binary selector and a piecewise version.
    # Simpler tractable approach: fix kappa per node at the strictest tenant's eps_i.
    # Here we use a conservative constant kappa per node computed from the
    # minimum eps across tenants (safest approximation without per-node binaries).
    kap_global = kappa(min(P['eps_i'].values()))

    for n in N:
        for r in R:
            for t in H:
                # Mean load term: sum_{i,j,k} eta[k,r] * mu[i,j,r] * xi[i,j,n,k,t]
                mean_load = gp.quicksum(
                    P['eta'][k,r] * P['mu'][i,j,r] * xi[i,j,n,k,t]
                    for i in T for j in Wi[i] for k in K
                )

                # Safety buffer via SOCP: kappa * ||L * xi_vec||_2
                # Build the coefficient vector for each row of chol[r]
                L_mat = chol[r]   # shape (n_wl, n_wl)

                # Auxiliary variable for the SOCP norm
                soc_aux = m.addVar(lb=0.0, name=f"soc_{n}_{r}_{t}")

                # Components of L * xi_vec  (one per row of chol)
                soc_components = []
                for s in range(P['n_wl']):
                    comp = m.addVar(lb=-GRB.INFINITY, name=f"soc_comp_{n}_{r}_{t}_{s}")
                    # comp = sum_q L[s,q] * xi[all_wl[q], n, k, t]
                    # (summed over all primitives k as xi already selects exactly one)
                    m.addConstr(
                        comp == gp.quicksum(
                            L_mat[s, q] *
                            gp.quicksum(xi[all_wl[q][0], all_wl[q][1], n, k, t]
                                        for k in K)
                            for q in range(P['n_wl'])
                        ),
                        name=f"soc_comp_def_{n}_{r}_{t}_{s}"
                    )
                    soc_components.append(comp)

                # ||soc_components||_2 <= soc_aux  via Gurobi SOCP
                m.addQConstr(
                    gp.quicksum(c * c for c in soc_components) <= soc_aux * soc_aux,
                    name=f"socp_norm_{n}_{r}_{t}"
                )

                # Full capacity constraint
                m.addConstr(
                    mean_load + kap_global * soc_aux <= P['C'][n,r] * z[n,t],
                    name=f"capacity_{n}_{r}_{t}"
                )

    # -----------------------------------------------------------------------
    # 4.6  CONSTRAINT 5.3 — Isolation primitive selection
    # -----------------------------------------------------------------------

    # Exactly one primitive per admitted workload
    for i in T:
        for j in Wi[i]:
            for t in H:
                m.addConstr(
                    gp.quicksum(w[i,j,k,t] for k in K) == a[i],
                    name=f"prim_select_{i}_{j}_{t}"
                )

    # Compliance floor: forbid primitives below k_min[i]
    for i in T:
        for j in Wi[i]:
            for k in K:
                if k < P['k_min'][i]:
                    for t in H:
                        m.addConstr(w[i,j,k,t] == 0,
                                    name=f"prim_floor_{i}_{j}_{k}_{t}")

    # Pair-conflict: co-located tenants must use compatible primitives
    # ζ_{ij,i'j',n,t} + w_{i,j,k,t} + w_{i',j',k',t} <= 2
    # for all (k,k') where rho[k,k'] > tau[i,i']
    for (i,j,ip,jp) in wl_pairs:
        for n in N:
            for t in H:
                for k in K:
                    for kp in K:
                        if P['rho'][k,kp] > P['tau'][i,ip]:
                            m.addConstr(
                                zeta[i,j,ip,jp,n,t] + w[i,j,k,t] + w[ip,jp,kp,t] <= 2,
                                name=f"conflict_{i}_{j}_{ip}_{jp}_{n}_{t}_{k}_{kp}"
                            )

    # -----------------------------------------------------------------------
    # 4.7  CONSTRAINT 5.4 — Control-plane resource budgets
    # -----------------------------------------------------------------------

    for t in H:
        # etcd budget
        m.addConstr(
            gp.quicksum(
                P['omega_etcd'][i,k] *
                gp.quicksum(w[i,j,k,t] for j in Wi[i])
                for i in T for k in K
            ) <= P['B_etcd'],
            name=f"etcd_{t}"
        )
        # API-server steady-state QPS
        m.addConstr(
            gp.quicksum(
                P['omega_qps'][i,k] *
                gp.quicksum(w[i,j,k,t] for j in Wi[i])
                for i in T for k in K
            ) <= P['B_qps'],
            name=f"qps_{t}"
        )
        # Service count
        m.addConstr(
            gp.quicksum(
                P['omega_svc'][i,k] *
                gp.quicksum(w[i,j,k,t] for j in Wi[i])
                for i in T for k in K
            ) <= P['B_svc'],
            name=f"svc_{t}"
        )
        # Migration-induced QPS churn
        m.addConstr(
            gp.quicksum(
                P['delta_qps'] * m_mig[i,j,n,t]
                for i in T for j in Wi[i] for n in N
            ) <= P['B_qps_churn'],
            name=f"qps_churn_{t}"
        )

    # -----------------------------------------------------------------------
    # 4.8  CONSTRAINT 5.5 — Migration linking and disruption budgets
    # -----------------------------------------------------------------------

    for i in T:
        for j in Wi[i]:
            for n in N:
                for t in H:
                    if t == 0:
                        # No previous period — no migration possible
                        m.addConstr(m_mig[i,j,n,t] == 0,
                                    name=f"mig_init_{i}_{j}_{n}")
                    else:
                        # m_mig >= x_{t} - x_{t-1} - (1 - sum_{n'!=n} x_{n',t-1})
                        other_prev = gp.quicksum(
                            x[i,j,np,t-1] for np in N if np != n
                        )
                        m.addConstr(
                            m_mig[i,j,n,t] >= x[i,j,n,t] - x[i,j,n,t-1] - (1 - other_prev),
                            name=f"mig_link_{i}_{j}_{n}_{t}"
                        )

    # Per-tenant disruption budget (PodDisruptionBudget mirror)
    for i in T:
        for t in H:
            m.addConstr(
                gp.quicksum(m_mig[i,j,n,t] for j in Wi[i] for n in N) <= P['Delta_i'][i],
                name=f"disruption_{i}_{t}"
            )

    # -----------------------------------------------------------------------
    # 4.9  CONSTRAINT 5.6 — SLA satisfaction (linearised latency model)
    #
    #  l_ij = l0_{ij}
    #        + sum_{(i',j') != (i,j)} alpha_{ij,i'j'} * zeta_{ij,i'j',n,t}
    #        + sum_k beta_{ijk} * w_{ijkt}
    #  l_ij <= L_{i,q(i,j)} + e_{ijt}
    # -----------------------------------------------------------------------

    def get_zeta(i,j,ip,jp,n,t):
        """Return the correct zeta variable regardless of ordering."""
        if (i,j) < (ip,jp):
            return zeta[i,j,ip,jp,n,t]
        else:
            return zeta[ip,jp,i,j,n,t]

    for i in T:
        for j in Wi[i]:
            for t in H:
                q_ij = P['q_assign'][i,j]
                L_target = P['L'][i, q_ij]

                # Interference term (summed over nodes since workload is on one node)
                interference = gp.quicksum(
                    P['alpha_lat'][i,j,ip,jp] * get_zeta(i,j,ip,jp,n,t)
                    for ip in T for jp in Wi[ip]
                    for n in N
                    if (i,j) != (ip,jp)
                )

                # Primitive overhead
                prim_overhead = gp.quicksum(
                    P['beta_lat'][i,j,k] * w[i,j,k,t] for k in K
                )

                latency = P['l0'][i,j] + interference + prim_overhead

                # SLA: l_ij <= L_target + e_{ijt}  (only when admitted)
                # Enforce with big-M on (1 - a[i])
                BIG_M = 1e4
                m.addConstr(
                    latency - e[i,j,t] <= L_target + BIG_M * (1 - a[i]),
                    name=f"sla_{i}_{j}_{t}"
                )

    # -----------------------------------------------------------------------
    # 4.10  FAIRNESS — max-min satisfaction (DRF-style)
    #
    #  s_i = sum_{j,n} d_{i,j,r*_i} * x_{i,j,n,t} / Q_{i,r*_i}
    #  sigma <= s_i   for all i (admitted)
    # -----------------------------------------------------------------------

    # Dominant resource per tenant: highest demand-to-quota ratio
    dom_r = {}
    for i in T:
        ratios = {r: sum(P['d'][i,j,r] for j in Wi[i]) / P['Q_quota'][i,r]
                  for r in R}
        dom_r[i] = max(ratios, key=ratios.get)

    for i in T:
        r_star = dom_r[i]
        for t in H:
            s_i = gp.quicksum(
                P['d'][i,j,r_star] * x[i,j,n,t]
                for j in Wi[i] for n in N
            ) / P['Q_quota'][i, r_star]

            # sigma <= s_i + (1 - a[i])  — inactive tenants don't drag sigma down
            m.addConstr(
                sigma <= s_i + (1 - a[i]),
                name=f"fairness_{i}_{t}"
            )

    # -----------------------------------------------------------------------
    # 5.  OBJECTIVE FUNCTION
    # -----------------------------------------------------------------------
    # min  pi_n * z  (infra cost)
    #    + lam1 * p_ij * e_ijt  (SLA penalty)
    #    - lam2 * (pi_bar_i - v_op_i) * a_i  (admission revenue)
    #    - lam3 * sigma  (fairness: maximise min satisfaction)
    #    + lam4 * c_k * w_ijkt  (isolation cost)
    #    + lam5 * gamma_ij * m_mig  (migration cost)
    # -----------------------------------------------------------------------

    lam = P['lam']

    infra_cost = gp.quicksum(P['pi_n'][n] * z[n,t] for n in N for t in H)

    sla_penalty = gp.quicksum(
        P['p'][i,j] * e[i,j,t]
        for i in T for j in Wi[i] for t in H
    )

    admission_revenue = gp.quicksum(
        (P['pi_bar'][i] - P['v_op'][i]) * a[i] for i in T
    )

    isolation_cost = gp.quicksum(
        P['c_k'][k] * w[i,j,k,t]
        for i in T for j in Wi[i] for k in K for t in H
    )

    migration_cost = gp.quicksum(
        P['gamma_ij'][i,j] * m_mig[i,j,n,t]
        for i in T for j in Wi[i] for n in N for t in H
    )

    obj = (
        infra_cost
        + lam[1] * sla_penalty
        - lam[2] * admission_revenue
        - lam[3] * sigma
        + lam[4] * isolation_cost
        + lam[5] * migration_cost
    )

    m.setObjective(obj, GRB.MINIMIZE)

    return m, dict(x=x, y=y, z=z, w=w, m_mig=m_mig, a=a,
                   e=e, eps_eff=eps_eff, xi=xi, zeta=zeta, sigma=sigma)


# ---------------------------------------------------------------------------
# 6.  SOLVE AND REPORT
# ---------------------------------------------------------------------------

def solve_and_report(model, vars_, P):
    m = model
    m.Params.TimeLimit   = 300
    m.Params.MIPGap      = 0.01
    m.Params.LogToConsole = 1

    m.optimize()

    if m.Status not in (GRB.OPTIMAL, GRB.TIME_LIMIT, GRB.SUBOPTIMAL):
        print(f"Model status: {m.Status} — no feasible solution found.")
        return

    print(f"\n=== Solution (obj = {m.ObjVal:.4f}) ===")

    T, Wi, N, H, K = P['T'], P['Wi'], P['N'], P['H'], P['K']
    x, w, a, z = vars_['x'], vars_['w'], vars_['a'], vars_['z']
    e, m_mig   = vars_['e'], vars_['m_mig']
    sigma      = vars_['sigma']

    print("\nAdmitted tenants:")
    for i in T:
        admitted = a[i].X > 0.5
        print(f"  tenant {i}: {'ADMITTED' if admitted else 'REJECTED'}")

    print("\nPlacement (x=1):")
    for i in T:
        for j in Wi[i]:
            for n in N:
                for t in H:
                    if x[i,j,n,t].X > 0.5:
                        k_chosen = next(k for k in K if w[i,j,k,t].X > 0.5)
                        print(f"  wl({i},{j}) -> node {n}  t={t}  primitive={k_chosen}"
                              f"  SLA_slack={vars_['e'][i,j,t].X:.2f}")

    print("\nActive nodes:")
    for n in N:
        for t in H:
            if z[n,t].X > 0.5:
                print(f"  node {n}  t={t}")

    print(f"\nFairness sigma (min satisfaction): {sigma.X:.4f}")

    total_mig = sum(m_mig[i,j,n,t].X
                    for i in T for j in Wi[i] for n in N for t in H)
    print(f"Total migrations: {total_mig:.0f}")


# ---------------------------------------------------------------------------
# 7.  MAIN
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    params = build_synthetic_data()
    model, variables = build_model(params)
    solve_and_report(model, variables, params)
