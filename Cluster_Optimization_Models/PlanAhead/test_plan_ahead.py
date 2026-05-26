"""
test_plan_ahead.py
───────────────────
Behavioral tests for the plan-ahead MISOCP / MILP model.

Tests are grouped into:
  - Core correctness (small default instance, solved once)
  - Structural / format tests
  - Large-scale test (25 nodes, 8 tenants) verifying good distribution

Run:
    python test_plan_ahead.py
"""

from __future__ import annotations

import sys

import gurobipy as gp
from gurobipy import GRB

from plan_ahead_data import build_synthetic_data, make_gurobi_env
from plan_ahead_optimizer import build_model, extract_plan_output, extract_tenant_access_schedule


# ── Shared model (default config — solved once) ───────────────────────────

_P    = build_synthetic_data(seed=42, n_tenants=4, n_nodes=6,
                              n_intervals=3, n_exclusive=1,
                              min_machines_per_tenant=2)
_env  = make_gurobi_env()
_m, _vars = build_model(_P, _env, use_socp=True)   # SOCP (Cantelli cone) — default mode
_m.Params.TimeLimit    = 60
_m.Params.MIPGap       = 0.01
_m.Params.LogToConsole = 0
_m.optimize()

SOLVED = _m.Status in (GRB.OPTIMAL, GRB.TIME_LIMIT, GRB.SUBOPTIMAL)


# ── Test helpers ──────────────────────────────────────────────────────────

_passed = 0
_failed = 0


def _assert(condition: bool, msg: str) -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  PASS  {msg}")
    else:
        _failed += 1
        print(f"  FAIL  {msg}")


def _require_solved() -> bool:
    if not SOLVED:
        print(f"  SKIP  (model not solved — status {_m.Status})")
        return False
    return True


# ── Test 1: model solves to feasibility ──────────────────────────────────

def test_model_solves():
    """Default synthetic instance must reach a feasible solution."""
    _assert(SOLVED, f"model solved (status={_m.Status})")


# ── Test 2: shared tenants assigned per period ────────────────────────────

def test_shared_tenants_assigned():
    """C_share: every shared tenant has >= 1 machine in every period."""
    if not _require_solved():
        return
    T_s, M, H = _P['T_s'], _P['M'], _P['H']
    y = _vars['y']

    violations = []
    for i in T_s:
        for h in H:
            count = sum(1 for n in M if y[i, n, h].X > 0.5)
            if count < 1:
                violations.append((i, h))
    _assert(len(violations) == 0,
            f"C_share: all shared tenants assigned every period (violations={violations})")


# ── Test 3: exclusive tenants assigned ───────────────────────────────────

def test_exclusive_tenants_assigned():
    """C_excl2: every exclusive tenant has >= 1 machine."""
    if not _require_solved():
        return
    T_e, M = _P['T_e'], _P['M']
    e = _vars['e']

    violations = [i for i in T_e if sum(1 for n in M if e[i, n].X > 0.5) < 1]
    _assert(len(violations) == 0,
            f"C_excl2: all exclusive tenants assigned (violations={violations})")


# ── Test 4: exclusive assignments are horizon-stable ─────────────────────

def test_exclusive_assignments_stable():
    """Exclusive tenants must have the same machines in every period."""
    if not _require_solved():
        return
    schedule = extract_tenant_access_schedule(_vars, _P)
    T_e, H = _P['T_e'], _P['H']

    for i in T_e:
        machines_by_period = [frozenset(schedule[(i, h)]) for h in H]
        all_same = all(m == machines_by_period[0] for m in machines_by_period)
        _assert(all_same,
                f"exclusive tenant {i}: same machines every period "
                f"({[list(s) for s in machines_by_period]})")


# ── Test 5: exclusive-shared separation ──────────────────────────────────

def test_exclusive_shared_separation():
    """C_sep: no machine is used exclusively AND by a shared tenant in the same period."""
    if not _require_solved():
        return
    T_e, T_s, M, H = _P['T_e'], _P['T_s'], _P['M'], _P['H']
    e, y = _vars['e'], _vars['y']

    violations = []
    for n in M:
        excl_sum = sum(e[i, n].X for i in T_e)
        if excl_sum > 0.5:
            for j in T_s:
                for h in H:
                    if y[j, n, h].X > 0.5:
                        violations.append((j, n, h))
    _assert(len(violations) == 0,
            f"C_sep: exclusive machines not shared (violations={violations})")


# ── Test 6: capacity + Cantelli buffer constraint (SOCP mode) ────────────

