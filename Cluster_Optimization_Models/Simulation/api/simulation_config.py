"""
simulation_config.py  (Simulation)
────────────────────────────────────
Config defaults + data-generation helpers for the Simulation API.

This is the canonical source.  simulation_data.py is a thin shim that
re-exports everything from here so that Realtime/cluster_manager.py and
Realtime/optimizer_google_or.py — which do `from simulation_data import …`
— get the config-aware generators when the sys.modules cache is primed by
main.py.

Drawing ideas from:
  • Realtime/simulation_data.py    — base logic and dataclasses
  • PlanAhead/plan_ahead_data.py   — plan-ahead parameters
  • Pipeline/pipeline_configs.py   — scenario config pattern
"""

from __future__ import annotations
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np


# ═══════════════════════════════════════════════════════════════════════════════
# § DEFAULT CONFIG  (all overridable from the frontend via POST /api/config)
# ═══════════════════════════════════════════════════════════════════════════════

DEFAULT_CONFIG: dict = {
    # ── Cluster topology ──────────────────────────────────────────────────────
    'num_nodes':              5,
    'num_tenants':            3,
    'node_mem_min_gb':       16,     # GB — smallest node RAM
    'node_mem_max_gb':       64,     # GB — largest node RAM
    'node_cpu_min':           8,     # cores — fewest CPU per node
    'node_cpu_max':          64,     # cores — most CPU per node
    # ── Workload ─────────────────────────────────────────────────────────────
    'jobs_per_round':        20,     # new jobs per scheduling epoch
    'req_mem_min_mb':       512,     # MB — min declared memory per job
    'req_mem_max_mb':      1024,     # MB — max declared memory per job
    'req_cpu_min':          0.25,    # cores — min CPU request per job
    'req_cpu_max':          4.0,     # cores — max CPU request per job
    'spike_prob_pct':        10,     # % of placed jobs that spike above pred_mem
    'min_lifetime_sec':      60,     # s — shortest job runtime
    'max_lifetime_sec':     600,     # s — longest job runtime
    # ── Model hyper-parameters ────────────────────────────────────────────────
    'k_window':              10,     # rolling window for v̄_n^SLA and W̄_t
    'mem_threshold_frac':  0.10,     # safety buffer = threshold_frac × M_n
    'request_per':          0.60,    # actual usage lower bound as fraction of request
    # ── Scheduler internals ───────────────────────────────────────────────────
    'batch_duration_sec':    60,     # simulated seconds per epoch
    'max_jobs_per_solve':     0,     # 0 = all queued jobs; any positive number caps the MILP window
    # ── Plan-ahead ────────────────────────────────────────────────────────────
    'plan_ahead_interval':   50,     # steps between plan-ahead refreshes
    'access_period':          4,     # steps per planning period
    # ── Plan-ahead data (mirrors PlanAhead/plan_ahead_data.py) ───────────────
    'node_capacity':         10.0,
    'tenant_usage_min':       0.8,   # lower bound for u[i,h] (capacity units)
    'tenant_usage_max':       6.0,   # upper bound for u[i,h] (capacity units)
    'plan_time_limit':        30,    # s — Gurobi wall-clock limit per plan solve
    'plan_mip_gap':           0.05,  # Gurobi relative optimality gap target
    'priority_boost':         2.0,   # objective multiplier for plan-ahead-endorsed nodes
    'use_socp':               0,     # 0 = MILP (fast, default for simulation); 1 = MISOCP (Cantelli)
    'sigma_frac':             0.20,  # demand uncertainty fraction — std dev = sigma_frac × u[i,h]
    'cantelli_epsilon':       0.10,  # Cantelli tail probability — κ = sqrt((1-ε)/ε); ε=0.1 → κ=3
    # ── Workload range (Simulation) ───────────────────────────────────────────
    'jobs_min_per_round':     5,     # Simulation: min jobs sampled per epoch
    'jobs_max_per_round':    20,     # Simulation: max jobs sampled per epoch
}


def load_config(override: dict | None = None) -> dict:
    """
    Return a config dict starting from DEFAULT_CONFIG, updated with any
    overrides that match known keys.  Called once at simulation reset.
    """
    cfg = dict(DEFAULT_CONFIG)
    if override:
        cfg.update({k: v for k, v in override.items() if k in DEFAULT_CONFIG})
    return cfg


# ═══════════════════════════════════════════════════════════════════════════════
# § MODULE-LEVEL CONSTANTS  (kept for Realtime import compatibility)
# ═══════════════════════════════════════════════════════════════════════════════

NUM_NODES:      int   = DEFAULT_CONFIG['num_nodes']
NUM_TENANTS:    int   = DEFAULT_CONFIG['num_tenants']
NUM_BATCHES:    int   = 10
JOBS_PER_ROUND: int   = DEFAULT_CONFIG['jobs_per_round']

