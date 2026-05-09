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

# ── Memory request range ───────────────────────────────────────────────────────
# Each job's declared (tenant-requested) memory is drawn from a truncated
# normal distribution over this range.  Tenants typically over-declare.
REQUEST_MEM_MIN_MB: float = 512.0    # minimum declared memory request (MB)
REQUEST_MEM_MAX_MB: float = 1024.0   # maximum declared memory request (MB)

# ── Prediction layer simulation ────────────────────────────────────────────────
# Predicted memory (P̂_j^mem) simulates the max memory usage observed over the
# job's lifetime. REQUEST_PER defines the lower bound of actual usage as a
# fraction of the declared request.
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
NUM_BATCHES:    int = 10

# Physical RAM (M_n) and OS/kubelet overhead (M_n^tax) per node (MB)
# Sizes are log-spaced between NODE_MEM_MIN_MB and NODE_MEM_MAX_MB, rounded to
# the nearest 1 GB (1024 MB).  Changing NUM_NODES automatically resizes both.
NODE_MEM_MIN_MB: float = 16_384.0   # 16 GB — smallest node
NODE_MEM_MAX_MB: float = 65_536.0   # 64 GB — largest node

# OS_TAX_FRAC: fraction of physical RAM reserved for OS, kubelet, and the
# local cluster agent. Based on Google cluster-usage traces v3: the Borglet
# agent reserves approximately 5% of physical RAM, making the effective
# usable ceiling for tasks ~95% of M_n.
OS_TAX_FRAC: float = 0.05


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

# ── Model hyper-parameters (§3, §6) ──────────────────────────────────────────
K_WINDOW: int = 10   # K — rolling window length for v̄_n^SLA and ω_delay,t

# ── Memory safety threshold ────────────────────────────────────────────────────
# M_theta_n = threshold_frac * M_n — fixed memory buffer reserved per node as
# protection against runtime memory spikes. Baked into M_cap_n so it is a
# static, pre-allocated reservation rather than a dynamic scaling factor.
# threshold_frac = 0.10 → reserves 10% of physical RAM per node.
MEM_THRESHOLD_FRAC: float = 0.10

# ── Sampling distribution ──────────────────────────────────────────────────────
DIST_FLAG: str = "normal"   # "normal" | "uniform"
                            # Controls how actual-usage samples are drawn
                            # when simulating max/P95 predictions

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
# This models cases where runtime behavior exceeds even the predicted maximum.
SPIKE_PROB:     float = 0.10   # 10 % of placed jobs trigger a spike
SPIKE_MAX_FRAC: float = 0.20   # max spike = 20 % above predicted memory

# ── Simulation clock ───────────────────────────────────────────────────────────
# Each batch represents BATCH_DURATION_SEC of simulated wall-clock time.
BATCH_DURATION_SEC: int = 60   # simulated seconds per scheduling epoch

# ── Scheduler retry limit ──────────────────────────────────────────────────────
# If the optimizer returns zero placements this many times consecutively
# within a single batch, the cluster manager gives up and carries remaining
# jobs to the next batch (nodes are likely saturated).
MAX_PLACEMENT_RETRIES: int = 5

# ── Solver queue cap ───────────────────────────────────────────────────────────
# Maximum number of jobs sent to the MILP solver per call.  Prevents the solver
# from receiving thousands of binary variables when JOBS_PER_ROUND is large.
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
        pred_mem_mb       Maximum predicted memory (P̂_j^mem) — worst-case peak
                          memory usage predicted over the job's lifetime. The
                          optimizer uses this for placement decisions (C2).
                          Based on Google traces `maximum_usage` field.
        pred_cpu_p95      P95 predicted CPU peak (P̂_j^CPU) — 95th percentile
                          of CPU demand. Used in C4. Based on Google traces
                          `tail_cpu_usage_distribution` (index 4).
        arrival_round     the batch number in which this job was generated

    Fields set by ClusterManager:
        arrival_timestamp     simulated UTC time when job entered the queue
        scheduling_timestamp  simulated UTC time when job was placed on a node
    """
    job_id:        str
    tenant_id:     int
    req_mem_mb:    float   # declared request (tenant over-declares in practice)
    req_cpu:       float   # declared CPU cores (tenant-specified)
    pred_mem_mb:   float   # P̂_j^mem — max predicted memory used by the optimizer
    pred_cpu_p95:  float   # P̂_j^CPU — P95 predicted CPU peak used in C4
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
        os_tax_mb         M_n^tax — fixed overhead reserved for OS/kubelet;
                          NOT available to tenant jobs
        used_mb           U_n^mem — sum of act_mem_mb of all running jobs on
                          this node (computed fresh each round)
        threshold_frac    fraction for M_n^theta = threshold_frac * M_n.
                          Reserves a fixed amount of physical RAM as a safety
                          buffer. Baked into M_cap_n = M_n - M_tax_n - M_theta_n.
        violation_history bool per batch: was used_mb > M_n^cap at batch start?
                          Used to compute the rolling violation rate v̄_n^SLA (§3)
    """
    node_id:           int
    capacity_mb:       float          # M_n      — physical RAM
    os_tax_mb:         float          # M_n^tax  — OS/kubelet overhead
    cpu_cores:         float = 0.0    # C_n      — total CPU cores (used in C4)
    used_mb:           float = 0.0    # U_n^mem  — recomputed each round from running jobs
    threshold_frac:    float = 0.10   # for M_n^theta = threshold_frac * M_n (default 10%)
    violation_history: list  = field(default_factory=list)  # bool per batch


