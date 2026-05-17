"""
plan_ahead_optimizer.py
────────────────────────
MISOCP build, solve, and output extraction for the plan-ahead model.

Mathematical model reference: plan_ahead_source_of_truth.txt
Constraint labels follow the lexicon in plan_ahead_lex.txt.
"""

from __future__ import annotations

import numpy as np
import gurobipy as gp
from gurobipy import GRB

from plan_ahead_data import kappa


# ── Build model ────────────────────────────────────────────────────────────

def build_model(P: dict, env: gp.Env) -> tuple[gp.Model, dict]:
    """Build and return the MISOCP model and its decision-variable dict.

    Parameters
    ----------
    P   : parameter dict from build_synthetic_data()
    env : Gurobi environment from make_gurobi_env()

    Returns
    -------
    (model, vars_dict)
        vars_dict keys: x, y, z, w, m_mig, a, e, eps_eff, xi, zeta, sigma
    """
    T, Wi, N, R, H, K = P['T'], P['Wi'], P['N'], P['R'], P['H'], P['K']
    all_wl = P['all_wl']

    m = gp.Model("MultiTenantCluster", env=env)

    # ── Decision variables ─────────────────────────────────────────────────

    x = m.addVars(
        [(i, j, n, t) for i in T for j in Wi[i] for n in N for t in H],
        vtype=GRB.BINARY, name="x"
    )
    y = m.addVars(
        [(i, n, t) for i in T for n in N for t in H],
        vtype=GRB.BINARY, name="y"
    )
    z = m.addVars(
        [(n, t) for n in N for t in H],
        vtype=GRB.BINARY, name="z"
    )
    w = m.addVars(
        [(i, j, k, t) for i in T for j in Wi[i] for k in K for t in H],
        vtype=GRB.BINARY, name="w"
    )
    m_mig = m.addVars(
        [(i, j, n, t) for i in T for j in Wi[i] for n in N for t in H],
        vtype=GRB.BINARY, name="m_mig"
    )
    a = m.addVars(T, vtype=GRB.BINARY, name="a")
    e = m.addVars(
        [(i, j, t) for i in T for j in Wi[i] for t in H],
        lb=0.0, name="e"
    )
    eps_eff = m.addVars(
        [(n, t) for n in N for t in H],
        lb=0.0, ub=1.0, name="eps_eff"
    )
    xi = m.addVars(
        [(i, j, n, k, t) for i in T for j in Wi[i] for n in N for k in K for t in H],
        vtype=GRB.BINARY, name="xi"
    )

    wl_pairs = [(i, j, ip, jp)
                for i in T for j in Wi[i]
                for ip in T for jp in Wi[ip]
                if (i, j) < (ip, jp)]
    zeta = m.addVars(
        [(i, j, ip, jp, n, t) for (i, j, ip, jp) in wl_pairs for n in N for t in H],
        vtype=GRB.BINARY, name="zeta"
    )
    sigma = m.addVar(lb=0.0, ub=1.0, name="sigma")

    m.update()

    # ── C1: Placement integrity ────────────────────────────────────────────

    for i in T:
        for j in Wi[i]:
            for t in H:
                m.addConstr(
                    gp.quicksum(x[i, j, n, t] for n in N) == a[i],
                    name=f"C1_{i}_{j}_{t}"
                )

    # C1b: tenant-on-node indicator
    for i in T:
        for j in Wi[i]:
            for n in N:
                for t in H:
                    m.addConstr(
                        y[i, n, t] >= x[i, j, n, t],
                        name=f"C1b_{i}_{j}_{n}_{t}"
                    )

    # C1c: compliance / data-residency hard constraint
    for i in T:
        for j in Wi[i]:
            for n in N:
                if n not in P['N_allowed'][(i, j)]:
                    for t in H:
                        m.addConstr(x[i, j, n, t] == 0,
                                    name=f"C1c_{i}_{j}_{n}_{t}")

    # ── McCormick linearizations: xi = x*w, zeta = x*x ────────────────────

    for i in T:
        for j in Wi[i]:
            for n in N:
                for k in K:
                    for t in H:
                        m.addConstr(xi[i, j, n, k, t] <= x[i, j, n, t])
                        m.addConstr(xi[i, j, n, k, t] <= w[i, j, k, t])
                        m.addConstr(xi[i, j, n, k, t] >= x[i, j, n, t] + w[i, j, k, t] - 1)

    for (i, j, ip, jp) in wl_pairs:
        for n in N:
            for t in H:
                m.addConstr(zeta[i, j, ip, jp, n, t] <= x[i, j, n, t])
                m.addConstr(zeta[i, j, ip, jp, n, t] <= x[ip, jp, n, t])
                m.addConstr(zeta[i, j, ip, jp, n, t] >= x[i, j, n, t] + x[ip, jp, n, t] - 1)

    # ── C2: Probabilistic capacity (SOCP / Cantelli) ───────────────────────
    #
    # mean_load + kap_n * ||chol_r * xi_vec||_2  <=  C[n,r] * z[n,t]
    #
    # Per-node kappa: take kappa(min eps_i) over tenants that have any
    # workload with access to node n (tighter than the global minimum used
    # in the original code, still a precomputed constant — no extra binaries).

    chol: dict[int, np.ndarray] = {}
    for r in R:
        try:
            chol[r] = np.linalg.cholesky(P['Sigma'][r] + 1e-6 * np.eye(P['n_wl']))
        except np.linalg.LinAlgError:
            chol[r] = np.eye(P['n_wl'])

    kap_node: dict[int, float] = {}
    for n in N:
        tenants_on_n = [i for i in T
                        if any(n in P['N_allowed'][(i, j)] for j in Wi[i])]
        eps_min_n = (min(P['eps_i'][i] for i in tenants_on_n)
                     if tenants_on_n else min(P['eps_i'].values()))
        kap_node[n] = kappa(eps_min_n)

    for n in N:
        for t in H:
            for i in T:
                m.addConstr(
                    eps_eff[n, t] <= P['eps_i'][i] + (1 - y[i, n, t]),
                    name=f"eps_eff_{n}_{t}_{i}"
                )

    for n in N:
        for r in R:
            for t in H:
                mean_load = gp.quicksum(
                    P['eta'][k, r] * P['mu'][i, j, r] * xi[i, j, n, k, t]
                    for i in T for j in Wi[i] for k in K
                )
                L_mat    = chol[r]
                soc_aux  = m.addVar(lb=0.0, name=f"soc_{n}_{r}_{t}")
                soc_comps = []
                for s in range(P['n_wl']):
                    comp = m.addVar(lb=-GRB.INFINITY, name=f"soc_c_{n}_{r}_{t}_{s}")
                    m.addConstr(
                        comp == gp.quicksum(
                            L_mat[s, q] *
                            gp.quicksum(xi[all_wl[q][0], all_wl[q][1], n, k, t] for k in K)
                            for q in range(P['n_wl'])
                        ),
                        name=f"soc_def_{n}_{r}_{t}_{s}"
                    )
                    soc_comps.append(comp)
                m.addQConstr(
                    gp.quicksum(c * c for c in soc_comps) <= soc_aux * soc_aux,
                    name=f"C2_socp_{n}_{r}_{t}"
                )
                m.addConstr(
                    mean_load + kap_node[n] * soc_aux <= P['C'][n, r] * z[n, t],
                    name=f"C2_cap_{n}_{r}_{t}"
                )

    # ── C3: Isolation primitive selection ─────────────────────────────────

    for i in T:
        for j in Wi[i]:
            for t in H:
                m.addConstr(
                    gp.quicksum(w[i, j, k, t] for k in K) == a[i],
                    name=f"C3a_{i}_{j}_{t}"
                )

    for i in T:
        for j in Wi[i]:
            for k in K:
                if k < P['k_min'][i]:
                    for t in H:
                        m.addConstr(w[i, j, k, t] == 0, name=f"C3b_{i}_{j}_{k}_{t}")

    for (i, j, ip, jp) in wl_pairs:
        for n in N:
            for t in H:
                for k in K:
                    for kp in K:
                        if P['rho'][k, kp] > P['tau'][i, ip]:
                            m.addConstr(
                                zeta[i, j, ip, jp, n, t] + w[i, j, k, t] + w[ip, jp, kp, t] <= 2,
                                name=f"C3c_{i}_{j}_{ip}_{jp}_{n}_{t}_{k}_{kp}"
                            )

    # ── C4: Control-plane budgets ──────────────────────────────────────────

    for t in H:
        m.addConstr(
            gp.quicksum(
                P['omega_etcd'][i, k] * gp.quicksum(w[i, j, k, t] for j in Wi[i])
                for i in T for k in K
            ) <= P['B_etcd'],
            name=f"C4a_etcd_{t}"
        )
        m.addConstr(
            gp.quicksum(
                P['omega_qps'][i, k] * gp.quicksum(w[i, j, k, t] for j in Wi[i])
                for i in T for k in K
            ) <= P['B_qps'],
            name=f"C4b_qps_{t}"
        )
        m.addConstr(
            gp.quicksum(
                P['omega_svc'][i, k] * gp.quicksum(w[i, j, k, t] for j in Wi[i])
                for i in T for k in K
            ) <= P['B_svc'],
            name=f"C4c_svc_{t}"
        )
        m.addConstr(
            gp.quicksum(
                P['delta_qps'] * m_mig[i, j, n, t]
                for i in T for j in Wi[i] for n in N
            ) <= P['B_qps_churn'],
            name=f"C4d_churn_{t}"
        )

    # ── C5: SLA latency satisfaction (linearised, big-M deactivation) ─────

    def get_zeta(i, j, ip, jp, n, t):
        return (zeta[i, j, ip, jp, n, t] if (i, j) < (ip, jp)
                else zeta[ip, jp, i, j, n, t])

    BIG_M = 1e4
    for i in T:
        for j in Wi[i]:
            for t in H:
                q_ij     = P['q_assign'][i, j]
                L_target = P['L'][i, q_ij]
                interference = gp.quicksum(
                    P['alpha_lat'][i, j, ip, jp] * get_zeta(i, j, ip, jp, n, t)
                    for ip in T for jp in Wi[ip]
                    for n in N
                    if (i, j) != (ip, jp)
                )
                prim_overhead = gp.quicksum(
                    P['beta_lat'][i, j, k] * w[i, j, k, t] for k in K
                )
                latency = P['l0'][i, j] + interference + prim_overhead
                m.addConstr(
                    latency - e[i, j, t] <= L_target + BIG_M * (1 - a[i]),
                    name=f"C5_{i}_{j}_{t}"
                )

    # ── C6: Migration linking and disruption budgets ───────────────────────

    for i in T:
        for j in Wi[i]:
            for n in N:
                for t in H:
                    if t == 0:
                        m.addConstr(m_mig[i, j, n, t] == 0,
                                    name=f"C6a_{i}_{j}_{n}")
                    else:
                        other_prev = gp.quicksum(
                            x[i, j, np_, t - 1] for np_ in N if np_ != n
                        )
                        m.addConstr(
                            m_mig[i, j, n, t] >=
                            x[i, j, n, t] - x[i, j, n, t - 1] - (1 - other_prev),
                            name=f"C6b_{i}_{j}_{n}_{t}"
                        )

    for i in T:
        for t in H:
            m.addConstr(
                gp.quicksum(m_mig[i, j, n, t] for j in Wi[i] for n in N) <= P['Delta_i'][i],
                name=f"C6c_{i}_{t}"
            )

    # ── C7: DRF fairness ──────────────────────────────────────────────────

    dom_r: dict[int, int] = {}
    for i in T:
        ratios = {r: sum(P['d'][i, j, r] for j in Wi[i]) / P['Q_quota'][i, r]
                  for r in R}
        dom_r[i] = max(ratios, key=ratios.get)

    for i in T:
        r_star = dom_r[i]
        for t in H:
            s_i = gp.quicksum(
                P['d'][i, j, r_star] * x[i, j, n, t]
                for j in Wi[i] for n in N
            ) / P['Q_quota'][i, r_star]
            m.addConstr(
                sigma <= s_i + (1 - a[i]),
                name=f"C7_{i}_{t}"
            )

    # ── Objective ─────────────────────────────────────────────────────────

    lam = P['lam']

    infra_cost = gp.quicksum(P['pi_n'][n] * z[n, t] for n in N for t in H)
    sla_penalty = gp.quicksum(
        P['p'][i, j] * e[i, j, t] for i in T for j in Wi[i] for t in H
    )
    admission_revenue = gp.quicksum(
        (P['pi_bar'][i] - P['v_op'][i]) * a[i] for i in T
    )
    isolation_cost = gp.quicksum(
        P['c_k'][k] * w[i, j, k, t]
        for i in T for j in Wi[i] for k in K for t in H
    )
    migration_cost = gp.quicksum(
        P['gamma_ij'][i, j] * m_mig[i, j, n, t]
        for i in T for j in Wi[i] for n in N for t in H
    )

    obj = (
          lam[0] * infra_cost
        + lam[1] * sla_penalty
        - lam[2] * admission_revenue
        - lam[3] * sigma
        + lam[4] * isolation_cost
        + lam[5] * migration_cost
    )
    m.setObjective(obj, GRB.MINIMIZE)

    vars_ = dict(x=x, y=y, z=z, w=w, m_mig=m_mig, a=a,
                 e=e, eps_eff=eps_eff, xi=xi, zeta=zeta, sigma=sigma)
    return m, vars_


