"""
tenant_priority.py
──────────────────
Sort the job queue so plan-ahead prioritised tenants are served first.

A tenant is "prioritised" for a given real-time scheduling round when the
plan-ahead model allocated them to at least one node in the current period.
Prioritised jobs appear first in the queue passed to the MILP solver;
unprioritised jobs follow in FIFO order.

Because the real-time model enforces no hard access restrictions (C5 is a
weight boost, not a block), all jobs will ultimately be placed if capacity
allows — this sort only influences which jobs the solver sees first when
MAX_JOBS_PER_SOLVE caps the queue slice.
"""

from __future__ import annotations
from simulation_data import Job


def sort_by_plan_priority(
    jobs:               list[Job],
    tenant_node_access: dict[int, list[int]] | None,
) -> list[Job]:
    """
    Return jobs reordered with plan-ahead-prioritised tenants first.

    Parameters
    ----------
    jobs               : current queue (any order)
    tenant_node_access : plan-ahead priority map for the current period —
                         tenant_id -> [priority_node_ids].
                         None = no plan-ahead available; return jobs unchanged.

    Returns
    -------
    list[Job]
        Prioritised tenants first (FIFO within group), then unprioritised
        tenants (FIFO within group).  Order is stable within each group.
    """
    if not tenant_node_access:
        return jobs

    prioritized   = [j for j in jobs if j.tenant_id in tenant_node_access]
    unprioritized = [j for j in jobs if j.tenant_id not in tenant_node_access]
    return prioritized + unprioritized
