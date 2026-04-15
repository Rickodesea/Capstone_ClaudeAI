# Potential Thresholds and Assumptions
## Live Migration vs. Relaunch Decision Framework

**Last Updated:** 2026-04-15  
**Based on Research Papers:**
- Hao et al. (2021) — *An Empirical Analysis of VM Startup Times in Public IaaS Clouds*
- Wu & Zhao — *Performance Modeling of Virtual Machine Live Migration*
- Mao & Humphrey (2012) — *A Performance Study on the VM Startup Time in the Cloud*

---

## 1. Background & Decision Problem

In a memory-overcommitted cluster, when DRAM pressure exceeds capacity two reactive strategies exist:

| Strategy | Description | Key Cost |
|---|---|---|
| **Live Migration** | Move a running VM to another physical host with minimal pause | QoS latency spike during stop-and-copy phase (~seconds); resource overhead during transfer |
| **Relaunch (Terminate + Restart)** | Kill the VM and restart it on a less-loaded node | Application is fully unavailable for 34–124 seconds (Linux cold/warm start); potential SLA violation |

The central questions are: **when to trigger each**, **using what threshold**, and **for what VM types**.

---

## 2. Empirical Data from Research Papers

### 2.1 VM Startup Times (Relaunch Cost)

#### Cold Start (New VM provisioning — uncached image)

| Provider / VM Size | Average Cold Start | 90th Percentile |
|---|---|---|
| AWS Linux (all types, modern) | **55.9 s** | ~158 s (t2 types) |
| AWS Linux t3/m5 (current gen.) | ~36–55 s | ~70–90 s |
| AWS Linux t2 (older gen.) | **85.15 s** | **158.22 s** |
| AWS Windows (all types) | 89.7 s | ~130–180 s |
| GCP Linux (uncached) | **124.1 s** | ~200–300 s |
| GCP Linux (cached image) | ~50 s (~60% faster) | ~80 s |
| EC2 Linux (Mao 2012) | 96.9 s | — |
| EC2 Windows (Mao 2012) | **810.2 s** | — |
| Rackspace Linux (Mao 2012) | 44.2 s | — |

#### Warm Start (Existing stopped VM restarted — image already on host)

| Provider | Average Warm Start |
|---|---|
| AWS Linux | **34.0 s** |
| AWS Windows | 24.5 s |
| GCP Linux | 32.8 s |
| GCP Windows | 22.2 s |

**Key takeaway:** Warm start is ~38–62% faster than cold start and is stable across VM types. This is relevant if the VM image is already on the destination host (i.e., if we pre-stage the image).

#### VM Release Time (Termination)
- EC2: 3–8 seconds
- Azure: slightly longer

### 2.2 Live Migration Times

From Wu & Zhao (Xen-based, 1GB memory VM):

| Workload Type | Dom0 CPU @ 10% | Dom0 CPU @ 50%+ | Stabilizes at |
|---|---|---|---|
| CPU-intensive | ~100 s | **~17–20 s** | 50% CPU |
| Memory read-intensive | ~20 s | **~20 s** | (stable) |
| Memory write-intensive | **Very variable** | ~30–40 s+ | 30% CPU cap on DomU triggers immediate stop-and-copy |
| Disk I/O-intensive | ~100 s | **~20 s** | 50% CPU |
| Network send-intensive | ~60 s | **~25–30 s** | 60% CPU |
| Network receive-intensive | ~60 s | **~25–30 s** | 60% CPU |

**Key takeaway:** With adequate CPU resources (≥50% Dom0 allocation), most live migrations complete in **17–30 seconds**. Without adequate CPU, migration time spikes to 60–100 seconds — longer than a warm restart.

**Migration performance model accuracy (R²):**
- CPU-intensive: 94–96%
- Memory read: 96%
- Memory write: 97%
- Disk I/O: 98%
- Network I/O: ~96%

Models are reliable enough to use for predictive threshold decisions.

### 2.3 VM Image Caching

From Hao et al. (2021) on GCP:
- Images are cached in a data center for **70–100 minutes** after last use
- Cached cold start: ~50s (Linux), vs ~124s uncached
- Cache is **zone-specific** — different zones in the same region do NOT share cache
- AWS shows near-constant startup regardless of image size (internal caching/optimization implied)

**Critical implication:** If we can pre-stage (cache) a VM image on the target host before migration, cold start becomes equivalent to warm start (~34s). This is the foundation of the **predictive caching strategy** discussed in Section 6.

---

## 3. Should a Single Threshold (Mean/Median) Make Sense?

**Answer: No — a single threshold is insufficient.**

### Reasons:

**a) VM startup times vary by VM type by up to 4x**
- t2.nano (smallest): 85.15s avg, 158.22s 90th percentile
- t3/m5 (modern, current gen): ~36–55s
- Using a single threshold treats migrating a t2.nano the same as an m5.xlarge, ignoring that the former will be unavailable 3x longer if terminated

