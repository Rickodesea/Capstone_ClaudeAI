"""
test_plan_ahead.py
───────────────────
Behavioral tests for the plan-ahead MISOCP.

Each test verifies one mathematical property of the optimal solution.
The model is solved once at module load and shared across all tests.

Run:
    python test_plan_ahead.py
"""

from __future__ import annotations

import sys

import gurobipy as gp
from gurobipy import GRB

from plan_ahead_data import build_synthetic_data, make_gurobi_env
from plan_ahead_optimizer import build_model, extract_tenant_access_schedule

# ── Solve once, share across all tests ────────────────────────────────────

_P    = build_synthetic_data()
_env  = make_gurobi_env()
_m, _vars = build_model(_P, _env)
_m.Params.TimeLimit    = 120
_m.Params.MIPGap       = 0.01
_m.Params.LogToConsole = 0
_m.optimize()

SOLVED = _m.Status in (GRB.OPTIMAL, GRB.TIME_LIMIT, GRB.SUBOPTIMAL)


# ── Test helpers ───────────────────────────────────────────────────────────

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
        print("  SKIP  (model not solved — Gurobi status "
              f"{_m.Status})")
        return False
    return True


# ── Test 1: model solves to feasibility ───────────────────────────────────

def test_model_solves():
    """The default synthetic instance must yield a feasible solution."""
    _assert(SOLVED, f"model solved (status={_m.Status})")


# ── Test 2: admission — at least one tenant admitted ──────────────────────

def test_at_least_one_tenant_admitted():
    """With profitable tenants and sufficient capacity, someone gets admitted."""
    if not _require_solved():
        return
    a = _vars['a']
    admitted = sum(1 for i in _P['T'] if a[i].X > 0.5)
    _assert(admitted >= 1,
            f"at least one tenant admitted (got {admitted}/{len(_P['T'])})")


# ── Test 3: admitted tenant has a placement ────────────────────────────────

def test_admitted_tenant_has_placement():
    """Every admitted tenant must have at least one workload placed."""
    if not _require_solved():
        return
    a, x = _vars['a'], _vars['x']
    T, Wi, N, H = _P['T'], _P['Wi'], _P['N'], _P['H']

    all_ok = True
    for i in T:
        if a[i].X > 0.5:
            placed = sum(x[i, j, n, t].X
                         for j in Wi[i] for n in N for t in H)
            if placed < 0.5:
                all_ok = False
                _assert(False,
                        f"admitted tenant {i} has no placement (placed={placed:.1f})")
    _assert(all_ok, "all admitted tenants have at least one workload placed")


# ── Test 4: placement integrity — at most one node per workload ────────────

def test_placement_integrity():
    """C1: sum_n x[i,j,n,t] <= 1 for every (i,j,t)."""
    if not _require_solved():
        return
    x = _vars['x']
    T, Wi, N, H = _P['T'], _P['Wi'], _P['N'], _P['H']

    violations = []
    for i in T:
        for j in Wi[i]:
            for t in H:
                total = sum(x[i, j, n, t].X for n in N)
                if total > 1.5:
                    violations.append((i, j, t, total))
    _assert(len(violations) == 0,
            f"C1 placement integrity (violations={violations})")


# ── Test 5: isolation primitive floor for tenant 2 ────────────────────────

def test_isolation_primitive_floor():
    """C3b: tenant 2 has k_min=1, so primitive 0 (none) must never be selected."""
    if not _require_solved():
        return
    w = _vars['w']
    Wi, H = _P['Wi'], _P['H']

    bad = [(2, j, t) for j in Wi[2] for t in H if w[2, j, 0, t].X > 0.5]
    _assert(len(bad) == 0,
            f"tenant 2 never uses primitive 0 (violations={bad})")


# ── Test 6: admitted tenant selects exactly one primitive ─────────────────

def test_exactly_one_primitive_per_admitted_workload():
    """C3a: admitted workloads have sum_k w[i,j,k,t] == 1."""
    if not _require_solved():
        return
    a, w = _vars['a'], _vars['w']
    T, Wi, K, H = _P['T'], _P['Wi'], _P['K'], _P['H']

    violations = []
    for i in T:
        if a[i].X > 0.5:
            for j in Wi[i]:
                for t in H:
                    total_w = sum(w[i, j, k, t].X for k in K)
                    if abs(total_w - 1.0) > 0.1:
                        violations.append((i, j, t, total_w))
    _assert(len(violations) == 0,
            f"C3a exactly one primitive per admitted workload (violations={violations})")


# ── Test 7: compliance / data-residency ───────────────────────────────────

def test_compliance_residency():
    """C1c: workload (2,1) may only be placed on nodes 2 or 3."""
    if not _require_solved():
        return
    x = _vars['x']
    H = _P['H']
    allowed = {2, 3}

    bad = [(n, t) for n in _P['N'] for t in H
           if n not in allowed and x[2, 1, n, t].X > 0.5]
    _assert(len(bad) == 0,
            f"wl(2,1) only on nodes {{2,3}} (bad placements={bad})")


