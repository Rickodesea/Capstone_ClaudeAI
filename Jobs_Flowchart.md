# Multi-Tenant Job Scheduler — Design Diagrams

**Project:** Predictive Multi-Tenant Job Scheduling in Kubernetes  
**Goal:** Maximize cluster utilization while preserving fairness and SLA compliance  
**Core principle:** Prediction is the oracle. Optimization is the decision-maker. Prediction feeds optimization.

---

## Diagram 1 — System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        MULTI-TENANT KUBERNETES CLUSTER                      │
│                                                                             │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌─────────────┐ │
│  │   Tenant A   │   │   Tenant B   │   │   Tenant C   │   │  Tenant ...  │ │
│  │  (ML Team)   │   │ (Batch ETL)  │   │ (Web APIs)   │   │             │ │
│  └──────┬───────┘   └──────┬───────┘   └──────┬───────┘   └──────┬──────┘ │
│         │                  │                  │                   │        │
│         └──────────────────┴──────────────────┴───────────────────┘        │
│                                    │ Job Submission (resource request YAML) │
│                                    ▼                                        │
│         ┌─────────────────────────────────────────────────────┐            │
│         │              ADMISSION WEBHOOK                       │            │
│         │   (First gate: predict → decide admit/queue/reject)  │            │
│         └───────────────────────┬─────────────────────────────┘            │
│                                 │                                           │
│              ┌──────────────────┼──────────────────┐                       │
│              │                  │                  │                        │
│         ┌────▼────┐      ┌──────▼──────┐    ┌─────▼──────┐                │
│         │ ADMIT   │      │    GATE     │    │   REJECT   │                 │
│         │ (now)   │      │ (schedule   │    │  (queue or │                 │
│         └────┬────┘      │  for T+Δt)  │    │  deny)     │                 │
│              │           └──────┬──────┘    └────────────┘                 │
│              │                  │                                           │
│              └──────────────────┘                                           │
│                        │ Pod released to scheduler                          │
│                        ▼                                                    │
│         ┌─────────────────────────────────────────────────────┐            │
│         │           CUSTOM SCHEDULER PLUGIN                    │            │
│         │  PreFilter → Filter → Score → Reserve → Bind        │            │
│         └───────────────────────┬─────────────────────────────┘            │
│                                 │ Pod assigned to node                      │
│                                 ▼                                           │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                         WORKER NODES                                 │  │
│  │  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐         │  │
│  │  │  Node 1  │   │  Node 2  │   │  Node 3  │   │  Node N  │         │  │
│  │  │ kubelet  │   │ kubelet  │   │ kubelet  │   │ kubelet  │         │  │
│  │  │ cgroups  │   │ cgroups  │   │ cgroups  │   │ cgroups  │         │  │
│  │  │ [pods]   │   │ [pods]   │   │ [pods]   │   │ [pods]   │         │  │
│  │  └────┬─────┘   └────┬─────┘   └────┬─────┘   └────┬─────┘         │  │
│  └───────┼──────────────┼──────────────┼──────────────┼────────────────┘  │
│          └──────────────┴──────────────┴──────────────┘                    │
│                                 │ Metrics (CPU/mem per pod every 15s)       │
│                                 ▼                                           │
│         ┌─────────────────────────────────────────────────────┐            │
│         │              PROMETHEUS + METRICS STORE              │            │
│         │    Live usage │ Historical traces │ Tenant history   │            │
│         └───────────────────────┬─────────────────────────────┘            │
│                                 │ Training data + live features             │
│                                 ▼                                           │
│         ┌─────────────────────────────────────────────────────┐            │
│         │               PREDICTION ENGINE                      │            │
│         │  Additive Model: cluster + tenant + job-type + now   │            │
│         │  Outputs: predicted P95 usage, duration, peaks       │            │
│         └───────────────────────┬─────────────────────────────┘            │
│                                 │ Predicted values (α, û, T̂_end)           │
│                                 └──► feeds back to Admission + Scheduler    │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Diagram 2 — The Optimization Problem (Formal Model)