**b) Memory-write-intensive VMs are dramatically harder to migrate**
- Memory-write VMs cause continuous dirty page writes, forcing iterative pre-copy rounds
- At sufficient DomU intensity, Xen triggers immediate stop-and-copy (full pause), causing longer downtime
- A threshold that works for a read-only database VM does not work for an in-memory write-heavy workload

**c) Mean vs. Median: the distribution is right-skewed**
- VM startup times show multi-modal distributions with "straggler" outliers (Fig. 2 in Hao et al.)
- The mean is pulled up by long-tail stragglers
- **The 90th percentile is the safer planning reference** for SLA calculations
- For decision thresholds, use the **median** as the expected cost and the **90th percentile** as the worst-case buffer

### Recommendation: Two-Tier VM Classification

| Tier | VM Memory Size | Expected Cold Start | Expected Live Migration | Threshold Behavior |
|---|---|---|---|---|
| **Small** | < 4 GB RAM | 34–55 s | 17–30 s | Tighter migration threshold (faster, cheaper) |
| **Large** | ≥ 4 GB RAM | 55–124 s | 20–60 s (memory-dependent) | Looser threshold; prioritize predictive caching |

> For the **capstone simulation**, starting with two tiers is tractable. A more granular per-instance-family model is a natural extension.

---

## 4. Should Thresholds Be VM-Type Specific?

**Answer: Yes, at minimum by memory size class.**

Evidence from Hao et al.:
- t2 vs t3 instances: **53% startup time difference** at same memory class
- f1-micro vs n2-standard: **up to 42% startup time difference** in GCP
- Zone location within same region: **up to 50% variation**

For the capstone, the following classification is recommended:

| VM Class | vCPUs | Memory | Expected Warm Start | Notes |
|---|---|---|---|---|
| Tiny | 0.5–1 | 0.5–1 GB | 34 s | Most unstable; avoid for SLA-critical workloads |
| Small | 1–2 | 1.7–2 GB | 34 s | Baseline behavior |
| Medium | 1–2 | 3.75–4 GB | 34–38 s | Current gen instances very stable |
| Large | 2 | 7.5–8 GB | 38–45 s | Migration time increases with dirty memory |
| XLarge | 4 | 15–16 GB | 45–55 s | Highest migration cost; cache pre-staging essential |

---

## 5. Linux-Only Assumption for Capstone

**Recommendation: Yes — restrict to Linux VMs for the capstone.**

### Justification:

| Factor | Linux | Windows |
|---|---|---|
| Cold start time | **55.9 s** (AWS) | **89.7 s** (AWS), **810 s** (EC2, 2012) |
| Startup variability | Moderate | High |
| Migration complexity | Well-studied (Xen, KVM) | More complex (larger memory footprint) |
| Industry deployment | Dominant for cloud infrastructure | Less common for backend VMs |
| Research literature | Extensive | Limited migration modeling |

**Windows VMs are 9× slower to cold-start** (Mao 2012) and have image sizes that are 3–5× larger, meaning migration traffic is substantially higher. They also do not benefit from the same caching acceleration.

**Assumption Statement:**
> All VM workloads in this capstone are assumed to run Linux-based OS images (Ubuntu/Debian). Windows VMs are excluded from scope. This is consistent with the majority of IaaS cloud deployments where Linux dominates backend infrastructure.

---

## 6. Should Live Migration and Termination Be Separated?

**Answer: Yes — they are fundamentally different operations and require different triggers.**

### Separation Rationale:

| Dimension | Live Migration | Terminate + Relaunch |
|---|---|---|
| **Continuity** | VM keeps running during transfer (only paused at stop-and-copy) | Full application downtime |
| **Duration** | 17–30 s (with resources) | 34–55 s warm, 55–124 s cold |
| **Resource cost** | High CPU + network bandwidth on both hosts during transfer | Low (simple kill; restart cost on destination) |
| **When to use** | When a suitable destination host is available; before memory crisis | When no migration path exists or migration would exceed SLA window |
| **SLA impact** | QoS degradation (latency spike, seconds) — may NOT formally violate SLA | Application unavailability — likely violates SLA |
| **State preservation** | Full state preserved | State lost (unless application-level checkpointing) |

### Two-Threshold Architecture:

```
Memory Utilization (% of committed capacity)
|
|--- 0%    → Normal operation
|
|--- 75%   → WARNING: Start predictive caching on target host
|             Begin monitoring for spike
|
|--- 82%   → MIGRATION THRESHOLD: Trigger live migration of
|             lowest-priority VM (if cached image on target)
|             OR trigger live migration with parallel pre-staging
|
|--- 92%   → CRITICAL THRESHOLD: Trigger forced relaunch of
|             lowest-priority VM if migration cannot complete
|             in time (or no migration host available)
|
|--- 98%   → EMERGENCY: Terminate immediately; SLA violation
|             accepted for lowest-priority tenant
```

