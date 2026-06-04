"""
plan_ahead_optimizer.py
────────────────────────
MISOCP build, solve, and output extraction for the plan-ahead model.

Key design properties
─────────────────────
  • Pool of M machines: M_a always available, M_b additional (model decides activation).
  • Exclusive tenants T_e: assigned machines per period (e[i,n,h]); can change across periods.
  • Shared tenants T_s: assigned machines per period (y[i,n,h], can change).
  • All tenants are assigned machines every interval — no admission / rejection.
  • Mix bonus: objective rewards assigning heavy + light shared tenants to same machine.
  • Cantelli cone (C1a + C1b) gives probabilistic capacity guarantee ≥ 1−ε.
  • Feedback parameters (v̄_n, W̄_i, queue counts) are baked into C_eff and u in plan_ahead_data.py.

Sets
────
  T        all tenants
  T_e      exclusive tenants (randomly tagged X%)
  T_s      shared tenants (T minus T_e)
  T_heavy  shared tenants with above-median average demand
  T_light  shared tenants with below-median average demand
  M        all machines (M = M_a ∪ M_b)
  M_a      always-available machines (always active)
  M_b      additional machines (model activates z_on[n])
  H        planning periods (slots in the horizon)

Variables
─────────
  e[i,n,h]        {0,1}   exclusive tenant i assigned to machine n in period h (per-period)
  z_on[n]         {0,1}   additional machine n ∈ M_b is activated at all
  z[n,h]          {0,1}   machine n is active in interval h
  y[i,n,h]        {0,1}   shared tenant i assigned to machine n in interval h
  f[i,n,h]        ≥ 0     capacity allocation of machine n to shared tenant i in interval h
  sigma           [0,1]   minimum demand-satisfaction ratio (fairness across shared tenants)
  t[n,h]          ≥ 0     Cantelli slack (SOCP mode only)
  has_heavy[n,h]  {0,1}   machine n has ≥1 heavy shared tenant in interval h
  has_light[n,h]  {0,1}   machine n has ≥1 light shared tenant in interval h
  mix[n,h]        {0,1}   machine n has both heavy and light tenants in interval h

Constraints
───────────
  C_aa         z[n,h] = 1                              ∀ n ∈ M_a, h          (always available)
  C_act        z[n,h] ≤ z_on[n]                        ∀ n ∈ M_b, h          (additional gate)
  C_zact       z[n,h] ≥ y[i,n,h]                       ∀ i∈T_s, n, h         (node active if shared assigned)
  C_zact_excl  z[n,h] ≥ e[i,n,h]                       ∀ i∈T_e, n, h         (node active if excl assigned)
  C_excl1      Σ_i e[i,n,h] ≤ 1                        ∀ n, h                (one exclusive per machine per period)
  C_excl2      Σ_n e[i,n,h] ≥ 1                        ∀ i∈T_e, h            (each exclusive assigned per period)
  C_excl_cap   Σ_n e[i,n,h]·C_eff[n] ≥ u[i,h]         ∀ i∈T_e, h            (exclusive capacity per period)
  C_sep        Σ_i e[i,n,h] + y[j,n,h] ≤ 1            ∀ j∈T_s, n, h         (no sharing exclusive machines)
  C_share      Σ_n y[i,n,h] ≥ 1                        ∀ i∈T_s, h            (shared always assigned)
  C1a          Σ_i f[i,n,h] + κ·t[n,h] ≤ C_eff[n]·z[n,h]  ∀ n, h           (capacity + buffer)
  C1b          Σ_i σ²[i,h]·y[i,n,h] ≤ t[n,h]²         ∀ n, h   (SOCP: cone)
  C2           f[i,n,h] ≤ C[n]·y[i,n,h]               ∀ i∈T_s, n, h         (priority link)
  C3           Σ_n f[i,n,h] ≥ u[i,h]                   ∀ i∈T_s, h            (demand satisfaction)
  C4           sigma ≤ Σ_{n,h} f[i,n,h] / Σ_h u[i,h]  ∀ i∈T_s               (fairness)
  C_mix_*      has_heavy, has_light, mix linking constraints

Objective
─────────
  Minimize: λ_0·infra_cost − λ_1·sigma − λ_2·mix_total

  infra_cost = Σ_{n ∈ M_b} π_n·z_on[n]
  mix_total  = Σ_{n,h} mix[n,h]
"""