The scheduler solves this constrained optimization at every job arrival event.

```
╔══════════════════════════════════════════════════════════════════════════╗
║                    OPTIMIZATION PROBLEM FORMULATION                      ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                          ║
║  DECISION VARIABLES                                                      ║
║  ─────────────────                                                       ║
║  x_{j,n} ∈ {0,1}     1 if job j is placed on node n, else 0            ║
║  y_j ∈ {0,1}         1 if job j is admitted now, 0 if queued            ║
║  t̂_start(j)          predicted start time for queued job j              ║
║                                                                          ║
║  OBJECTIVE FUNCTION                                                      ║
║  ──────────────────                                                      ║
║  Maximize:                                                               ║
║                                                                          ║
║    U = Σ_j Σ_r  [ y_j × û_{j,r} / C_total(r) ]                        ║
║                                                                          ║
║    where û_{j,r} = PREDICTED actual peak usage of job j for resource r  ║
║          C_total(r) = total cluster capacity for resource r             ║
║                                                                          ║
║  → Maximize the sum of predicted actual utilization across all          ║
║    admitted jobs and all resources (CPU, memory, GPU)                   ║
║                                                                          ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                          ║
║  CONSTRAINTS                                                             ║
║  ───────────                                                             ║
║                                                                          ║
║  [C1] CAPACITY (with overcommit):                                        ║
║    Σ_{j: x_{j,n}=1} û_{j,r}  ≤  C_node(n,r) × α_r     ∀ n, r         ║
║                                                                          ║
║    α_r = overcommit ratio for resource r                                ║
║         = min( declared_request / predicted_P95,  α_max )              ║
║         derived from prediction accuracy — higher accuracy → higher α  ║
║                                                                          ║
║  [C2] FAIRNESS (DRF):                                                    ║
║    DS(i) - DS(j)  ≤  ε          ∀ tenants i, j                         ║
║                                                                          ║
║    DS(i) = dominant_share(tenant i)                                     ║
║           = max_r [ Σ_{j∈tenant(i)} û_{j,r} / C_total(r) ]            ║
║    ε = fairness tolerance (tunable, e.g. 0.10 = 10%)                   ║
║                                                                          ║
║  [C3] SLA (predicted utilization stays below threshold):                ║
║    P̂95(n, t)  ≤  T_sla          ∀ n,  t ∈ [now, now + H]              ║
║                                                                          ║
║    P̂95(n, t) = 95th-percentile predicted utilization on node n at t    ║
║    H = lookahead horizon (e.g. 30 minutes)                              ║
║    T_sla = SLA threshold (e.g. 0.90 = 90%)                             ║
║                                                                          ║
║  [C4] PLACEMENT:                                                         ║
║    Σ_n x_{j,n} = y_j            ∀ j   (placed on exactly one node)     ║
║                                                                          ║
║  [C5] TEMPORAL SAFETY:                                                   ║
║    Σ_{j: x_{j,n}=1} pulse_{j,r}(t)  ≤  C_node(n,r) × α_r   ∀ n,r,t  ║
║                                                                          ║
║    pulse_{j,r}(t) = predicted usage of job j for resource r at time t  ║
║    (the additive peaks model — sum of all pulses must stay under cap)   ║
║                                                                          ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                          ║
║  WHY IT'S NP-HARD AND HOW WE SOLVE IT                                   ║
║  ─────────────────────────────────────                                   ║
║  This is a variant of multi-dimensional bin packing → NP-hard.         ║
║  We solve it with a GREEDY HEURISTIC using a composite SCORE function.  ║
║  The score is a relaxation of the optimization objective.               ║
║                                                                          ║
║  score(j, n) = w1 × fairness_score(j,n)                                ║
║              + w2 × utilization_score(j,n)                             ║
║              + w3 × temporal_score(j,n)                                ║
║              - w4 × violation_risk(j,n)                                ║
║                                                                          ║
║  Place job j on node n* = argmax_n score(j, n)                         ║
║                                                                          ║
╚══════════════════════════════════════════════════════════════════════════╝
```

