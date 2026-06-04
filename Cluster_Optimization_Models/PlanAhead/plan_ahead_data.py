"""
plan_ahead_data.py
──────────────────
Configuration, Gurobi environment initialisation, and synthetic data
generation for the plan-ahead MISOCP.

Key design properties:
  • Pool of M machines: M_a always available, M_b additional (model decides activation).
  • Exclusive tenants T_e (X% of all tenants): assigned machines for entire horizon.
  • Shared tenants T_s: assigned machines per interval; can change between intervals.
  • Demand variance σ²[i,h] enables Cantelli probabilistic capacity constraint.
  • Feedback: SLA violation rates and wait times from realtime adjust capacity and demand.
  • Output: ordered list of intervals, each with groups (tenant_ids, machine_ids, exclusive).

All tenants are assigned machines every interval — no admission/rejection.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np
import gurobipy as gp


# ── Load Gurobi credentials from .env ──────────────────────────────────────
#
# Expected .env keys (same directory as this file):
#   WLSACCESSID  — Gurobi WLS access ID (UUID string)
#   WLSSECRET    — Gurobi WLS secret    (UUID string)
#   LICENSEID    — Gurobi license ID    (integer)

def _load_env_file(path: Path) -> None:
    """Parse a simple KEY=VALUE .env file and write into os.environ."""
    if not path.exists():
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())


_load_env_file(Path(__file__).parent / ".env")


def make_gurobi_env() -> gp.Env:
    """Create and return a Gurobi WLS environment from .env credentials."""
    params = {
        "WLSACCESSID": os.environ.get("WLSACCESSID", ""),
        "WLSSECRET":   os.environ.get("WLSSECRET", ""),
        "LICENSEID":   int(os.environ.get("LICENSEID", "0")),
    }
    return gp.Env(params=params)


# ── Prediction API integration ────────────────────────────────────────────────
# When True, build_synthetic_data() calls Prediction/prediction_api.predict_workload()
# to obtain u[i,h] and sigma2[i,h] instead of synthesising them from uniform random.
# Kept False for all analysis and computational benchmarks — only the Borg
# simulation dashboard enables this flag via borg_configuration.BORG_CONFIG.
USE_PREDICTION_API: bool = False

# ── Synthetic data generation ────────────────────────────────────────────────

def build_synthetic_data(
    seed:                 int   = 42,
    n_tenants:            int   = 4,
    n_nodes:              int   = 6,
    n_intervals:          int   = 3,
    node_capacity:        float = 10.0,
    n_always_available:   int   = 3,       # |M_a| — always-on machines
    n_exclusive:          int   = 1,       # number of exclusive tenants (clamped to [0, n_tenants])
    tenant_usage_min:     float = 0.8,
    tenant_usage_max:     float = 6.0,
    sigma_frac:           float = 0.20,
    epsilon:              float = 0.10,
    feedback_vbar:        dict  | None = None,  # {node_id: v̄_n} from realtime
    feedback_wait:        dict  | None = None,  # {tenant_id: W̄_i} from realtime
    feedback_queue:       dict  | None = None,  # {tenant_id: queued_job_count}
    feedback_alpha:       float = 0.5,     # SLA feedback capacity scaling
    feedback_beta:        float = 0.3,     # wait-time demand scaling
    feedback_gamma:       float = 0.3,     # queue-size demand scaling
    feedback_wait_ref:    float = 10.0,    # W̄_ref (seconds) for normalisation
    queue_ref:            int   = 10,      # reference queue size for normalisation
    capacity_buffer_frac:    float = 0.0,  # fraction of C_eff withheld for realtime (MILP only)
    min_machines_per_tenant: int   = 1,   # minimum machines each shared tenant must receive per period
    **_ignored,
) -> dict:
    """
    Return a parameter dict for a synthetic instance of the plan-ahead MISOCP.

    Parameters
    ----------
    seed                : random seed for reproducibility
    n_tenants           : total number of tenants (T = T_e ∪ T_s)
    n_nodes             : total machines in pool (M = M_a ∪ M_b)
    n_intervals         : planning horizon length |H|
    node_capacity       : C[n] — resource capacity per machine (uniform)
    n_always_available  : |M_a| — machines always on; rest are additional (M_b)
    n_exclusive         : exact number of tenants tagged as exclusive; clamped to [0, n_tenants]
    tenant_usage_min    : lower bound for u[i,h]
    tenant_usage_max    : upper bound for u[i,h]
    sigma_frac          : demand uncertainty fraction — std dev = sigma_frac × u[i,h]
    epsilon             : Cantelli tail probability — κ = sqrt((1-ε)/ε)
    feedback_vbar       : SLA violation rates per node {node_id: float} (0..1)
    feedback_wait       : average wait times per tenant {tenant_id: float} (seconds)
    feedback_queue      : queued job count per tenant {tenant_id: int} (0 on first run)
    feedback_alpha      : capacity reduction factor per unit violation rate
    feedback_beta       : demand inflation factor per unit normalised wait
    feedback_gamma      : demand inflation factor per unit normalised queue size
    feedback_wait_ref   : reference wait time for normalising W̄_i
    queue_ref           : reference queue size for normalising tenant queue counts
    """
    rng = np.random.default_rng(seed)

    # --- Sets ----------------------------------------------------------------
    T = list(range(n_tenants))

    # Validate and tag exclusive tenants
    n_exclusive = int(min(max(0, n_exclusive), n_tenants))
    exclusive_ids = sorted(rng.choice(T, size=n_exclusive, replace=False).tolist()) if n_exclusive > 0 else []
    T_e = exclusive_ids
    T_s = [i for i in T if i not in exclusive_ids]

    # Machines: M_a always available, M_b additional
    n_always = min(n_always_available, n_nodes)
    M = list(range(n_nodes))
    M_a = list(range(n_always))              # always-available: indices 0..A-1
    M_b = list(range(n_always, n_nodes))     # additional: indices A..M-1

    H = list(range(n_intervals))

    # --- Machine parameters --------------------------------------------------
    C = {n: node_capacity for n in M}        # base capacity per machine (uniform)
    pi_n = {n: 1.0 for n in M_b}            # activation cost (only for additional machines)

    # Effective capacity after SLA feedback: C_eff[n] = C[n] × (1 - α·v̄_n)
    fb_vbar = feedback_vbar or {}
    C_eff = {
        n: C[n] * (1.0 - feedback_alpha * min(1.0, fb_vbar.get(n, 0.0)))
        for n in M
    }

    # --- Tenant usage profiles u[i,h] ----------------------------------------
    u_raw = {
        (i, h): float(rng.uniform(tenant_usage_min, tenant_usage_max))
        for i in T for h in H
    }

    # Feedback-adjusted demand for ALL tenants (exclusive and shared)
    fb_wait  = feedback_wait  or {}
    fb_queue = feedback_queue or {}
    W_bar_ref = feedback_wait_ref
    u_fb = {}
    for i in T:
        w_i = fb_wait.get(i, 0.0)
        q_i = fb_queue.get(i, 0)
        wait_scale  = 1.0 + feedback_beta  * min(2.0, w_i / max(1.0, W_bar_ref))
        queue_scale = 1.0 + feedback_gamma * min(2.0, q_i / max(1, queue_ref))
        scale = min(3.0, wait_scale * queue_scale)
        for h in H:
            u_fb[(i, h)] = u_raw[(i, h)] * scale

    # u: feedback-adjusted demand for all tenants
    u = {(i, h): u_fb[(i, h)] for i in T for h in H}

    # Peak demand for exclusive tenants (max over intervals, feedback-adjusted)
    u_max = {i: max(u_fb[i, h] for h in H) for i in T_e}

    # --- Cantelli uncertainty model ------------------------------------------
    kappa  = math.sqrt((1.0 - epsilon) / epsilon)
    sigma2 = {
        (i, h): (sigma_frac * u[i, h]) ** 2
        for i in T_s for h in H    # variance only for shared tenants
    }

    # --- Identify heavy / light shared tenants for mix objective -----------
    if T_s:
        avg_usage = {i: sum(u[i, h] for h in H) / len(H) for i in T_s}
        median_usage = float(np.median(list(avg_usage.values())))
        T_s_heavy = [i for i in T_s if avg_usage[i] >= median_usage]
        T_s_light = [i for i in T_s if avg_usage[i] < median_usage]
    else:
        T_s_heavy, T_s_light = [], []

    # --- Objective weights ---------------------------------------------------
    lam = {0: 1.0, 1: 5.0, 2: 2.0}   # [infra_cost, fairness σ, mix bonus]

    # cap_frac: fraction of C_eff the plan-ahead model may allocate (MILP only).
    # Remaining fraction (capacity_buffer_frac) is reserved as headroom for realtime.
    cap_frac = max(0.0, min(1.0, 1.0 - capacity_buffer_frac))

    # min_machines: clamp to [1, len(M)] to stay feasible
    min_machines_per_tenant = int(min(max(1, min_machines_per_tenant), max(1, len(M) // max(1, len(T_s)))))

    return dict(
        # Sets
        T=T, T_e=T_e, T_s=T_s, T_s_heavy=T_s_heavy, T_s_light=T_s_light,
        M=M, M_a=M_a, M_b=M_b,
        H=H,
        # Machine parameters
        C=C, C_eff=C_eff, pi_n=pi_n,
        # Demand
        u=u, u_max=u_max,
        # Uncertainty
        sigma2=sigma2, kappa=kappa,
        # Capacity headroom (MILP only)
        cap_frac=cap_frac,
        # Minimum machines per shared tenant per period
        min_machines_per_tenant=min_machines_per_tenant,
        # Objective
        lam=lam,
        # Metadata
        n_tenants=n_tenants, n_nodes=n_nodes, n_intervals=n_intervals,
        n_exclusive=n_exclusive,
    )