def test_capacity_constraint():
    """C1a (SOCP): alloc + kappa*t <= C_eff*z for each machine per period."""
    if not _require_solved():
        return
    T_s, M, H = _P['T_s'], _P['M'], _P['H']
    f, z, t   = _vars['f'], _vars['z'], _vars['t']
    C_eff     = _P['C_eff']
    kappa     = _P['kappa']

    violations = []
    for n in M:
        for h in H:
            alloc  = sum(f[i, n, h].X for i in T_s)
            t_val  = t[n, h].X if t is not None else 0.0
            rhs    = C_eff[n] * z[n, h].X
            if alloc + kappa * t_val > rhs + 1e-3:
                violations.append((n, h, round(alloc + kappa*t_val, 4), round(rhs, 4)))
    _assert(len(violations) == 0,
            f"C1a SOCP capacity respected (violations={violations})")


# ── Test 7: fairness sigma in [0, 1] ─────────────────────────────────────

def test_fairness_sigma_range():
    """sigma (fairness ratio) must be in [0, 1]."""
    if not _require_solved():
        return
    sig = _vars['sigma'].X
    _assert(0.0 - 1e-6 <= sig <= 1.0 + 1e-6,
            f"sigma in [0,1]  (sigma={sig:.4f})")


# ── Test 8: always-available machines always active ──────────────────────

def test_always_available_active():
    """C_aa: always-available machines (M_a) have z[n,h]=1 in every period."""
    if not _require_solved():
        return
    M_a, H = _P['M_a'], _P['H']
    z = _vars['z']

    violations = [(n, h) for n in M_a for h in H if z[n, h].X < 0.5]
    _assert(len(violations) == 0,
            f"C_aa: all M_a machines always active (violations={violations})")


# ── Test 9: additional machines gated by z_on ────────────────────────────

def test_additional_machine_gate():
    """C_act: additional machine n active in period h only if z_on[n]=1."""
    if not _require_solved():
        return
    M_b, H = _P['M_b'], _P['H']
    z, z_on = _vars['z'], _vars['z_on']

    violations = [
        (n, h) for n in M_b for h in H
        if z[n, h].X > 0.5 and z_on[n].X < 0.5
    ]
    _assert(len(violations) == 0,
            f"C_act: additional machines gated by z_on (violations={violations})")


# ── Test 10: at most one exclusive tenant per machine ─────────────────────

def test_at_most_one_exclusive_per_machine():
    """C_excl1: each machine holds at most one exclusive tenant."""
    if not _require_solved():
        return
    T_e, M = _P['T_e'], _P['M']
    e = _vars['e']

    violations = [
        n for n in M
        if sum(e[i, n].X for i in T_e) > 1.5
    ]
    _assert(len(violations) == 0,
            f"C_excl1: at most one exclusive per machine (violations={violations})")


# ── Test 11: extract_plan_output format ──────────────────────────────────

def test_extract_plan_output_format():
    """extract_plan_output returns dict with 'intervals' key of correct length."""
    if not _require_solved():
        return
    out = extract_plan_output(_vars, _P)

    has_intervals = 'intervals' in out
    _assert(has_intervals, "extract_plan_output has 'intervals' key")
    if not has_intervals:
        return

    n_intervals = len(out['intervals'])
    _assert(n_intervals == len(_P['H']),
            f"correct number of intervals (expected {len(_P['H'])}, got {n_intervals})")

    all_tenants_present = True
    T_all = set(_P['T'])
    for idict in out['intervals']:
        tenants_in_interval = set(
            tid for g in idict['groups'] for tid in g['tenant_ids']
        )
        if tenants_in_interval != T_all:
            all_tenants_present = False
            _assert(False, f"all tenants in interval {idict['interval']} "
                           f"(missing={T_all - tenants_in_interval})")
    _assert(all_tenants_present, "all tenants appear in every interval")


# ── Test 12: extract_tenant_access_schedule format ───────────────────────

def test_extract_tenant_access_schedule_format():
    """extract_tenant_access_schedule returns {(tenant, period): [machines]}."""
    if not _require_solved():
        return
    schedule = extract_tenant_access_schedule(_vars, _P)

    T, H = _P['T'], _P['H']
    expected_keys = {(i, h) for i in T for h in H}

    keys_ok  = set(schedule.keys()) == expected_keys
    types_ok = all(isinstance(v, list) for v in schedule.values())

    _assert(keys_ok,  f"schedule has all (tenant, period) keys (got {len(schedule)})")
    _assert(types_ok, "all schedule values are lists")

    n_total = len(_P['M'])
    range_ok = all(
        all(0 <= n < n_total for n in machines)
        for machines in schedule.values()
    )
    _assert(range_ok, "all machine IDs in schedule are valid")