from __future__ import annotations

import gurobipy as gp
from gurobipy import GRB


# ── Build model ────────────────────────────────────────────────────────────

def build_model(P: dict, env: gp.Env, use_socp: bool = True) -> tuple[gp.Model, dict]:
    """
    Build and return the plan-ahead MISOCP model and its decision-variable dict.

    Parameters
    ----------
    P        : parameter dict from build_synthetic_data()
    env      : Gurobi environment from make_gurobi_env()
    use_socp : True  → MISOCP with Cantelli cone (C1a + C1b).
               False → plain MILP with linear capacity constraint.

    Returns
    -------
    (model, vars_dict)
        vars_dict keys: e, z_on, z, y, f, sigma, t (None when use_socp=False),
                        has_heavy, has_light, mix
    """
    T       = P['T']
    T_e     = P['T_e']
    T_s     = P['T_s']
    T_heavy = P.get('T_s_heavy', [])
    T_light = P.get('T_s_light', [])
    M       = P['M']
    M_a     = P['M_a']
    M_b     = P['M_b']
    H       = P['H']
    C       = P['C']
    C_eff   = P.get('C_eff', C)          # feedback-adjusted capacity
    u       = P['u']
    u_max   = P.get('u_max', {})
    sigma2  = P.get('sigma2', {})
    kappa   = P.get('kappa', 0.0)
    pi_n    = P.get('pi_n', {n: 1.0 for n in M_b})
    lam     = P['lam']
    # Fraction of C_eff available to plan-ahead (MILP only); remainder is reserved for realtime.
    # SOCP mode uses the Cantelli cone buffer instead, so cap_frac is ignored there.
    cap_frac = float(P.get('cap_frac', 1.0))

    m = gp.Model("PlanAhead", env=env)

    # ── Decision variables ─────────────────────────────────────────────────

    # Exclusive tenant machine assignment (per planning period — can change across periods)
    e = m.addVars(
        [(i, n, h) for i in T_e for n in M for h in H],
        vtype=GRB.BINARY, name="e"
    )

    # Additional machine activation (horizon-wide)
    z_on = m.addVars(M_b, vtype=GRB.BINARY, name="z_on") if M_b else {}

    # Machine active per interval
    z = m.addVars(
        [(n, h) for n in M for h in H],
        vtype=GRB.BINARY, name="z"
    )

    # Shared tenant assignment per interval
    y = m.addVars(
        [(i, n, h) for i in T_s for n in M for h in H],
        vtype=GRB.BINARY, name="y"
    ) if T_s else {}

    # Allocation of machine capacity to shared tenant per interval
    f = m.addVars(
        [(i, n, h) for i in T_s for n in M for h in H],
        lb=0.0, name="f"
    ) if T_s else {}

    # Fairness ratio over shared tenants
    sigma = m.addVar(lb=0.0, ub=1.0, name="sigma")

    # Cantelli slack (SOCP mode only)
    t = m.addVars([(n, h) for n in M for h in H], lb=0.0, name="t") if use_socp else None

    # Mix bonus helpers (only relevant when both T_heavy and T_light are non-empty)
    has_mix = bool(T_heavy and T_light and T_s)
    if has_mix:
        has_heavy = m.addVars(
            [(n, h) for n in M for h in H], vtype=GRB.BINARY, name="hh"
        )
        has_light = m.addVars(
            [(n, h) for n in M for h in H], vtype=GRB.BINARY, name="hl"
        )
        mix = m.addVars(
            [(n, h) for n in M for h in H], vtype=GRB.BINARY, name="mix"
        )
    else:
        has_heavy = has_light = mix = {}

    m.update()

    # ── C_aa: Always-available machines always active ───────────────────────
    for n in M_a:
        for h in H:
            m.addConstr(z[n, h] == 1, name=f"C_aa_{n}_{h}")

    # ── C_act: Additional machines active only if switched on ───────────────
    for n in M_b:
        for h in H:
            m.addConstr(z[n, h] <= z_on[n], name=f"C_act_{n}_{h}")

    # ── C_zact: Machine must be active if any shared tenant assigned ────────
    for i in T_s:
        for n in M:
            for h in H:
                m.addConstr(z[n, h] >= y[i, n, h], name=f"C_zact_{i}_{n}_{h}")

    # ── C_zact_excl: Machine must be active if exclusive tenant assigned ────
    for i in T_e:
        for n in M:
            for h in H:
                m.addConstr(z[n, h] >= e[i, n, h], name=f"C_zact_excl_{i}_{n}_{h}")

    # ── C_excl1: At most one exclusive tenant per machine per period ────────
    if T_e:
        for n in M:
            for h in H:
                m.addConstr(
                    gp.quicksum(e[i, n, h] for i in T_e) <= 1,
                    name=f"C_excl1_{n}_{h}"
                )

    # ── C_excl2: Each exclusive tenant must be assigned at least one machine per period ─
    for i in T_e:
        for h in H:
            m.addConstr(
                gp.quicksum(e[i, n, h] for n in M) >= 1,
                name=f"C_excl2_{i}_{h}"
            )

    # ── C_excl_cap: Exclusive machines must cover per-period demand ──────────
    for i in T_e:
        for h in H:
            demand_ih = u.get((i, h), 0.0)
            if demand_ih > 0:
                m.addConstr(
                    gp.quicksum(e[i, n, h] * C_eff.get(n, C[n]) for n in M) >= demand_ih,
                    name=f"C_excl_cap_{i}_{h}"
                )

    # ── C_sep: Exclusive machines cannot be used by shared tenants ──────────
    if T_e and T_s:
        for j in T_s:
            for n in M:
                for h in H:
                    m.addConstr(
                        gp.quicksum(e[i, n, h] for i in T_e) + y[j, n, h] <= 1,
                        name=f"C_sep_{j}_{n}_{h}"
                    )

    # ── C_share: Each shared tenant must have ≥1 machine per interval ───────
    min_mach = int(P.get('min_machines_per_tenant', 1))
    for i in T_s:
        for h in H:
            m.addConstr(
                gp.quicksum(y[i, n, h] for n in M) >= min_mach,
                name=f"C_share_{i}_{h}"
            )

    # ── C1: Capacity constraint ────────────────────────────────────────────
    for n in M:
        for h in H:
            shared_alloc = gp.quicksum(f[i, n, h] for i in T_s) if T_s else gp.LinExpr()
            if use_socp:
                # C1a (linear part with Cantelli buffer)
                m.addConstr(
                    shared_alloc + kappa * t[n, h] <= C_eff.get(n, C[n]) * z[n, h],
                    name=f"C1a_{n}_{h}"
                )
                # C1b (cone: t² ≥ Σ σ²[i,h]·y[i,n,h])
                if sigma2 and kappa > 0 and T_s:
                    m.addQConstr(
                        t[n, h] * t[n, h] >= gp.quicksum(
                            sigma2.get((i, h), 0.0) * y[i, n, h] for i in T_s
                        ),
                        name=f"C1b_{n}_{h}"
                    )
            else:
                # MILP mode: cap at cap_frac × C_eff to reserve headroom for realtime
                m.addConstr(
                    shared_alloc <= cap_frac * C_eff.get(n, C[n]) * z[n, h],
                    name=f"C1_{n}_{h}"
                )

    # ── C2: Priority link (shared) ──────────────────────────────────────────
    for i in T_s:
        for n in M:
            for h in H:
                m.addConstr(f[i, n, h] <= C[n] * y[i, n, h], name=f"C2_{i}_{n}_{h}")

    # ── C3: Demand satisfaction (shared) ────────────────────────────────────
    for i in T_s:
        for h in H:
            m.addConstr(
                gp.quicksum(f[i, n, h] for n in M) >= u[i, h],
                name=f"C3_{i}_{h}"
            )

    # ── C4: Fairness (min demand-satisfaction ratio over shared tenants) ─────
    EPS = 1e-9
    for i in T_s:
        total_demand = sum(u[i, h] for h in H)
        if total_demand < EPS:
            continue
        total_alloc = gp.quicksum(f[i, n, h] for n in M for h in H)
        m.addConstr(
            sigma <= total_alloc / total_demand,
            name=f"C4_{i}"
        )

    # ── Mix bonus constraints ────────────────────────────────────────────────
    if has_mix:
        for n in M:
            for h in H:
                # has_heavy[n,h] = 1 if any heavy tenant assigned to n in h
                for i in T_heavy:
                    m.addConstr(has_heavy[n, h] >= y[i, n, h], name=f"Cmh_{i}_{n}_{h}")
                # has_light[n,h] = 1 if any light tenant assigned to n in h
                for j in T_light:
                    m.addConstr(has_light[n, h] >= y[j, n, h], name=f"Cml_{j}_{n}_{h}")
                # mix[n,h] = 1 iff both heavy and light present
                m.addConstr(mix[n, h] <= has_heavy[n, h], name=f"Cmix_h_{n}_{h}")
                m.addConstr(mix[n, h] <= has_light[n, h], name=f"Cmix_l_{n}_{h}")
                m.addConstr(
                    mix[n, h] >= has_heavy[n, h] + has_light[n, h] - 1,
                    name=f"Cmix_{n}_{h}"
                )

    # ── Objective ──────────────────────────────────────────────────────────
    infra_cost = (
        gp.quicksum(pi_n[n] * z_on[n] for n in M_b)
        if M_b else gp.LinExpr()
    )
    mix_total = (
        gp.quicksum(mix[n, h] for n in M for h in H)
        if has_mix else gp.LinExpr()
    )

    m.setObjective(
        lam[0] * infra_cost - lam[1] * sigma - lam[2] * mix_total,
        GRB.MINIMIZE
    )

    vars_ = dict(
        e=e, z_on=z_on, z=z, y=y, f=f, sigma=sigma, t=t,
        has_heavy=has_heavy, has_light=has_light, mix=mix,
    )
    return m, vars_


