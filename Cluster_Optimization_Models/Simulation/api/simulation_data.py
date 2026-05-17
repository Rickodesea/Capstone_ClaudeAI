"""
simulation_data.py  (Simulation copy — runtime-configurable)
────────────────────────────────────────────────────────────
All constants here are DEFAULT values.  At runtime, pass a config dict to
generate_nodes() / generate_jobs() / sample_spike_fraction() to override any
of them.  This allows the FastAPI app to expose every parameter to the UI
without restarting the server — only a Reset is needed.

Config dict keys accepted by each function are listed in its docstring.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import numpy as np
from dataclasses import dataclass, field


# ═══════════════════════════════════════════════════════════════════════════════
# § DEFAULT CONSTANTS  (can all be overridden via config dict at runtime)
# ═══════════════════════════════════════════════════════════════════════════════

REQUEST_MEM_MIN_MB: float = 512.0
REQUEST_MEM_MAX_MB: float = 1024.0
REQUEST_PER:        float = 0.6
REQ_CPU_MIN:        float = 0.25
REQ_CPU_MAX:        float = 4.0
NODE_CPU_MIN:       float = 8.0
NODE_CPU_MAX:       float = 64.0
NUM_NODES:          int   = 5
NUM_TENANTS:        int   = 3
JOBS_PER_ROUND:     int   = 20
NUM_BATCHES:        int   = 10
NODE_MEM_MIN_MB:    float = 16_384.0   # 16 GB
NODE_MEM_MAX_MB:    float = 65_536.0   # 64 GB
OS_TAX_FRAC:        float = 0.05
K_WINDOW:           int   = 10
MEM_THRESHOLD_FRAC: float = 0.10
DIST_FLAG:          str   = "normal"
MIN_LIFETIME_SEC:   float = 60.0
MAX_LIFETIME_SEC:   float = 600.0
SPIKE_PROB:         float = 0.10
SPIKE_MAX_FRAC:     float = 0.20
BATCH_DURATION_SEC: int   = 60
MAX_PLACEMENT_RETRIES: int = 5
MAX_JOBS_PER_SOLVE: int   = 200


def _make_node_mems(n: int, lo: float, hi: float) -> list[float]:
    if n == 1:
        return [lo]
    ratio = (hi / lo) ** (1.0 / (n - 1))
    return [round(lo * ratio ** i / 1024.0) * 1024.0 for i in range(n)]


def _make_node_cpu(n: int, lo: float, hi: float) -> list[float]:
    if n == 1:
        return [float(round(lo))]
    ratio = (hi / lo) ** (1.0 / (n - 1))
    return [float(round(lo * ratio ** i)) for i in range(n)]


# Module-level arrays (used when no config is passed)
NODE_MEM_MB:    list[float] = _make_node_mems(NUM_NODES, NODE_MEM_MIN_MB, NODE_MEM_MAX_MB)
OS_TAX_MB:      list[float] = [round(m * OS_TAX_FRAC / 1024.0) * 1024.0 for m in NODE_MEM_MB]
NODE_CPU_CORES: list[float] = _make_node_cpu(NUM_NODES, NODE_CPU_MIN, NODE_CPU_MAX)


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

def _sample_truncnorm(rng: np.random.Generator, lo: float, hi: float) -> float:
    mean = (lo + hi) / 2.0
    std  = (hi - lo) / 6.0
    return float(np.clip(rng.normal(mean, std), lo, hi))


def _sample_actual_usage(requested: float, lower_frac: float, dist: str,
                          rng: np.random.Generator) -> float:
    lo, hi = lower_frac * requested, requested
    if dist == "normal":
        return _sample_truncnorm(rng, lo, hi)
    return float(rng.uniform(lo, hi))


def simulate_max_mem(requested_mb: float, lower_frac: float = REQUEST_PER,
                     dist: str = DIST_FLAG, n_samples: int = 200,
                     rng: Optional[np.random.Generator] = None) -> float:
    rng = rng or np.random.default_rng()
    return float(np.max([_sample_actual_usage(requested_mb, lower_frac, dist, rng)
                          for _ in range(n_samples)]))


def simulate_p95(requested_mb: float, lower_frac: float = REQUEST_PER,
                 dist: str = DIST_FLAG, n_samples: int = 200,
                 rng: Optional[np.random.Generator] = None) -> float:
    return simulate_max_mem(requested_mb, lower_frac, dist, n_samples, rng)


def simulate_p95_cpu(requested_cpu: float, lower_frac: float = REQUEST_PER,
                     dist: str = DIST_FLAG, n_samples: int = 200,
                     rng: Optional[np.random.Generator] = None) -> float:
    rng = rng or np.random.default_rng()
    return float(np.percentile([_sample_actual_usage(requested_cpu, lower_frac, dist, rng)
                                  for _ in range(n_samples)], 95))


def sample_spike_fraction(rng: np.random.Generator,
                           spike_prob: float = SPIKE_PROB) -> float:
    """
    Config key: 'spike_prob_pct' (e.g. 10 → 0.10).
    The cluster manager passes spike_prob from its sim_config.
    """
    if rng.random() < spike_prob:
        return float(rng.uniform(0.0, SPIKE_MAX_FRAC))
    return 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# § GENERATORS — configurable via the config dict
# ═══════════════════════════════════════════════════════════════════════════════

def generate_nodes(rng: Optional[np.random.Generator] = None,
                   config: Optional[dict] = None) -> list[NodeState]:
    """
    Config keys (all optional, fall back to module defaults):
        num_nodes         int    (default 5)
        node_mem_min_gb   float  (default 16)
        node_mem_max_gb   float  (default 64)
        node_cpu_min      float  (default 8)
        node_cpu_max      float  (default 64)
    """
    rng    = rng or np.random.default_rng()
    cfg    = config or {}
    n      = int(cfg.get('num_nodes', NUM_NODES))
    m_min  = float(cfg.get('node_mem_min_gb', NODE_MEM_MIN_MB / 1024.0)) * 1024.0
    m_max  = float(cfg.get('node_mem_max_gb', NODE_MEM_MAX_MB / 1024.0)) * 1024.0
    c_min  = float(cfg.get('node_cpu_min', NODE_CPU_MIN))
    c_max  = float(cfg.get('node_cpu_max', NODE_CPU_MAX))

    mems   = _make_node_mems(n, m_min, m_max)
    taxes  = [round(m * OS_TAX_FRAC / 1024.0) * 1024.0 for m in mems]
    cpus   = _make_node_cpu(n, c_min, c_max)

    return [
        NodeState(node_id=i, capacity_mb=mems[i], os_tax_mb=taxes[i],
                  cpu_cores=cpus[i], used_mb=0.0,
                  threshold_frac=MEM_THRESHOLD_FRAC)
        for i in range(n)
    ]


def generate_jobs(round_num: int,
                  num_jobs: int = JOBS_PER_ROUND,
                  num_tenants: int = NUM_TENANTS,
                  rng: Optional[np.random.Generator] = None,
                  config: Optional[dict] = None) -> list[Job]:
    """
    Config keys (all optional, fall back to module defaults):
        num_tenants       int    (default 3)
        jobs_per_round    int    (default 20)
        req_mem_min_mb    float  (default 512)
        req_mem_max_mb    float  (default 1024)
        req_cpu_min       float  (default 0.25)
        req_cpu_max       float  (default 4.0)
    """
    rng         = rng or np.random.default_rng()
    cfg         = config or {}
    num_jobs    = int(cfg.get('jobs_per_round', num_jobs))
    num_tenants = int(cfg.get('num_tenants', num_tenants))
    mem_lo      = float(cfg.get('req_mem_min_mb', REQUEST_MEM_MIN_MB))
    mem_hi      = float(cfg.get('req_mem_max_mb', REQUEST_MEM_MAX_MB))
    cpu_lo      = float(cfg.get('req_cpu_min', REQ_CPU_MIN))
    cpu_hi      = float(cfg.get('req_cpu_max', REQ_CPU_MAX))

    jobs = []
    for i in range(num_jobs):
        tenant   = int(rng.integers(0, num_tenants))
        req      = _sample_truncnorm(rng, mem_lo, mem_hi)
        cpu      = _sample_truncnorm(rng, cpu_lo, cpu_hi)
        pred_mem = simulate_max_mem(req, rng=rng)
        pred_cpu = simulate_p95_cpu(cpu, rng=rng)
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