# ── Solve and report ───────────────────────────────────────────────────────

def solve_and_report(model: gp.Model, vars_: dict, P: dict) -> None:
    """Set solver parameters, optimize, and print a human-readable summary."""
    model.Params.TimeLimit    = 300
    model.Params.MIPGap       = 0.01
    model.Params.LogToConsole = 1

    model.optimize()

    if model.Status not in (GRB.OPTIMAL, GRB.TIME_LIMIT, GRB.SUBOPTIMAL):
        print(f"Model status: {model.Status} -- no feasible solution found.")
        return

    print(f"\n=== Solution (obj = {model.ObjVal:.4f}) ===")

    T, Wi, N, H, K = P['T'], P['Wi'], P['N'], P['H'], P['K']
    x, w, a, z     = vars_['x'], vars_['w'], vars_['a'], vars_['z']
    e_var, m_mig   = vars_['e'], vars_['m_mig']
    sigma          = vars_['sigma']

    print("\nAdmitted tenants:")
    for i in T:
        print(f"  tenant {i}: {'ADMITTED' if a[i].X > 0.5 else 'REJECTED'}")

    print("\nPlacement (x=1):")
    for i in T:
        for j in Wi[i]:
            for n in N:
                for t in H:
                    if x[i, j, n, t].X > 0.5:
                        k_chosen = next(k for k in K if w[i, j, k, t].X > 0.5)
                        print(f"  wl({i},{j}) -> node {n}  t={t}  "
                              f"primitive={k_chosen}  "
                              f"SLA_slack={e_var[i, j, t].X:.2f}")

    print("\nActive nodes:")
    for n in N:
        for t in H:
            if z[n, t].X > 0.5:
                print(f"  node {n}  t={t}")

    print(f"\nFairness sigma (min satisfaction): {sigma.X:.4f}")

    total_mig = sum(m_mig[i, j, n, t].X
                    for i in T for j in Wi[i] for n in N for t in H)
    print(f"Total migrations: {total_mig:.0f}")