# ═══════════════════════════════════════════════════════════════════════════════
# § SAMPLING HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _sample_requested_mem(rng: np.random.Generator) -> float:
    """
    Draw one job's declared memory request from a truncated normal
    distribution over [REQUEST_MEM_MIN_MB, REQUEST_MEM_MAX_MB].
    """
    lo, hi = REQUEST_MEM_MIN_MB, REQUEST_MEM_MAX_MB
    mean   = (lo + hi) / 2.0
    std    = (hi - lo) / 6.0
    return float(np.clip(rng.normal(mean, std), lo, hi))


def _sample_cpu_request(rng: np.random.Generator) -> float:
    """
    Draw one job's declared CPU request (cores) from a truncated normal
    over [REQ_CPU_MIN, REQ_CPU_MAX].
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
    Used internally by simulate_max_mem / simulate_p95_cpu to build the
    empirical usage distribution.

    Actual usage is bounded to [lower_frac * requested_mb, requested_mb].
    """
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
    """
    Simulate what the ML prediction layer would output as the maximum memory
    estimate (P̂_j^mem) for a job with the given declared request.

    Draws n_samples from the actual-usage distribution and returns the maximum
    observed value. This mirrors Google cluster-usage traces v3 `maximum_usage`
    — the largest observed memory during a measurement window.

    Using the maximum (rather than a lower percentile) is conservative: the
    scheduler plans for the worst-case memory spike the model has seen.
    An SLA violation occurs only if actual runtime memory exceeds even this
    predicted maximum (behavior outside the training distribution).
    """
    rng = rng or np.random.default_rng()
    samples = [
        _sample_actual_usage(requested_mb, lower_frac, dist, rng)
        for _ in range(n_samples)
    ]
    return float(np.max(samples))


# Keep simulate_p95 as an alias so existing test imports don't break.
def simulate_p95(
    requested_mb: float,
    lower_frac:   float = REQUEST_PER,
    dist:         str   = DIST_FLAG,
    n_samples:    int   = 200,
    rng:          Optional[np.random.Generator] = None,
) -> float:
    """Alias for simulate_max_mem — retained for backward compatibility."""
    return simulate_max_mem(requested_mb, lower_frac, dist, n_samples, rng)


def simulate_p95_cpu(
    requested_cpu: float,
    lower_frac:    float = REQUEST_PER,
    dist:          str   = DIST_FLAG,
    n_samples:     int   = 200,
    rng:           Optional[np.random.Generator] = None,
) -> float:
    """
    Simulate the P95 CPU peak estimate (P̂_j^CPU) for a job  (§3, C4).

    Draws n_samples from the actual-usage distribution and returns the
    95th percentile. Mirrors Google cluster-usage traces v3
    `tail_cpu_usage_distribution` (index 4 = 95th percentile).

    CPU overcommitment is intentionally not modelled: throttling handles
    CPU excess at runtime, so no SLA-driven capacity reduction is applied.
    """
    rng = rng or np.random.default_rng()
    samples = [
        _sample_actual_usage(requested_cpu, lower_frac, dist, rng)
        for _ in range(n_samples)
    ]
    return float(np.percentile(samples, 95))