---

## 7. SLA vs. QoS: Does Live Migration Violate SLA?

**Answer: Generally no — but it depends on the SLA definition.**

### Two Distinct Performance Metrics (from Wu & Zhao):

| Metric | Definition | Typical Value (adequate resources) |
|---|---|---|
| **Down time** | Duration VM is completely stopped (stop-and-copy phase) | **Seconds** (typically 1–10 s) |
| **Migration time** | Total elapsed time from start to VM active on destination | **17–30 s** (application degraded) |

**Down time** is the SLA-relevant metric. For most SLAs:
- If SLA defines availability as "< 10 seconds of unplanned downtime per migration event," live migration typically complies
- If SLA defines response time (e.g., < 200ms latency), the migration period causes degradation but not formal downtime

### QoS Impact During Migration:
- Application in the migrating VM perceives increased latency during the pre-copy phase
- Memory-read-heavy VMs: minimal QoS impact (~20s migration, near-constant performance)
- Memory-write-heavy VMs: QoS degrades proportionally to dirty page rate
- CPU-intensive VMs: ~17–20s at adequate CPU, manageable degradation

**Capstone Assumption:**
> Live migration is classified as a **QoS event** (latency degradation, tracked as a metric) rather than an **SLA violation event**. Only full termination or relaunch constitutes a potential SLA violation. QoS events are tracked separately as a secondary optimization objective.

---

## 8. The Uncached Image Problem: Does Migration Still Cause SLA Violation?

**Problem Statement:**
If the VM image is NOT pre-cached on the destination host, the "live migration" effectively becomes a cold start on arrival, meaning the destination host must pull the image from the image repository. For GCP, this adds **~74 seconds** (124s uncached vs 50s cached) to the process.

### Scenarios:

| Scenario | Image Status | Expected Total Time | SLA Risk |
|---|---|---|---|
| Live migration + image cached | Cached on destination | **17–30 s migration + near-instant resume** | Low |
| Live migration + image uncached | Must be fetched | **17–30 s migration + 50–124 s image pull** | **High** |
| Relaunch + warm start (image staged) | Already on destination | **34 s** | Medium |
| Relaunch + cold start (uncached) | Must be fetched | **55–124 s** | High |

### Key Insight:
**"Live migration" where the image is not pre-cached on the destination is functionally as disruptive as a cold relaunch.**

The image pull adds 50–124 seconds of delay — this is not the "live" pause (seconds) but rather the application's startup time after the VM arrives at the destination. From the user's perspective, the application is effectively unavailable for minutes.

**GCP cache period:** 70–100 minutes. If the target zone has not used the image recently, cache is cold.

---

## 9. Predictive Caching Strategy

This is the recommended mitigation for the uncached image problem.

### Strategy Design:

```
T-0:     Spike detection / prediction begins
         (Memory utilization trending upward / ML prediction signals)

T-10min: WARNING threshold reached (75% memory utilization)
         → Action: Pre-stage VM image to candidate destination host
         → Destination host fetches and caches image (~50–124 s to cache)
         → Cost: Background network I/O; low urgency

T-2min:  Spike still predicted and trajectory confirmed
         → Action: Initiate live migration with image ALREADY cached
         → Expected migration time: 17–30 s
         → Result: Application paused for seconds only

T-0:     If spike dissipates → cancel migration (no cost incurred)
         If spike arrives without pre-staging → fall back to
         relaunch or emergency termination
```

### Cache Window Requirement:
- GCP caches images for 70–100 minutes per zone
- AWS: near-constant startup times suggest internal caching already occurs
- Minimum lead time for effective pre-staging: **~2–5 minutes** (image transfer + cache)
- For larger VM images (128–256 GB), transfer time may be 5–10+ minutes at 10.9 MB/s (EC2 internal transfer rate)

### Is It Worth It?

| Condition | Pre-staging worthwhile? | Reasoning |
|---|---|---|
| Spike predicted ≥ 10 min ahead | **Yes** | Time to cache image; migration will be fast |
| Spike predicted 2–10 min ahead | **Conditional** | Depends on image size and network speed; may partially cache |
| Spike predicted < 2 min ahead | **No** (for caching) | Insufficient time; consider immediate migration or relaunch |
| Spike dissipates frequently | **Analyze cost** | Unnecessary image transfers consume network bandwidth; use confidence threshold |

**Recommendation:** Only trigger pre-staging when spike prediction confidence exceeds a threshold (e.g., 70–80% confidence) to avoid wasted bandwidth from false positives.

---

## 10. Recommended Threshold Values for Capstone

