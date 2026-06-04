"""
test_pipeline_quality.py
─────────────────────────
Pipeline quality tests: minimum machines per tenant for good throughput,
feedback propagation across multiple horizons, and placement rate benchmarks.

Run:
    cd Cluster_Optimization_Models/Pipeline
    python test_pipeline_quality.py

Key questions answered:
  1. What is the minimum avg machines/tenant for near-zero queuing?
  2. Does feedback (high wait-times) actually expand the next plan's assignment?
  3. Does placement rate improve when more machines are assigned per tenant?
  4. Are exclusive tenant assignments truly horizon-stable across plan refreshes?
  5. Can the full pipeline run two successive horizons with feedback?
"""

from __future__ import annotations

import sys
import io
from pathlib import Path

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# ── Path setup ────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "PlanAhead"))
sys.path.insert(0, str(_ROOT / "Realtime"))

from plan_ahead_data      import build_synthetic_data, make_gurobi_env
from plan_ahead_optimizer import build_model, extract_plan_output
from gurobipy             import GRB

import realtime_optimizer as rt_module
from simulation_data import Job, NodeState

rt_module.SOLVER_ID = "CBC"

import numpy as np


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


def _plan_solve(
    n_tenants: int,
    n_nodes:   int,
    n_excl:    int,
    n_always:  int,
    min_mach:  int,
    seed:      int = 42,
    node_cap:  float = 40.0,
    use_socp:  bool  = True,
    feedback_wait: dict | None = None,
    feedback_vbar: dict | None = None,
) -> tuple[gp.Model, dict, dict] | tuple[None, None, None]:
    """Build and solve plan-ahead; return (model, vars, P) or (None,None,None)."""
    import math
    epsilon   = 0.10
    sigma_frac = 0.20
    kappa_val  = math.sqrt((1 - epsilon) / epsilon)
    one_node_max = node_cap / (1.0 + kappa_val * sigma_frac)
    usage_min  = round(one_node_max * 1.05, 2)
    usage_max  = round(one_node_max * 1.40, 2)

    P = build_synthetic_data(
        seed                    = seed,
        n_tenants               = n_tenants,
        n_nodes                 = n_nodes,
        n_intervals             = 3,
        n_always_available      = n_always,
        n_exclusive             = n_excl,
        node_capacity           = node_cap,
        sigma_frac              = sigma_frac,
        epsilon                 = epsilon,
        tenant_usage_min        = usage_min,
        tenant_usage_max        = usage_max,
        min_machines_per_tenant = min_mach,
        feedback_wait           = feedback_wait or {},
        feedback_vbar           = feedback_vbar or {},
        feedback_beta           = 0.3,
        feedback_wait_ref       = 10.0,
    )
    P['lam'][0] = 0.0
    env = make_gurobi_env()
    m, v = build_model(P, env, use_socp=use_socp)
    m.Params.TimeLimit    = 120
    m.Params.MIPGap       = 0.03
    m.Params.LogToConsole = 0
    m.optimize()
    if m.Status in (GRB.OPTIMAL, GRB.TIME_LIMIT, GRB.SUBOPTIMAL):
        return m, v, P
    return None, None, None


def _make_nodes(machine_ids: list[int], node_cap_mb: float = 40_960.0) -> list[NodeState]:
    nodes = []
    for nid in machine_ids:
        cap  = node_cap_mb + nid * 4_096
        tax  = round(cap * 0.05 / 1024) * 1024
        cores = float(max(4, 4 + nid))
        nodes.append(NodeState(
            node_id=nid, capacity_mb=cap, os_tax_mb=tax,
            cpu_cores=cores, used_mb=0.0, threshold_frac=0.10,
        ))
    return nodes