NODE_MEM_MIN_MB: float = DEFAULT_CONFIG['node_mem_min_gb'] * 1024.0
NODE_MEM_MAX_MB: float = DEFAULT_CONFIG['node_mem_max_gb'] * 1024.0

OS_TAX_FRAC:        float = 0.05
MEM_THRESHOLD_FRAC: float = DEFAULT_CONFIG['mem_threshold_frac']

REQUEST_MEM_MIN_MB: float = DEFAULT_CONFIG['req_mem_min_mb']
REQUEST_MEM_MAX_MB: float = DEFAULT_CONFIG['req_mem_max_mb']
REQUEST_PER:        float = DEFAULT_CONFIG['request_per']

REQ_CPU_MIN: float = DEFAULT_CONFIG['req_cpu_min']
REQ_CPU_MAX: float = DEFAULT_CONFIG['req_cpu_max']

NODE_CPU_MIN: float = DEFAULT_CONFIG['node_cpu_min']
NODE_CPU_MAX: float = DEFAULT_CONFIG['node_cpu_max']

K_WINDOW:           int   = DEFAULT_CONFIG['k_window']
DIST_FLAG:          str   = "normal"
MIN_LIFETIME_SEC:   float = DEFAULT_CONFIG['min_lifetime_sec']
MAX_LIFETIME_SEC:   float = DEFAULT_CONFIG['max_lifetime_sec']

SPIKE_PROB:         float = DEFAULT_CONFIG['spike_prob_pct'] / 100.0
SPIKE_MAX_FRAC:     float = 0.20

BATCH_DURATION_SEC: int   = DEFAULT_CONFIG['batch_duration_sec']
MAX_PLACEMENT_RETRIES: int = 5
MAX_JOBS_PER_SOLVE: int   = 0   # 0 = no cap (send all queued jobs)


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


NODE_MEM_MB:    list[float] = _make_node_mems(NUM_NODES, NODE_MEM_MIN_MB, NODE_MEM_MAX_MB)
OS_TAX_MB:      list[float] = [round(m * OS_TAX_FRAC / 1024.0) * 1024.0 for m in NODE_MEM_MB]
NODE_CPU_CORES: list[float] = _make_node_cpu(NUM_NODES, NODE_CPU_MIN, NODE_CPU_MAX)


# ═══════════════════════════════════════════════════════════════════════════════
# § DATACLASSES  (identical to Realtime/simulation_data.py)
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
    overflow_history:  list  = field(default_factory=list)   # used_mb > M_n^cap
    violation_history: list  = field(default_factory=list)   # used_mb > M_n


# ═══════════════════════════════════════════════════════════════════════════════
# § SAMPLING HELPERS  (identical to Realtime)
# ═══════════════════════════════════════════════════════════════════════════════

def _sample_truncnorm(rng: np.random.Generator, lo: float, hi: float) -> float:
    mean = (lo + hi) / 2.0
    std  = (hi - lo) / 6.0
    return float(np.clip(rng.normal(mean, std), lo, hi))


def simulate_max_mem(
    requested_mb: float,
    lower_frac:   float = REQUEST_PER,
    dist:         str   = DIST_FLAG,
    n_samples:    int   = 200,
    rng: Optional[np.random.Generator] = None,
) -> float:
    rng = rng or np.random.default_rng()
    lo, hi = lower_frac * requested_mb, requested_mb
    if dist == "normal":
        mean = (lo + hi) / 2.0
        std  = (hi - lo) / 6.0
        samples = np.clip(rng.normal(mean, std, n_samples), lo, hi)
    else:
        samples = rng.uniform(lo, hi, n_samples)
    return float(np.max(samples))


def simulate_p95(requested_mb: float, lower_frac: float = REQUEST_PER,
                 dist: str = DIST_FLAG, n_samples: int = 200,
                 rng: Optional[np.random.Generator] = None) -> float:
    return simulate_max_mem(requested_mb, lower_frac, dist, n_samples, rng)


def simulate_p95_cpu(
    requested_cpu: float,
    lower_frac:    float = REQUEST_PER,
    dist:          str   = DIST_FLAG,
    n_samples:     int   = 200,
    rng: Optional[np.random.Generator] = None,
) -> float:
    rng = rng or np.random.default_rng()
    lo, hi = lower_frac * requested_cpu, requested_cpu
    if dist == "normal":
        mean = (lo + hi) / 2.0
        std  = (hi - lo) / 6.0
        samples = np.clip(rng.normal(mean, std, n_samples), lo, hi)
    else:
        samples = rng.uniform(lo, hi, n_samples)
    return float(np.percentile(samples, 95))


def sample_spike_fraction(rng: np.random.Generator, spike_prob: float = SPIKE_PROB) -> float:
    """Return spike fraction; 0.0 with probability (1 - spike_prob)."""
    if rng.random() < spike_prob:
        return float(rng.uniform(0.0, SPIKE_MAX_FRAC))
    return 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# § GENERATORS  (config-aware — compatible with Realtime call signatures)
# ═══════════════════════════════════════════════════════════════════════════════