def sample_spike_fraction(rng: np.random.Generator) -> float:
    """
    Determine whether a placed job will experience a memory spike.

    With probability (1 - SPIKE_PROB): no spike, return 0.0.
    With probability SPIKE_PROB: return a random spike fraction in
    (0, SPIKE_MAX_FRAC], meaning the job's actual memory = pred_mem × (1+frac).

    This simulates cases where a job's runtime behaviour exceeds the predicted
    maximum. The cluster manager applies this to act_mem when recording a
    RunningJob.
    """
    if rng.random() < SPIKE_PROB:
        return float(rng.uniform(0.0, SPIKE_MAX_FRAC))
    return 0.0


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
      pred_mem_mb  ← simulate_max_mem(req_mem_mb)  — simulated prediction layer output
      pred_cpu_p95 ← simulate_p95_cpu(req_cpu)     — simulated prediction layer output
    """
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
        # Initial usage: random fraction of (capacity - OS tax)
        used_mem = float(rng.uniform(0.10, 0.40)) * (cap - tax)
        nodes.append(NodeState(
            node_id        = i,
            capacity_mb    = cap,
            os_tax_mb      = tax,
            cpu_cores      = cores,
            used_mb        = round(used_mem, 2),
            threshold_frac = MEM_THRESHOLD_FRAC,
        ))
    return nodes


# ═══════════════════════════════════════════════════════════════════════════════
# § MATH-MODEL DERIVED QUANTITIES  (§3)
# All of these are called by the optimizer before each solve() call.
# ═══════════════════════════════════════════════════════════════════════════════

def compute_available_capacity(node: NodeState) -> float:
    """
    M_n^cap — node capacity for tenant jobs  (§3).

    Formula:  M_n^cap = M_n - M_n^tax - M_n^theta
              where M_n^theta = threshold_frac * M_n

    The OS tax and the memory safety threshold buffer are both subtracted as
    fixed static reservations. This is the hard ceiling for all tenant job
    memory on node n; it does not change between scheduling rounds.
    """
    m_theta = node.threshold_frac * node.capacity_mb
    return node.capacity_mb - node.os_tax_mb - m_theta


def compute_remaining_avail(node: NodeState, m_cap: float) -> float:
    """
    M_n^avail — remaining available memory  (§3).

    Formula:  M_n^avail = M_n^cap - U_n^mem

    Represents remaining capacity after subtracting current usage.
    May be negative when the node is over-committed.
    """
    return m_cap - node.used_mb


def compute_remaining_eff(r_avail: float, v_bar: float) -> float:
    """
    M_n^eff — effective remaining memory offered to new jobs  (§3).

    Formula:  M_n^eff = max(0, M_n^avail * (1 - v̄_n^SLA))

    The safety threshold buffer M_n^theta is already incorporated in M_n^cap
    (and therefore in M_n^avail), so only the SLA violation rate scales the
    remaining capacity here.

    v_bar=0 → M_eff = M_avail  (full remaining capacity)
    v_bar=1 → M_eff = 0        (node fully blocked)

    M_n^eff is the RHS of constraint C2 in the optimizer.
    """
    return max(0.0, r_avail * (1.0 - v_bar))


def compute_utilization_weight(node: NodeState) -> float:
    """
    u_n^mem — memory utilization weight ∈ [1, 2]  (§3, Appendix B).

    Formula:  u_n^mem = 1 + min(1, max(0, U_n^mem / max(1, M_n)))

    Denominator is the physical maximum M_n (not M_n^cap) so the weight
    reflects how loaded the machine is relative to its hardware ceiling.
    Jobs will never fully saturate M_n in practice (tax and threshold prevent
    it), so the weight operates in the lower portion of [1, 2] under normal
    conditions — consistent with the "maxed utilization" baseline.

    u_n^mem = 1 → node is idle (no consolidation preference)
    u_n^mem = 2 → node physically full (max consolidation; not reachable in practice)
    Applied in the objective to consolidate jobs onto memory-busier nodes.
    """
    frac = min(1.0, max(0.0, node.used_mb / max(1.0, node.capacity_mb)))
    return 1.0 + frac


def compute_violation_rate(history: list[bool], K: int = K_WINDOW) -> float:
    """
    v̄_n^SLA — rolling SLA violation rate for one node  (§3, Appendix A).

    Returns the fraction of the most recent K batches in which the node's
    actual memory usage exceeded M_n^cap.

    v̄_n = 0  → no recent violations (node is healthy)
    v̄_n = 1  → every recent batch had an overload (node is consistently stressed)
    """
    recent = history[-K:] if len(history) >= K else history
    return sum(recent) / len(recent) if recent else 0.0


def compute_node_weight(node_id: int, num_nodes: int = NUM_NODES) -> float:
    """
    σ_n^{consolid} — fixed consolidation weight for node n ∈ {1, …, NUM_NODES}.

    Returns (num_nodes - node_id), giving node 0 the highest weight and
    node NUM_NODES-1 the lowest.  Applied in the objective to bias placement
    toward lower-indexed nodes even when all u_n^mem = 1 (empty cluster).
    """
    return float(num_nodes - node_id)


def compute_omega(W_t: dict[int, float]) -> dict[int, float]:
    """
    ω_delay,t — per-tenant delay weight  (§3, Appendix C).

    Formula:  ω_delay,t = 1 + max(0, (W̄_t − W̄) / max(1, W̄))

    Both W̄_t and W̄ are computed over the last K scheduling rounds (rolling
    window), enforced by the ClusterManager which maintains a K-size deque
    of per-tenant wait times.

    Tenants whose average wait exceeds the cluster-wide mean W̄ receive
    ω_delay,t > 1; their jobs contribute more to the LP objective (§5),
    so the solver naturally prefers placing them — fairness as a side effect
    of weighted maximisation.

    ω_delay,t = 1 when W̄_t ≤ W̄ (no boost for tenants at or below average wait).
    ω_delay,t > 1 when W̄_t > W̄ (boost grows proportionally with excess wait).

    Returns an empty dict if W_t is empty (handled by the optimizer as ω=1).
    """
    if not W_t:
        return {}
    W_bar   = sum(W_t.values()) / len(W_t)
    W_denom = max(1.0, W_bar)
    return {
        t: 1.0 + max(0.0, (w - W_bar) / W_denom)
        for t, w in W_t.items()
    }
