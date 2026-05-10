"""
pipeline/interface.py
──────────────────────
End-to-end pipeline connecting the three model layers:

  1. Synthesis / prediction  — build_synthetic_data() generates resource
     demand, mean usage, and covariance matching Google cluster-usage traces v3.

  2. Plan-ahead MISOCP (Gurobi) — solves the periodic scheduling problem over
     the planning horizon H.  Decides which tenants are admitted and which nodes
     each tenant is authorised to use per time slot.
     Output: TenantAccessSchedule = dict[(tenant_id, t) -> list[node_id]]

  3. Real-time MILP / heuristic (OR-Tools) — one call per scheduling round.
     Receives tenant_node_access from the plan-ahead and enforces constraint C5.

Usage
─────
    python interface.py          # Sample 1 — Simple  (default)
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
sys.path.insert(0, str(_ROOT / "optimization"))
sys.path.insert(0, str(_ROOT / "PlanAheadModel"))

from plan_ahead_data      import build_synthetic_data, make_gurobi_env
from plan_ahead_optimizer import build_model, extract_tenant_access_schedule
from gurobipy import GRB

import optimizer_google_or as rt_module
from simulation_data import Job, NodeState

from pipeline_configs import SAMPLES, PipelineConfig


# ============================================================================
# § DATA STRUCTURES
# ============================================================================

@dataclass
class TenantLease:
    """A single reservation grant from the plan-ahead model.

    Represents: "tenant_id may use authorised_nodes during [start_slot, end_slot)."
    Contiguous slots with the same node set are merged into one lease.
    """
    tenant_id:        int
    authorised_nodes: list[int]
    start_slot:       int
    end_slot:         int    # exclusive — [start_slot, end_slot)

    def is_active(self, slot: int) -> bool:
        return self.start_slot <= slot < self.end_slot


@dataclass
class TenantSchedule:
    """Full plan-ahead output: raw schedule + derived lease list."""
    schedule:   dict[tuple[int, int], list[int]]   # (tenant_id, slot) -> [node_id]
    leases:     list[TenantLease]
    tenant_ids: list[int]
    time_slots: list[int]
    node_ids:   list[int]


# ============================================================================
# § PIPELINE UTILITIES
# ============================================================================

def schedule_to_leases(
    schedule:   dict[tuple[int, int], list[int]],
    tenant_ids: list[int],
    time_slots: list[int],
) -> list[TenantLease]:
    """Convert TenantAccessSchedule to a compact list of TenantLease tuples.

    Contiguous slots with the same node set are merged into one lease.
    """
    leases: list[TenantLease] = []
    for i in tenant_ids:
        current_nodes: list[int] | None = None
        start: int | None = None
        for t in time_slots:
            nodes = sorted(schedule.get((i, t), []))
            if nodes != current_nodes:
                if current_nodes is not None and start is not None:
                    leases.append(TenantLease(i, current_nodes, start, t))
                current_nodes = nodes
                start = t
        if current_nodes is not None and start is not None:
            leases.append(TenantLease(i, current_nodes, start, time_slots[-1] + 1))
    return sorted(leases, key=lambda l: (l.tenant_id, l.start_slot))


def filter_active_access(
    schedule: dict[tuple[int, int], list[int]],
    time_t:   int,
) -> dict[int, list[int]]:
    """Slice TenantAccessSchedule to a single time slot.

    Returns dict[tenant_id -> list[node_id]] for tenants with at least
    one authorised node at time_t.  Passed directly as tenant_node_access
    to the real-time solve() call.
    """
    return {tenant: nodes
            for (tenant, t), nodes in schedule.items()
            if t == time_t and nodes}


def _make_realtime_nodes(node_ids: list[int]) -> list[NodeState]:
    """Create NodeState objects whose IDs match the plan-ahead node set."""
    nodes = []
    for nid in node_ids:
        mem_mb = float(16_384 + nid * 2_048)             # 16, 18, 20 … GB
        tax_mb = round(mem_mb * 0.05 / 1024.0) * 1024.0
        cores  = float(max(8, 8 + nid * 2))              # 8, 10, 12 … cores
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
    slot:       int,
    admitted:   list[int],
    n_jobs:     int,
    rng:        np.random.Generator,
) -> list[Job]:
    """Generate real-time jobs for admitted tenants in the given slot."""
    jobs = []
    for k in range(n_jobs):
        tenant   = admitted[k % len(admitted)]
        req_mem  = float(rng.uniform(256.0, 1024.0))
        req_cpu  = float(rng.uniform(0.5, 4.0))
        pred_mem = req_mem * rng.uniform(0.80, 1.00)
        pred_cpu = req_cpu * rng.uniform(0.80, 0.95)
        jobs.append(Job(
            job_id        = f"s{slot}_j{k}",
            tenant_id     = tenant,
            req_mem_mb    = round(req_mem,  2),
            req_cpu       = round(req_cpu,  3),
            pred_mem_mb   = round(pred_mem, 2),
            pred_cpu_p95  = round(pred_cpu, 3),
            arrival_round = slot,
        ))
    return jobs


# ============================================================================
# § DEMO RUNNER
# ============================================================================

def run_pipeline(cfg: PipelineConfig) -> None:
    """Run the full three-layer pipeline for the given configuration."""

    rng = np.random.default_rng(cfg.seed)

    # ── Layer 1: Synthesis / prediction ───────────────────────────────────
    _banner(f"LAYER 1  Synthesis / prediction  [{cfg.name}]")
    P = build_synthetic_data(
        seed                   = cfg.seed,
        n_tenants              = cfg.n_tenants,
        n_workloads_per_tenant = cfg.n_workloads_per_tenant,
        n_nodes                = cfg.n_nodes,
        n_time_slots           = cfg.n_time_slots,
        node_capacity          = cfg.node_capacity,
        sla_eps                = cfg.sla_eps,
    )
    last_t = P['T'][-1]
    last_j = P['Wi'][last_t][-1]
    print(f"  Tenants:    {len(P['T'])}  ({P['T'][0]}..{P['T'][-1]})")
    print(f"  Nodes:      {len(P['N'])}  ({P['N'][0]}..{P['N'][-1]})")
    print(f"  Time slots: {len(P['H'])}")
    print(f"  Workloads:  {len(P['all_wl'])} total  "
          f"({cfg.n_workloads_per_tenant} per tenant)")
    print(f"  Compliance: wl({last_t},{last_j}) restricted to "
          f"nodes {P['N_allowed'][(last_t, last_j)]}")
    print(f"  Node capacity:    {cfg.node_capacity}  |  "
          f"SLA eps: {cfg.sla_eps}  (kappa ~ "
          f"{(((1-cfg.sla_eps)/cfg.sla_eps)**0.5):.2f})")
    print(f"  Real-time solver: {cfg.realtime_solver}  "
          f"({cfg.n_jobs_per_slot} jobs/slot)")

    # ── Layer 2: Plan-ahead MISOCP ────────────────────────────────────────
    _banner("LAYER 2  Plan-ahead MISOCP  (Gurobi)")
    env   = make_gurobi_env()
    model, vars_ = build_model(P, env)
    model.Params.TimeLimit    = cfg.plan_time_limit
    model.Params.MIPGap       = cfg.plan_mip_gap
    # Show Gurobi log for medium/high so progress is visible during long solves.
    # Sample 1 is fast enough that the log is noise; suppress it there.
    model.Params.LogToConsole = 0 if cfg.plan_time_limit <= 60 else 1

    print(f"  Solving MISOCP  (time limit: {cfg.plan_time_limit}s, "
          f"gap target: {cfg.plan_mip_gap*100:.0f}%)  ...")
    print(f"  Workloads: {len(P['all_wl'])}  |  "
          f"Nodes: {len(P['N'])}  |  Slots: {len(P['H'])}", flush=True)
    model.optimize()

    status_str = {2: "OPTIMAL", 9: "TIME_LIMIT", 13: "SUBOPTIMAL"}.get(
        model.Status, f"STATUS_{model.Status}"
    )
    if model.Status not in (GRB.OPTIMAL, GRB.TIME_LIMIT, GRB.SUBOPTIMAL):
        print(f"  Plan-ahead infeasible ({status_str}). Aborting.")
        return

    print(f"  Status:       {status_str}")
    print(f"  Objective:    {model.ObjVal:.4f}")
    print(f"  Fairness sigma: {vars_['sigma'].X:.4f}")
    print(f"  MIP gap:      {model.MIPGap * 100:.2f}%")
    print()

    a = vars_['a']
    admitted = [i for i in P['T'] if a[i].X > 0.5]
    rejected = [i for i in P['T'] if a[i].X <= 0.5]
    print(f"  Admitted ({len(admitted)}): {admitted}")
    print(f"  Rejected ({len(rejected)}): {rejected}")

    # ── Layer 3: TenantAccessSchedule ─────────────────────────────────────
    _banner("LAYER 3  TenantAccessSchedule  (plan-ahead output)")
    raw_schedule = extract_tenant_access_schedule(vars_, P)
    leases       = schedule_to_leases(raw_schedule, P['T'], P['H'])
    ts = TenantSchedule(
        schedule=raw_schedule, leases=leases,
        tenant_ids=P['T'], time_slots=P['H'], node_ids=P['N'],
    )

    print("  Raw schedule  (tenant, slot) -> authorised nodes:")
    for (tenant, slot), nodes in sorted(ts.schedule.items()):
        if nodes:
            print(f"    tenant={tenant:>3}  t={slot}  nodes={nodes}")

    print()
    print("  Lease view  (tenant, node_set, start_slot, end_slot):")
    for lease in ts.leases:
        if lease.authorised_nodes:
            print(f"    tenant={lease.tenant_id:>3}  "
                  f"nodes={lease.authorised_nodes}  "
                  f"slots=[{lease.start_slot}, {lease.end_slot})")

    # ── Layer 4+5: Real-time scheduling per slot ──────────────────────────
    rt_module.SOLVER_ID = cfg.realtime_solver
    realtime_nodes = _make_realtime_nodes(P['N'])

    for t in P['H']:
        _banner(f"LAYER 4+5  Real-time scheduling  (slot t={t}  "
                f"solver={cfg.realtime_solver})")

        tenant_node_access = filter_active_access(ts.schedule, t)
        print(f"  Access constraints (t={t}):")
        for tenant, nodes in sorted(tenant_node_access.items()):
            print(f"    tenant {tenant:>3} -> nodes {nodes}")
        print()

        if not admitted:
            print("  No admitted tenants — skipping.")
            continue

        jobs = _make_realtime_jobs(t, admitted, cfg.n_jobs_per_slot, rng)
        placements = rt_module.solve(
            jobs               = jobs,
            nodes              = realtime_nodes,
            W_t                = {},
            tenant_node_access = tenant_node_access,
        )

        placed   = {jid: nid for jid, nid in placements.items() if nid is not None}
        unplaced = [jid for jid, nid in placements.items() if nid is None]
        print(f"  Placed: {len(placed)}/{len(jobs)}  |  "
              f"Unplaced: {len(unplaced)}")

        # Group by node for a compact summary
        node_jobs: dict[int, list[str]] = {}
        for jid, nid in sorted(placed.items()):
            node_jobs.setdefault(nid, []).append(jid)
        for nid in sorted(node_jobs):
            jids = node_jobs[nid]
            tenants_here = sorted({next(j for j in jobs if j.job_id == jid).tenant_id
                                   for jid in jids})
            total_mem = sum(
                next(j for j in jobs if j.job_id == jid).pred_mem_mb
                for jid in jids
            )
            print(f"    node {nid:>3}: {len(jids):>3} jobs  "
                  f"tenants={tenants_here}  "
                  f"total_pred_mem={total_mem:,.0f} MB")

        if unplaced:
            print(f"  Unplaced job IDs: {unplaced}")

    _banner("PIPELINE COMPLETE")


# ── Helpers ───────────────────────────────────────────────────────────────

def _banner(title: str) -> None:
    print(flush=True)
    print("=" * 64, flush=True)
    print(f"  {title}", flush=True)
    print("=" * 64, flush=True)


# ── Entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    sample_num = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    if sample_num not in SAMPLES:
        print(f"Unknown sample '{sample_num}'. Choose 1, 2, or 3.")
        sys.exit(1)

    cfg = SAMPLES[sample_num]
    print(f"Running pipeline — Sample {sample_num}: {cfg.name}")
    run_pipeline(cfg)
