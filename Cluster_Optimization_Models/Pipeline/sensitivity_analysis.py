"""
Pipeline/sensitivity_analysis.py
──────────────────────────────────
Sensitivity analysis for the full pipeline.

Exposes limitations and performance characteristics by sweeping:
  1. Interval frequency   — how often the Cluster Manager calls the Realtime model
                            (simulated as job generation rate vs fixed capacity)
  2. Machine capacity     — 10 vs 20 machines
  3. Tenant count         — 3 vs 5 vs 15 tenants
  4. Queue saturation     — when does the queue grow unboundedly?
  5. Exclusive fraction   — impact of exclusive tenants on shared-tenant wait times
  6. Cantelli epsilon     — safety parameter effect on resource utilisation

Solver modes
------------
  --iterative (default)   Use optimizer_iterative.solve() — batch MILP, faster at scale.
  --no-iterative          Use realtime_optimizer.solve()  — single-shot MILP baseline.

Run:
    cd Pipeline/
    python sensitivity_analysis.py                    # iterative RT (default)
    python sensitivity_analysis.py --no-iterative     # single-shot MILP
    python sensitivity_analysis.py --rt-batch-jobs 16 --rt-batch-nodes 16

Outputs:
  • Console: tabular summaries with insights
  • Graphs:  PNG plots in Pipeline/sensitivity_plots/
  • CSVs:    raw data in Pipeline/sensitivity_data/
"""

from __future__ import annotations

import sys
import os
from pathlib import Path
from dataclasses import dataclass

import numpy as np

# ── Path setup ────────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "Realtime"))
sys.path.insert(0, str(_ROOT / "PlanAhead"))

from simulation_data import (
    Job, NodeState, generate_nodes, generate_jobs,
    compute_available_capacity, compute_remaining_avail, compute_remaining_eff,
    compute_violation_rate, compute_utilization_weight, compute_node_weight,
    compute_omega, sample_spike_fraction,
    BATCH_DURATION_SEC, MIN_LIFETIME_SEC, MAX_LIFETIME_SEC,
    SPIKE_PROB, K_WINDOW,
)
import realtime_optimizer as rt_module

# ── Output directories ─────────────────────────────────────────────────────
PLOT_DIR = Path(__file__).parent / "sensitivity_plots"
DATA_DIR = Path(__file__).parent / "sensitivity_data"
PLOT_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)


# ============================================================================
# § LIGHTWEIGHT SIMULATION  (no Gurobi — realtime-only)
# ============================================================================

@dataclass
class SimResult:
    """Aggregate result for one simulation configuration."""
    label:              str
    placement_rate:     float   # fraction of generated jobs placed
    avg_queue_size:     float   # average queue depth across intervals
    avg_wait_sec:       float   # average wait time across all tenants
    queue_overflow_pct: float   # fraction of intervals where queue grew (more jobs than placed)
    avg_eff_mem_pct:    float   # average effective memory utilisation across nodes