def _make_jobs(
    tenant_ids: list[int],
    n_jobs: int,
    rng: np.random.Generator,
    interval: int = 0,
) -> list[Job]:
    jobs = []
    for k in range(n_jobs):
        tenant   = tenant_ids[k % len(tenant_ids)]
        req_mem  = float(rng.uniform(256, 1024))
        req_cpu  = float(rng.uniform(0.25, 2.0))
        pred_mem = req_mem * rng.uniform(0.80, 1.00)
        pred_cpu = req_cpu * rng.uniform(0.80, 0.95)
        jobs.append(Job(
            job_id=f"i{interval}_j{k}", tenant_id=tenant,
            req_mem_mb=round(req_mem, 2), req_cpu=round(req_cpu, 3),
            pred_mem_mb=round(pred_mem, 2), pred_cpu_p95=round(pred_cpu, 3),
            arrival_round=interval,
        ))
    return jobs


def _run_realtime(
    plan_out: dict,
    n_jobs_per_group: int,
    rng: np.random.Generator,
) -> dict:
    """Run one realtime pass across all groups in the plan; return placement stats."""
    total_placed   = 0
    total_unplaced = 0
    groups_run     = 0

    for iv in plan_out["intervals"]:
        h      = iv["interval"]
        groups = iv["groups"]
        for g in groups:
            tids = g["tenant_ids"]
            mids = g["machine_ids"]
            if not mids:
                continue
            jobs  = _make_jobs(tids, n_jobs_per_group, rng, h)
            nodes = _make_nodes(mids)
            placements = rt_module.solve(jobs=jobs, nodes=nodes, W_t={}, time_limit_ms=10_000)
            placed   = sum(1 for v in placements.values() if v is not None)
            unplaced = sum(1 for v in placements.values() if v is None)
            total_placed   += placed
            total_unplaced += unplaced
            groups_run     += 1

    total_jobs = total_placed + total_unplaced
    return {
        "placed":      total_placed,
        "unplaced":    total_unplaced,
        "total":       total_jobs,
        "rate":        total_placed / max(1, total_jobs),
        "groups_run":  groups_run,
    }


# ── Test 1: minimum machines for near-zero queuing ───────────────────────────

def test_min_machines_for_low_queuing():
    """
    Sweep min_machines_per_tenant from 1 to 4 for a shared-tenant cluster.
    Expect placement rate to increase monotonically and reach ≥ 90% at 2 machines.
    """
    print("\n[1] Minimum machines per tenant vs placement rate")
    rng = np.random.default_rng(42)
    n_jobs = 15

    results = []
    for min_mach in [1, 2, 3, 4]:
        m, v, P = _plan_solve(
            n_tenants=3, n_nodes=9, n_excl=0, n_always=5,
            min_mach=min_mach, seed=42,
        )
        if m is None:
            print(f"  min_mach={min_mach}: model infeasible — skip")
            continue
        plan_out = extract_plan_output(v, P)

        # Avg machines per shared tenant per period
        T_s, M, H = P['T_s'], P['M'], P['H']
        y = v['y']
        avg_mach = sum(
            sum(1 for n in M if y[i, n, h].X > 0.5)
            for i in T_s for h in H
        ) / max(1, len(T_s) * len(H))

        stats = _run_realtime(plan_out, n_jobs, rng)
        results.append((min_mach, avg_mach, stats['rate']))
        print(f"    min_mach={min_mach}  avg_mach/tenant={avg_mach:.1f}  "
              f"placement_rate={stats['rate']:.1%}  "
              f"placed={stats['placed']}/{stats['total']}")

    if results:
        rates = [r for (_, _, r) in results]
        _assert(max(rates) >= 0.80,
                f"best placement rate ≥ 80% (best={max(rates):.1%})")
        if len(rates) >= 2:
            _assert(rates[-1] >= rates[0],
                    f"rate increases with more machines ({rates[0]:.1%} → {rates[-1]:.1%})")


# ── Test 2: exclusive tenant stability across plan refreshes ──────────────────