---

## Diagram 3 — Optimization Scoring Function (Scheduler Decision)

```mermaid
flowchart TD
    JOB["New Job j\n(resource request, tenant ID, priority)"]
    
    JOB --> PRED["Prediction Engine\nOutputs: û_j (predicted P95 usage)\npulse_j(t) (temporal shape)\nT̂_end (predicted duration)"]
    
    PRED --> SCORE["Score Each Candidate Node"]
    
    SCORE --> S1["fairness_score(j, n)\n= 1 - DS(tenant_i) after placing j on n\nHigh score = tenant is underserved\n(DRF: equalize dominant shares)"]
    
    SCORE --> S2["utilization_score(j, n)\n= û_j / remaining_predicted_capacity(n)\nHigh score = tighter packing\n(fills node efficiently)"]
    
    SCORE --> S3["temporal_score(j, n)\n= complementarity(pulse_j, pulses_on_n)\nHigh score = job peaks don't overlap\nwith neighbors (Coach strategy)"]
    
    SCORE --> S4["violation_risk(j, n)\n= P(P̂95(n,t) > T_sla | j placed on n)\nHigh score = HIGH RISK\n(this is a PENALTY — subtract it)"]
    
    S1 --> COMPOSITE["Composite Score\nscore(j,n) = w1×S1 + w2×S2 + w3×S3 - w4×S4\n\nweights w1..w4 tuned on Google Cluster Trace"]
    S2 --> COMPOSITE
    S3 --> COMPOSITE
    S4 --> COMPOSITE
    
    COMPOSITE --> BEST["n* = argmax score(j, n)\nacross all nodes passing Filter phase"]
    
    BEST --> PLACE["Bind job j to node n*\nUpdate cluster state\nUpdate tenant dominant share"]
    
    style S4 fill:#ffcccc
    style COMPOSITE fill:#cce5ff
    style BEST fill:#d4edda
```

---

## Diagram 4 — Prediction Architecture: Additive Model

The prediction model decomposes predicted utilization into additive layers. Each layer corrects the one above it.

```mermaid
flowchart LR
    subgraph INPUTS["INPUT FEATURES"]
        F1["Cluster state\n(time of day, day of week,\ncurrent total load)"]
        F2["Tenant history\n(past jobs: declared vs actual,\navg ratio, variance)"]
        F3["Job type features\n(resource request profile,\njob class: batch/ML/service)"]
        F4["Current node state\n(running jobs, recent metrics,\npredicted completions)"]
    end

    subgraph MODEL["ADDITIVE PREDICTION MODEL"]
        L1["Layer 1: Cluster Baseline\nf_cluster(t) = cluster-level\nperiodic pattern\n(daily/weekly cycles)"]
        
        L2["Layer 2: Tenant Correction\nΔ_tenant(i) = tenant i's\nhistorical usage ratio\n(e.g. uses 62% of declared)"]
        
        L3["Layer 3: Job-Type Adjustment\nΔ_type(class) = job class\nbehavior profile\n(ML: slow ramp, plateau;\nbatch: spike then zero;\nservice: steady state)"]
        
        L4["Layer 4: Context Correction\nΔ_context(n,t) = node-level\nrecent deviation from baseline\n(captures current anomalies)"]
        
        SUM["PREDICTED PULSE\npulse_j(t) = f_cluster(t)\n         + Δ_tenant(i)\n         + Δ_type(class)\n         + Δ_context(n,t)\n\nFor each resource r: CPU, Memory, GPU"]
    end

    subgraph OUTPUTS["PREDICTION OUTPUTS → Optimization Inputs"]
        O1["û_j = max(pulse_j(t))\nPredicted peak usage"]
        O2["P̂95_j = 95th percentile\nof pulse_j(t)"]
        O3["T̂_end = predicted\njob duration"]
        O4["α_r = C_declared/P̂95\nSafe overcommit ratio"]
    end

    F1 --> L1
    F2 --> L2
    F3 --> L3
    F4 --> L4
    
    L1 --> SUM
    L2 --> SUM
    L3 --> SUM
    L4 --> SUM
    
    SUM --> O1
    SUM --> O2
    SUM --> O3
    O2 --> O4
```