# ── Solve and report ───────────────────────────────────────────────────────

def solve_and_report(model: gp.Model, vars_: dict, P: dict) -> None:
    """Set solver parameters, optimize, and print a human-readable summary."""
    model.Params.TimeLimit    = 300
    model.Params.MIPGap       = 0.01
    model.Params.LogToConsole = 1

    model.optimize()

    if model.Status not in (GRB.OPTIMAL, GRB.TIME_LIMIT, GRB.SUBOPTIMAL):
        print(f"Model status: {model.Status} — no feasible solution found.")
        return

    T_e, T_s = P['T_e'], P['T_s']
    M, H     = P['M'], P['H']
    e, z_on, z, y, f, sigma = (
        vars_['e'], vars_['z_on'], vars_['z'], vars_['y'], vars_['f'], vars_['sigma']
    )
    mix = vars_.get('mix', {})

    print(f"\n=== Plan-Ahead Solution (obj = {model.ObjVal:.4f}) ===")

    print(f"\nMachine pool: total={len(M)}  always-available={len(P['M_a'])}  "
          f"additional={len(P['M_b'])}")
    if P['M_b'] and z_on:
        activated = [n for n in P['M_b'] if z_on[n].X > 0.5]
        print(f"  Additional machines activated: {activated}")

    print(f"\nExclusive tenants {T_e}: (per-period machine assignment)")
    for i in T_e:
        for h in H:
            machines = [n for n in M if e[i, n, h].X > 0.5]
            demand_h = P['u'].get((i, h), 0.0)
            print(f"  tenant {i} period {h}: machines {machines}  (demand={demand_h:.2f})")

    print(f"\nShared tenants {T_s}: (per-interval assignment)")
    for h in H:
        print(f"  interval {h}:")
        for i in T_s:
            machines = [n for n in M if y[i, n, h].X > 0.5]
            alloc    = sum(f[i, n, h].X for n in M)
            demand   = P['u'][i, h]
            print(f"    tenant {i}: machines={machines}  "
                  f"alloc={alloc:.2f} / demand={demand:.2f}")
        if mix:
            mixed_machines = [n for n in M if mix[n, h].X > 0.5]
            print(f"    mixed machines (heavy+light): {mixed_machines}")

    print(f"\nFairness sigma (min demand-satisfaction ratio, shared tenants): {sigma.X:.4f}")