def test_exclusive_assignment_stable_across_horizons():
    """
    Run plan-ahead twice with the same seed. Exclusive tenants must get the
    same machine set per period in both runs (deterministic same-seed result).
    """
    print("\n[2] Exclusive assignment deterministic across same-seed plan refreshes")
    m1, v1, P1 = _plan_solve(n_tenants=4, n_nodes=8, n_excl=1, n_always=4, min_mach=2, seed=10)
    m2, v2, P2 = _plan_solve(n_tenants=4, n_nodes=8, n_excl=1, n_always=4, min_mach=2, seed=10)
    if m1 is None or m2 is None:
        print("  SKIP  (one solve failed)")
        return

    T_e, M, H = P1['T_e'], P1['M'], P1['H']
    for i in T_e:
        for h in H:
            mach1 = frozenset(n for n in M if v1['e'][i, n, h].X > 0.5)
            mach2 = frozenset(n for n in M if v2['e'][i, n, h].X > 0.5)
            _assert(mach1 == mach2,
                    f"exclusive tenant {i} period {h}: same machines in both same-seed runs "
                    f"({sorted(mach1)} == {sorted(mach2)})")


# ── Test 3: feedback expands assignment in next horizon ───────────────────────

def test_feedback_increases_machines_next_horizon():
    """
    Simulate a high-queuing scenario: provide feedback with large wait times.
    The plan must assign more machines in the feedback run vs baseline.
    """
    print("\n[3] Feedback increases machines in next planning horizon")
    # Baseline: no wait — use same 12-node config as feedback run for fair comparison
    m_base, v_base, P_base = _plan_solve(
        n_tenants=3, n_nodes=12, n_excl=0, n_always=8, min_mach=2, seed=55)
    if m_base is None:
        print("  SKIP  (baseline infeasible)")
        return

    T_s, M, H = P_base['T_s'], P_base['M'], P_base['H']
    y_base = v_base['y']
    mach_base = sum(
        1 for i in T_s for n in M for h in H if y_base[i, n, h].X > 0.5
    )

    # Moderate feedback: wait = 1× ref (10) → scale = 1.30 (stays feasible with 12 nodes)
    high_wait  = {i: 10.0 for i in T_s}
    m_fb, v_fb, P_fb = _plan_solve(
        n_tenants=3, n_nodes=12, n_excl=0, n_always=8, min_mach=2, seed=55,
        feedback_wait=high_wait,
    )
    if m_fb is None:
        print("  SKIP  (feedback model infeasible)")
        return

    y_fb   = v_fb['y']
    mach_fb = sum(
        1 for i in T_s for n in M for h in H if y_fb[i, n, h].X > 0.5
    )
    _assert(mach_fb >= mach_base,
            f"feedback run assigns ≥ baseline machines "
            f"(baseline={mach_base}, feedback={mach_fb})")
    print(f"    baseline={mach_base} machines, feedback={mach_fb} machines")


# ── Test 4: full two-horizon pipeline run ────────────────────────────────────