---

## Diagram 5 — Why Additive Peaks? (Intuition)

```
Predicted cluster utilization on Node 3 over the next 2 hours:

CPU %
100 |
 90 |              ████          ← SLA threshold T_sla = 90%
 80 |         ████████
 70 |    ██████████████████
 60 |████████████████████████
 50 |████████████████████████████████
 40 |
    └──────────────────────────────────► time
      now     +30m    +60m    +90m  +120m

Each running job contributes a "pulse" (its predicted usage shape):

Job A (ML training):  ▁▂▄▆▇████████████▇▅▃▂▁  (slow ramp, plateau, slow release)
Job B (batch ETL):    ████████▁▁▁▁▁▁▁▁▁▁▁▁▁▁  (spike at start, finishes fast)
Job C (web service):  ▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄  (steady state)

Sum of pulses = node predicted utilization at each time t
             = pulse_A(t) + pulse_B(t) + pulse_C(t)

New job D wants to run on Node 3. Its pulse:
Job D (batch):        ▁▁▁▁▁▁▁▁████████▁▁▁▁▁▁  (starts in 30 min, peaks at +60m)

Question the optimizer asks:
  ∀ t: pulse_A(t) + pulse_B(t) + pulse_C(t) + pulse_D(t) ≤ T_sla ?

At t=+60m: 40% + 0% + 20% + 35% = 95% > 90%  → UNSAFE → reject this node
At t=+60m on Node 5 (different mix): 25% + 15% + 20% + 35% = 95% → still unsafe
At t=+60m on Node 7: 10% + 0% + 20% + 35% = 65% → SAFE → place here

This is additive peak prediction applied to admission control.
```

---

## Diagram 6 — Prediction Model Training Pipeline (Offline)

```mermaid
flowchart TD
    RAW["Google Cluster Trace v3\nRaw data: job arrivals, resource requests,\nactual usage measurements, tenant IDs,\njob durations, priorities"]

    PRE["Preprocessing Pipeline\n1. Logarithmic scaling (handle skew)\n2. Savitzky-Golay filtering (smooth noise)\n3. Min-max normalization (0-1 scale)\n(Kofi 2025 methodology)"]

    FEATURES["Feature Engineering\n• Declared request (CPU, mem, GPU)\n• Tenant historical usage ratio\n• Job class (ML/batch/service)\n• Time of day, day of week\n• Queue depth at submission time\n• Tenant's recent job durations"]

    SPLIT["Train / Validation / Test Split\n70% train │ 15% validation │ 15% test\n(time-ordered split — no future leakage)"]

    TRAIN["Model Training\n\nPrimary: Random Forest (Doukha 2025)\n• MAPE target < 5%\n• Handles abrupt workload changes\n• Interpretable feature importance\n\nBaseline: LSTM (Wang & Yang 2025)\n• Compare R² against Kofi's 0.99 benchmark\n• 2-3 layers, 128 neurons, 12h window"]

    EVAL["Evaluation Metrics\n• MAPE (Mean Absolute % Error)\n• R² (coefficient of determination)\n• P95 accuracy (critical for SLA)\n• Duration prediction accuracy"]

    DRIFT["Drift Detection\n• Monitor prediction error over time\n• Alert if MAPE > threshold\n• Trigger retraining\n(addresses Perera 2026 gap)"]

    DEPLOY["Deploy to Prediction Engine\n• Serves predictions in < 10ms\n• Used by Admission Webhook\n• Used by Scheduler Score phase"]

    RAW --> PRE --> FEATURES --> SPLIT --> TRAIN --> EVAL --> DRIFT --> DEPLOY

    DEPLOY -->|"Live metrics feed back\nas new training data"| PRE
```

