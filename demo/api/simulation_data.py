"""
simulation_data.py
──────────────────
Configuration constants, dataclasses, and data-generation helpers for the
multi-tenant cluster scheduling simulation.

This module is the single source of truth for:
  • Global tunable parameters (memory ranges, lifetime, spike probability, etc.)
  • The Job and NodeState dataclasses used everywhere else
  • Helper functions that generate synthetic input data and compute the
    derived quantities defined in the math model (§3, §4)

Notation used in comments matches the math model source of truth:
  M_n           physical RAM on node n
  M_n^tax       OS/kubelet memory tax (reserved; not available to tenant jobs)
  M_n^theta     memory safety threshold buffer (= threshold_frac * M_n)
  M_n^cap       node capacity for tenant jobs = M_n - M_n^tax - M_n^theta    (§3)
  M_n^avail     remaining available = M_n^cap - U_n^mem                       (§3)
  M_n^eff       effective remaining = max(0, M_n^avail * (1 - v̄_n^SLA))      (§3)
  v̄_n^SLA      rolling SLA violation rate on node n (last K rounds)           (§3)
  U_n^mem       currently used memory on node n
  u_n^mem       memory utilization weight = 1 + U_n^mem / M_n ∈ [1, 2]       (§3)
  ω_delay,t     per-tenant delay weight (K-round rolling window)               (§3)
  W̄_t          per-tenant avg scheduling delay over last K rounds             (§3)
  P̂_j^mem      maximum predicted memory for job j (from training maximum_usage)(§3)
  P̂_j^CPU      P95 predicted CPU peak for job j (tail_cpu_usage_distribution) (§3)
  x_{jn}        decision variable — 1 if job j placed on node n               (§1)
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import numpy as np
from dataclasses import dataclass, field


# ═══════════════════════════════════════════════════════════════════════════════
# § CONFIG — Simulation-wide tunable parameters
# ═══════════════════════════════════════════════════════════════════════════════

REQUEST_MEM_MIN_MB: float = 512.0
REQUEST_MEM_MAX_MB: float = 1024.0

REQUEST_PER: float = 0.6

REQ_CPU_MIN: float = 0.25
REQ_CPU_MAX: float = 4.0

NODE_CPU_MIN: float = 8.0
NODE_CPU_MAX: float = 64.0

NUM_NODES:      int = 5
NUM_TENANTS:    int = 3
JOBS_PER_ROUND: int = 20
NUM_BATCHES:    int = 10

NODE_MEM_MIN_MB: float = 16_384.0
NODE_MEM_MAX_MB: float = 65_536.0

OS_TAX_FRAC: float = 0.05


def _make_node_mems(n: int, lo: float, hi: float) -> list[float]:
    if n == 1:
        return [lo]
    ratio = (hi / lo) ** (1.0 / (n - 1))
    return [round(lo * ratio ** i / 1024.0) * 1024.0 for i in range(n)]


NODE_MEM_MB:   list[float] = _make_node_mems(NUM_NODES, NODE_MEM_MIN_MB, NODE_MEM_MAX_MB)
OS_TAX_MB:     list[float] = [round(m * OS_TAX_FRAC / 1024.0) * 1024.0 for m in NODE_MEM_MB]


def _make_node_cpu(n: int, lo: float, hi: float) -> list[float]:
    if n == 1:
        return [float(round(lo))]
    ratio = (hi / lo) ** (1.0 / (n - 1))
    return [float(round(lo * ratio ** i)) for i in range(n)]


NODE_CPU_CORES: list[float] = _make_node_cpu(NUM_NODES, NODE_CPU_MIN, NODE_CPU_MAX)

K_WINDOW: int = 10

MEM_THRESHOLD_FRAC: float = 0.10

DIST_FLAG: str = "normal"

MIN_LIFETIME_SEC: float = 60.0
MAX_LIFETIME_SEC: float = 600.0

SPIKE_PROB:     float = 0.10
SPIKE_MAX_FRAC: float = 0.20

BATCH_DURATION_SEC: int = 60

MAX_PLACEMENT_RETRIES: int = 5

MAX_JOBS_PER_SOLVE: int = 200


# ═══════════════════════════════════════════════════════════════════════════════
# § DATACLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Job:
    job_id:        str
    tenant_id:     int
    req_mem_mb:    float
    req_cpu:       float
    pred_mem_mb:   float
    pred_cpu_p95:  float
    arrival_round: int

    arrival_timestamp:    Optional[datetime] = None
    scheduling_timestamp: Optional[datetime] = None


@dataclass
class NodeState:
    node_id:           int
    capacity_mb:       float
    os_tax_mb:         float
    cpu_cores:         float = 0.0
    used_mb:           float = 0.0
    threshold_frac:    float = 0.10
    overflow_history:  list  = field(default_factory=list)
    violation_history: list  = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# § SAMPLING HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _sample_requested_mem(rng: np.random.Generator) -> float:
    lo, hi = REQUEST_MEM_MIN_MB, REQUEST_MEM_MAX_MB
    mean   = (lo + hi) / 2.0
    std    = (hi - lo) / 6.0
    return float(np.clip(rng.normal(mean, std), lo, hi))


def _sample_cpu_request(rng: np.random.Generator) -> float:
    lo, hi = REQ_CPU_MIN, REQ_CPU_MAX
    mean   = (lo + hi) / 2.0
    std    = (hi - lo) / 6.0
    return float(np.clip(rng.normal(mean, std), lo, hi))


def _sample_actual_usage(
    requested_mb: float,
    lower_frac:   float,
    dist:         str,
    rng:          np.random.Generator,
) -> float:
    lo, hi = lower_frac * requested_mb, requested_mb
    if dist == "normal":
        mean = (lo + hi) / 2.0
        std  = (hi - lo) / 6.0
        return float(np.clip(rng.normal(mean, std), lo, hi))
    return float(rng.uniform(lo, hi))


def simulate_max_mem(
    requested_mb: float,
    lower_frac:   float = REQUEST_PER,
    dist:         str   = DIST_FLAG,
    n_samples:    int   = 200,
    rng:          Optional[np.random.Generator] = None,
) -> float:
    rng = rng or np.random.default_rng()
    samples = [
        _sample_actual_usage(requested_mb, lower_frac, dist, rng)
        for _ in range(n_samples)
    ]
    return float(np.max(samples))


def simulate_p95(
    requested_mb: float,
    lower_frac:   float = REQUEST_PER,
    dist:         str   = DIST_FLAG,
    n_samples:    int   = 200,
    rng:          Optional[np.random.Generator] = None,
) -> float:
    return simulate_max_mem(requested_mb, lower_frac, dist, n_samples, rng)


def simulate_p95_cpu(
    requested_cpu: float,
    lower_frac:    float = REQUEST_PER,
    dist:          str   = DIST_FLAG,
    n_samples:     int   = 200,
    rng:           Optional[np.random.Generator] = None,
) -> float:
    rng = rng or np.random.default_rng()
    samples = [
        _sample_actual_usage(requested_cpu, lower_frac, dist, rng)
        for _ in range(n_samples)
    ]
    return float(np.percentile(samples, 95))


def sample_spike_fraction(rng: np.random.Generator) -> float:
    if rng.random() < SPIKE_PROB:
        return float(rng.uniform(0.0, SPIKE_MAX_FRAC))
    return 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# § GENERATORS
# ═══════════════════════════════════════════════════════════════════════════════

def generate_jobs(
    round_num:   int,
    num_jobs:    int = JOBS_PER_ROUND,
    num_tenants: int = NUM_TENANTS,
    rng:         Optional[np.random.Generator] = None,
) -> list[Job]:
    rng  = rng or np.random.default_rng()
    jobs = []
    for i in range(num_jobs):
        tenant      = int(rng.integers(0, num_tenants))
        req         = _sample_requested_mem(rng)
        cpu         = _sample_cpu_request(rng)
        pred_mem    = simulate_max_mem(req, rng=rng)
        pred_cpu    = simulate_p95_cpu(cpu, rng=rng)
        jobs.append(Job(
            job_id        = f"r{round_num}_j{i}",
            tenant_id     = tenant,
            req_mem_mb    = round(req, 2),
            req_cpu       = round(cpu, 3),
            pred_mem_mb   = round(pred_mem, 2),
            pred_cpu_p95  = round(pred_cpu, 3),
            arrival_round = round_num,
        ))
    return jobs


def generate_nodes(rng: Optional[np.random.Generator] = None) -> list[NodeState]:
    rng   = rng or np.random.default_rng()
    nodes = []
    for i in range(NUM_NODES):
        cap   = NODE_MEM_MB[i]
        tax   = OS_TAX_MB[i]
        cores = NODE_CPU_CORES[i]
        nodes.append(NodeState(
            node_id        = i,
            capacity_mb    = cap,
            os_tax_mb      = tax,
            cpu_cores      = cores,
            used_mb        = 0.0,
            threshold_frac = MEM_THRESHOLD_FRAC,
        ))
    return nodes


# ═══════════════════════════════════════════════════════════════════════════════
# § MATH-MODEL DERIVED QUANTITIES  (§3)
# ═══════════════════════════════════════════════════════════════════════════════

def compute_available_capacity(node: NodeState) -> float:
    m_theta = node.threshold_frac * node.capacity_mb
    return node.capacity_mb - node.os_tax_mb - m_theta


def compute_remaining_avail(node: NodeState, m_cap: float) -> float:
    return m_cap - node.used_mb


def compute_remaining_eff(r_avail: float, v_bar: float) -> float:
    return max(0.0, r_avail * (1.0 - v_bar))


def compute_utilization_weight(node: NodeState) -> float:
    m_cap = compute_available_capacity(node)
    frac  = min(1.0, max(0.0, node.used_mb / max(1.0, m_cap)))
    return 1.0 + frac


def compute_violation_rate(history: list[bool], K: int = K_WINDOW) -> float:
    recent = history[-K:] if len(history) >= K else history
    return sum(recent) / len(recent) if recent else 0.0


def compute_node_weight(node_id: int, num_nodes: int = NUM_NODES) -> float:
    return float(num_nodes - node_id)


def compute_omega(W_t: dict[int, float]) -> dict[int, float]:
    if not W_t:
        return {}
    W_bar   = sum(W_t.values()) / len(W_t)
    W_denom = max(1.0, W_bar)
    return {
        t: 1.0 + max(0.0, (w - W_bar) / W_denom)
        for t, w in W_t.items()
    }