def _run_simulation(
    n_nodes:           int,
    n_tenants:         int,
    jobs_per_interval: int,
    n_intervals:       int,
    seed:              int      = 42,
    k_window:          int      = K_WINDOW,
    solver_fn                   = None,   # if None, uses realtime_optimizer.solve
) -> SimResult:
    """
    Run a lightweight realtime-only simulation (no plan-ahead / Gurobi).
    All tenants compete for all machines — single-group per interval.
    """
    rng   = np.random.default_rng(seed)

    # Build fixed nodes (log-spaced memory between 16 GB and 64 GB)
    nodes = []
    for i in range(n_nodes):
        if n_nodes == 1:
            cap = 32_768.0
        else:
            ratio = (65_536.0 / 16_384.0) ** (i / (n_nodes - 1))
            cap   = round(16_384.0 * ratio / 1024) * 1024
        tax = round(cap * 0.05 / 1024) * 1024
        cores = max(8.0, 8.0 + i * 4.0)
        nodes.append(NodeState(
            node_id        = i,
            capacity_mb    = cap,
            os_tax_mb      = tax,
            cpu_cores      = cores,
            used_mb        = 0.0,
            threshold_frac = 0.10,
        ))

    running_jobs: list[tuple] = []    # (node_id, act_mem, end_interval)
    job_queue:    list[Job]   = []
    W_t:          dict        = {}
    wait_deques:  dict        = {t: [] for t in range(n_tenants)}

    total_generated = 0
    total_placed    = 0
    queue_sizes: list[int]   = []
    wait_times:  list[float] = []
    eff_pcts:    list[float] = []
    overflow_intervals = 0

    for interval in range(n_intervals):

        # Expire completed jobs
        running_jobs = [(nid, mem, end) for (nid, mem, end) in running_jobs
                        if end > interval]

        # Recompute node usage
        used = {n.node_id: 0.0 for n in nodes}
        for (nid, mem, _) in running_jobs:
            used[nid] += mem
        for n in nodes:
            n.used_mb = used[n.node_id]

        # Record SLA history
        for n in nodes:
            m_cap = compute_available_capacity(n)
            n.overflow_history.append(n.used_mb > m_cap)

        # Generate new jobs
        new_jobs = generate_jobs(interval, num_jobs=jobs_per_interval,
                                 num_tenants=n_tenants, rng=rng)
        for j in new_jobs:
            from datetime import datetime, timezone
            j.arrival_timestamp = datetime.now(timezone.utc)
        job_queue.extend(new_jobs)
        total_generated += len(new_jobs)

        # Solve (all jobs, all nodes — no grouping)
        _solve = solver_fn if solver_fn is not None else rt_module.solve
        if job_queue and nodes:
            placements = _solve(
                jobs          = job_queue,
                nodes         = nodes,
                W_t           = W_t,
                K             = k_window,
                time_limit_ms = 5_000,
            )
        else:
            placements = {j.job_id: None for j in job_queue}

        # Process placements
        placed_jobs = [j for j in job_queue if placements.get(j.job_id) is not None]
        unplaced    = [j for j in job_queue if placements.get(j.job_id) is None]

        for j in placed_jobs:
            nid = placements[j.job_id]
            spike = sample_spike_fraction(rng)
            act_mem = j.pred_mem_mb * (1 + spike)
            lifetime = rng.uniform(2, 8)   # intervals
            end_interval = interval + max(1, int(lifetime))
            running_jobs.append((nid, act_mem, end_interval))

            from datetime import datetime, timezone
            j.scheduling_timestamp = datetime.now(timezone.utc)
            wait_sec = 0.0
            if j.arrival_timestamp:
                wait_sec = (j.scheduling_timestamp - j.arrival_timestamp).total_seconds()
            wait_deques[j.tenant_id].append(wait_sec)
            wait_times.append(wait_sec)

        # Bump wait for unplaced
        for j in unplaced:
            wait_deques[j.tenant_id].append(float(BATCH_DURATION_SEC))

        # Update W_t
        W_t = {
            t: sum(ws[-k_window:]) / len(ws[-k_window:])
            for t, ws in wait_deques.items() if ws
        }

        # Remove placed from queue
        placed_ids = {j.job_id for j in placed_jobs}
        job_queue  = [j for j in job_queue if j.job_id not in placed_ids]
        total_placed += len(placed_jobs)

        queue_sizes.append(len(job_queue))
        if len(placed_jobs) < jobs_per_interval:
            overflow_intervals += 1

        # Eff mem %
        total_eff = []
        for n in nodes:
            m_cap = compute_available_capacity(n)
            total_eff.append((n.used_mb / max(1, m_cap)) * 100)
        eff_pcts.append(sum(total_eff) / len(total_eff))

    return SimResult(
        label              = f"N={n_nodes} T={n_tenants} J={jobs_per_interval}",
        placement_rate     = total_placed / max(1, total_generated),
        avg_queue_size     = sum(queue_sizes) / max(1, len(queue_sizes)),
        avg_wait_sec       = sum(wait_times) / max(1, len(wait_times)),
        queue_overflow_pct = overflow_intervals / max(1, n_intervals) * 100,
        avg_eff_mem_pct    = sum(eff_pcts) / max(1, len(eff_pcts)),
    )


# ============================================================================
# § SWEEP 1: Interval Frequency
# ============================================================================