---

## Diagram 7 — Admission Control Decision Flow

```mermaid
flowchart TD
    SUB["Tenant submits job j\nPOD YAML with resource request\n(cpu: 4, memory: 8Gi, priority: high)"]

    QUOTA["Check Namespace Quota\n(SloPolicy CRD — Priya 2025)\nIs tenant within their declared\nresource share limit?"]

    QUOTA -->|"Over quota"| DENY1["DENY\nReturn error:\n'Tenant quota exceeded'\nTenant must wait for\ntheir own jobs to finish"]

    QUOTA -->|"Within quota"| PREDICT["Run Prediction Engine\n• û_j = predicted P95 usage per resource\n• pulse_j(t) = temporal usage shape\n• T̂_end = predicted job duration\n• α_r = safe overcommit ratio"]

    PREDICT --> SAFETY["Safety Check: C3 + C5\n∀ candidate nodes n:\n  ∀ t in [now, now+H]:\n    sum_of_pulses(n,t) + pulse_j(t) ≤ T_sla × C_node(n,r)?"]

    SAFETY -->|"NO safe node found right now"| LOOKAHEAD["Lookahead: Predict Future Slots\nFor each node n:\n  When will a running job finish?\n  Will that free enough capacity?\n  At what time t* is a safe slot available?"]

    LOOKAHEAD -->|"Safe slot found at t*"| GATE["GATE the pod\n• Create pod in schedulingGate state\n• Scheduler ignores it until t*\n• Notify tenant: 'Job starts at t*'\n• Set timer to release gate at t*\n• NO retry loop — precise release"]

    LOOKAHEAD -->|"No slot within timeout window"| QUEUE["QUEUE the job\n• Place in tenant's pending queue\n• Re-evaluate when any job completes\n• Notify tenant: 'Waiting for capacity'"]

    SAFETY -->|"Safe node(s) exist now"| FAIRNESS["Fairness Check: C2\nCompute DS(tenant) post-placement:\n  DS = max_r(û_j,r / C_total(r))\nDoes admitting this job violate\nDRF fairness constraint?"]

    FAIRNESS -->|"Fairness violated\n(tenant already dominant)"| GATE

    FAIRNESS -->|"Fairness OK"| ADMIT["ADMIT NOW\nRelease pod to Scheduler Plugin\nschedulingGate = none"]

    style DENY1 fill:#ffcccc
    style GATE fill:#fff3cd
    style QUEUE fill:#fff3cd
    style ADMIT fill:#d4edda
```

---

## Diagram 8 — Kubernetes Scheduler Plugin Phases

```mermaid
flowchart LR
    POD["Pod admitted\n(schedulingGate removed)"]

    PF["PreFilter\n• Validate SloPolicy constraints\n• Compute tenant dominant share DS(i)\n• Fetch latest prediction for job j\n• Attach prediction data to cycle context"]

    F["Filter\nEliminate nodes that CANNOT host job j:\n• Physical capacity: declared request fits?\n• Predicted P95: sum_pulses(n) + û_j ≤ T_sla?\n• Node affinity / taints satisfied?"]

    PS["PostFilter\n(if no node passed Filter)\n• Trigger preemption evaluation:\n  Can a BestEffort job be evicted\n  to make room? (Atropos-style:\n  target culprit, not victim)\n• OR re-gate for future slot"]

    S["Score\nRank remaining nodes:\n\nscore(j,n) =\n  w1 × fairness_score\n     (1 - DS(tenant) post-placement)\n+ w2 × utilization_score\n     (û_j / remaining_capacity(n))\n+ w3 × temporal_score\n     (peak complementarity)\n- w4 × violation_risk\n     (P(SLA breach | place here))"]

    NR["NormalizeScore\nMap scores to [0, 100]\nfor consistent comparison\nacross scheduler plugins"]

    R["Reserve\nTentatively assign job to n*\nUpdate in-memory cluster state\n(prevents double-booking during\nconcurrent scheduling cycles)"]

    B["Bind\nWrite pod → node assignment\nto Kubernetes API Server\nkubelet on n* starts container\nAssign cgroup CPU weight based on\njob priority class"]

    POD --> PF --> F --> S --> NR --> R --> B
    F -->|"No node passes"| PS --> B

    style S fill:#cce5ff
    style B fill:#d4edda
    style PS fill:#fff3cd
```