### Memory Utilization Thresholds (Linux VMs, single node)

| Level | Utilization | Action | Condition |
|---|---|---|---|
| **Baseline** | < 75% | No action | Normal operation |
| **Early Warning** | 75–80% | Trigger predictive caching on target host for lowest-priority VM | Spike trending upward for ≥ 2 consecutive samples |
| **Migration Trigger** | 80–90% | Initiate live migration of lowest-priority VM | Image cached on target (≥70% confidence of cache hit) |
| **Force Relaunch** | 90–95% | Terminate and relaunch lowest-priority VM | Migration cannot complete within SLA window, OR no suitable migration host |
| **Emergency** | > 95% | Immediate termination of lowest-priority VM | SLA violation accepted; prevent OOM crash |

### Migration Decision Logic (per VM)

```
if memory_utilization >= 80%:
    target = find_least_loaded_host()
    if image_is_cached(vm, target):
        live_migrate(vm, target)         # 17–30 s, QoS event
    elif prediction_lead_time >= 10min:
        pre_stage_image(vm, target)      # trigger background cache
        defer_migration(vm, target)      # will migrate once cached
    else:
        relaunch(vm, target)             # 34–55 s, SLA risk

if memory_utilization >= 92%:
    terminate_lowest_priority(vm)        # immediate relief
    relaunch_on_best_host(vm)
```

### VM Type-Specific Adjustment Factors

| VM Class | Memory | Migration Time Multiplier | Startup Time Multiplier |
|---|---|---|---|
| Tiny (≤1 GB) | < 1 GB | 1.0× (baseline) | 1.5× (less stable) |
| Small (2 GB) | 2 GB | 1.0× | 1.0× |
| Medium (4 GB) | 4 GB | 1.2× | 1.0× |
| Large (8 GB) | 8 GB | 1.5× | 1.1× |
| XLarge (16 GB) | 16 GB | 2.0× | 1.2× |

*Multipliers based on memory scaling of migration time and startup time variance observed in the papers.*

---

## 11. Summary of Assumptions

| # | Assumption | Justification |
|---|---|---|
| A1 | **Linux VMs only** | Windows startup is 9–14× slower; insufficient migration modeling in literature for capstone scope |
| A2 | **Live migration and relaunch are separate operations with separate thresholds** | Different time costs, SLA impacts, and resource requirements |
| A3 | **Live migration is a QoS event, not an SLA violation** | Down time is seconds; full availability loss only occurs during relaunch |
| A4 | **VM-type-specific thresholds are preferred over a single mean/median** | Up to 4× startup time variation across VM types; memory write intensity changes migration cost dramatically |
| A5 | **If 90th percentile threshold used for planning, use median for expected cost** | Startup distributions are right-skewed with straggler outliers |
| A6 | **Predictive caching is required for live migration to be low-cost** | Uncached cold start adds 50–124 s; equivalent to relaunch in disruption |
| A7 | **Minimum lead time for effective pre-staging is 2–10 minutes** | Depends on image size (32–256 GB) and internal network speed (~10.9 MB/s for EC2) |
| A8 | **Pre-staging should only trigger above 70–80% prediction confidence** | False positives consume unnecessary network bandwidth |
| A9 | **Dom0 CPU allocation ≥ 50% required for fast live migration (17–30 s)** | Below 50%, migration time spikes to 60–100 s (Wu & Zhao) |
| A10 | **Memory-write-intensive VMs are candidates for relaunch over migration** | High dirty page rate prolongs migration unpredictably; may exceed SLA window |

---

## 12. Open Questions for Capstone Design

1. **Workload characterization:** Can we classify VMs as CPU-intensive, memory-read, memory-write, disk I/O, or network I/O at runtime? This determines whether live migration or relaunch is the better response.

2. **Spike prediction horizon:** How far in advance can the memory spike predictor give a reliable signal? If < 2 minutes, pre-staging is rarely viable and relaunch becomes the default.

3. **Multi-VM scenarios:** Wu & Zhao note that parallel migrations interfere — migration time increases when multiple VMs migrate simultaneously. The threshold model should account for the number of concurrent migrations.

4. **SLA definition precision:** The capstone needs a formal SLA definition. Is the SLA clause based on:
   - Maximum downtime per event (e.g., < 60 seconds)?
   - Maximum total downtime per month?
   - Application-level response time (e.g., < 500ms p99)?
   
   The answer directly determines whether live migration "violates" SLA or is merely a QoS event.

5. **Image size constraint:** If the capstone simulates VMs with large images (128–256 GB), pre-staging becomes expensive (11–23 minutes at 10.9 MB/s). Should image size be bounded in the simulation, or should image size be a simulation variable?

---

*Document based on empirical data from three research papers. Threshold values are informed estimates for simulation; production deployment would require empirical calibration on target hardware.*
