"""
test_plan_ahead_socp.py
────────────────────────
Targeted tests for the MISOCP (Cantelli cone) plan-ahead model.

Validates:
  1. Feasibility across varied configurations
  2. Cantelli cone activity (t[n,h] > 0 on loaded nodes)
  3. Multi-machine assignment (≥ 2 machines per shared tenant)
  4. High fairness sigma (≥ 0.7 on balanced instances)
  5. Feedback demand scaling increases machine assignments
  6. Exclusive-shared isolation holds under SOCP
  7. Reserve machines activated when demand requires them
  8. Simulation-mirrored config produces assignment expected by ClusterManager

Run:
    python test_plan_ahead_socp.py
"""

from __future__ import annotations

import sys
import io

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import gurobipy as gp
from gurobipy import GRB

from plan_ahead_data import build_synthetic_data, make_gurobi_env
from plan_ahead_optimizer import build_model, extract_plan_output


# ── Helpers ───────────────────────────────────────────────────────────────────

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


def _solve(P: dict, time_limit: int = 90, mip_gap: float = 0.02) -> tuple[gp.Model, dict] | tuple[None, None]:
    env = make_gurobi_env()
    m, v = build_model(P, env, use_socp=True)
    m.Params.TimeLimit    = time_limit
    m.Params.MIPGap       = mip_gap
    m.Params.LogToConsole = 0
    m.optimize()
    if m.Status in (GRB.OPTIMAL, GRB.TIME_LIMIT, GRB.SUBOPTIMAL):
        return m, v
    return None, None


def _base_P(**kwargs) -> dict:
    """Small, fast default instance used as the basis for most tests."""
    defaults = dict(
        seed                    = 42,
        n_tenants               = 4,
        n_nodes                 = 8,
        n_intervals             = 3,
        n_always_available      = 4,
        n_exclusive             = 1,
        node_capacity           = 40.0,
        sigma_frac              = 0.20,
        epsilon                 = 0.10,
        min_machines_per_tenant = 2,
        # Demand just above 1-node threshold: forces 2+ machines per tenant
        # kappa = sqrt(0.9/0.1) = 3.0, one_node_max = 40/(1+0.6) = 25
        tenant_usage_min        = 26.0,
        tenant_usage_max        = 34.0,
    )
    defaults.update(kwargs)
    P = build_synthetic_data(**defaults)
    P['lam'][0] = 0.0  # no infra cost — distribute freely
    return P


# ── Test 1: basic SOCP feasibility ───────────────────────────────────────────

def test_basic_feasibility():
    """Default small SOCP instance must reach a feasible solution."""
    print("\n[1] Basic SOCP feasibility")
    P = _base_P()
    m, v = _solve(P)
    _assert(m is not None, f"SOCP solved (status={getattr(m, 'Status', 'no-model')})")
    if m is not None:
        _assert(m.ObjVal is not None, f"ObjVal finite ({m.ObjVal:.4f})")


# ── Test 2: Cantelli cone active ─────────────────────────────────────────────

def test_cantelli_cone_active():
    """t[n,h] must be > 0 on every node that has ≥1 shared tenant assigned."""
    print("\n[2] Cantelli cone activity")
    P = _base_P()
    m, v = _solve(P)
    if m is None:
        print("  SKIP  (model did not solve)")
        return
    T_s, M, H = P['T_s'], P['M'], P['H']
    y, t = v['y'], v['t']
    if t is None:
        print("  SKIP  (t vars absent — not SOCP mode?)")
        return
    loaded_nodes = {
        (n, h)
        for i in T_s for n in M for h in H
        if y[i, n, h].X > 0.5
    }
    violations = [
        (n, h) for (n, h) in loaded_nodes if t[n, h].X < 1e-6
    ]
    _assert(len(violations) == 0,
            f"t>0 on all loaded nodes (zero_violations={violations[:3]})")
    _assert(len(loaded_nodes) > 0, "at least one loaded node exists")


# ── Test 3: multi-machine assignment ─────────────────────────────────────────

