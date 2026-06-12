"""
Realtime/optimizer_iterative.py
─────────────────────────────────
Iterative batch-placement wrapper around realtime_optimizer.py.

Instead of one large J×N MILP, this version loops:
  1. Select up to BATCH_JOBS unplaced jobs  (largest pred_mem first — FFD heuristic).
  2. Select up to BATCH_NODES nodes with the most remaining capacity.
  3. Call realtime_optimizer.solve() on that sub-problem (same MILP, smaller scope).
  4. Record placements, update node memory usage, remove placed jobs from pending.
  5. Evict nodes whose remaining capacity < FULL_THRESH × total (only when spares exist).
  6. Repeat until all jobs placed or a full iteration places nothing (stall).
  7. Unplaced jobs at termination are returned as None — identical to the base solver;
     ClusterManager re-queues them for the next scheduling round.

Why batch?
───────────
  Full J×N MILP: J×N binary variables — exponential in both dimensions.
  Batch MILP:    BATCH_JOBS×BATCH_NODES variables per call — stays tractable.
  Trade-off:     greedy sub-problem order may use more node capacity than the global
                 optimum, but each sub-MILP is solved to integer optimality.

Default configuration
──────────────────────
  BATCH_JOBS  = 32   max jobs per sub-MILP
  BATCH_NODES = 32   max nodes per sub-MILP
  FULL_THRESH = 0.05 evict node when remaining < 5% of capacity (only if spares exist)
  SOLVER_ID   = "CBC" integer backend for each sub-MILP

Public API
──────────
  solve(jobs, nodes, W_t, K, time_limit_ms,
        batch_jobs, batch_nodes, full_thresh, solver_id)
    → dict[job_id -> node_id | None]

  Same return contract as realtime_optimizer.py — drop-in compatible.
"""

from __future__ import annotations

import dataclasses
import io
import math
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import realtime_optimizer as _rt

from simulation_data import (
    Job, NodeState,
    compute_available_capacity,
    compute_remaining_avail,
    K_WINDOW,
)

BATCH_JOBS:  int   = 16   # chosen default — see Pipeline timing analysis (16-job batch)
BATCH_NODES: int   = 16
FULL_THRESH: float = 0.05
SOLVER_ID:   str   = "CBC"


# ── Public API ─────────────────────────────────────────────────────────────────