---

## Diagram 9 — Predictive Future Scheduling (Scheduling Gates)

```
Timeline view: how scheduling gates work

t=0:00  Job D arrives. Prediction: all nodes too hot for next 12 minutes.
        Lookahead: Node 3's Job B finishes at t=0:12. That frees 6 CPUs.
        
        Action: CREATE pod with schedulingGate="wait-slot-node3"
                SET timer: release gate at t=0:12
                NOTIFY tenant: "Job D scheduled to start at 12:00"

                                    gate released here
                                           ↓
t=0:00 ──────────────────────────── t=0:12 ──────────────────── t=0:30
         Pod exists, scheduler              Pod enters scheduler,
         ignores it completely.             immediately placed on
         No retry loop.                     now-free Node 3.
         No wasted CPU cycles.
         No thundering herd.

Comparison:

DEFAULT KUBERNETES             YOUR SCHEDULER (PREDICTIVE GATES)
──────────────────────────     ────────────────────────────────────────
t=0:00  Job D → PENDING        t=0:00  Job D → GATE (release at 0:12)
t=0:15  retry → still pending  t=0:00  Tenant told: starts at 0:12
t=0:30  retry → still pending  t=0:12  Gate released, placed instantly
t=0:45  retry → still pending  t=0:12  Job running ✓
t=1:00  retry → placed (lucky)
        (depends on when scheduler
        happens to check)

Result: faster placement, predictable start times, zero wasted retries.
```

```mermaid
sequenceDiagram
    participant T as Tenant
    participant AW as Admission Webhook
    participant PE as Prediction Engine
    participant GC as Gate Controller
    participant KS as K8s Scheduler
    participant N3 as Node 3

    T->>AW: Submit Job D (4 CPU, 8GB)
    AW->>PE: Predict usage + check all nodes
    PE-->>AW: All nodes at risk for 12min; Node 3 free at t+12m
    AW->>GC: Create pod with schedulingGate, release at t+12m
    AW-->>T: Job accepted. Estimated start: 12 minutes
    
    Note over GC: Timer running. Scheduler ignores gated pod.
    Note over N3: Job B completes at t+12m, frees 6 CPUs
    
    GC->>KS: Remove schedulingGate at t+12m
    KS->>PE: Score nodes for Job D
    PE-->>KS: Node 3 scores highest (just freed, complementary pattern)
    KS->>N3: Bind Job D to Node 3
    N3-->>T: Job D running ✓
```

---

## Diagram 10 — Runtime SLA Enforcement (Prometheus + cgroups)