# ── Output interface: TenantAccessSchedule ────────────────────────────────

def extract_tenant_access_schedule(
    vars_: dict,
    P: dict,
) -> dict[tuple[int, int], list[int]]:
    """Extract A_t_i from solved x variables.

    Returns
    -------
    TenantAccessSchedule : dict[(tenant_id, time_slot) -> list[node_id]]
        Passed to the real-time model's tenant_node_access parameter
        after slicing to the current time slot.

    A_t_i = { n in N : sum_j x[i,j,n,t].X >= 1 }
    """
    T, Wi, N, H = P['T'], P['Wi'], P['N'], P['H']
    x = vars_['x']

    schedule: dict[tuple[int, int], list[int]] = {}
    for i in T:
        for t in H:
            nodes = [n for n in N
                     if sum(x[i, j, n, t].X for j in Wi[i]) >= 0.5]
            schedule[(i, t)] = nodes
    return schedule


# ── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from plan_ahead_data import build_synthetic_data, make_gurobi_env

    P   = build_synthetic_data()
    env = make_gurobi_env()
    m, vars_ = build_model(P, env)
    solve_and_report(m, vars_, P)

    schedule = extract_tenant_access_schedule(vars_, P)
    print("\nTenantAccessSchedule:")
    for key, nodes in sorted(schedule.items()):
        print(f"  tenant={key[0]}, t={key[1]}: nodes={nodes}")
