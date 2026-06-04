"""
PlanAhead/plan_ahead_iterative.py
───────────────────────────────────
Experimental streaming plan-ahead: rolling-window tenant × node allocation.

Motivation
──────────
The full MISOCP (plan_ahead_optimizer.py) solves all T tenants × N machines
in one call.  At T=256, N=512 (1.06M variables) Gurobi runs out of memory.
This file experiments with a different strategy: process tenants in a rolling
window of UNIT_TENANTS × UNIT_NODES, running many small, fast solves instead
of one large one.

Algorithm
─────────
  Parameters:
    UNIT_TENANTS  = 8    active tenants in the current window
    UNIT_NODES    = 64   nodes in the active pool
    TOTAL_TENANTS = 128  total tenant population to place
    N_PERIODS     = 4    planning horizon periods
    NODE_CAPACITY = 10.0 capacity per node per period

  State:
    active_tenants  — current window (≤ UNIT_TENANTS tenants being placed)
    active_nodes    — current pool  (≤ UNIT_NODES nodes with remaining capacity)
    tenant_queue    — tenants not yet started
    completed       — tenants fully placed

  Per iteration:
    1. Greedy allocation (first-fit decreasing demand order):
       For each active tenant (sorted by remaining total demand, largest first):
         For each period h:
           Find the node n with most remaining capacity[n,h].
           Allocate min(remaining_demand[i,h], remaining_capacity[n,h]).

    2. Evict complete tenants:
       If all demand[i,h] satisfied for all h → mark complete, pull next from queue.

    3. Evict full nodes:
       If max(remaining_capacity[n,h] for all h) < EVICT_THRESH → retire node,
       provision a fresh node.

    4. Stall detection:
       If total demand satisfied this iteration < MIN_PROGRESS:
         stall_count++
         After STALL_LIMIT consecutive stalls → forcibly evict the least-
         remaining-capacity node (even if partially used), provision a fresh one.

    5. Termination:
       Loop ends when active_tenants is empty AND tenant_queue is empty.
       All tenant workload has been placed.

Why 8 tenants × 64 nodes?
  Based on computational timing data: T=8, N=64 solves in ~2s with the full
  MISOCP.  This is the fastest (T,N) combination with meaningful load.
  Using it as the window size keeps each iteration very fast.

Run
───
  cd PlanAhead/
  python plan_ahead_iterative.py

  Optional flags:
    --tenants N    total tenant population (default 128)
    --seed N       random seed (default 42)
    --csv          save results to ../Pipeline/timing_data/pa_iterative_results.csv
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import numpy as np

# ═══════════════════════════════════════════════════════════════════════════════
# § CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

UNIT_TENANTS  = 8      # active tenant window size (T dimension per iteration)
UNIT_NODES    = 64     # active node pool size     (N dimension per iteration)
TOTAL_TENANTS = 128    # total tenant population to place

N_PERIODS     = 4      # planning horizon length (number of periods h)
NODE_CAPACITY = 10.0   # capacity per node per period (abstract units)
DEMAND_MIN    = 0.5    # minimum u[i,h] per tenant per period
DEMAND_MAX    = 2.5    # maximum u[i,h] per tenant per period

EVICT_THRESH  = 0.05   # evict node when max remaining capacity < 5% of NODE_CAPACITY
MIN_PROGRESS  = 1e-4   # minimum demand placed per iteration before stall triggered
STALL_LIMIT   = 3      # consecutive low-progress iterations before forced node eviction

SEED = 42


# ═══════════════════════════════════════════════════════════════════════════════
# § DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class IterTenant:
    tenant_id: int
    demand:    dict[int, float]   # period h → demand amount u[i,h]
    placed:    dict[int, float] = field(default_factory=dict)

    def remaining_demand(self, h: int) -> float:
        return max(0.0, self.demand[h] - self.placed.get(h, 0.0))

    def total_remaining(self) -> float:
        return sum(self.remaining_demand(h) for h in self.demand)

    def total_demand(self) -> float:
        return sum(self.demand.values())

    def is_complete(self) -> bool:
        return all(self.remaining_demand(h) < 1e-6 for h in self.demand)

    def satisfaction_frac(self) -> float:
        td = self.total_demand()
        if td < 1e-9:
            return 1.0
        placed = sum(self.placed.get(h, 0.0) for h in self.demand)
        return placed / td


@dataclass
class IterNode:
    node_id:   int
    remaining: dict[int, float]   # period h → remaining capacity

    def max_remaining(self) -> float:
        return max(self.remaining.values()) if self.remaining else 0.0

    def is_full(self) -> bool:
        return self.max_remaining() < EVICT_THRESH * NODE_CAPACITY

    def total_used(self) -> float:
        return sum(
            NODE_CAPACITY - self.remaining.get(h, NODE_CAPACITY)
            for h in self.remaining
        )


# ═══════════════════════════════════════════════════════════════════════════════
# § GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

def _make_tenants(n: int, seed: int) -> deque[IterTenant]:
    rng = np.random.default_rng(seed)
    q: deque[IterTenant] = deque()
    for i in range(n):
        demand = {
            h: float(rng.uniform(DEMAND_MIN, DEMAND_MAX))
            for h in range(N_PERIODS)
        }
        q.append(IterTenant(tenant_id=i, demand=demand))
    return q


def _fresh_node(node_id: int) -> IterNode:
    return IterNode(
        node_id=node_id,
        remaining={h: NODE_CAPACITY for h in range(N_PERIODS)},
    )


# ═══════════════════════════════════════════════════════════════════════════════
# § GREEDY ALLOCATION (one iteration)
# ═══════════════════════════════════════════════════════════════════════════════

def _allocate_greedy(tenants: list[IterTenant], nodes: list[IterNode]) -> float:
    """
    First-fit decreasing allocation: sort tenants by total remaining demand
    (largest first); for each tenant and period, assign to the node with the
    most remaining capacity in that period.

    Returns total demand placed in this call.
    """
    sorted_tenants = sorted(tenants, key=lambda t: t.total_remaining(), reverse=True)
    placed_total = 0.0

    for tenant in sorted_tenants:
        for h in range(N_PERIODS):
            rem = tenant.remaining_demand(h)
            if rem < 1e-9:
                continue
            # Pick node with most remaining capacity in period h
            best_node = max(nodes, key=lambda n: n.remaining[h])
            avail = best_node.remaining[h]
            if avail < 1e-9:
                continue
            alloc = min(rem, avail)
            tenant.placed[h] = tenant.placed.get(h, 0.0) + alloc
            best_node.remaining[h] -= alloc
            placed_total += alloc

    return placed_total


# ═══════════════════════════════════════════════════════════════════════════════
# § MAIN LOOP
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class IterStats:
    iteration:         int
    active_tenants:    int
    active_nodes:      int
    demand_placed:     float   # demand placed this iteration
    tenants_completed: int     # tenants completed this iteration
    nodes_evicted:     int     # nodes evicted this iteration
    nodes_provisioned: int     # fresh nodes added this iteration
    elapsed_s:         float


def run_iterative(
    total_tenants: int = TOTAL_TENANTS,
    seed:          int = SEED,
    verbose:       bool = True,
) -> tuple[list[IterStats], list[IterTenant]]:
    """
    Run the iterative allocation and return (iteration_log, completed_tenants).
    """
    tenant_queue  = _make_tenants(total_tenants, seed)
    next_node_id  = 0

    # Seed active window
    active_tenants: list[IterTenant] = []
    while tenant_queue and len(active_tenants) < UNIT_TENANTS:
        active_tenants.append(tenant_queue.popleft())

    active_nodes: list[IterNode] = [_fresh_node(i) for i in range(UNIT_NODES)]
    next_node_id = UNIT_NODES

    completed:  list[IterTenant] = []
    stats_log:  list[IterStats]  = []
    stall_count = 0
    t_wall = time.perf_counter()

    if verbose:
        print(f"\n  Starting iterative allocation")
        print(f"  Total tenants  : {total_tenants}")
        print(f"  Window         : {UNIT_TENANTS} tenants × {UNIT_NODES} nodes")
        print(f"  Node capacity  : {NODE_CAPACITY} units/period × {N_PERIODS} periods")
        print(f"  {'Iter':>6}  {'Active T':>9}  {'Active N':>9}  "
              f"{'Placed':>10}  {'Cmpl':>6}  {'Evict':>6}  {'New':>5}")
        print(f"  {'─'*65}")

    iteration = 0
    while active_tenants:
        iteration += 1
        n_active_t = len(active_tenants)
        n_active_n = len(active_nodes)

        # 1. Greedy allocation
        demand_placed = _allocate_greedy(active_tenants, active_nodes)

        # 2. Evict completed tenants
        tenants_completed = 0
        remaining_tenants: list[IterTenant] = []
        for t in active_tenants:
            if t.is_complete():
                completed.append(t)
                tenants_completed += 1
                if tenant_queue:
                    remaining_tenants.append(tenant_queue.popleft())
            else:
                remaining_tenants.append(t)
        active_tenants = remaining_tenants

        # 3. Evict full nodes; provision fresh replacements
        nodes_evicted = 0
        nodes_provisioned = 0
        new_nodes: list[IterNode] = []
        for node in active_nodes:
            if node.is_full():
                nodes_evicted += 1
                fresh = _fresh_node(next_node_id)
                next_node_id += 1
                new_nodes.append(fresh)
                nodes_provisioned += 1
            else:
                new_nodes.append(node)
        active_nodes = new_nodes

        # 4. Stall detection — force-evict one partial node if stuck
        if demand_placed < MIN_PROGRESS:
            stall_count += 1
            if stall_count >= STALL_LIMIT and active_nodes:
                # Evict the node with the least remaining capacity (most consumed)
                worst_idx = min(range(len(active_nodes)),
                                key=lambda i: active_nodes[i].max_remaining())
                active_nodes.pop(worst_idx)
                fresh = _fresh_node(next_node_id)
                next_node_id += 1
                active_nodes.append(fresh)
                nodes_evicted    += 1
                nodes_provisioned += 1
                stall_count = 0
        else:
            stall_count = 0

        elapsed = time.perf_counter() - t_wall
        stat = IterStats(
            iteration=iteration,
            active_tenants=n_active_t,
            active_nodes=n_active_n,
            demand_placed=demand_placed,
            tenants_completed=tenants_completed,
            nodes_evicted=nodes_evicted,
            nodes_provisioned=nodes_provisioned,
            elapsed_s=elapsed,
        )
        stats_log.append(stat)

        if verbose and (iteration <= 20 or iteration % 20 == 0):
            print(f"  {iteration:>6}  {n_active_t:>9}  {n_active_n:>9}  "
                  f"{demand_placed:>10.4f}  {tenants_completed:>6}  "
                  f"{nodes_evicted:>6}  {nodes_provisioned:>5}")

        # Safety: if no nodes left, provision more
        if not active_nodes:
            for _ in range(UNIT_NODES):
                active_nodes.append(_fresh_node(next_node_id))
                next_node_id += 1

    return stats_log, completed


# ═══════════════════════════════════════════════════════════════════════════════
# § OUTPUT / REPORTING
# ═══════════════════════════════════════════════════════════════════════════════

def _report(stats: list[IterStats], completed: list[IterTenant], total_tenants: int) -> None:
    print(f"\n  {'─'*65}")
    if not stats:
        print("  No iterations — nothing to report.")
        return

    total_time = stats[-1].elapsed_s
    total_demand_placed = sum(s.demand_placed for s in stats)
    total_nodes_used    = sum(s.nodes_provisioned for s in stats) + UNIT_NODES
    avg_placed_per_iter = total_demand_placed / max(1, len(stats))
    satisfied = [t.satisfaction_frac() for t in completed]

    print(f"\n  Iterative Plan-Ahead — Final Report")
    print(f"  {'─'*52}")
    print(f"  Tenants placed        : {len(completed)} / {total_tenants}")
    print(f"  Total iterations      : {len(stats)}")
    print(f"  Total wall time       : {total_time:.3f} s")
    print(f"  Total nodes used      : {total_nodes_used}")
    print(f"  Total demand placed   : {total_demand_placed:.4f} capacity-units")
    print(f"  Avg demand / iter     : {avg_placed_per_iter:.4f}")
    print(f"  Avg satisfaction      : {sum(satisfied)/max(1,len(satisfied))*100:.1f}%")
    print(f"  Min satisfaction      : {min(satisfied)*100:.1f}%" if satisfied else "  —")
    print(f"\n  Stall events (iter where placed < {MIN_PROGRESS}):")
    stalls = [s for s in stats if s.demand_placed < MIN_PROGRESS]
    print(f"    {len(stalls)} stall iterations out of {len(stats)}")
    print(f"\n  Node evictions (forced replacements):")
    total_evict = sum(s.nodes_evicted for s in stats)
    print(f"    {total_evict} total node evictions over {len(stats)} iterations")
    print(f"\n  Throughput: {len(completed)/max(0.001,total_time):.1f} tenants/s")


def _save_csv(stats: list[IterStats], out_path: Path) -> None:
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([
            "iteration", "active_tenants", "active_nodes",
            "demand_placed", "tenants_completed",
            "nodes_evicted", "nodes_provisioned", "elapsed_s",
        ])
        for s in stats:
            w.writerow([
                s.iteration, s.active_tenants, s.active_nodes,
                round(s.demand_placed, 6), s.tenants_completed,
                s.nodes_evicted, s.nodes_provisioned, round(s.elapsed_s, 4),
            ])
    print(f"\n  CSV → {out_path}")


# ═══════════════════════════════════════════════════════════════════════════════
# § ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Iterative plan-ahead: streaming tenant × node allocation experiment"
    )
    parser.add_argument("--tenants", type=int, default=TOTAL_TENANTS,
                        help=f"Total tenant population (default {TOTAL_TENANTS})")
    parser.add_argument("--seed",    type=int, default=SEED,
                        help=f"Random seed (default {SEED})")
    parser.add_argument("--csv",     action="store_true",
                        help="Save iteration log to Pipeline/timing_data/pa_iterative_results.csv")
    args = parser.parse_args()

    print("═" * 70)
    print("  Plan-Ahead Iterative Allocation — Experimental")
    print("═" * 70)
    print(f"  Unit   : {UNIT_TENANTS} tenants × {UNIT_NODES} nodes per iteration")
    print(f"  Config : {args.tenants} total tenants, seed={args.seed}")
    print(f"  Demand : u[i,h] ∈ [{DEMAND_MIN}, {DEMAND_MAX}]  ×  {N_PERIODS} periods")
    print(f"  Node   : {NODE_CAPACITY} capacity units/period, evict < {EVICT_THRESH*100:.0f}% remaining")

    stats, completed = run_iterative(
        total_tenants=args.tenants,
        seed=args.seed,
        verbose=True,
    )
    _report(stats, completed, args.tenants)

    if args.csv:
        out_dir = Path(__file__).resolve().parent.parent / "Pipeline" / "timing_data"
        out_dir.mkdir(parents=True, exist_ok=True)
        _save_csv(stats, out_dir / "pa_iterative_results.csv")

    print()


if __name__ == "__main__":
    main()