```mermaid
flowchart TD
    RUN["Jobs running on Node n\nKubernetes assigns QoS class at launch:\n• Critical jobs → Guaranteed (request=limit)\n• Standard jobs → Burstable\n• Background jobs → BestEffort"]

    PROM["Prometheus scrapes node metrics\nevery 15 seconds:\n• CPU usage per pod\n• Memory usage per pod\n• P99 response latency per service"]

    SLO["SloPolicy Controller\n(Priya 2025 architecture)\nCompares live metrics\nagainst tenant's declared SLO:\n  e.g. P99 latency ≤ 200ms\n  CPU usage ≤ declared limit"]

    OK["SLO Met ✓\nNo action needed.\nContinue monitoring."]

    VIOL["SLO Violation Detected\nCritical tenant's P99 > threshold\nOR CPU throttled > X%"]

    ID["Identify culprit\n(Atropos approach)\nWhich co-located job is\ncausing the interference?\n→ highest CPU steal?\n→ most memory pressure?"]

    THROTTLE["Throttle culprit job\nAdjust cgroup CPU weight:\n  critical job weight: 10,000\n  culprit (batch) job weight: 2\n\nkubectl patch pod culprit\n  --cpu-limit reduced\n\nNOT termination — just throttling.\nConsistent with 'no blind termination' rule."]

    RECOVER["SLO Recovers\nCritical job's latency drops\nback below threshold\nwithin 1–2 monitor cycles (15–30s)"]

    RESTORE["Restore batch job weights\nGradually return to normal\nonce SLO stable for N cycles"]

    REPLAN["Replan: flag node as 'hot'\nAdmission webhook raises\nT_sla threshold for this node\nuntil load stabilizes\n(prevents new admissions from\nmaking situation worse)"]

    RUN --> PROM --> SLO
    SLO -->|"No violation"| OK --> PROM
    SLO -->|"Violation"| VIOL --> ID --> THROTTLE --> RECOVER --> RESTORE --> PROM
    THROTTLE --> REPLAN --> AW["Back to Admission Webhook\nupdated node capacity estimate"]

    style OK fill:#d4edda
    style VIOL fill:#ffcccc
    style THROTTLE fill:#fff3cd
```

---

## Diagram 11 — Full End-to-End: Job Submission to Execution

```mermaid
sequenceDiagram
    participant T as Tenant
    participant AW as Admission Webhook
    participant PE as Prediction Engine
    participant SC as Scheduler Plugin
    participant GC as Gate Controller
    participant KB as kubelet (Node)
    participant PR as Prometheus

    T->>AW: kubectl apply job.yaml
    AW->>AW: Check SloPolicy quota (C4)
    
    alt Quota exceeded
        AW-->>T: DENIED — quota exceeded
    else Within quota
        AW->>PE: Get predictions for job j
        PE-->>AW: û_j, pulse_j(t), T̂_end, α_r
        
        AW->>AW: Check C3: any node safe now?
        
        alt No safe node now
            AW->>PE: Lookahead — when is next safe slot?
            PE-->>AW: Node 4 free at t+8min
            AW->>GC: Create pod with schedulingGate
            AW-->>T: Accepted. Starts in ~8 min
            GC->>GC: Wait for t+8min
            GC->>SC: Release scheduling gate
        else Safe node exists
            AW->>SC: Admit pod immediately
        end
        
        SC->>SC: PreFilter: fetch predictions, check quotas
        SC->>SC: Filter: eliminate unsafe nodes
        SC->>SC: Score: fairness + utilization + temporal - risk
        SC->>SC: Reserve: tentatively assign to n*
        SC->>KB: Bind: assign pod to node n*
        
        KB->>KB: Pull container image
        KB->>KB: Set cgroup CPU weight (by priority class)
        KB->>KB: Start container
        KB-->>T: Pod Running ✓
        
        loop Every 15 seconds
            KB->>PR: Report CPU/memory usage metrics
            PR->>AW: Update cluster state (live features)
            PR->>PE: New data point for online learning
        end
    end
```

---

## Diagram 12 — How Prediction Feeds Optimization (Variable Mapping)

