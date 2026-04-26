"""
simulation_data.py
──────────────────
Configuration constants, dataclasses, and data-generation helpers for the
multi-tenant cluster scheduling simulation.

This module is the single source of truth for:
  • Global tunable parameters (memory ranges, lifetime, spike probability, etc.)
  • The Job and NodeState dataclasses used everywhere else
  • Helper functions that generate synthetic input data and compute the
    derived quantities defined in goal_programming_v4.html (§3, §4)

The math model (goal_programming_v4) ONLY optimises memory.
CPU fields (req_cpu) are generated here for completeness and future work,
but are never used in the LP formulation.

Notation used in comments matches goal_programming_v4:
  M_n          physical RAM on node n
  τ_n          OS/kubelet memory tax (reserved; not available to tenant jobs)
  M_n^avail    available memory = M_n − τ_n                        (§3)
  v̄_n          rolling SLA violation rate on node n
  U_n          currently used memory on node n (sum of running job act_mem)
  R_n^avail    remaining available = M_n^avail − U_n               (§3)
  R_n^eff      effective remaining = S_{fn}(R_n^avail, v̄_n)       (§3)
  u_n          node utilization weight ∈ [1,2]                     (§3)
  ω_t          per-tenant priority weight ∈ [1,2]                  (§3)
  W̄_t         per-tenant average scheduling delay (seconds)        (§3)
  m̂_j         P95 predicted memory for job j                       (§3)
  x_{jn}      decision variable — 1 if job j placed on node n      (§1)
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import numpy as np
from dataclasses import dataclass, field


# ═══════════════════════════════════════════════════════════════════════════════
# § CONFIG — Simulation-wide tunable parameters
# ═══════════════════════════════════════════════════════════════════════════════

# ── Memory request range ───────────────────────────────────────────────────────
# Each job's declared (tenant-requested) memory is drawn from a truncated
# normal distribution over this range.  Tenants typically over-declare.
REQUEST_MEM_MIN_MB: float = 512.0    # minimum declared memory request (MB)
REQUEST_MEM_MAX_MB: float = 1024.0   # maximum declared memory request (MB)

# ── Prediction layer simulation ────────────────────────────────────────────────
# P95 predicted memory (m̂_j) is estimated by sampling the actual-usage
# distribution and taking the 95th percentile.  REQUEST_PER defines the
# lower bound of actual usage as a fraction of the declared request.
# e.g. REQUEST_PER=0.6 means a 20 MB request actually uses 12–20 MB.
REQUEST_PER: float = 0.6

# ── CPU request range ─────────────────────────────────────────────────────────
REQ_CPU_MIN: float = 0.25   # minimum CPU cores requested
REQ_CPU_MAX: float = 4.0    # maximum CPU cores requested

# ── Cluster CPU topology ───────────────────────────────────────────────────────
# Each node has a fixed number of CPU cores.  Log-spaced between these bounds.
NODE_CPU_MIN: float = 8.0    # smallest node: 8 cores
NODE_CPU_MAX: float = 64.0   # largest node: 64 cores

# ── Cluster topology ───────────────────────────────────────────────────────────
NUM_NODES:      int = 5
NUM_TENANTS:    int = 3
JOBS_PER_ROUND: int = 20     # new jobs generated each batch (keep low for fast tests)
NUM_ROUNDS:     int = 5     # used only by optimizer_pulp.py standalone demo;
                            # ClusterManager.run() takes num_batches directly
NUM_BATCHES:    int = 10

# Physical RAM (M_n) and OS/kubelet overhead (τ_n) per node (MB)
# Sizes are log-spaced between NODE_MEM_MIN_MB and NODE_MEM_MAX_MB, rounded to
# the nearest 1 GB (1024 MB).  Changing NUM_NODES automatically resizes both.
NODE_MEM_MIN_MB: float = 16_384.0   # 16 GB — smallest node
NODE_MEM_MAX_MB: float = 65_536.0   # 64 GB — largest node
OS_TAX_FRAC:     float = 0.09       # fraction of physical RAM reserved for OS/kubelet


def _make_node_mems(n: int, lo: float, hi: float) -> list[float]:
    """Return n log-spaced node RAM sizes between lo and hi (MB), rounded to 1 GB."""
    if n == 1:
        return [lo]
    ratio = (hi / lo) ** (1.0 / (n - 1))
    return [round(lo * ratio ** i / 1024.0) * 1024.0 for i in range(n)]


NODE_MEM_MB:   list[float] = _make_node_mems(NUM_NODES, NODE_MEM_MIN_MB, NODE_MEM_MAX_MB)
OS_TAX_MB:     list[float] = [round(m * OS_TAX_FRAC / 1024.0) * 1024.0 for m in NODE_MEM_MB]
def _make_node_cpu(n: int, lo: float, hi: float) -> list[float]:
    """Return n log-spaced CPU core counts between lo and hi, rounded to integer."""
    if n == 1:
        return [float(round(lo))]
    ratio = (hi / lo) ** (1.0 / (n - 1))
    return [float(round(lo * ratio ** i)) for i in range(n)]

NODE_CPU_CORES: list[float] = _make_node_cpu(NUM_NODES, NODE_CPU_MIN, NODE_CPU_MAX)

# ── Model hyper-parameters (goal_programming_v4 §3, §6) ──────────────────────
K_WINDOW: int = 10   # K — rolling window length for the violation rate v̄_n

# CPU constraint (C4) — global on/off switch.
# False (default): C4 is omitted from the LP.  CPU overcommitment is handled
#   by kernel throttling at runtime; treating cores as exclusive capacity is
#   too aggressive because the OS time-slices them across hundreds of processes.
# True: C4 is added as a hard constraint — total predicted CPU peak on a node
#   may not exceed its remaining cores.  Use only if the workload is CPU-bound
#   and throttling is not acceptable.
ENABLE_CPU_CONSTRAINT: bool = False

# ── Sampling distribution ──────────────────────────────────────────────────────
DIST_FLAG: str = "normal"   # "normal" | "uniform"
                            # Controls how actual-usage samples are drawn
                            # when simulating P95 predictions

# ── Job lifetime (simulated time) ─────────────────────────────────────────────
# When the cluster manager places a job, it assigns a random lifetime drawn
# uniformly from [MIN_LIFETIME_SEC, MAX_LIFETIME_SEC].  Once that many
# simulated seconds pass, the job is considered complete and its memory is
# released back to the node.
MIN_LIFETIME_SEC: float = 60.0    # 60 seconds (shortest possible job)
MAX_LIFETIME_SEC: float = 600.0   # 10 minutes (longest possible job)

# ── Spike simulation ───────────────────────────────────────────────────────────
# In the simulation we assume:  act_mem = pred_mem  (most of the time)
# But with probability SPIKE_PROB, a job's actual usage spikes above pred_mem:
#   act_mem = pred_mem × (1 + spike_fraction)
#   spike_fraction ~ Uniform(0, SPIKE_MAX_FRAC)
# This models the 5 % tail that the P95 estimate is designed to cover,
# plus rare severe spikes that could cause SLA violations.
SPIKE_PROB:     float = 0.10   # 10 % of placed jobs trigger a spike
SPIKE_MAX_FRAC: float = 0.20   # max spike = 20 % above predicted memory

# ── Simulation clock ───────────────────────────────────────────────────────────
# Each batch represents BATCH_DURATION_SEC of simulated wall-clock time.
# Timestamps on jobs are expressed in this simulated time so that
# wait times (in seconds) are meaningful across batches.
BATCH_DURATION_SEC: int = 60   # simulated seconds per scheduling epoch

# ── Scheduler retry limit ──────────────────────────────────────────────────────
# If the optimizer returns zero placements this many times consecutively
# within a single batch, the cluster manager gives up and carries remaining
# jobs to the next batch (nodes are likely saturated).
MAX_PLACEMENT_RETRIES: int = 5

# ── Solver queue cap ───────────────────────────────────────────────────────────
# Maximum number of jobs sent to the MILP solver per call.  Prevents the solver
# from receiving thousands of binary variables when JOBS_PER_ROUND is large,
# which would cause the C++ solver to hang and block Python's KeyboardInterrupt.
# Oldest jobs (by arrival_round) are always sent first.
MAX_JOBS_PER_SOLVE: int = 200



# ═══════════════════════════════════════════════════════════════════════════════
# § DATACLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Job:
    """
    Represents a containerized workload submitted by a tenant.

    In Kubernetes this corresponds to a Pod — the smallest schedulable unit.
    We call it "Job" for generality (the model is scheduler-agnostic).

    Fields set at creation (by generate_jobs):
        job_id            unique identifier  e.g. "r0_j3"
        tenant_id         which tenant owns this job
        req_mem_mb        declared memory request (MB)  — tenant-specified,
                          often inflated (overcommitment)
        req_cpu           declared CPU request (cores)  — tracked but NOT
                          used in the LP (memory-only model)
        pred_mem_mb       P95 predicted memory (m̂_j in §3) — the optimizer
                          uses this, not req_mem_mb, for placement decisions
        arrival_round     the batch number in which this job was generated

    Fields set by ClusterManager:
        arrival_timestamp     simulated UTC time when job entered the queue
        scheduling_timestamp  simulated UTC time when job was placed on a node
    """
    job_id:        str
    tenant_id:     int
    req_mem_mb:    float   # declared request (tenant over-declares in practice)
    req_cpu:       float   # declared CPU cores (tenant-specified)
    pred_mem_mb:   float   # m̂_j — P95 predicted memory used by the optimizer
    pred_cpu_p90:  float   # p̂_j^CPU — P90 predicted CPU peak used in C4
    arrival_round: int

    # Set by ClusterManager when the job enters the queue / is placed
    arrival_timestamp:    Optional[datetime] = None
    scheduling_timestamp: Optional[datetime] = None


@dataclass
class NodeState:
    """
    Mutable state of one cluster node, updated every scheduling round.

    The cluster manager recomputes used_mb from the set of currently running
    jobs before every solver call — it is NOT accumulated directly.

    Fields:
        node_id           index 0..NUM_NODES-1
        capacity_mb       M_n — physical RAM installed
        os_tax_mb         τ_n — fixed overhead reserved for OS/kubelet;
                          NOT available to tenant jobs
        used_mb           U_n — sum of act_mem_mb of all running jobs on
                          this node (computed fresh each round)
        violation_history bool per batch: was used_mb > M_n^avail at batch start?
                          Used to compute the rolling violation rate v̄_n (§3)
    """
    node_id:           int
    capacity_mb:       float          # M_n  — physical RAM
    os_tax_mb:         float          # τ_n  — OS/kubelet overhead
    cpu_cores:         float = 0.0    # C_n  — total CPU cores on the node
    used_mb:           float = 0.0    # U_n  — recomputed each round from running jobs
    used_cpu:          float = 0.0    # A_n  — recomputed each round from running jobs
    violation_history: list  = field(default_factory=list)  # bool per batch


# ═══════════════════════════════════════════════════════════════════════════════
# § SAMPLING HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _sample_requested_mem(rng: np.random.Generator) -> float:
    """
    Draw one job's declared memory request from a truncated normal
    distribution over [REQUEST_MEM_MIN_MB, REQUEST_MEM_MAX_MB].

    The mean is the midpoint; ±3σ spans the full range, so extreme values
    are possible but rare.  The clip ensures we never go out of bounds.
    """
    lo, hi = REQUEST_MEM_MIN_MB, REQUEST_MEM_MAX_MB
    mean   = (lo + hi) / 2.0
    std    = (hi - lo) / 6.0
    return float(np.clip(rng.normal(mean, std), lo, hi))


def _sample_cpu_request(rng: np.random.Generator) -> float:
    """
    Draw one job's declared CPU request (cores) from a truncated normal
    over [REQ_CPU_MIN, REQ_CPU_MAX].

    NOTE: CPU is generated for realism but is NOT used in the optimizer.
    The math model (goal_programming_v4) is memory-only.
    Future work could extend the LP to include CPU constraints.
    """
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
    """
    Draw one actual-usage sample for a job with a given declared request.
    Used internally by simulate_p95 to build the empirical usage distribution.

    Actual usage is bounded to [lower_frac * requested_mb, requested_mb].
    With dist="normal" samples come from a truncated normal centred at the
    midpoint.  With dist="uniform" it is a flat draw over the same range.
    """
    lo, hi = lower_frac * requested_mb, requested_mb
    if dist == "normal":
        mean = (lo + hi) / 2.0
        std  = (hi - lo) / 6.0
        return float(np.clip(rng.normal(mean, std), lo, hi))
    return float(rng.uniform(lo, hi))


def simulate_p95(
    requested_mb: float,
    lower_frac:   float = REQUEST_PER,
    dist:         str   = DIST_FLAG,
    n_samples:    int   = 200,
    rng:          Optional[np.random.Generator] = None,
) -> float:
    """
    Simulate what the ML prediction layer would output as the P95 memory
    estimate (m̂_j) for a job with the given declared request.

    Draws n_samples from the actual-usage distribution and returns the
    95th percentile.  The result is used as pred_mem_mb in the Job and
    passed to the optimizer as m̂_j in the capacity constraint (§6 C2).

    Why P95?  The optimizer uses P95 to decide placement.  A violation
    occurs when the actual usage (runtime) exceeds P95 — a 5 % tail event.
    """
    rng = rng or np.random.default_rng()
    samples = [
        _sample_actual_usage(requested_mb, lower_frac, dist, rng)
        for _ in range(n_samples)
    ]
    return float(np.percentile(samples, 95))


def simulate_p90_cpu(
    requested_cpu: float,
    lower_frac:    float = REQUEST_PER,
    dist:          str   = DIST_FLAG,
    n_samples:     int   = 200,
    rng:           Optional[np.random.Generator] = None,
) -> float:
    """
    Simulate the P90 CPU peak estimate (p̂_j^CPU) for a job  (§3, C4).

    Draws n_samples from the actual-usage distribution and returns the
    90th percentile.  Used as pred_cpu_p90 in the Job and passed to the
    optimizer as the CPU demand in constraint C4.

    CPU overcommitment is intentionally not modelled: throttling handles
    CPU excess at runtime, so no SLA-driven capacity reduction is applied.
    """
    rng = rng or np.random.default_rng()
    samples = [
        _sample_actual_usage(requested_cpu, lower_frac, dist, rng)
        for _ in range(n_samples)
    ]
    return float(np.percentile(samples, 90))


def sample_spike_fraction(rng: np.random.Generator) -> float:
    """
    Determine whether a placed job will experience a memory spike.

    With probability (1 - SPIKE_PROB): no spike, return 0.0.
    With probability SPIKE_PROB: return a random spike fraction in
    (0, SPIKE_MAX_FRAC], meaning the job's actual memory = pred_mem × (1+frac).

    This simulates the cases where a job's runtime behaviour exceeds what
    the P95 prediction anticipated.  The cluster manager applies this to
    act_mem when recording a RunningJob.
    """
    if rng.random() < SPIKE_PROB:
        # Spike: Uniform(0, SPIKE_MAX_FRAC) — could be small or large
        return float(rng.uniform(0.0, SPIKE_MAX_FRAC))
    return 0.0   # no spike (the common case)


# ═══════════════════════════════════════════════════════════════════════════════
# § GENERATORS — produce simulation inputs for the cluster manager
# ═══════════════════════════════════════════════════════════════════════════════

def generate_jobs(
    round_num:   int,
    num_jobs:    int = JOBS_PER_ROUND,
    num_tenants: int = NUM_TENANTS,
    rng:         Optional[np.random.Generator] = None,
) -> list[Job]:
    """
    Generate a batch of pending jobs for one scheduling round.

    Called by ClusterManager at the start of each batch.  The cluster manager
    then stamps arrival_timestamp on each returned job before adding it to
    the queue.

    For each job:
      req_mem_mb   ← truncated normal over [REQUEST_MEM_MIN_MB, REQUEST_MEM_MAX_MB]
      req_cpu      ← truncated normal over [REQ_CPU_MIN, REQ_CPU_MAX]
      pred_mem_mb  ← simulate_p95(req_mem_mb)   — simulated prediction layer output
      pred_cpu_p90 ← simulate_p90_cpu(req_cpu)  — simulated prediction layer output
    """
    rng  = rng or np.random.default_rng()
    jobs = []
    for i in range(num_jobs):
        tenant   = int(rng.integers(0, num_tenants))
        req      = _sample_requested_mem(rng)
        cpu      = _sample_cpu_request(rng)
        p95      = simulate_p95(req, rng=rng)
        cpu_p90  = simulate_p90_cpu(cpu, rng=rng)
        jobs.append(Job(
            job_id        = f"r{round_num}_j{i}",
            tenant_id     = tenant,
            req_mem_mb    = round(req, 2),
            req_cpu       = round(cpu, 3),
            pred_mem_mb   = round(p95, 2),
            pred_cpu_p90  = round(cpu_p90, 3),
            arrival_round = round_num,
            # arrival_timestamp and scheduling_timestamp are set by ClusterManager
        ))
    return jobs


def generate_nodes(rng: Optional[np.random.Generator] = None) -> list[NodeState]:
    """
    Create the initial node states with randomised pre-existing usage.

    Each node starts with 10–40 % of its available capacity (after OS tax)
    already used — simulating a cluster that is partially loaded at t=0.
    The cluster manager will recompute used_mb from running jobs from batch 1
    onward; this initial value matters only for the first solver call.
    """
    rng   = rng or np.random.default_rng()
    nodes = []
    for i in range(NUM_NODES):
        cap      = NODE_MEM_MB[i]
        tax      = OS_TAX_MB[i]
        cores    = NODE_CPU_CORES[i]
        # Initial memory usage: random fraction of (capacity - OS tax)
        used_mem = float(rng.uniform(0.10, 0.40)) * (cap - tax)
        # Initial CPU usage: random fraction of total cores
        used_cpu = float(rng.uniform(0.10, 0.40)) * cores
        nodes.append(NodeState(
            node_id     = i,
            capacity_mb = cap,
            os_tax_mb   = tax,
            cpu_cores   = cores,
            used_mb     = round(used_mem, 2),
            used_cpu    = round(used_cpu, 3),
        ))
    return nodes


# ═══════════════════════════════════════════════════════════════════════════════
# § MATH-MODEL DERIVED QUANTITIES  (goal_programming_v4 §3)
# All of these are called by the optimizer before each solve() call.
# ═══════════════════════════════════════════════════════════════════════════════

def compute_violation_rate(history: list[bool], K: int = K_WINDOW) -> float:
    """
    v̄_n — rolling SLA violation rate for one node  (§3, "Node Memory — Dynamic").

    Returns the fraction of the most recent K batches in which the node's
    actual memory usage exceeded its effective capacity M_eff.

    v̄_n = 0  → no recent violations (node is healthy)
    v̄_n = 1  → every recent batch had an overload (node is consistently stressed)
    """
    recent = history[-K:] if len(history) >= K else history
    return sum(recent) / len(recent) if recent else 0.0


def compute_available_capacity(node: NodeState) -> float:
    """
    M_n^avail — available memory for tenant jobs on node n  (§3).

    Formula:  M_n^avail = M_n − τ_n

    The OS tax τ_n (OS/kubelet overhead) is always subtracted.  This is the
    fixed upper bound for all tenant job memory on node n.
    """
    return node.capacity_mb - node.os_tax_mb


def compute_remaining_avail(node: NodeState, m_avail: float) -> float:
    """
    R_n^avail — remaining available memory before SLA adjustment  (§3).

    Formula:  R_n^avail = M_n^avail − U_n

    Represents raw remaining capacity after subtracting current usage.
    May be negative when the node is over-committed.
    """
    return m_avail - node.used_mb


def compute_remaining_eff(r_avail: float, v_bar: float) -> float:
    """
    R_n^eff — effective remaining memory offered to new jobs  (§3).

    Formula:  R_n^eff = S_{fn}(R_n^avail, v̄_n) = max(0, R_n^avail · (1 − v̄_n))

    The scaling function S_{fn} applies the rolling SLA violation rate v̄_n
    as a percentage reduction of the available remaining capacity.
    v̄_n = 0 → R_n^eff = R_n^avail (no penalty).
    v̄_n = 1 → R_n^eff = 0 (node fully blocked for new admissions).
    R_n^eff is the RHS of constraint C2 in the optimizer.
    """
    return max(0.0, r_avail * (1.0 - v_bar))


def compute_remaining_cpu(node: NodeState) -> float:
    """
    R_n^CPU — remaining CPU capacity available for new jobs  (§3, C4).

    Formula:  R_n^CPU = C_n − A_n

    No SLA adjustment is applied; CPU overcommitment is handled at runtime
    by kernel throttling rather than admission control.
    """
    return max(0.0, node.cpu_cores - node.used_cpu)


def compute_utilization_weight(node: NodeState, m_avail: float) -> float:
    """
    u_n — node utilization weight ∈ [1, 2]  (§3, "Node Utilization Weight").

    Formula:  u_n = 1 + min(1, max(0, U_n / max(1, M_n^avail)))

    Derived from the fraction of available memory currently in use.
    u_n = 1 → node is idle (no consolidation preference).
    u_n = 2 → node is fully loaded (highest consolidation preference).
    Applied directly in the objective to consolidate jobs onto busier nodes.
    """
    frac = min(1.0, max(0.0, node.used_mb / max(1.0, m_avail)))
    return 1.0 + frac


def compute_omega(W_t: dict[int, float]) -> dict[int, float]:
    """
    ω_t — per-tenant priority weight ∈ [1, 2]  (§3, "Fairness Feedback").

    Formula:  ω_t = f_ω(W̄_t, W̄) = 1 + min(1, max(0, (W̄_t − W̄) / max(1, W̄)))

    The function f_ω maps excess wait fraction into [0, 1] and adds 1, giving
    ω_t ∈ [1, 2].  Tenants whose average wait exceeds the cluster-wide mean W̄
    receive ω_t > 1; their jobs contribute more to the LP objective (§5), so
    the solver naturally prefers placing them — fairness as a side effect of
    weighted maximisation.  No free parameter is needed; the function
    automatically saturates at 2× for severely underserved tenants.

    ω_t = 1 when W̄_t ≤ W̄ (no penalty for low-waiting tenants).
    ω_t = 2 when W̄_t ≥ 2·W̄ (maximum priority boost).

    Returns an empty dict if W_t is empty (handled by the optimizer as ω=1).
    """
    if not W_t:
        return {}
    W_bar = sum(W_t.values()) / len(W_t)
    W_denom = max(1.0, W_bar)
    return {
        t: 1.0 + min(1.0, max(0.0, (w - W_bar) / W_denom))
        for t, w in W_t.items()
    }