def generate_nodes(
    rng:    Optional[np.random.Generator] = None,
    config: Optional[dict]               = None,
) -> list[NodeState]:
    """Create initial NodeState list using config values (or module defaults)."""
    rng = rng or np.random.default_rng()
    cfg = config or {}
    n        = int(cfg.get('num_nodes',       NUM_NODES))
    mem_min  = float(cfg.get('node_mem_min_gb', NODE_MEM_MIN_MB / 1024)) * 1024.0
    mem_max  = float(cfg.get('node_mem_max_gb', NODE_MEM_MAX_MB / 1024)) * 1024.0
    cpu_min  = float(cfg.get('node_cpu_min',   NODE_CPU_MIN))
    cpu_max  = float(cfg.get('node_cpu_max',   NODE_CPU_MAX))
    t_frac   = float(cfg.get('mem_threshold_frac', MEM_THRESHOLD_FRAC))

    mems   = _make_node_mems(n, mem_min, mem_max)
    taxes  = [round(m * OS_TAX_FRAC / 1024.0) * 1024.0 for m in mems]
    cores  = _make_node_cpu(n, cpu_min, cpu_max)

    return [
        NodeState(
            node_id        = i,
            capacity_mb    = mems[i],
            os_tax_mb      = taxes[i],
            cpu_cores      = cores[i],
            used_mb        = 0.0,
            threshold_frac = t_frac,
        )
        for i in range(n)
    ]


def generate_jobs(
    round_num:   int,
    num_jobs:    int                           = JOBS_PER_ROUND,
    num_tenants: int                           = NUM_TENANTS,
    rng:         Optional[np.random.Generator] = None,
    config:      Optional[dict]               = None,
) -> list[Job]:
    """Generate a batch of jobs using config values (or module defaults)."""
    rng = rng or np.random.default_rng()
    cfg = config or {}
    n_tenants  = int(cfg.get('num_tenants',   num_tenants))
    mem_lo     = float(cfg.get('req_mem_min_mb', REQUEST_MEM_MIN_MB))
    mem_hi     = float(cfg.get('req_mem_max_mb', REQUEST_MEM_MAX_MB))
    cpu_lo     = float(cfg.get('req_cpu_min',    REQ_CPU_MIN))
    cpu_hi     = float(cfg.get('req_cpu_max',    REQ_CPU_MAX))
    req_per    = float(cfg.get('request_per',    REQUEST_PER))

    jobs = []
    for i in range(num_jobs):
        tenant   = int(rng.integers(0, n_tenants))
        req      = _sample_truncnorm(rng, mem_lo, mem_hi)
        cpu      = _sample_truncnorm(rng, cpu_lo, cpu_hi)
        pred_mem = simulate_max_mem(req, lower_frac=req_per, rng=rng)
        pred_cpu = simulate_p95_cpu(cpu, lower_frac=req_per, rng=rng)
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
# § MATH-MODEL DERIVED QUANTITIES  (identical to Realtime)
# ═══════════════════════════════════════════════════════════════════════════════

def compute_available_capacity(node: NodeState) -> float:
    """M_n^cap = M_n - M_n^tax - M_n^theta"""
    m_theta = node.threshold_frac * node.capacity_mb
    return node.capacity_mb - node.os_tax_mb - m_theta


def compute_remaining_avail(node: NodeState, m_cap: float) -> float:
    """M_n^avail = M_n^cap - U_n^mem"""
    return m_cap - node.used_mb


def compute_remaining_eff(r_avail: float, v_bar: float) -> float:
    """M_n^eff = max(0, M_n^avail × (1 - v̄_n^SLA))"""
    return max(0.0, r_avail * (1.0 - v_bar))


def compute_utilization_weight(node: NodeState) -> float:
    """ω_n^utilize = 1 + clamp(U_n / M_n^cap, 0, 1)  ∈ [1, 2]"""
    m_cap = compute_available_capacity(node)
    frac  = min(1.0, max(0.0, node.used_mb / max(1.0, m_cap)))
    return 1.0 + frac


def compute_violation_rate(history: list, K: int = K_WINDOW) -> float:
    """v̄_n^SLA = fraction of last K batches where used_mb > M_n^cap"""
    recent = history[-K:] if len(history) >= K else history
    return sum(recent) / len(recent) if recent else 0.0


def compute_node_weight(node_id: int, num_nodes: int = NUM_NODES) -> float:
    """σ_n^consolid = num_nodes - node_id"""
    return float(num_nodes - node_id)


def compute_omega(W_t: dict[int, float]) -> dict[int, float]:
    """ω_delay,t = 1 + max(0, (W̄_t - W̄) / max(1, W̄))"""
    if not W_t:
        return {}
    W_bar   = sum(W_t.values()) / len(W_t)
    W_denom = max(1.0, W_bar)
    return {t: 1.0 + max(0.0, (w - W_bar) / W_denom) for t, w in W_t.items()}