```
PREDICTION ENGINE OUTPUTS         OPTIMIZATION MODEL USAGE
────────────────────────          ─────────────────────────────────────────

û_{j,r}                    ──►   Objective function: maximize Σ û_{j,r}/C_total
(predicted peak usage)            Constraint C1: Σ û_{j,r} ≤ C_node × α_r
                                  Score: utilization_score = û_j / capacity_left

P̂95(n, t)                  ──►   Constraint C3: P̂95(n,t) ≤ T_sla
(node utilization at t)           Score: violation_risk = P(P̂95 > T | place here)
                                  Gate: admission allowed only if C3 satisfied

pulse_j(t)                 ──►   Constraint C5: Σ pulses ≤ C_node × α_r ∀t
(temporal usage shape)            Score: temporal_score = complementarity(pulse_j, others)
                                  Gate: lookahead uses pulses to find future safe slots

T̂_end(j)                   ──►   Gate timing: release gate at t = T̂_end(running_job)
(predicted job duration)          Queue management: estimate when slots open
                                  Tenant notification: "your job starts in X minutes"

α_r                        ──►   Constraint C1: effective capacity = C_node × α_r
(overcommit ratio)                If prediction accurate → α > 1.0 → more jobs fit
derived from P̂95 vs declared      If prediction uncertain → α = 1.0 → conservative

DS(tenant_i)               ──►   Constraint C2: DS(i) - DS(j) ≤ ε
(dominant resource share)         Score: fairness_score = 1 - DS(i) post-placement
derived from Σ û_{j,r}            Controls scheduling priority among tenants


WITHOUT PREDICTION:               WITH PREDICTION (YOUR SYSTEM):
────────────────────              ─────────────────────────────────────
α_r = 1.0 always                 α_r > 1.0 when model is accurate
Use declared requests             Use predicted actual usage
Conservative → low utilization    Safe overcommit → high utilization
No lookahead                      Predict job end times → gate scheduling
Fairness by count                 Fairness by predicted dominant share
React to SLA violations           Prevent SLA violations before they happen
```

---

## Diagram 13 — Capstone Model Summary

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    YOUR CAPSTONE MODEL AT A GLANCE                      │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  INPUT:  Job submission (resource request, tenant ID, priority)         │
│  OUTPUT: Placement decision (node assignment or scheduled future time)  │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  PREDICTION LAYER (the oracle)                                  │   │
│  │  What:  Additive model → predicted actual usage per resource    │   │
│  │  How:   Random Forest on Google Cluster Trace                   │   │
│  │  Gives: û_j, pulse_j(t), T̂_end, α_r                           │   │
│  └──────────────────────────┬──────────────────────────────────────┘   │
│                             │ feeds                                     │
│  ┌──────────────────────────▼──────────────────────────────────────┐   │
│  │  OPTIMIZATION LAYER (the decision-maker)                        │   │
│  │  What:  Constrained placement optimization                      │   │
│  │  Obj:   Maximize Σ û_{j,r} / C_total   (utilization)           │   │
│  │  C1:    Σ û_j ≤ C_node × α  (safe overcommit capacity)        │   │
│  │  C2:    DRF fairness: equalize dominant shares                  │   │
│  │  C3:    P̂95(n,t) ≤ T_sla   (SLA safety over time)            │   │
│  │  C5:    Σ pulses ≤ capacity  (no temporal spike overflow)       │   │
│  │  Solve: Greedy heuristic via composite score function           │   │
│  └──────────────────────────┬──────────────────────────────────────┘   │
│                             │ decides                                   │
│  ┌──────────────────────────▼──────────────────────────────────────┐   │
│  │  SCHEDULING LAYER (the executor)                                │   │
│  │  Admit now  → K8s Scheduler Plugin places pod                   │   │
│  │  Gate       → Scheduling gate released at predicted time T*     │   │
│  │  Queue      → Re-evaluated on next job completion event         │   │
│  └──────────────────────────┬──────────────────────────────────────┘   │
│                             │ runs on                                   │
│  ┌──────────────────────────▼──────────────────────────────────────┐   │
│  │  ENFORCEMENT LAYER (the guarantor)                              │   │
│  │  cgroups CPU weights  → protect critical jobs at runtime        │   │
│  │  Prometheus feedback  → detect SLA deviations within 15s       │   │
│  │  SloPolicy controller → throttle culprit, not victim            │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  EVALUATION TARGETS:                                                    │
│  • Utilization ≥ 85%        (vs ~60% default K8s baseline)            │
│  • SLA compliance ≥ 95%     (vs < 80% under load)                     │
│  • Fairness variance < 10%  (DS spread across tenants)                 │
│  • MAPE < 5%                (prediction accuracy)                      │
│  • Queue wait time reduced  (vs default Pending retry loop)            │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```