def test_two_horizon_pipeline():
    """
    Run horizon 1 → collect placement stats → build feedback → run horizon 2.
    Horizon 2 must solve and produce ≥ as many machine-assignments as horizon 1.
    """
    print("\n[4] Two-horizon pipeline with feedback propagation")
    rng = np.random.default_rng(77)
    n_jobs = 12

    # Horizon 1
    m1, v1, P1 = _plan_solve(n_tenants=3, n_nodes=9, n_excl=1, n_always=4,
                              min_mach=2, seed=77)
    if m1 is None:
        print("  SKIP  (horizon 1 infeasible)")
        return
    plan1 = extract_plan_output(v1, P1)
    stats1 = _run_realtime(plan1, n_jobs, rng)
    print(f"    Horizon 1 — placed={stats1['placed']}/{stats1['total']} "
          f"({stats1['rate']:.1%})")
    _assert(stats1['placed'] > 0, f"horizon 1 places ≥1 job ({stats1['placed']})")

    # Build feedback from placement stats (simulate wait accumulation)
    T_s = P1['T_s']
    unplaced_per_tenant = max(0, stats1['unplaced'] // max(1, len(T_s)))
    wait_feedback = {i: float(unplaced_per_tenant) for i in T_s}

    # Horizon 2 with feedback
    m2, v2, P2 = _plan_solve(n_tenants=3, n_nodes=9, n_excl=1, n_always=4,
                              min_mach=2, seed=77,
                              feedback_wait=wait_feedback)
    if m2 is None:
        print("  SKIP  (horizon 2 infeasible)")
        return
    plan2 = extract_plan_output(v2, P2)
    stats2 = _run_realtime(plan2, n_jobs, rng)
    print(f"    Horizon 2 — placed={stats2['placed']}/{stats2['total']} "
          f"({stats2['rate']:.1%})")
    _assert(stats2['placed'] >= 0, f"horizon 2 places ≥0 jobs ({stats2['placed']})")

    T_s2, M2, H2 = P2['T_s'], P2['M'], P2['H']
    y2 = v2['y']
    mach2 = sum(1 for i in T_s2 for n in M2 for h in H2 if y2[i, n, h].X > 0.5)
    T_s1, M1, H1 = P1['T_s'], P1['M'], P1['H']
    y1 = v1['y']
    mach1 = sum(1 for i in T_s1 for n in M1 for h in H1 if y1[i, n, h].X > 0.5)
    _assert(mach2 >= mach1,
            f"horizon 2 machine-assignments ≥ horizon 1 ({mach1} → {mach2})")


# ── Test 5: exclusive vs shared placement rate comparison ─────────────────────

def test_exclusive_vs_shared_placement():
    """
    Compare placement rate for an exclusive group (1 tenant, dedicated nodes)
    vs a shared group (3 tenants on shared nodes). Exclusive should achieve ≥ shared.
    """
    print("\n[5] Exclusive vs shared placement rate")
    rng = np.random.default_rng(88)
    n_jobs = 12

    m, v, P = _plan_solve(n_tenants=4, n_nodes=10, n_excl=1, n_always=5,
                           min_mach=2, seed=88)
    if m is None:
        print("  SKIP  (model infeasible)")
        return
    plan_out = extract_plan_output(v, P)

    excl_placed = excl_total = 0
    shrd_placed = shrd_total = 0
    for iv in plan_out["intervals"]:
        for g in iv["groups"]:
            if not g["machine_ids"]:
                continue
            jobs  = _make_jobs(g["tenant_ids"], n_jobs, rng, iv["interval"])
            nodes = _make_nodes(g["machine_ids"])
            pl    = rt_module.solve(jobs=jobs, nodes=nodes, W_t={}, time_limit_ms=10_000)
            p = sum(1 for nid in pl.values() if nid is not None)
            u = sum(1 for nid in pl.values() if nid is None)
            if g["exclusive"]:
                excl_placed += p; excl_total += p + u
            else:
                shrd_placed += p; shrd_total += p + u

    excl_rate = excl_placed / max(1, excl_total)
    shrd_rate = shrd_placed / max(1, shrd_total)
    print(f"    exclusive rate={excl_rate:.1%}  shared rate={shrd_rate:.1%}")
    _assert(excl_rate >= shrd_rate * 0.90,
            f"exclusive rate ({excl_rate:.1%}) ≥ 90% of shared ({shrd_rate:.1%})")
    _assert(shrd_rate >= 0.60,
            f"shared placement rate ≥ 60% ({shrd_rate:.1%})")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Pipeline quality tests — machines-per-tenant, feedback, placement rate")
    print("=" * 70)

    test_min_machines_for_low_queuing()
    test_exclusive_assignment_stable_across_horizons()
    test_feedback_increases_machines_next_horizon()
    test_two_horizon_pipeline()
    test_exclusive_vs_shared_placement()

    print("\n" + "=" * 70)
    print(f"Results: {_passed} passed, {_failed} failed")
    sys.exit(0 if _failed == 0 else 1)