def sweep_interval_frequency(solver_fn=None) -> list[SimResult]:
    """
    Sweep 1: Interval frequency — jobs per interval (equivalent to calling
    the scheduler faster vs slower with proportional workload).

    Fixed: 10 nodes, 5 tenants, 50 intervals.
    Sweep: jobs_per_interval ∈ {5, 10, 20, 40, 80}

    Insight: At low jobs/interval the queue drains quickly. As jobs/interval
    increases beyond cluster capacity, the queue grows without bound.
    This reveals the saturation point.
    """
    jobs_options = [5, 10, 20, 40, 80]
    results = []
    for jpi in jobs_options:
        r = _run_simulation(n_nodes=10, n_tenants=5, jobs_per_interval=jpi,
                            n_intervals=50, solver_fn=solver_fn)
        results.append(r)
    return results


# ============================================================================
# § SWEEP 2: Machine Capacity
# ============================================================================

def sweep_machine_capacity(solver_fn=None) -> list[SimResult]:
    """
    Sweep 2: Machine count — 5 vs 10 vs 20 vs 30 machines.

    Fixed: 5 tenants, 15 jobs/interval, 50 intervals.
    Sweep: n_nodes ∈ {5, 10, 20, 30}

    Insight: Beyond a certain machine count, additional machines add
    no throughput (queue already drains at lower N). Identifies the
    minimum viable cluster size for this workload.
    """
    node_options = [5, 10, 20, 30]
    results = []
    for n in node_options:
        r = _run_simulation(n_nodes=n, n_tenants=5, jobs_per_interval=15,
                            n_intervals=50, solver_fn=solver_fn)
        results.append(r)
    return results


# ============================================================================
# § SWEEP 3: Tenant Count
# ============================================================================

def sweep_tenant_count(solver_fn=None) -> list[SimResult]:
    """
    Sweep 3: Tenant count — 3 vs 5 vs 10 vs 15 vs 20 tenants.

    Fixed: 10 nodes, 15 jobs/interval, 50 intervals.
    Sweep: n_tenants ∈ {3, 5, 10, 15, 20}

    Insight: More tenants → more fairness weight diversity → potential
    starvation for tenants whose W_t is consistently low. Also increases
    MILP problem size.
    """
    tenant_options = [3, 5, 10, 15, 20]
    results = []
    for t in tenant_options:
        r = _run_simulation(n_nodes=10, n_tenants=t, jobs_per_interval=15,
                            n_intervals=50, solver_fn=solver_fn)
        results.append(r)
    return results


# ============================================================================
# § SWEEP 4: Queue Saturation Cross-Section
# ============================================================================

def sweep_saturation_grid(solver_fn=None) -> list[SimResult]:
    """
    Sweep 4: Cross-sweep of (n_nodes, jobs_per_interval) to map the
    saturation boundary — where queue_overflow_pct transitions from 0% to 100%.

    Insight: Reveals the operating envelope. Above the saturation boundary,
    the queue grows unboundedly and SLA violations increase.
    """
    grid = [
        (5, 5), (5, 10), (5, 20), (5, 40),
        (10, 5), (10, 10), (10, 20), (10, 40),
        (20, 5), (20, 10), (20, 20), (20, 40),
    ]
    results = []
    for (n, jpi) in grid:
        r = _run_simulation(n_nodes=n, n_tenants=5, jobs_per_interval=jpi,
                            n_intervals=30, solver_fn=solver_fn)
        results.append(r)
    return results


# ============================================================================
# § REPORTING + GRAPHING
# ============================================================================

def _print_table(title: str, headers: list[str], rows: list[list]) -> None:
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")
    col_w = max(14, max(len(h) for h in headers))
    header_line = "  " + "  ".join(h.ljust(col_w) for h in headers)
    print(header_line)
    print("  " + "-" * (col_w * len(headers) + 2 * len(headers)))
    for row in rows:
        line = "  " + "  ".join(str(v).ljust(col_w) for v in row)
        print(line)


def _save_csv(filename: str, headers: list[str], rows: list[list]) -> None:
    path = DATA_DIR / filename
    with open(path, "w", encoding="utf-8") as f:
        f.write(",".join(headers) + "\n")
        for row in rows:
            f.write(",".join(str(v) for v in row) + "\n")
    print(f"  Saved: {path}")