# ── Test 13: admitted tenant appears in schedule ─────────────────────────

def test_shared_tenant_in_schedule():
    """Every shared tenant has >= 1 machine in every period in the schedule."""
    if not _require_solved():
        return
    schedule = extract_tenant_access_schedule(_vars, _P)
    T_s, H = _P['T_s'], _P['H']

    for i in T_s:
        has_node = all(len(schedule[(i, h)]) > 0 for h in H)
        _assert(has_node,
                f"shared tenant {i} in schedule every period "
                f"(nodes={[schedule[(i,h)] for h in H]})")


# ── Test 14: allocation covers demand per shared tenant ───────────────────

def test_demand_satisfaction():
    """C3: total allocation per shared tenant per period >= feedback demand."""
    if not _require_solved():
        return
    T_s, M, H = _P['T_s'], _P['M'], _P['H']
    f, u   = _vars['f'], _P['u']

    violations = []
    for i in T_s:
        for h in H:
            alloc  = sum(f[i, n, h].X for n in M)
            demand = u[(i, h)]
            if alloc < demand - 1e-3:
                violations.append((i, h, alloc, demand))
    _assert(len(violations) == 0,
            f"C3 demand satisfied (violations={violations})")


# ── Test 15: large config — 25 nodes, 8 tenants ──────────────────────────

def test_large_config_distribution():
    """
    With 25 nodes and 8 all-shared tenants (0% exclusive):
      - Model solves
      - Infra cost = 0 (always-on dominates) or model uses many nodes
      - Each tenant gets assigned to >= 2 nodes per period on average
        (distribution is better than packing into 3 nodes)
    """
    print("\n  [large config: 25 nodes, 8 tenants, 0% exclusive]")

    P_big = build_synthetic_data(
        seed                    = 99,
        n_tenants               = 8,
        n_nodes                 = 25,
        n_intervals             = 4,
        n_always_available      = 10,
        n_exclusive             = 0,
        node_capacity           = 10.0,
        min_machines_per_tenant = 2,
    )
    # Zero infra cost so model distributes tenants across many machines
    P_big['lam'][0] = 0.0

    env = make_gurobi_env()
    m, v = build_model(P_big, env, use_socp=True)  # SOCP (default)
    m.Params.TimeLimit    = 60
    m.Params.MIPGap       = 0.05
    m.Params.LogToConsole = 0
    m.optimize()

    big_solved = m.Status in (GRB.OPTIMAL, GRB.TIME_LIMIT, GRB.SUBOPTIMAL)
    _assert(big_solved, f"large config solves (status={m.Status})")
    if not big_solved:
        return

    T_s, M, H = P_big['T_s'], P_big['M'], P_big['H']
    y = v['y']

    # Count average machines per shared tenant per period
    total_machines = sum(
        1 for i in T_s for h in H for n in M if y[i, n, h].X > 0.5
    )
    avg_per_tenant_per_period = total_machines / max(1, len(T_s) * len(H))

    _assert(avg_per_tenant_per_period >= 2.0,
            f"large config: avg machines/tenant/period >= 2.0 "
            f"(got {avg_per_tenant_per_period:.1f})")

    sig = v['sigma'].X
    _assert(sig > 0.5, f"large config: fairness sigma > 0.5 (got {sig:.3f})")

    print(f"    avg machines/tenant/period = {avg_per_tenant_per_period:.1f}, "
          f"sigma = {sig:.3f}")


# ── Entry point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Plan-ahead model behavioral tests")
    print("=" * 55)

    test_model_solves()
    test_shared_tenants_assigned()
    test_exclusive_tenants_assigned()
    test_exclusive_assignments_stable()
    test_exclusive_shared_separation()
    test_capacity_constraint()
    test_fairness_sigma_range()
    test_always_available_active()
    test_additional_machine_gate()
    test_at_most_one_exclusive_per_machine()
    test_extract_plan_output_format()
    test_extract_tenant_access_schedule_format()
    test_shared_tenant_in_schedule()
    test_demand_satisfaction()
    test_large_config_distribution()

    print("=" * 55)
    print(f"Results: {_passed} passed, {_failed} failed")
    sys.exit(0 if _failed == 0 else 1)
