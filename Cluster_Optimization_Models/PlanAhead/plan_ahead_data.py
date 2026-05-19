"""
plan_ahead_data.py
──────────────────
Configuration, Gurobi environment initialisation, and synthetic data
generation for the plan-ahead MILP.

The model forecasts which nodes each tenant should be prioritised on for
each planning period h ∈ H.  Individual workloads are not modelled: each
tenant i has a usage profile u[i,h] that estimates total resource consumption
in period h.  This is a placeholder for a prediction layer; in production
u[i,h] comes from historical cluster-usage traces.

Assignment output is a priority hint (not a hard constraint).  The real-time
model receives y[i,n,h] and uses it to boost objective coefficients — not to
block node access.
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
#
# Never commit .env to source control.

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


# ── Synthetic data generation ────────────────────────────────────────────────

def build_synthetic_data(
    seed:             int   = 42,
    n_tenants:        int   = 3,
    n_nodes:          int   = 4,
    n_time_slots:     int   = 2,
    node_capacity:    float = 10.0,
    tenant_usage_min: float = 0.8,
    tenant_usage_max: float = 6.0,
    sigma_frac:       float = 0.20,
    epsilon:          float = 0.10,
    **_ignored,          # absorb deprecated kwargs (e.g. n_workloads_per_tenant)
) -> dict:
    """Return a parameter dict for a synthetic instance of the plan-ahead MISOCP.

    Parameters
    ----------
    seed             : random seed for reproducibility
    n_tenants        : number of tenants  (|T|)
    n_nodes          : number of cluster nodes  (|N|)
    n_time_slots     : planning horizon in periods  (|H|)
    node_capacity    : C[n] — resource capacity per node (uniform)
    tenant_usage_min : lower bound for u[i,h] (capacity units)
    tenant_usage_max : upper bound for u[i,h] (capacity units)
    sigma_frac       : demand uncertainty fraction — std dev = sigma_frac * u[i,h]
    epsilon          : Cantelli tail probability — κ = sqrt((1-ε)/ε)

    u[i,h] is a placeholder for the prediction team's output.  In production,
    derive from historical CollectionEvents SUBMIT counts per tenant per period.

    Uncertainty model
    -----------------
    σ²[i,h] = (sigma_frac × u[i,h])²  — per-tenant, per-period demand variance.
    κ = sqrt((1-ε)/ε)                  — Cantelli safety factor for tail prob ε.
    With ε=0.10 → κ=3.0 (capacity holds with at least 90% probability).
    """
    rng = np.random.default_rng(seed)

    # --- Sets ----------------------------------------------------------------
    T = list(range(n_tenants))
    N = list(range(n_nodes))
    H = list(range(n_time_slots))

    # --- Node parameters -----------------------------------------------------
    C    = {n: node_capacity for n in N}          # resource capacity per node
    pi_n = {n: 1.0           for n in N}          # infrastructure cost per node-period

    # --- Tenant contract parameters ------------------------------------------
    # pi_bar and v_op scale with n_time_slots so contract value grows with horizon.
    pi_bar = {i: 3.0 * n_time_slots for i in T}   # contract revenue
    v_op   = {i: 0.5 * n_time_slots for i in T}   # operational cost

    # --- Tenant usage profiles u[i,h] ----------------------------------------
    # Placeholder — replace with prediction layer output in production.
    u = {
        (i, h): float(rng.uniform(tenant_usage_min, tenant_usage_max))
        for i in T for h in H
    }

    # --- Cantelli uncertainty model ------------------------------------------
    # σ²[i,h] = (sigma_frac × u[i,h])²  — proportional-to-demand variance.
    # κ = sqrt((1-ε)/ε)                  — one-sided Cantelli safety factor.
    kappa  = math.sqrt((1.0 - epsilon) / epsilon)
    sigma2 = {
        (i, h): (sigma_frac * u[i, h]) ** 2
        for i in T for h in H
    }

    # --- Objective weights ---------------------------------------------------
    # lam[0] = infrastructure cost weight  (minimize active nodes)
    # lam[1] = admission revenue weight    (maximize admitted tenants)
    # lam[2] = fairness weight             (maximize min demand satisfaction)
    lam = {0: 1.0, 1: 1.0, 2: 5.0}

    return dict(
        T=T, N=N, H=H, C=C, pi_n=pi_n, pi_bar=pi_bar, v_op=v_op, u=u, lam=lam,
        sigma2=sigma2, kappa=kappa,
    )