def solve(
    jobs:           list[Job],
    nodes:          list[NodeState],
    W_t:            dict[int, float],
    K:              int   = K_WINDOW,
    time_limit_ms:  int   = 10_000,
    batch_jobs:     int   = BATCH_JOBS,
    batch_nodes:    int   = BATCH_NODES,
    full_thresh:    float = FULL_THRESH,
    solver_id:      str   = SOLVER_ID,
    stats:          dict | None = None,
) -> dict[str, int | None]:
    """
    Iterative batch placement.

    Parameters
    ----------
    jobs, nodes, W_t, K : same meaning as realtime_optimizer.solve()
    time_limit_ms        : total budget; each sub-MILP gets budget / n_batches
    batch_jobs           : max jobs per sub-MILP call
    batch_nodes          : max nodes per sub-MILP call
    full_thresh          : retire a node when remaining < full_thresh × total capacity
    solver_id            : integer backend ("CBC", "SCIP", "GUROBI", "HIGHS")

    Returns
    -------
    dict  job_id -> node_id (int) if placed, None if still pending.
    """
    if not jobs or not nodes:
        if stats is not None:
            stats["iterations"] = 0
        return {j.job_id: None for j in jobs}

    # Track extra memory added to each node by placements in this call
    extra_used: dict[int, float] = {n.node_id: 0.0 for n in nodes}
    node_map:   dict[int, NodeState] = {n.node_id: n for n in nodes}

    result: dict[str, int | None] = {}

    # FFD: largest pred_mem first so big jobs don't get squeezed out
    unplaced = sorted(jobs, key=lambda j: j.pred_mem_mb, reverse=True)

    # All nodes start as available
    available_ids: list[int] = [n.node_id for n in nodes]

    def _remaining(nid: int) -> float:
        n = node_map[nid]
        m_cap = compute_available_capacity(n)
        return compute_remaining_avail(n, m_cap) - extra_used[nid]

    def _total_cap(nid: int) -> float:
        return compute_available_capacity(node_map[nid])

    # Distribute time budget across expected number of batches
    n_batches_est  = max(1, math.ceil(len(jobs) / batch_jobs))
    per_batch_ms   = max(500, time_limit_ms // n_batches_est)

    iterations = 0   # number of sub-MILP solves actually performed
    while unplaced:
        # ── Select batch ──────────────────────────────────────────────────────
        available_ids.sort(key=_remaining, reverse=True)
        batch_n_ids = available_ids[:batch_nodes]

        if not batch_n_ids:
            break

        # Build updated NodeState copies reflecting current in-call usage
        batch_nodes_updated = [
            dataclasses.replace(node_map[nid], used_mb=node_map[nid].used_mb + extra_used[nid])
            for nid in batch_n_ids
        ]
        batch_j = unplaced[:batch_jobs]

        # ── Solve sub-MILP via realtime_optimizer core ────────────────────────
        iterations += 1
        sub_result = _rt.solve(
            batch_j, batch_nodes_updated, W_t, K,
            per_batch_ms, solver_id=solver_id,
        )

        # ── Record placements ─────────────────────────────────────────────────
        placed_this: list[Job] = []
        for j in batch_j:
            nid = sub_result.get(j.job_id)
            result[j.job_id] = nid
            if nid is not None:
                placed_this.append(j)
                extra_used[nid] += j.pred_mem_mb

        if not placed_this:
            break  # stall — no progress; remaining jobs stay pending

        # Remove placed jobs from pending list
        placed_ids = {j.job_id for j in placed_this}
        unplaced   = [j for j in unplaced if j.job_id not in placed_ids]

        # ── Evict full/near-full nodes (only when spares exist) ───────────────
        if len(available_ids) > len(batch_n_ids):
            still_ok = [
                nid for nid in available_ids
                if _remaining(nid) >= full_thresh * _total_cap(nid)
            ]
            if still_ok:
                available_ids = still_ok

    # Jobs still unplaced → None (ClusterManager re-queues them)
    for j in unplaced:
        result[j.job_id] = None

    if stats is not None:
        stats["iterations"] = iterations

    return result


# ── Interactive test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import time as _time
    from datetime import datetime, timezone

    import numpy as np

    ap = argparse.ArgumentParser(description="Interactive test for optimizer_iterative.py")
    ap.add_argument("--jobs",        type=int,   default=16,         help="Number of jobs  (default 16)")
    ap.add_argument("--nodes",       type=int,   default=64,         help="Number of nodes (default 64)")
    ap.add_argument("--batch-jobs",  type=int,   default=BATCH_JOBS, help=f"Batch job size   (default {BATCH_JOBS})")
    ap.add_argument("--batch-nodes", type=int,   default=BATCH_NODES,help=f"Batch node size  (default {BATCH_NODES})")
    ap.add_argument("--solver",      default=SOLVER_ID,              help="Backend solver   (default CBC)")
    ap.add_argument("--time-limit",  type=int,   default=10_000,     help="Time limit ms    (default 10000)")
    ap.add_argument("--seed",        type=int,   default=42,         help="Random seed      (default 42)")
    ap.add_argument("--compare",     action="store_true",
                    help="Also run single-shot MILP for comparison")
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    now = datetime.now(timezone.utc)

    n_tenants = max(2, args.jobs // 6)

    jobs = []
    for i in range(args.jobs):
        mem = float(np.clip(rng.normal(5_120, 2_048), 512, 32_768))
        cpu = float(rng.uniform(0.5, 4.0))
        jobs.append(Job(
            job_id=f"j{i}", tenant_id=int(rng.integers(0, n_tenants)),
            req_mem_mb=round(mem, 1), req_cpu=round(cpu, 3),
            pred_mem_mb=round(mem * float(rng.uniform(0.85, 1.0)), 1),
            pred_cpu_p95=round(cpu * float(rng.uniform(0.85, 1.0)), 3),
            arrival_round=0, arrival_timestamp=now,
        ))

    nodes = []
    for i in range(args.nodes):
        cap = 65_536.0
        tax = round(cap * 0.05 / 1024) * 1024
        used = float(np.clip(rng.normal(5_000, 2_000), 0, cap * 0.70))
        nodes.append(NodeState(
            node_id=i, capacity_mb=cap, os_tax_mb=tax,
            cpu_cores=8.0, used_mb=used, threshold_frac=0.10,
        ))

    W_t = {t: 0.0 for t in range(n_tenants)}

    print("═" * 60)
    print("  RT Iterative Solver — Interactive Test")
    print("═" * 60)
    print(f"  Jobs={args.jobs}  Nodes={args.nodes}  "
          f"Batch={args.batch_jobs}×{args.batch_nodes}  Solver={args.solver}")
    print()

    t0 = _time.perf_counter()
    result = solve(
        jobs, nodes, W_t,
        time_limit_ms=args.time_limit,
        batch_jobs=args.batch_jobs,
        batch_nodes=args.batch_nodes,
        solver_id=args.solver,
    )
    elapsed_ms = (_time.perf_counter() - t0) * 1000.0

    placed  = sum(1 for v in result.values() if v is not None)
    pending = args.jobs - placed
    print(f"  Iterative : placed={placed}/{args.jobs}  pending={pending}  "
          f"time={elapsed_ms:.1f}ms")

    if args.compare:
        import realtime_optimizer as _rt_cmp

        t0 = _time.perf_counter()
        result2 = _rt_cmp.solve(jobs, nodes, W_t, K_WINDOW, args.time_limit, solver_id=args.solver)
        t_ms    = (_time.perf_counter() - t0) * 1000.0
        p2     = sum(1 for v in result2.values() if v is not None)
        print(f"  Single-shot: placed={p2}/{args.jobs}  pending={args.jobs-p2}  "
              f"time={t_ms:.1f}ms")
        print(f"\n  Δ placed : {placed - p2:+d}   Δ time : {elapsed_ms - t_ms:+.1f}ms")
    print()
