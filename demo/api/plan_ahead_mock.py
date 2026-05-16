"""
plan_ahead_mock.py
──────────────────
Mock plan-ahead model output for the demo.

Produces the same data shape as the real MISOCP plan-ahead (Gurobi) but
uses only numpy — no license required.

Replace this module with the real plan_ahead_optimizer.py call in Phase 3
once a Gurobi WLS license is available in demo/api/.env.

Output contract (matches PlanAheadResult in frontend/src/types.ts):
    {
      "interval": int,
      "tenant_node_access": { tenant_id: [node_id, ...] },
      "heatmap": [{ "tenant_id", "node_id", "intensity", "authorized" }],
      "summary": { "avg_nodes_per_tenant": float, "isolation_score": float }
    }
"""

from __future__ import annotations
import numpy as np


# Tenant profiles: (min_node_frac, max_node_frac, intensity_range)
# Mirrors realistic capacity reservation patterns.
_TENANT_PROFILES = {
    0: (0.70, 0.90, (0.5, 0.95)),   # heavy tenant
    1: (0.50, 0.70, (0.25, 0.70)),  # medium tenant
    2: (0.30, 0.50, (0.10, 0.50)),  # light tenant
}
_DEFAULT_PROFILE = (0.40, 0.65, (0.15, 0.60))


def generate_plan_ahead(
    num_tenants: int,
    num_nodes: int,
    interval: int,
) -> dict:
    """
    Generate mock plan-ahead output for the given simulation interval.

    Seeded by (interval // num_tenants) so each "week" produces different
    but reproducible assignments. Tenant profiles bias the output toward
    realistic load distribution patterns.
    """
    week_num = interval // max(1, num_tenants)
    rng = np.random.default_rng(week_num + 7919)  # prime offset avoids trivial seeds

    tenant_node_access: dict[int, list[int]] = {}

    for t in range(num_tenants):
        lo, hi, _ = _TENANT_PROFILES.get(t, _DEFAULT_PROFILE)
        n_assigned = max(1, int(num_nodes * rng.uniform(lo, hi)))
        chosen = rng.choice(num_nodes, n_assigned, replace=False).tolist()
        tenant_node_access[t] = sorted(int(n) for n in chosen)

    heatmap: list[dict] = []
    for t in range(num_tenants):
        _, _, (i_lo, i_hi) = _TENANT_PROFILES.get(t, _DEFAULT_PROFILE)
        authorized_nodes = set(tenant_node_access.get(t, []))
        for n in range(num_nodes):
            if n in authorized_nodes:
                intensity = float(rng.uniform(i_lo, i_hi))
                # Primary nodes (first 1-2 assigned) get higher predicted load
                primary_count = max(1, len(authorized_nodes) // 2)
                if authorized_nodes and n == sorted(authorized_nodes)[0]:
                    intensity = min(1.0, intensity * 1.3)
            else:
                intensity = 0.0
            heatmap.append({
                "tenant_id": t,
                "node_id": n,
                "intensity": round(intensity, 3),
                "authorized": n in authorized_nodes,
            })

    avg_nodes = (
        sum(len(v) for v in tenant_node_access.values()) / max(1, num_tenants)
    )
    # Isolation score: fraction of node assignments that are exclusive to one tenant
    node_tenant_counts = [0] * num_nodes
    for nodes in tenant_node_access.values():
        for n in nodes:
            node_tenant_counts[n] += 1
    exclusive = sum(1 for c in node_tenant_counts if c == 1)
    isolation = exclusive / max(1, num_nodes)

    return {
        "interval": interval,
        "tenant_node_access": {str(k): v for k, v in tenant_node_access.items()},
        "heatmap": heatmap,
        "summary": {
            "avg_nodes_per_tenant": round(avg_nodes, 2),
            "isolation_score": round(isolation, 3),
            "week_number": week_num,
        },
    }