# ── Output: Tenant Access Schedule (flat dict for tests / frontend) ───────

def extract_tenant_access_schedule(
    vars_: dict, P: dict
) -> dict[tuple[int, int], list[int]]:
    """
    Return {(tenant_id, period): [machine_ids]} for all tenants and periods.

    Exclusive tenants have the same machines in every period.
    Shared tenants reflect per-period y[i,n,h] assignments.
    """
    T_e, T_s = P['T_e'], P['T_s']
    M, H     = P['M'],   P['H']
    e, y     = vars_['e'], vars_['y']

    schedule: dict[tuple[int, int], list[int]] = {}

    for i in T_e:
        for h in H:
            schedule[(i, h)] = [n for n in M if e[i, n, h].X > 0.5]

    for i in T_s:
        for h in H:
            schedule[(i, h)] = [n for n in M if y[i, n, h].X > 0.5]

    return schedule


# ── Output: Plan Ahead Interval Schedule ─────────────────────────────────

def extract_plan_output(vars_: dict, P: dict) -> dict:
    """
    Extract the plan-ahead output from the solved model.

    Returns
    -------
    dict with key "intervals": list of interval dicts.
    Each interval dict has key "groups": list of group dicts.
    Each group dict: {"tenant_ids": [...], "machine_ids": [...], "exclusive": bool}

    All tenants appear in every interval. Exclusive tenant groups have the same
    machine_ids across all intervals.
    """
    T_e, T_s = P['T_e'], P['T_s']
    M, H     = P['M'], P['H']
    e, y     = vars_['e'], vars_['y']

    intervals = []
    for h in H:
        groups = []

        # Exclusive tenant groups (per-period assignment — machines can change across periods)
        for i in T_e:
            machines = [n for n in M if e[i, n, h].X > 0.5]
            groups.append({
                "tenant_ids":  [i],
                "machine_ids": machines,
                "exclusive":   True,
            })

        # Shared tenant groups (per-interval assignment)
        for i in T_s:
            machines = [n for n in M if y[i, n, h].X > 0.5]
            groups.append({
                "tenant_ids":  [i],
                "machine_ids": machines,
                "exclusive":   False,
            })

        intervals.append({
            "interval": h,
            "groups":   groups,
        })

    return {
        "intervals": intervals,
        "n_tenants": len(P['T']),
        "n_machines": len(M),
        "n_intervals": len(H),
        "exclusive_tenants": T_e,
        "shared_tenants": T_s,
    }


# ── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import csv
    import time as _time
    from pathlib import Path as _Path
    from plan_ahead_data import build_synthetic_data, make_gurobi_env

    ap = argparse.ArgumentParser(description="Plan-Ahead MISOCP — timed solve")
    ap.add_argument("--tenants",    type=int,   default=128,   help="Number of tenants        (default 128)")
    ap.add_argument("--nodes",      type=int,   default=64,    help="Number of machines       (default 64)")
    ap.add_argument("--seed",       type=int,   default=42,    help="Random seed              (default 42)")
    ap.add_argument("--periods",    type=int,   default=4,     help="Planning periods         (default 4)")
    ap.add_argument("--time-limit", type=float, default=300.0, help="Solver wall-clock limit s (default 300)")
    ap.add_argument("--mip-gap",    type=float, default=0.01,  help="MIP gap tolerance        (default 0.01)")
    ap.add_argument("--csv",        action="store_true",       help="Append result row to CSV")
    args = ap.parse_args()

    n_t   = args.tenants
    n_n   = args.nodes
    seed  = args.seed
    n_per = args.periods

    # Same capacity / demand defaults as plan_ahead_iterative.py for fair comparison
    n_always = max(1, n_n // 2)
    n_excl   = max(1, min(int(n_t * 0.20), n_t - 1))

    print("═" * 64)
    print("  Plan-Ahead MISOCP — Timed Solve")
    print("═" * 64)
    print(f"  Tenants  : {n_t}   Machines : {n_n}   Periods : {n_per}   Seed : {seed}")
    print(f"  Always-on: {n_always}   Exclusive tenants : {n_excl}")
    print(f"  Demand   : u[i,h] ∈ [0.5, 2.5]   Node capacity : 10.0 units/period")
    print(f"  Solver   : Gurobi   TimeLimit : {args.time_limit:.0f}s   MIPGap : {args.mip_gap:.2%}")
    print()

    P = build_synthetic_data(
        seed=seed,
        n_tenants=n_t,
        n_nodes=n_n,
        n_intervals=n_per,
        node_capacity=10.0,
        n_always_available=n_always,
        n_exclusive=n_excl,
        tenant_usage_min=0.5,
        tenant_usage_max=2.5,
        sigma_frac=0.20,
        epsilon=0.10,
        min_machines_per_tenant=1,
    )

    env = make_gurobi_env()
    try:
        print("  Building model ...")
        t_build0 = _time.perf_counter()
        model, vars_ = build_model(P, env, use_socp=True)
        build_s = _time.perf_counter() - t_build0
        n_constrs = model.NumConstrs + model.NumQConstrs
        print(f"  Build  : {build_s:.3f} s  |  vars={model.NumVars:,}  constrs={n_constrs:,}")
        print()

        model.Params.TimeLimit    = args.time_limit
        model.Params.MIPGap       = args.mip_gap
        model.Params.LogToConsole = 0
        model.Params.OutputFlag   = 0

        print("  Solving ...")
        t_solve0 = _time.perf_counter()
        model.optimize()
        solve_s = _time.perf_counter() - t_solve0

        _STATUS = {
            GRB.OPTIMAL:     "OPTIMAL",
            GRB.TIME_LIMIT:  "TIME_LIMIT",
            GRB.SUBOPTIMAL:  "SUBOPTIMAL",
            GRB.INFEASIBLE:  "INFEASIBLE",
            GRB.INF_OR_UNBD: "INF_OR_UNBD",
        }
        status_str = _STATUS.get(model.Status, str(model.Status))
        has_sol    = model.SolCount > 0

        print()
        print("  " + "─" * 44)
        print("  Plan-Ahead MISOCP — Results")
        print("  " + "─" * 44)
        print(f"  Status       : {status_str}")
        print(f"  Build time   : {build_s:.3f} s")
        print(f"  Solve time   : {solve_s:.3f} s  (wall clock)")
        print(f"  Total time   : {build_s + solve_s:.3f} s")
        print(f"  Variables    : {model.NumVars:,}")
        if has_sol:
            activated = sum(1 for n in P['M_b'] if vars_['z_on'][n].X > 0.5) if P['M_b'] else 0
            print(f"  Objective    : {model.ObjVal:.4f}")
            print(f"  MIP gap      : {model.MIPGap:.4%}")
            print(f"  Fairness (σ) : {vars_['sigma'].X:.4f}")
            print(f"  Nodes used   : {len(P['M_a']) + activated} / {n_n}"
                  f"  (always={len(P['M_a'])}  activated={activated})")
        else:
            print("  No feasible solution within time limit.")
        print()

        if args.csv:
            csv_path = (_Path(__file__).resolve().parent.parent
                        / "Pipeline" / "timing_data" / "pa_misocp_cli_results.csv")
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            write_header = not csv_path.exists()
            with open(csv_path, "a", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                if write_header:
                    w.writerow(["tenants", "nodes", "periods", "seed", "n_vars",
                                "build_s", "solve_s", "total_s", "status",
                                "obj_val", "mip_gap", "sigma"])
                w.writerow([
                    n_t, n_n, n_per, seed, model.NumVars,
                    f"{build_s:.4f}", f"{solve_s:.4f}", f"{build_s + solve_s:.4f}",
                    status_str,
                    f"{model.ObjVal:.4f}"   if has_sol else "",
                    f"{model.MIPGap:.6f}"   if has_sol else "",
                    f"{vars_['sigma'].X:.4f}" if has_sol else "",
                ])
            print(f"  CSV → {csv_path}")

        model.dispose()
    finally:
        try:
            env.dispose()
        except Exception:
            pass
