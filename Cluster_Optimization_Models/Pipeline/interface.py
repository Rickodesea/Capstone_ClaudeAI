"""
pipeline/interface.py
──────────────────────
End-to-end pipeline connecting the three model layers.

Architecture
─────────────
  1. Synthesis — build_synthetic_data() generates tenant usage profiles u[i,h]
     with exclusive tenant tagging and machine pool partitioning.

  2. Plan-Ahead MISOCP (Gurobi) — solves over the planning horizon H.
     Output: ordered list of intervals, each with tenant groups
     (tenant_ids, machine_ids, exclusive_flag). All tenants assigned every interval.

  3. Real-Time MILP (OR-Tools) — called once per tenant group per interval.
     The Cluster Manager filters jobs and machines before each call.
     The Realtime model has no knowledge of tenants or plan-ahead structure.

Usage
─────
    python interface.py          # Sample 1 — Simple (default)
    python interface.py 2        # Sample 2 — Medium
    python interface.py 3        # Sample 3 — High
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# ── Path setup ────────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "Realtime"))
sys.path.insert(0, str(_ROOT / "PlanAhead"))

from plan_ahead_data      import build_synthetic_data, make_gurobi_env
from plan_ahead_optimizer import build_model, solve_and_report, extract_plan_output
from gurobipy import GRB

import realtime_optimizer as rt_module
from simulation_data import Job, NodeState

from pipeline_configs import SAMPLES, PipelineConfig


# ============================================================================
# § HELPERS
# ============================================================================

def _make_realtime_nodes(machine_ids: list[int]) -> list[NodeState]:
    """Create NodeState objects for the given machine IDs."""
    nodes = []
    for nid in machine_ids:
        mem_mb = float(16_384 + nid * 2_048)
        tax_mb = round(mem_mb * 0.05 / 1024.0) * 1024.0
        cores  = float(max(8, 8 + nid * 2))
        nodes.append(NodeState(
            node_id        = nid,
            capacity_mb    = mem_mb,
            os_tax_mb      = tax_mb,
            cpu_cores      = cores,
            used_mb        = 0.0,
            threshold_frac = 0.10,
        ))
    return nodes


def _make_realtime_jobs(
    interval:   int,
    tenant_ids: list[int],
    n_jobs:     int,
    rng:        np.random.Generator,
) -> list[Job]:
    """Generate real-time jobs for the given tenant group."""
    jobs = []
    for k in range(n_jobs):
        tenant   = tenant_ids[k % len(tenant_ids)]
        req_mem  = float(rng.uniform(256.0, 1024.0))
        req_cpu  = float(rng.uniform(0.5, 4.0))
        pred_mem = req_mem * rng.uniform(0.80, 1.00)
        pred_cpu = req_cpu * rng.uniform(0.80, 0.95)
        jobs.append(Job(
            job_id        = f"h{interval}_g{k}",
            tenant_id     = tenant,
            req_mem_mb    = round(req_mem,  2),
            req_cpu       = round(req_cpu,  3),
            pred_mem_mb   = round(pred_mem, 2),
            pred_cpu_p95  = round(pred_cpu, 3),
            arrival_round = interval,
        ))
    return jobs


def _banner(title: str) -> None:
    print(flush=True)
    print("=" * 70, flush=True)
    print(f"  {title}", flush=True)
    print("=" * 70, flush=True)


# ============================================================================
# § PIPELINE RUNNER
# ============================================================================

def run_pipeline(cfg: PipelineConfig) -> None:
    """Run the full three-layer pipeline for the given configuration."""

    rng = np.random.default_rng(cfg.seed)
    rt_module.SOLVER_ID = cfg.realtime_solver

    # ── Layer 1: Synthesis ─────────────────────────────────────────────────
    _banner(f"LAYER 1  Synthesis  [{cfg.name}]")
    P = build_synthetic_data(
        seed                = cfg.seed,
        n_tenants           = cfg.n_tenants,
        n_nodes             = cfg.n_nodes,
        n_intervals         = cfg.n_intervals,
        node_capacity       = cfg.node_capacity,
        n_always_available  = cfg.n_always_available,
        n_exclusive         = cfg.n_exclusive,
        tenant_usage_min    = cfg.tenant_usage_min,
        tenant_usage_max    = cfg.tenant_usage_max,
    )
    T_e, T_s = P['T_e'], P['T_s']
    M_a, M_b = P['M_a'], P['M_b']

    print(f"  Tenants:           {len(P['T'])}  total")
    print(f"    Exclusive T_e:   {T_e}  (fixed machine assignment, entire horizon)")
    print(f"    Shared    T_s:   {T_s}  (per-interval assignment)")
    print(f"  Machines:          {len(P['M'])}  total")
    print(f"    Always-available M_a: {M_a}")
    print(f"    Additional       M_b: {M_b}  (model decides which to activate)")
    print(f"  Intervals (horizon): {len(P['H'])}")
    print(f"  Node capacity:       {cfg.node_capacity}")
    print(f"  Usage range:         [{cfg.tenant_usage_min}, {cfg.tenant_usage_max}]")
    print(f"  Realtime solver:     {cfg.realtime_solver}  ({cfg.n_jobs_per_slot} jobs/group/interval)")
    print()
    print("  Tenant demand profiles u[i,h]:")
    for i in P['T']:
        tag  = "EXCL" if i in T_e else "SHRD"
        row  = "  ".join(f"h{h}:{P['u'][i,h]:.2f}" for h in P['H'])
        print(f"    [{tag}] tenant {i}: {row}")

    # ── Layer 2: Plan-Ahead MISOCP ─────────────────────────────────────────
    mode = "MISOCP" if cfg.use_socp else "MILP"
    _banner(f"LAYER 2  Plan-Ahead {mode}  (Gurobi)")
    env = make_gurobi_env()
    model, vars_ = build_model(P, env, use_socp=cfg.use_socp)
    model.Params.TimeLimit    = cfg.plan_time_limit
    model.Params.MIPGap       = cfg.plan_mip_gap
    model.Params.LogToConsole = 0

    print(f"  Solving {mode}  (time limit: {cfg.plan_time_limit}s, "
          f"gap target: {cfg.plan_mip_gap*100:.0f}%)  ...", flush=True)
    model.optimize()

    status_str = {2: "OPTIMAL", 9: "TIME_LIMIT", 13: "SUBOPTIMAL"}.get(
        model.Status, f"STATUS_{model.Status}"
    )
    if model.Status not in (GRB.OPTIMAL, GRB.TIME_LIMIT, GRB.SUBOPTIMAL):
        print(f"  Plan-ahead infeasible ({status_str}). Aborting.")
        return

    print(f"  Status:          {status_str}")
    print(f"  Objective:       {model.ObjVal:.4f}")
    print(f"  Fairness sigma:  {vars_['sigma'].X:.4f}")
    print(f"  MIP gap:         {model.MIPGap * 100:.2f}%")

    # ── Layer 3: Extract plan output ───────────────────────────────────────
    _banner("LAYER 3  Plan-Ahead Output  (interval groups)")
    plan_out = extract_plan_output(vars_, P)

    for interval_dict in plan_out["intervals"]:
        h = interval_dict["interval"]
        print(f"  Interval {h}:")
        for g in interval_dict["groups"]:
            tag = "EXCL" if g["exclusive"] else "SHRD"
            print(f"    [{tag}] tenants={str(g['tenant_ids']):<20}  machines={g['machine_ids']}")

    # ── Layer 4+5: Real-Time scheduling per interval ────────────────────────
    for interval_dict in plan_out["intervals"]:
        h      = interval_dict["interval"]
        groups = interval_dict["groups"]

        _banner(f"LAYER 4+5  Real-Time Scheduling  (interval h={h})")

        total_placed   = 0
        total_unplaced = 0

        for g_idx, group in enumerate(groups):
            tenant_ids  = group["tenant_ids"]
            machine_ids = group["machine_ids"]
            exclusive   = group["exclusive"]
            tag         = "EXCL" if exclusive else "SHRD"

            if not machine_ids:
                print(f"  [{tag}] group {g_idx} tenants={tenant_ids}: "
                      f"no machines assigned — skipping.")
                continue

            # Generate jobs for this tenant group
            jobs  = _make_realtime_jobs(h, tenant_ids, cfg.n_jobs_per_slot, rng)
            nodes = _make_realtime_nodes(machine_ids)

            placements = rt_module.solve(
                jobs          = jobs,
                nodes         = nodes,
                W_t           = {},
                time_limit_ms = 10_000,
            )

            placed   = {jid: nid for jid, nid in placements.items() if nid is not None}
            unplaced_count = sum(1 for nid in placements.values() if nid is None)

            print(f"  [{tag}] group {g_idx}  tenants={tenant_ids}  "
                  f"machines={machine_ids}")
            print(f"          jobs={len(jobs)}  placed={len(placed)}  "
                  f"unplaced={unplaced_count}")

            # Per-machine summary
            node_jobs: dict[int, list[str]] = {}
            for jid, nid in placed.items():
                node_jobs.setdefault(nid, []).append(jid)
            for nid in sorted(node_jobs):
                job_objs = [j for j in jobs if j.job_id in node_jobs[nid]]
                total_mem = sum(j.pred_mem_mb for j in job_objs)
                print(f"          machine {nid:>3}: {len(node_jobs[nid]):>3} jobs  "
                      f"total_pred_mem={total_mem:,.0f} MB")

            total_placed   += len(placed)
            total_unplaced += unplaced_count

        print(f"\n  Interval {h} total: placed={total_placed}  unplaced={total_unplaced}")

    _banner("PIPELINE COMPLETE")


# ── Entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    sample_num = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    if sample_num not in SAMPLES:
        print(f"Unknown sample '{sample_num}'. Choose 1, 2, or 3.")
        sys.exit(1)

    cfg = SAMPLES[sample_num]
    print(f"Running pipeline — Sample {sample_num}: {cfg.name}")
    run_pipeline(cfg)