# ── Test 8: migration budget per tenant ───────────────────────────────────

def test_migration_budget():
    """C6c: migrations per tenant per slot must not exceed Delta_i."""
    if not _require_solved():
        return
    m_mig = _vars['m_mig']
    T, Wi, N, H = _P['T'], _P['Wi'], _P['N'], _P['H']

    violations = []
    for i in T:
        for t in H:
            total_mig = sum(m_mig[i, j, n, t].X for j in Wi[i] for n in N)
            if total_mig > _P['Delta_i'][i] + 0.5:
                violations.append((i, t, total_mig))
    _assert(len(violations) == 0,
            f"C6c migration budget respected (violations={violations})")


# ── Test 9: no migrations at t=0 ──────────────────────────────────────────

def test_no_migration_at_t0():
    """C6a: m_mig[i,j,n,0] == 0 for all i,j,n (no prior period)."""
    if not _require_solved():
        return
    m_mig = _vars['m_mig']
    T, Wi, N = _P['T'], _P['Wi'], _P['N']

    bad = [(i, j, n) for i in T for j in Wi[i] for n in N
           if m_mig[i, j, n, 0].X > 0.5]
    _assert(len(bad) == 0,
            f"C6a no migrations at t=0 (violations={bad})")


# ── Test 10: DRF fairness — sigma nonneg and bounded ─────────────────────

def test_drf_sigma_range():
    """C7: sigma (min DRF satisfaction) in [0, 1]."""
    if not _require_solved():
        return
    sig = _vars['sigma'].X
    _assert(0.0 - 1e-6 <= sig <= 1.0 + 1e-6,
            f"sigma in [0,1] (sigma={sig:.4f})")


# ── Test 11: rejected tenant has no placement ─────────────────────────────

def test_rejected_tenant_has_no_placement():
    """Rejected tenants (a[i]=0) must have all x[i,j,n,t]=0."""
    if not _require_solved():
        return
    a, x = _vars['a'], _vars['x']
    T, Wi, N, H = _P['T'], _P['Wi'], _P['N'], _P['H']

    violations = []
    for i in T:
        if a[i].X < 0.5:
            for j in Wi[i]:
                for n in N:
                    for t in H:
                        if x[i, j, n, t].X > 0.5:
                            violations.append((i, j, n, t))
    _assert(len(violations) == 0,
            f"rejected tenants have no placements (violations={violations})")


# ── Test 12: TenantAccessSchedule format ──────────────────────────────────

def test_tenant_access_schedule_format():
    """extract_tenant_access_schedule returns dict[(int,int) -> list[int]]."""
    if not _require_solved():
        return
    schedule = extract_tenant_access_schedule(_vars, _P)

    T, H, N = _P['T'], _P['H'], _P['N']
    expected_keys = {(i, t) for i in T for t in H}

    keys_ok  = set(schedule.keys()) == expected_keys
    types_ok = all(isinstance(v, list) for v in schedule.values())
    range_ok = all(
        all(0 <= n < len(N) for n in nodes)
        for nodes in schedule.values()
    )

    _assert(keys_ok,  f"schedule has all (tenant, slot) keys (got {len(schedule)})")
    _assert(types_ok, "all schedule values are lists")
    _assert(range_ok, "all node IDs in schedule are valid")


# ── Test 13: admitted tenant appears in access schedule ───────────────────

def test_admitted_tenant_in_schedule():
    """An admitted tenant with placements appears in TenantAccessSchedule."""
    if not _require_solved():
        return
    a = _vars['a']
    schedule = extract_tenant_access_schedule(_vars, _P)
    H = _P['H']

    for i in _P['T']:
        if a[i].X > 0.5:
            has_node = any(len(schedule[(i, t)]) > 0 for t in H)
            _assert(has_node,
                    f"admitted tenant {i} appears in schedule (nodes="
                    f"{[schedule[(i,t)] for t in H]})")


# ── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Plan-ahead MISOCP behavioral tests")
    print("=" * 50)

    test_model_solves()
    test_at_least_one_tenant_admitted()
    test_admitted_tenant_has_placement()
    test_placement_integrity()
    test_isolation_primitive_floor()
    test_exactly_one_primitive_per_admitted_workload()
    test_compliance_residency()
    test_migration_budget()
    test_no_migration_at_t0()
    test_drf_sigma_range()
    test_rejected_tenant_has_no_placement()
    test_tenant_access_schedule_format()
    test_admitted_tenant_in_schedule()

    print("=" * 50)
    print(f"Results: {_passed} passed, {_failed} failed")
    sys.exit(0 if _failed == 0 else 1)