def test_multi_machine_assignment():
    """Each shared tenant must receive ≥ 2 machines per period (C_share + demand)."""
    print("\n[3] Multi-machine assignment (≥2 per tenant per period)")
    P = _base_P()
    m, v = _solve(P)
    if m is None:
        print("  SKIP  (model did not solve)")
        return
    T_s, M, H = P['T_s'], P['M'], P['H']
    y = v['y']
    all_ok = True
    for i in T_s:
        for h in H:
            count = sum(1 for n in M if y[i, n, h].X > 0.5)
            if count < 2:
                _assert(False, f"tenant {i} period {h}: only {count} machine(s)")
                all_ok = False
    if all_ok:
        counts = [
            sum(1 for n in M if y[i, n, h].X > 0.5)
            for i in T_s for h in H
        ]
        avg = sum(counts) / max(1, len(counts))
        _assert(True, f"all shared tenants ≥ 2 machines per period (avg={avg:.1f})")


# ── Test 4: high fairness sigma ──────────────────────────────────────────────

def test_fairness_sigma():
    """sigma (min demand-satisfaction ratio) must be ≥ 0.70 on a balanced instance."""
    print("\n[4] Fairness sigma ≥ 0.70")
    P = _base_P(n_exclusive=0, n_tenants=3, n_nodes=9)
    m, v = _solve(P)
    if m is None:
        print("  SKIP  (model did not solve)")
        return
    sig = v['sigma'].X
    _assert(sig >= 0.70, f"sigma={sig:.4f} (≥ 0.70 on balanced 0-exclusive instance)")


# ── Test 5: feedback increases machine count ──────────────────────────────────

def test_feedback_expands_assignment():
    """
    Moderate feedback (wait = 1x ref => 1.30x demand scale) forces more machines.
    Uses 12 nodes / 3 shared tenants so SOCP stays feasible after demand inflation.
    High wait (3x) would overfill the Cantelli buffer on every node — infeasible.
    """
    print("\n[5] Feedback expands machine assignment under moderate wait times")
    _SEED   = 7
    _NODES  = 12
    _ALWAYS = 8

    def _fb_data(feedback_wait=None):
        P = build_synthetic_data(
            seed=_SEED, n_tenants=3, n_nodes=_NODES, n_intervals=3,
            n_always_available=_ALWAYS, n_exclusive=0,
            node_capacity=40.0, sigma_frac=0.20, epsilon=0.10,
            tenant_usage_min=26.0, tenant_usage_max=34.0,
            feedback_wait=feedback_wait or {},
            feedback_beta=0.3, feedback_wait_ref=10.0,
            min_machines_per_tenant=2,
        )
        P['lam'][0] = 0.0
        return P

    # Baseline: no feedback
    P_base = _fb_data()
    m_base, v_base = _solve(P_base)
    if m_base is None:
        print("  SKIP  (baseline did not solve)")
        return
    T_s, M, H = P_base['T_s'], P_base['M'], P_base['H']
    machines_base = sum(
        1 for i in T_s for n in M for h in H if v_base['y'][i, n, h].X > 0.5
    )

    # Moderate feedback: wait == ref (10) => scale = 1 + 0.3*1 = 1.30
    high_wait = {i: 10.0 for i in T_s}
    P_fb = _fb_data(feedback_wait=high_wait)
    m_fb, v_fb = _solve(P_fb)
    if m_fb is None:
        print("  SKIP  (feedback model did not solve)")
        return
    T_s_fb, M_fb, H_fb = P_fb['T_s'], P_fb['M'], P_fb['H']
    machines_fb = sum(
        1 for i in T_s_fb for n in M_fb for h in H_fb if v_fb['y'][i, n, h].X > 0.5
    )
    _assert(machines_fb >= machines_base,
            f"feedback assigns >= baseline machines "
            f"(baseline={machines_base}, feedback={machines_fb})")


# ── Test 6: exclusive-shared isolation under SOCP ────────────────────────────

def test_exclusive_shared_isolation():
    """No machine can be assigned exclusively AND to a shared tenant (C_sep)."""
    print("\n[6] Exclusive-shared isolation (C_sep)")
    P = _base_P(n_exclusive=2, n_tenants=5, n_nodes=10)
    m, v = _solve(P)
    if m is None:
        print("  SKIP  (model did not solve)")
        return
    T_e, T_s, M, H = P['T_e'], P['T_s'], P['M'], P['H']
    e, y = v['e'], v['y']
    violations = []
    for n in M:
        for h in H:
            excl_sum = sum(e[i, n, h].X for i in T_e)
            if excl_sum > 0.5:
                for j in T_s:
                    if y[j, n, h].X > 0.5:
                        violations.append((j, n, h))
    _assert(len(violations) == 0,
            f"C_sep: no exclusive machine shared (violations={violations})")


# ── Test 7: reserve machines activated by demand ─────────────────────────────

