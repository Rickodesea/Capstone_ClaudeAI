"""
plan_ahead_data.py
──────────────────
Configuration, Gurobi environment initialisation, and synthetic data
generation for the plan-ahead MISOCP.

Mathematical model reference: plan_ahead_source_of_truth.txt
"""

from __future__ import annotations

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


# ── Cantelli quantile ────────────────────────────────────────────────────────

def kappa(eps: float) -> float:
    """One-sided Cantelli quantile factor: sqrt((1 - eps) / eps).

    kappa(0.05) ~= 4.36  (95% confidence)
    kappa(0.10) ~= 3.00  (90% confidence)
    """
    return float(np.sqrt((1.0 - eps) / eps))


# ── Index helper ─────────────────────────────────────────────────────────────

def wl_index(all_wl: list[tuple[int, int]], i: int, j: int) -> int:
    """Row index of workload (i, j) in the flattened covariance matrix."""
    return all_wl.index((i, j))


# ── Synthetic data generation ────────────────────────────────────────────────

def build_synthetic_data(
    seed:                   int = 42,
    n_tenants:              int = 3,
    n_workloads_per_tenant: int = 2,
    n_nodes:                int = 4,
    n_time_slots:           int = 2,
) -> dict:
    """Return a parameter dict for a synthetic instance of given size.

    Parameters
    ----------
    seed                   : random seed for reproducibility
    n_tenants              : number of tenants  (|T|)
    n_workloads_per_tenant : workloads per tenant  (|W_i|)
    n_nodes                : number of cluster nodes  (|N|)
    n_time_slots           : planning horizon length  (|H|)

    All values are synthetic — replace with real inputs from Google
    cluster-usage traces v3 (see plan_ahead_source_of_truth.txt §9).
    """
    rng = np.random.default_rng(seed)

    # --- Sets ----------------------------------------------------------------
    T  = list(range(n_tenants))
    Wi = {i: list(range(n_workloads_per_tenant)) for i in T}
    N  = list(range(n_nodes))
    R  = list(range(2))                          # resources: 0=CPU, 1=MEM
    H  = list(range(n_time_slots))               # time periods
    Q  = list(range(2))                          # QoS: 0=guaranteed, 1=burstable
    K  = list(range(3))                          # primitives: 0=none,1=gVisor,2=Kata

    all_wl = [(i, j) for i in T for j in Wi[i]]
    n_wl   = len(all_wl)

    # --- Resource demand and mean usage --------------------------------------
    d  = {(i, j, r): rng.uniform(0.5, 2.0)
          for i in T for j in Wi[i] for r in R}
    mu = {(i, j, r): d[i, j, r] * rng.uniform(0.9, 1.1)
          for i in T for j in Wi[i] for r in R}

    # Empirical covariance matrix (PSD), one per resource
    Sigma: dict[int, np.ndarray] = {}
    for r in R:
        A = rng.standard_normal((n_wl, n_wl))
        Sigma[r] = (A @ A.T) / n_wl

    # Node capacities and tenant quotas
    C        = {(n, r): 10.0 for n in N for r in R}
    Q_quota  = {(i, r): 5.0  for i in T for r in R}
    alpha_oc = {r: 1.5       for r in R}

    # --- Pricing and risk ----------------------------------------------------
    p      = {(i, j): float(i + j + 1)  for i in T for j in Wi[i]}
    pi_n   = {n: 1.0                    for n in N}
    pi_bar = {i: 3.0                    for i in T}
    v_op   = {i: 0.5                    for i in T}
    eps_i  = {i: 0.05                   for i in T}   # 5% SLA risk tolerance

    # --- Isolation primitive parameters -------------------------------------
    eta = {(0, r): 1.00 for r in R}
    eta.update({(1, r): 1.20 for r in R})   # gVisor +20%
    eta.update({(2, r): 1.05 for r in R})   # Kata   +5%

    c_k = {0: 0.0, 1: 0.3, 2: 0.2}

    rho = {
        (0, 0): 0.80, (0, 1): 0.40, (0, 2): 0.30,
        (1, 0): 0.40, (1, 1): 0.15, (1, 2): 0.10,
        (2, 0): 0.30, (2, 1): 0.10, (2, 2): 0.10,
    }
    tau = {(i, ip): 0.35 for i in T for ip in T}

    k_min = {i: 0 for i in T}
    k_min[T[-1]] = 1   # last tenant requires at least gVisor (compliance example)

    # --- Control-plane budgets -----------------------------------------------
    omega_etcd  = {(i, k): float(10 * (k + 1)) for i in T for k in K}
    omega_qps   = {(i, k): float(5  * (k + 1)) for i in T for k in K}
    omega_svc   = {(i, k): float(2  * (k + 1)) for i in T for k in K}
    B_etcd      = 200.0
    B_qps       = 100.0
    B_svc       = 50.0
    delta_qps   = 2.0
    B_qps_churn = 20.0

    # --- SLA and migration ---------------------------------------------------
    q_assign = {(i, j): 0     for i in T for j in Wi[i]}  # all guaranteed
    L        = {(i, q_): 50.0 for i in T for q_ in Q}     # 50 ms latency target
    gamma_ij = {(i, j): 1.0   for i in T for j in Wi[i]}
    Delta_i  = {i: 2          for i in T}

    # Compliance / data-residency feasible node sets
    # Last tenant's last workload is restricted to the last two nodes (example rule
    # that scales automatically with n_tenants and n_nodes).
    N_allowed = {(i, j): list(N) for i in T for j in Wi[i]}
    if n_nodes >= 2:
        last_t = T[-1]
        last_j = Wi[last_t][-1]
        N_allowed[(last_t, last_j)] = N[-2:]   # restricted to last 2 nodes

    # --- Latency model -------------------------------------------------------
    l0        = {(i, j): 10.0 for i in T for j in Wi[i]}
    alpha_lat = {(i, j, ip, jp): 2.0
                 for i in T for j in Wi[i]
                 for ip in T for jp in Wi[ip]
                 if (i, j) != (ip, jp)}
    beta_lat  = {(i, j, k): [0.0, 5.0, 2.0][k]
                 for i in T for j in Wi[i] for k in K}

    # --- Objective weights ---------------------------------------------------
    # lam[0] = infrastructure cost weight
    # lam[1] = SLA penalty weight
    # lam[2] = admission revenue weight
    # lam[3] = fairness (sigma) weight
    # lam[4] = isolation cost weight
    # lam[5] = migration cost weight
    lam = {0: 1.0, 1: 10.0, 2: 1.0, 3: 5.0, 4: 0.5, 5: 2.0}

    return dict(
        T=T, Wi=Wi, N=N, R=R, H=H, Q=Q, K=K,
        all_wl=all_wl, n_wl=n_wl,
        d=d, mu=mu, Sigma=Sigma,
        C=C, Q_quota=Q_quota, alpha_oc=alpha_oc,
        p=p, pi_n=pi_n, pi_bar=pi_bar, v_op=v_op, eps_i=eps_i,
        eta=eta, c_k=c_k, rho=rho, tau=tau, k_min=k_min,
        omega_etcd=omega_etcd, omega_qps=omega_qps, omega_svc=omega_svc,
        B_etcd=B_etcd, B_qps=B_qps, B_svc=B_svc,
        delta_qps=delta_qps, B_qps_churn=B_qps_churn,
        q_assign=q_assign, L=L, gamma_ij=gamma_ij, Delta_i=Delta_i,
        N_allowed=N_allowed,
        l0=l0, alpha_lat=alpha_lat, beta_lat=beta_lat,
        lam=lam,
    )