def _try_plot(plot_fn) -> None:
    try:
        import matplotlib.pyplot as plt
        import matplotlib
        matplotlib.use("Agg")
        plot_fn(plt)
    except ImportError:
        print("  (matplotlib not installed — skipping plots; data CSVs saved)")


def run_all(solver_fn=None) -> None:
    print("\nRunning pipeline sensitivity analysis...")
    print("This may take a few minutes.\n")

    # ── Sweep 1: Interval Frequency ────────────────────────────────────────
    print("Sweep 1: Interval frequency (jobs per interval)...")
    s1 = sweep_interval_frequency(solver_fn=solver_fn)
    headers1 = ["Jobs/interval", "PlaceRate%", "AvgQueue", "AvgWait(s)", "QueueOverflow%", "EffMem%"]
    rows1 = [
        [5*(i+1), f"{r.placement_rate*100:.1f}", f"{r.avg_queue_size:.1f}",
         f"{r.avg_wait_sec:.1f}", f"{r.queue_overflow_pct:.1f}", f"{r.avg_eff_mem_pct:.1f}"]
        for i, r in enumerate(s1)
    ]
    _print_table("SWEEP 1: Interval Frequency", headers1, rows1)

    # Insight
    saturation_idx = next((i for i, r in enumerate(s1) if r.queue_overflow_pct > 50), len(s1)-1)
    print(f"\n  INSIGHT: Queue starts saturating at "
          f"{[5, 10, 20, 40, 80][saturation_idx]} jobs/interval "
          f"(queue overflow > 50%).")
    print(f"  Below this threshold, the realtime model drains the queue each interval.")
    print(f"  Above it, the queue grows without bound — a sign the cluster is undersized.")

    _save_csv("sweep1_frequency.csv", headers1,
              [[r.label, r.placement_rate, r.avg_queue_size,
                r.avg_wait_sec, r.queue_overflow_pct, r.avg_eff_mem_pct]
               for r in s1])

    def plot1(plt):
        x = [5, 10, 20, 40, 80]
        fig, axes = plt.subplots(1, 3, figsize=(15, 4))
        axes[0].plot(x, [r.placement_rate * 100 for r in s1], "o-b")
        axes[0].set_title("Placement Rate vs Interval Frequency")
        axes[0].set_xlabel("Jobs per interval")
        axes[0].set_ylabel("Placement rate (%)")
        axes[0].set_ylim(0, 105)
        axes[0].grid(True)

        axes[1].plot(x, [r.avg_queue_size for r in s1], "o-r")
        axes[1].set_title("Avg Queue Depth vs Interval Frequency")
        axes[1].set_xlabel("Jobs per interval")
        axes[1].set_ylabel("Avg queue size")
        axes[1].grid(True)

        axes[2].plot(x, [r.avg_eff_mem_pct for r in s1], "o-g")
        axes[2].set_title("Effective Memory Utilisation")
        axes[2].set_xlabel("Jobs per interval")
        axes[2].set_ylabel("Avg eff mem %")
        axes[2].grid(True)

        fig.tight_layout()
        path = PLOT_DIR / "sweep1_interval_frequency.png"
        fig.savefig(path, dpi=100)
        plt.close(fig)
        print(f"  Saved plot: {path}")

    _try_plot(plot1)

    # ── Sweep 2: Machine Capacity ──────────────────────────────────────────
    print("\nSweep 2: Machine capacity (number of nodes)...")
    s2 = sweep_machine_capacity(solver_fn=solver_fn)
    headers2 = ["Nodes", "PlaceRate%", "AvgQueue", "AvgWait(s)", "QueueOverflow%", "EffMem%"]
    rows2 = [
        [n, f"{r.placement_rate*100:.1f}", f"{r.avg_queue_size:.1f}",
         f"{r.avg_wait_sec:.1f}", f"{r.queue_overflow_pct:.1f}", f"{r.avg_eff_mem_pct:.1f}"]
        for n, r in zip([5, 10, 20, 30], s2)
    ]
    _print_table("SWEEP 2: Machine Capacity", headers2, rows2)

    knee_idx = next((i for i, r in enumerate(s2) if r.placement_rate > 0.99), len(s2)-1)
    print(f"\n  INSIGHT: Placement rate reaches >99% at "
          f"{[5, 10, 20, 30][knee_idx]} nodes.")
    print(f"  Beyond this point, additional machines do not improve throughput.")
    print(f"  Effective memory utilisation DECREASES with more machines (same workload spread wider).")

    _save_csv("sweep2_capacity.csv", headers2,
              [[r.label, r.placement_rate, r.avg_queue_size,
                r.avg_wait_sec, r.queue_overflow_pct, r.avg_eff_mem_pct]
               for r in s2])

    def plot2(plt):
        x = [5, 10, 20, 30]
        fig, axes = plt.subplots(1, 2, figsize=(12, 4))
        axes[0].plot(x, [r.placement_rate * 100 for r in s2], "o-b")
        axes[0].set_title("Placement Rate vs Machine Count")
        axes[0].set_xlabel("Number of machines")
        axes[0].set_ylabel("Placement rate (%)")
        axes[0].set_ylim(0, 105)
        axes[0].grid(True)

        axes[1].plot(x, [r.avg_eff_mem_pct for r in s2], "o-g")
        axes[1].set_title("Effective Memory Utilisation vs Machine Count")
        axes[1].set_xlabel("Number of machines")
        axes[1].set_ylabel("Avg eff mem %")
        axes[1].grid(True)

        fig.tight_layout()
        path = PLOT_DIR / "sweep2_machine_capacity.png"
        fig.savefig(path, dpi=100)
        plt.close(fig)
        print(f"  Saved plot: {path}")

    _try_plot(plot2)

    # ── Sweep 3: Tenant Count ──────────────────────────────────────────────
    print("\nSweep 3: Tenant count...")
    s3 = sweep_tenant_count(solver_fn=solver_fn)
    headers3 = ["Tenants", "PlaceRate%", "AvgQueue", "AvgWait(s)", "QueueOverflow%", "EffMem%"]
    rows3 = [
        [t, f"{r.placement_rate*100:.1f}", f"{r.avg_queue_size:.1f}",
         f"{r.avg_wait_sec:.1f}", f"{r.queue_overflow_pct:.1f}", f"{r.avg_eff_mem_pct:.1f}"]
        for t, r in zip([3, 5, 10, 15, 20], s3)
    ]
    _print_table("SWEEP 3: Tenant Count", headers3, rows3)

    print(f"\n  INSIGHT: More tenants → higher average wait time due to fairness weight")
    print(f"  competition. With many tenants, low-frequency tenants may experience starvation")
    print(f"  if their W_t is consistently lower than others.")
    print(f"  The MILP problem size grows as O(|J|·|N|) — more tenants = more binary variables.")

    _save_csv("sweep3_tenants.csv", headers3,
              [[r.label, r.placement_rate, r.avg_queue_size,
                r.avg_wait_sec, r.queue_overflow_pct, r.avg_eff_mem_pct]
               for r in s3])

    def plot3(plt):
        x = [3, 5, 10, 15, 20]
        fig, axes = plt.subplots(1, 2, figsize=(12, 4))
        axes[0].plot(x, [r.avg_wait_sec for r in s3], "o-r")
        axes[0].set_title("Avg Wait Time vs Tenant Count")
        axes[0].set_xlabel("Number of tenants")
        axes[0].set_ylabel("Avg wait (seconds)")
        axes[0].grid(True)

        axes[1].plot(x, [r.placement_rate * 100 for r in s3], "o-b")
        axes[1].set_title("Placement Rate vs Tenant Count")
        axes[1].set_xlabel("Number of tenants")
        axes[1].set_ylabel("Placement rate (%)")
        axes[1].set_ylim(0, 105)
        axes[1].grid(True)

        fig.tight_layout()
        path = PLOT_DIR / "sweep3_tenant_count.png"
        fig.savefig(path, dpi=100)
        plt.close(fig)
        print(f"  Saved plot: {path}")

    _try_plot(plot3)

    # ── Sweep 4: Saturation Grid ───────────────────────────────────────────
    print("\nSweep 4: Saturation grid (nodes × jobs/interval)...")
    s4 = sweep_saturation_grid(solver_fn=solver_fn)
    print("\n  Queue overflow % — rows=nodes, cols=jobs/interval")
    nodes_list = [5, 10, 20]
    jpi_list   = [5, 10, 20, 40]
    print("  " + " " * 10 + "  ".join(f"J={j:>4}" for j in jpi_list))
    for n in nodes_list:
        row_vals = []
        for jpi in jpi_list:
            r = next(x for x in s4 if f"N={n}" in x.label and f"J={jpi}" in x.label)
            row_vals.append(f"{r.queue_overflow_pct:>6.1f}%")
        print(f"  N={n:>3}:  {'  '.join(row_vals)}")

    print(f"\n  INSIGHT: The diagonal from top-left (low jobs, many nodes) to bottom-right")
    print(f"  (many jobs, few nodes) marks the saturation boundary of the pipeline.")
    print(f"  Any configuration in the bottom-right quadrant will accumulate an unbounded queue.")

    _save_csv("sweep4_saturation.csv",
              ["n_nodes", "jobs_per_interval", "placement_rate", "queue_overflow_pct", "avg_wait"],
              [[r.label.split()[0].split("=")[1], r.label.split()[2].split("=")[1],
                round(r.placement_rate, 3), round(r.queue_overflow_pct, 1),
                round(r.avg_wait_sec, 1)]
               for r in s4])

    def plot4(plt):
        import matplotlib.patches as mpatches
        fig, ax = plt.subplots(figsize=(8, 6))
        node_vals = [5, 10, 20]
        jpi_vals  = [5, 10, 20, 40]

        for ni, n in enumerate(node_vals):
            for ji, jpi in enumerate(jpi_vals):
                r = next(x for x in s4 if f"N={n}" in x.label and f"J={jpi}" in x.label)
                color = "green" if r.queue_overflow_pct < 20 else \
                        "orange" if r.queue_overflow_pct < 60 else "red"
                ax.scatter(jpi, n, s=400, c=color, alpha=0.8, edgecolors="black")
                ax.text(jpi, n, f"{r.queue_overflow_pct:.0f}%",
                        ha="center", va="center", fontsize=9, color="white", fontweight="bold")

        ax.set_xlabel("Jobs per interval")
        ax.set_ylabel("Number of machines")
        ax.set_title("Queue Overflow % — Saturation Map\n(green=safe, orange=borderline, red=saturated)")
        ax.set_xticks(jpi_vals)
        ax.set_yticks(node_vals)
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        path = PLOT_DIR / "sweep4_saturation_grid.png"
        fig.savefig(path, dpi=100)
        plt.close(fig)
        print(f"  Saved plot: {path}")

    _try_plot(plot4)

    print(f"\n{'='*70}")
    print(f"  Sensitivity analysis complete.")
    print(f"  Data CSVs: {DATA_DIR}")
    print(f"  Plots:     {PLOT_DIR}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    import argparse as _ap
    _parser = _ap.ArgumentParser(
        description="Pipeline sensitivity analysis — RT solver sweep",
        formatter_class=_ap.ArgumentDefaultsHelpFormatter,
    )
    _parser.add_argument("--iterative", default=True, action=_ap.BooleanOptionalAction,
                         help="Use iterative RT solver (default: True)")
    _parser.add_argument("--rt-batch-jobs",  type=int, default=32,
                         help="Jobs per sub-MILP — iterative RT only")
    _parser.add_argument("--rt-batch-nodes", type=int, default=32,
                         help="Nodes per sub-MILP — iterative RT only")
    _args = _parser.parse_args()

    _solver_fn = None
    if _args.iterative:
        import optimizer_iterative as _oi
        _bj, _bn = _args.rt_batch_jobs, _args.rt_batch_nodes
        _solver_fn = lambda jobs, nodes, W_t, K, time_limit_ms=5_000: _oi.solve(
            jobs, nodes, W_t, K, time_limit_ms, batch_jobs=_bj, batch_nodes=_bn,
        )
        print(f"RT solver : iterative (batch={_bj}×{_bn})")
    else:
        print("RT solver : regular (single-shot MILP)")

    run_all(solver_fn=_solver_fn)