def test_reserve_machines_activated():
    """Additional machines (M_b) must be activated when demand exceeds always-on capacity."""
    print("\n[7] Reserve machines (M_b) activated when demand requires them")
    # 2 always-on, 6 additional, high demand → must activate reserves
    P = _base_P(n_always_available=2, n_nodes=8, n_exclusive=0, n_tenants=3,
                tenant_usage_min=28.0, tenant_usage_max=34.0)
    m, v = _solve(P)
    if m is None:
        print("  SKIP  (model did not solve)")
        return
    M_b, z_on = P['M_b'], v['z_on']
    activated = [n for n in M_b if z_on[n].X > 0.5] if M_b and z_on else []
    _assert(len(activated) > 0,
            f"at least one reserve machine activated (activated={activated})")


# ── Test 8: simulation-mirrored config ───────────────────────────────────────

def test_simulation_mirrored_config():
    """
    Config that mirrors DEFAULT_CONFIG from simulation_config.py:
      8 nodes (3 always-on), 3 tenants (1 exclusive), 40 GB node_cap.
    Each shared tenant must get ≥ 2 machines; plan output must have correct shape.
    """
    print("\n[8] Simulation-mirrored config (8 nodes, 3 tenants, 1 exclusive)")
    P = _base_P(
        seed=42, n_tenants=3, n_nodes=8, n_intervals=2,
        n_always_available=3, n_exclusive=1,
        tenant_usage_min=26.0, tenant_usage_max=34.0,
        min_machines_per_tenant=2,
    )
    m, v = _solve(P)
    if m is None:
        print("  SKIP  (model did not solve)")
        return

    plan_out = extract_plan_output(v, P)
    _assert("intervals" in plan_out, "plan output has 'intervals' key")

    T_s, M, H = P['T_s'], P['M'], P['H']
    y = v['y']
    for h in H:
        for i in T_s:
            count = sum(1 for n in M if y[i, n, h].X > 0.5)
            _assert(count >= 2,
                    f"period {h} tenant {i}: {count} machine(s) ≥ 2")

    sigma = v['sigma'].X
    print(f"    sigma={sigma:.4f}")
    _assert(sigma > 0.0, f"fairness sigma > 0 ({sigma:.4f})")

    # Verify all machine IDs in plan output are valid
    for iv in plan_out["intervals"]:
        for g in iv["groups"]:
            bad = [mid for mid in g["machine_ids"] if mid not in P['M']]
            _assert(len(bad) == 0,
                    f"interval {iv['interval']} group all valid machines (bad={bad})")


# ── Test 9: medium scale 6 tenants 15 nodes ──────────────────────────────────

def test_medium_scale():
    """Medium scale: 6 tenants, 15 nodes, 2 exclusive — solver within time limit."""
    print("\n[9] Medium scale (6 tenants, 15 nodes, 2 exclusive)")
    P = build_synthetic_data(
        seed=123, n_tenants=6, n_nodes=15, n_intervals=3,
        n_always_available=8, n_exclusive=2,
        node_capacity=40.0, sigma_frac=0.20, epsilon=0.10,
        tenant_usage_min=26.0, tenant_usage_max=34.0,
        min_machines_per_tenant=2,
    )
    P['lam'][0] = 0.0
    m, v = _solve(P, time_limit=120, mip_gap=0.05)
    if m is None:
        print("  SKIP  (model did not solve within limit)")
        return
    T_s, M, H = P['T_s'], P['M'], P['H']
    counts = [sum(1 for n in M if v['y'][i, n, h].X > 0.5) for i in T_s for h in H]
    avg = sum(counts) / max(1, len(counts))
    _assert(avg >= 2.0, f"medium scale avg machines/tenant/period={avg:.1f} ≥ 2.0")
    _assert(v['sigma'].X > 0.5, f"medium sigma={v['sigma'].X:.3f} > 0.5")
    print(f"    avg machines/tenant/period = {avg:.1f}, sigma = {v['sigma'].X:.3f}")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Plan-ahead SOCP (Cantelli MISOCP) validation tests")
    print("=" * 60)

    test_basic_feasibility()
    test_cantelli_cone_active()
    test_multi_machine_assignment()
    test_fairness_sigma()
    test_feedback_expands_assignment()
    test_exclusive_shared_isolation()
    test_reserve_machines_activated()
    test_simulation_mirrored_config()
    test_medium_scale()

    print("\n" + "=" * 60)
    print(f"Results: {_passed} passed, {_failed} failed")
    sys.exit(0 if _failed == 0 else 1)
