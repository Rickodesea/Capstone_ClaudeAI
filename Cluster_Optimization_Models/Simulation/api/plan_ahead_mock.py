"""
plan_ahead_mock.py
──────────────────
Mock of extract_tenant_access_schedule() from plan_ahead_optimizer.py.

The real MISOCP (Gurobi) outputs a TenantAccessSchedule:
    dict[(tenant_id, time_slot) -> list[node_id]]

This mock produces the same structure using only numpy.
Replace with the real Gurobi call once a WLS license is available.

Output matches PlanAheadResult in frontend/src/types.ts.
"""

from __future__ import annotations
import numpy as np

_TENANT_PROFILES = {
    0: (0.60, 0.90),   # T-1A: exclusive/high-priority
    1: (0.40, 0.70),   # T-1B: shared
    2: (0.20, 0.50),   # T-1C: shared
}
_DEFAULT_PROFILE = (0.30, 0.60)


def generate_plan_ahead(
    num_tenants: int,
    num_nodes: int,
    interval: int,
    plan_ahead_horizon: int = 50,
    access_period: int = 4,
) -> dict:
    """
    Generate mock TenantAccessSchedule for the given interval.

    Mirrors extract_tenant_access_schedule() output from plan_ahead_optimizer.py:
        schedule[(tenant_id, time_slot)] = [node_id, ...]

    Parameters
    ----------
    plan_ahead_horizon : total intervals in the planning window (e.g. 50)
    access_period      : intervals per time slot (e.g. 4i → slots "0-3i", "4-7i", ...)
    num_slots          : derived as horizon // access_period

    Seeded by week_number for reproducible but different weekly assignments.
    """
    num_slots = max(1, plan_ahead_horizon // access_period)
    week_num  = interval // max(1, plan_ahead_horizon)
    rng       = np.random.default_rng(week_num + 7919)

    # Build schedule: tenant_id -> slot -> [node_ids]
    tenant_schedule: dict[int, dict[int, list[int]]] = {}
    for t in range(num_tenants):
        lo, hi = _TENANT_PROFILES.get(t, _DEFAULT_PROFILE)
        tenant_schedule[t] = {}
        for slot in range(num_slots):
            n_assigned = max(1, int(num_nodes * rng.uniform(lo, hi)))
            chosen = sorted(int(x) for x in rng.choice(num_nodes, n_assigned, replace=False))
            tenant_schedule[t][slot] = chosen

    # Which slot is active right now within this planning period
    pos_in_period = interval % max(1, plan_ahead_horizon)
    current_slot  = min(num_slots - 1, pos_in_period // access_period)

    # Human-readable slot labels: "0-3i", "4-7i", ...
    slot_labels = [
        f"{s * access_period}–{(s + 1) * access_period - 1}i"
        for s in range(num_slots)
    ]

    # Summary metrics over the current slot
    node_tenant_counts = [0] * num_nodes
    for t in range(num_tenants):
        for n in tenant_schedule[t].get(current_slot, []):
            node_tenant_counts[n] += 1
    exclusive = sum(1 for c in node_tenant_counts if c == 1)
    isolation  = exclusive / max(1, num_nodes)

    avg_nodes = sum(
        len(tenant_schedule[t][s])
        for t in range(num_tenants)
        for s in range(num_slots)
    ) / max(1, num_tenants * num_slots)

    return {
        "interval":         interval,
        "num_slots":        num_slots,
        "access_period":    access_period,
        "planning_horizon": plan_ahead_horizon,
        "slot_labels":      slot_labels,
        "tenant_schedule":  {
            str(t): {str(s): tenant_schedule[t][s] for s in range(num_slots)}
            for t in range(num_tenants)
        },
        "current_slot": current_slot,
        "summary": {
            "avg_nodes_per_tenant": round(avg_nodes, 2),
            "isolation_score":      round(isolation, 3),
            "week_number":          week_num,
        },
    }
