# Research Paper Reviews — Jobs & Workload Scheduling Focus

**Focus:** Multi-tenant job/workload scheduling | Improving cluster utilization | Maintaining tenant fairness | Maintaining SLA | Container and job-level scheduling | No memory ballooning/swapping/termination | Overcommitment only with accurate prediction

**Out of scope for this document:** VM-specific memory management (ballooning, page sharing, migration), VM startup/provisioning time, memory virtualization surveys. Those topics are covered in `Research_Paper_Reviews.md`.

---

## Kubernetes Architecture Reference (Quick Orientation)

Your capstone targets a **Kubernetes-hosted multi-tenant cluster**. Here is how the layers map:

```
Cloud Provider (AWS / Azure / GCP / Linode)
└── Nodes (VMs or bare-metal, managed as a K8s cluster)
    └── Kubernetes (cluster manager — same layer as Google Borg, Apache Mesos)
        ├── Control Plane: API Server + etcd + Scheduler + Controller Manager
        └── Worker Nodes: kubelet + container runtime
            └── Pods (container groups) ← your scheduled workloads
                └── Jobs (batch) / Deployments (services) ← tenant submissions
```

**Scheduling hook points your capstone uses:**
- **Admission Webhook** — runs before the scheduler; enforces tenant quotas and predictive admission
- **Scheduler Plugin (PreFilter → Score → Bind)** — admits, ranks, and places pods
- **cgroups** — enforces CPU/memory limits at runtime

---

## Papers

---

### 1. Chaudhari (2025) — Multi-Tenant AI Workload Scheduling on Kubernetes

**What it's about:**
A synthesis paper reviewing why Kubernetes's default scheduler fails for AI/ML workloads. Covers three scheduling strategies: **gang scheduling** (run only when all required resources are simultaneously available), **topology-aware placement** (co-locate distributed workers on same rack/NVLink interconnect), and **predictive resource management** (anticipate demand before admission). Proposes a 4-component architecture: Workload Classifier → Fairness Engine → Topology Optimizer → Priority Queue. All numbers are borrowed from cited papers — no original experiments run by the author.

**The Gap:**
The proposed framework was never built or evaluated. There is no admission control mechanism, no overcommitment handling, and no SLA violation prevention. The Fairness Engine and Predictive Resource Manager are described at a high level but never specified algorithmically. No ML model is trained or tested.

**How to build your capstone on it:**
This paper IS the gap your capstone fills. Build the Fairness Engine + Predictive Resource Management components as working Kubernetes scheduler plugins. Your differentiation: safe overcommitment with ML-based prediction — something this paper explicitly calls for but never delivers. Use Table 1 (GPU utilization by scheduler type) and Table 2 (gang scheduling completion times) as baseline performance numbers to compare against.

| Has Math Optimization Model | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| No | No (cites others) | No — conceptual framework only |

---

### 2. Jiang Zhi (2025) — A Study on Overcommitment in Cloud Providers

**What it's about:**
A master's thesis building two tools: (1) a data collector that pulls real workload traces from **Google Borg, Azure, and Alibaba Cloud**, and (2) a cloud simulator called **Clovers** that lets you test overcommitment and scheduling policies without real infrastructure. Tests machine-centric overcommitment (at the physical machine level) vs. container-centric overcommitment (at the container level). Compares scheduling algorithms on real trace data and measures QoS impact.

Key finding: current overcommitment policies are static and blunt — they either waste resources or violate QoS unpredictably. Explicitly treats containers as a first-class scheduling unit alongside VMs.

**The Gap:**
No ML-based prediction — overcommitment policies are static or rule-based. No predictive admission control. No fairness analysis across tenants. The future directions section explicitly names ML-based overcommitment policies and clustering-based scheduling as open problems.

**How to build your capstone on it:**
Clovers is a ready-made evaluation environment. Extend it with your ML admission policy (Random Forest predicting P95 utilization before admitting a job). Use Zhi's Google Borg and Alibaba traces as your dataset. You are directly targeting the open research questions he lists. This gives your capstone an evaluation environment + real dataset without running a live cluster.

| Has Math Optimization Model | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| Basic formulation (overcommit ratio) | No | Yes — Clovers simulator (Python), real trace data from Google Borg/Azure/Alibaba |

---

### 3. Reidys et al. / Coach (ASPLOS 2025) — All-Resource Oversubscription Using Temporal Patterns

**What it's about:**
A Microsoft Research paper analyzing 1 million+ Azure workloads. Core finding: many containers and services have **complementary temporal usage patterns** — one peaks at noon, another peaks at night. Coach exploits this by co-locating workloads whose peaks don't overlap, yielding ~26% more workloads hosted with minimal degradation. Introduces a new workload type that splits each resource into a guaranteed base + oversubscribed burst allocation. Uses long-term predictions to detect/prevent contention.

**The Gap:**
Coach handles oversubscription without guaranteeing inter-tenant fairness. The co-location policy is greedy — it picks complementary pairs but doesn't account for which tenant gets the oversubscribed slots equitably. SLA compliance is measured but not formally enforced. The temporal prediction model is not open-sourced.

**How to build your capstone on it:**
Coach is your overcommitment strategy. Predict each job's temporal usage pattern from historical submissions. When placing a new job, prefer nodes where running jobs have off-peak patterns at the expected runtime of the new job. Add DRF fairness constraints (from Paper #4) so no tenant is systematically denied the oversubscribed slots. Your improvement over Coach: explicit multi-tenant fairness guarantees alongside the temporal co-location strategy.

| Has Math Optimization Model | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| No formal optimization | Yes — temporal pattern prediction, long-term forecasting | Partial — CoachVM mechanics described, evaluated on Azure traces |

---

### 4. Ghodsi et al. (2011) — Dominant Resource Fairness (DRF)

**What it's about:**
The foundational multi-resource fair scheduling algorithm. The problem: how do you fairly allocate CPU, memory, and other resources to tenants with heterogeneous demands? DRF identifies each user's **dominant resource** (whichever resource they consume the highest fraction of) and equalizes dominant shares across all users. Formally proven to satisfy: sharing incentive, strategy-proofness, Pareto efficiency, and envy-freeness. Implemented in **Apache Mesos**. Beats Hadoop's slot-based fair scheduler by up to 66% on large job completion times using Facebook production traces.

**The Gap:**
DRF assumes static resource pools. It does not account for temporal variation — if a tenant's dominant resource shifts between morning (CPU-heavy) and night (memory-heavy), DRF doesn't adapt proactively. No prediction component. Does not handle latency-sensitive SLA deadlines separately from batch workloads.

**How to build your capstone on it:**
DRF is your **fairness backbone**. Use it as the fairness algorithm in your scheduler's Score phase. Extend it with time-awareness: instead of computing dominant share on current observed usage, compute it on **predicted future usage** over the job's expected runtime. This gives you Predictive DRF — no one in your paper set has combined DRF with temporal prediction. Directly addresses your "maintain fairness" requirement.

| Has Math Optimization Model | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| Yes — formal LP formulation and proofs | No | Yes — Algorithm 1 (pseudocode), implemented in Mesos, Facebook trace evaluation |

---

### 5. Cortez et al. / Resource Central (SOSP 2017) — Workload Prediction at Azure

**What it's about:**
Microsoft Research builds **Resource Central (RC)**, a system that collects container and VM telemetry from Azure and trains ML models to predict: average CPU utilization, P95 CPU utilization, workload lifetime, and workload class (interactive vs. batch). Uses **Random Forest** and **XGBoost** classifiers. Accuracy: 79–90% depending on metric. Key insight: workloads from the same subscription (tenant) behave consistently, so tenant history is a strong predictor. Shows that prediction-informed oversubscription prevents CPU exhaustion while reducing scheduling failures by 65% vs. baseline.

**The Gap:**
RC predicts at workload creation time — no dynamic re-prediction as conditions change. Does not enforce fairness — it's a scheduling helper, not a fair allocator. No gang scheduling or topology awareness.

**How to build your capstone on it:**
RC is your **prediction architecture**. Use tenant/subscription history as your primary feature. Train Random Forest to predict P95 utilization per job. Plug predictions into your admission decision: only admit a new job if the predicted combined utilization of all co-located jobs stays below a safe threshold. This is the "overcommitment only with accurate prediction" requirement made concrete. Their public Azure dataset can be used directly.

| Has Math Optimization Model | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| No | Yes — Random Forest + XGBoost, 79–90% accuracy | Yes — full training pipeline, Azure dataset publicly available |

---

### 6. Blagodurov et al. — Maximizing Server Utilization While Meeting Critical SLAs

**What it's about:**
Co-locates **critical (latency-sensitive)** jobs with **non-critical (batch)** jobs on the same server. Uses **Linux cgroups CPU weights** to enforce priority access. Static mode: critical jobs get weight 10,000, batch gets weight 2. Dynamic mode: a controller adjusts weights every 5 seconds based on SLA attainment. Result: server runs at near 100% utilization while critical job SLAs remain satisfied. Directly applicable to containers — cgroups is the mechanism Kubernetes uses internally for CPU limits.

**The Gap:**
Single-server only — no cluster-level scheduler. No admission control (assumes co-location decision was made elsewhere). Dynamic model is a simple proportional feedback controller — no ML or demand prediction. No multi-resource fairness.

**How to build your capstone on it:**
Two expansion angles specifically relevant to your capstone:

1. **Add ML prediction**: Replace the reactive proportional controller with a predictive model. Predict whether co-locating a new batch job will cause a critical job's SLA to be violated in the next N minutes. Only allow co-location when the model says it's safe.

2. **Extend to multi-cluster**: Blagodurov's weight model applies per-node. Your scheduler manages a cluster of nodes. Apply the weight assignment logic cluster-wide: when a critical job is submitted, the scheduler not only places it on the best node but also adjusts cgroup weights on that node dynamically. This is your enforcement layer — admission control decides whether to admit, weight management ensures SLA once admitted.

| Has Math Optimization Model | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| Yes — Equations 1–3 for dynamic weight calculation | No | Yes — working prototype on KVM/Linux, real workload experiments (RUBiS, Wikipedia benchmark) |

---

### 7. Wang & Yang (2025) — Intelligent Resource Allocation via LSTM + DQN

**What it's about:**
End-to-end cloud resource allocation system on **Kubernetes** combining **LSTM** for demand prediction and **Deep Q-Network (DQN)** for scheduling decisions. LSTM uses a 12-hour sliding window to predict demand 30 minutes ahead. DQN decides expand/contract/migrate actions for running pods. Deployed with a custom Kubernetes controller. Results: 32.5% better utilization, 43.3% reduction in response time, 26.6% cost reduction. Tested on a 208-core Kubernetes cluster with real e-commerce workloads.

**The Gap:**
No fairness mechanism — the system optimizes global metrics, ignoring per-tenant equity. DQN training requires 10 TB of data and 4.5 hours to train. No formal SLA enforcement — compliance is tracked but not guaranteed. Multi-tenant isolation is absent.

**How to build your capstone on it:**
This paper gives you the Kubernetes deployment blueprint. Simpler approach for your capstone: use LSTM only for prediction (they specify 2–3 LSTM layers, 128 neurons, 12-hour window, 30-minute prediction horizon — you can reproduce this exactly), and replace DQN with a rule-based admission decision guided by the prediction + DRF fairness score. Wang & Yang is your Kubernetes integration reference. You are adding what they left out: multi-tenant fairness and formal SLA enforcement.

| Has Math Optimization Model | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| Yes — Equations 1–2 (objective function, reward) | Yes — LSTM (RMSE 0.086, MAPE 7.2%) | Yes — full architecture, Kubernetes setup, training dataset described |

---

### 8. Doukha & Ez-zahout (2025) — Enhanced VM Resource Optimization via ML

**What it's about:**
Head-to-head comparison of **Random Forest vs. LSTM** for predicting CPU utilization. Uses Prometheus for real-time monitoring and Grafana for visualization. Tested on actual container/VM workloads. Result: **Random Forest wins clearly** — MAPE 2.65% vs LSTM's 17.43%. Random Forest handles abrupt workload changes better and requires far less data. LSTM struggles without massive datasets and careful hyperparameter tuning.

**The Gap:**
Single-resource focus (CPU only). No integration with an actual scheduler. No multi-tenant setting. Monitoring is reactive, not predictive for admission control.

**How to build your capstone on it:**
This is your **model selection justification**. When you build the prediction component, use Random Forest as your primary model — not LSTM — unless you have a very large dataset. This paper provides the rigorous comparison that justifies that choice. Also establishes your evaluation metrics: use MSE and MAPE to report your prediction accuracy, exactly as they do. Prometheus + Grafana is your standard monitoring stack — they're both natively available in Kubernetes clusters.

| Has Math Optimization Model | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| No | Yes — RF (MAPE 2.65%), LSTM (MAPE 17.43%) | Yes — PROXMOX environment, Prometheus/Grafana, MSE/MAPE evaluation |

---

### 9. Hu et al. / Atropos (SOSP 2025) — Targeted Task Cancellation for Overload

**What it's about:**
When a system is overloaded, existing approaches kill victim requests (those waiting in queue — lowest priority). Atropos identifies the **culprit request** — the one holding a resource and blocking everyone else — and cancels that instead. Continuously monitors resource usage per executing job. Result: higher SLO attainment with fewer total cancellations. Integrates with 6 large-scale applications, tested across 16 overload scenarios.

**The Gap:**
Application-level, not cluster-level. Focuses on within-application resource contention (e.g., lock contention), not cross-tenant scheduling competition. No fairness model. Cancellation is reactive — not preventive.

**How to build your capstone on it:**
This is your **overload fallback mechanism**. Admission control prevents overload 95% of the time. For the remaining cases — when prediction is wrong and overload occurs — instead of blindly killing the lowest-priority tenant's jobs (unfair), identify the culprit workload causing contention and throttle or preempt it. This is consistent with your "no termination" constraint: you're not doing blind termination, you're doing targeted intervention based on who is causing the problem. Admit control first; Atropos-style response second.

| Has Math Optimization Model | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| No | No (monitoring-based detection) | Yes — implementation in 6 real systems, 16 benchmarked overload scenarios |

---

### 10. Priya (2025) — QoS-Aware Multi-Tenant Container Orchestration Using Kubernetes

**What it's about:**
Builds a complete **QoS-aware multi-tenant scheduling framework directly in Kubernetes**. Three core components:
1. **SloPolicy CRD** — a custom Kubernetes resource where tenants declare their SLOs in YAML (latency percentiles, throughput requirements, weighted resource shares)
2. **QoS-SLO Controller** — translates SloPolicy into Kubernetes native constructs (PriorityClasses, ResourceQuotas, LimitRanges)
3. **Scheduler Plugin** — computes real-time QoS scores to guide scheduling and preemption decisions

A **closed-loop feedback mechanism** using Prometheus metrics dynamically adjusts priorities and autoscaling parameters when SLO violations are detected.

Results: 45% reduction in P99 latency, SLA violation rate < 5%, cluster utilization maintained above 70%, improved fairness in resource allocation across tenants.

**The Gap:**
No predictive admission control — the framework reacts to SLO violations rather than preventing them. QoS scores are computed from current metrics, not predicted future state. No overcommitment strategy — the system enforces hard caps rather than safe overcommitment. Workload temporal patterns are not exploited.

**How to build your capstone on it:**
This is the **most directly applicable paper** to your capstone. Priya gives you the exact Kubernetes architecture to implement: SloPolicy CRD + custom scheduler plugin + Prometheus feedback loop. Your improvement: add predictive admission (Random Forest prediction before scheduling decision) and temporal co-location (co-place jobs with complementary patterns). You are building on this framework and making it proactive rather than reactive. Use their evaluation metrics (P99 latency, SLA violation rate %, utilization %) directly as your own benchmark targets. Their < 5% SLA violation rate becomes your baseline to beat.

| Has Math Optimization Model | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| Partial — QoS scoring function | No — reactive feedback only | Yes — Kubernetes scheduler plugin implementation, Prometheus integration, microservices benchmark |

---

### 11. Kofi (2025) — Data-Driven Cloud Workload Optimization Using ML for Proactive Resource Management

**What it's about:**
Trains an **LSTM model on the Google Cluster Trace dataset** for proactive resource management. Pipeline: raw trace data → logarithmic scaling + Savitzky-Golay filtering + min-max normalization → LSTM training. Predicts workload count, CPU usage, and RAM usage simultaneously. Evaluation: R² = 0.99, MSE = 13,934 (workload), 128.89 (CPU), 131.29 (RAM), RMSLE = 0.14–0.16. Compared against VAMBig, SVM, and SATCN baselines — LSTM outperforms all.

Key contribution: the preprocessing pipeline (filtering + normalization) is what makes the LSTM achieve near-perfect accuracy. The raw trace data without preprocessing gives much worse results.

**The Gap:**
Single-model approach — no fairness component, no admission control, no multi-tenant framework. Predicts at a cluster aggregate level, not per-tenant or per-job. No Kubernetes integration. Future work explicitly lists multi-cloud and federated environments as extensions.

**How to build your capstone on it:**
Two contributions you can take directly: (1) **Preprocessing pipeline** — apply Savitzky-Golay filtering and min-max normalization to your Google Cluster Trace data before training. This directly improves your prediction accuracy. (2) **Dataset validation** — this paper confirms that the Google Cluster Trace is a viable, well-studied dataset for this exact type of prediction task. Use their R² = 0.99 as a benchmark for your own model accuracy. Note that if you use Random Forest (based on Doukha's comparison), you should report your own R² to compare against Kofi's LSTM baseline.

| Has Math Optimization Model | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| No | Yes — LSTM (R²=0.99, RMSLE 0.14–0.16) | Yes — Google Cluster Trace dataset, preprocessing pipeline fully described, evaluation methodology |

---

### 12. Perera (2025/2026) — Machine Learning for Cloud Workload Scheduling Optimization

**What it's about:**
A comprehensive review of the transition from heuristic cloud scheduling (First-Come-First-Served, Round Robin, PSO, ACO) to ML-driven scheduling. Categorizes current ML approaches into: Reinforcement Learning (DQN, PPO, A3C), Deep Neural Networks, and Multi-Agent Systems. Evaluates each category against energy efficiency, SLA compliance, and cost. Identifies two critical gaps across all existing work:
1. **Model drift** — ML schedulers trained on historical patterns fail silently when workload patterns shift
2. **Interpretability** — black-box models make debugging and compliance difficult in production

Also covers emerging directions: Agentic AI for resource orchestration and federated learning for privacy-preserving scheduling across organizations.

**The Gap:**
This is a review paper — no original algorithm or implementation. Its value is as a map of the research landscape and an identification of what no existing paper has solved cleanly.

**How to build your capstone on it:**
Two actionable insights: (1) **Address model drift explicitly** — include a drift detection mechanism or periodic retraining step in your design. This directly addresses a gap that Perera says no current paper solves. (2) **Justify your model choice** — Perera shows DQN and RL approaches require large training datasets and are interpretability-challenged. Framing your use of Random Forest (interpretable, lower data requirement) against this backdrop makes your design choice principled, not arbitrary. Cite Perera as evidence that RL-based approaches carry production risks your design avoids.

| Has Math Optimization Model | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| No | No — review paper | No — synthesizes existing work |

---

### 13. Pinnapareddy (2025) — Cloud Cost Optimization and Sustainability in Kubernetes

**What it's about:**
Examines resource management strategies in Kubernetes from cost and sustainability angles. Covers: **right-sizing** (matching pod resource requests to actual usage), **autoscaling** (HPA, VPA, KEDA), **cost-efficient scheduling** (bin packing via node affinity and taints), and **multi-cluster orchestration** (distributing workloads across regions/clouds to minimize cost). Practical tooling covered: Kubecost (resource cost attribution per namespace/tenant), Kyverno (policy enforcement), Open Policy Agent (admission control for compliance).

**The Gap:**
Cost optimization focus — not multi-tenant fairness or SLA enforcement. Autoscaling recommendations are reactive (trigger thresholds), not predictive. No ML models. Multi-cluster scheduling is discussed conceptually, not with a concrete algorithm.

**How to build your capstone on it:**
Two supporting contributions: (1) **Bin packing framing** — your scheduler's placement optimization (DRF + temporal co-location) is a principled form of bin packing. Pinnapareddy confirms this is a standard Kubernetes technique; your contribution is making it fairness-aware and predictive. (2) **Evaluation tooling** — Kubecost can be used in your experimental setup to measure per-tenant resource consumption and cost attribution, giving you a second evaluation dimension beyond utilization and SLA. Cite as motivation: his finding that unmanaged K8s leads to over-provisioning and waste is the problem your scheduler addresses.

| Has Math Optimization Model | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| No | No | No — practitioner analysis, no experiments |

---

### 14. Patchamatla — Optimizing Kubernetes-Based Multi-Tenant Container Environments in OpenStack for Scalable AI Workflows

**What it's about:**
Examines running Kubernetes on top of OpenStack (open-source private cloud IaaS) for AI workloads. Compares three deployment models: **bare-metal containers**, **VM-hosted containers**, and **pure VMs**. Evaluates GPU scheduling efficiency, workload isolation, and resource utilization. Key finding: VM-hosted containers (K8s on OpenStack VMs) balances isolation and utilization better than pure VMs, but bare-metal containers achieve the best raw performance. OpenStack's Nova scheduler and Kubernetes scheduler need coordination — running them independently causes suboptimal placement.

**The Gap:**
No ML-based scheduling. Scheduler coordination between OpenStack and Kubernetes is manual/rule-based. No fairness guarantees across tenants. No predictive admission control.

**How to build your capstone on it:**
Contextual relevance — validates that multi-tenant K8s deployments are a real production concern and that the scheduling layer matters for AI workloads. If your capstone targets a cloud environment where nodes are OpenStack VMs (which is common in private clouds at universities, enterprises, and research institutions), this paper describes exactly your deployment context. The scheduler coordination gap it identifies (OpenStack Nova + Kubernetes Scheduler not talking to each other) is a problem your admission control layer would address — since your ML admission step can take node-level VM capacity into account before submitting jobs to Kubernetes.

| Has Math Optimization Model | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| No | No | Yes — comparative experiments across bare-metal, VM-hosted, and pure VM configurations |

---

## Summary: Paper Tier List for Your Capstone

| Tier | Paper | Why It Matters for Scheduling |
|---|---|---|
| **Must use** | DRF — Ghodsi 2011 | Your fairness algorithm — formal, proven, implementable in a scheduler plugin |
| **Must use** | Priya 2025 (QoS K8s) | Your Kubernetes architecture reference — SloPolicy CRD + scheduler plugin + Prometheus loop |
| **Must use** | Resource Central — Cortez 2017 | Your prediction architecture — Random Forest, tenant history as features, safe oversubscription |
| **Must use** | Coach — Reidys 2025 | Your co-location strategy — temporal pattern complementarity for safe overcommitment |
| **Core** | Blagodurov | Your SLA enforcement layer — cgroups weights, dynamic prioritization; expand with ML + multi-cluster |
| **Core** | Wang & Yang 2025 | Your Kubernetes deployment model; LSTM+DQN baseline to compare against |
| **Core** | Chaudhari 2025 | Instructor-assigned anchor — the unbuilt framework your capstone constructs |
| **Core** | Kofi 2025 (LSTM/Google Trace) | Dataset + preprocessing pipeline; R²=0.99 accuracy benchmark to match or exceed |
| **Support** | Jiang Zhi 2025 | Evaluation environment (Clovers simulator) + real traces; your test harness |
| **Support** | Doukha 2025 | Use Random Forest over LSTM — evidence for your model selection decision |
| **Support** | Perera 2025/2026 | Landscape review; justifies avoiding RL; addresses model drift gap |
| **Support** | Atropos 2025 | Overload fallback if admission control fails — targeted preemption |
| **Reference** | Pinnapareddy 2025 | Bin packing motivation; Kubecost for evaluation tooling |
| **Reference** | Patchamatla | Deployment context for K8s-on-OpenStack environments |

---

## Recommended Capstone Direction (Jobs-Focused)

Build a **multi-tenant Kubernetes job scheduler** that does three things:

### 1. Predict Before Admitting
Train a **Random Forest** model (Doukha: better than LSTM for smaller datasets) on workload history using the **Google Cluster Trace** dataset (Kofi: preprocessed with Savitzky-Golay + min-max normalization). Predict P95 CPU and memory utilization for the requesting tenant if the job is admitted alongside current co-tenants. Only admit if predicted combined utilization stays below threshold. Implement as a **Kubernetes Admission Webhook** (Priya: SloPolicy CRD maps tenant SLOs into K8s constructs).

### 2. Schedule Fairly
Use **Dominant Resource Fairness** (DRF) to rank nodes during the scheduler's Score phase. The tenant with the lowest dominant resource share gets priority for the next scheduling slot. Extend to Predictive DRF: compute dominant share on predicted utilization over the job's lifetime, not just current snapshot. No existing paper combines DRF + temporal prediction — this is your novel contribution.

### 3. Collocate Intelligently
Among all valid placement options (nodes that pass admission threshold), prefer nodes where the temporal usage patterns of running jobs complement the new job's predicted pattern (Coach: co-locate workloads with non-overlapping peaks). Enforce SLA during execution via **dynamic cgroups CPU weights** (Blagodurov: critical jobs get high weight, batch jobs get low weight; adjust every 5 seconds based on SLA attainment).

### Architecture

```
Job Submission
    │
    ▼
Admission Webhook
├── Fetch tenant history from metrics store
├── Run Random Forest → predict P95 utilization
├── If predicted utilization > threshold → DENY (queue or reject)
└── If within threshold → ADMIT to scheduler

    │
    ▼
Custom Scheduler Plugin (Kubernetes Scheduling Framework)
├── PreFilter: enforce namespace ResourceQuotas (SloPolicy CRD)
├── Filter: eliminate nodes that cannot satisfy resource request
├── Score:
│   ├── DRF fairness score (higher = tenant is more underserved)
│   └── Temporal complementarity score (higher = running jobs have off-peak patterns)
└── Bind: place job on highest-scoring node

    │
    ▼
Runtime Enforcement
├── cgroups weights assigned at pod creation (critical=10000, batch=2)
├── Prometheus scrapes per-pod utilization every 5 seconds
├── SloPolicy Controller adjusts weights dynamically on SLA deviation
└── Atropos-style: if overload detected → throttle culprit job (not victim)
```

### Evaluation Targets

| Metric | Target | Baseline (no predictor) | Reference |
|---|---|---|---|
| Cluster utilization | ≥ 85% | ~60–65% typical | Chaudhari Table 1 |
| SLA compliance | ≥ 95% | < 80% under load | Priya: < 5% violation |
| Fairness variance across tenants | < 10% | Up to 30–40% | DRF envy-freeness proof |
| Prediction accuracy (MAPE) | < 5% | N/A | Doukha: RF MAPE 2.65% |
| P99 latency reduction vs. default K8s | ≥ 40% | Baseline | Priya: 45% achieved |

### Dataset
Use **Google Cluster Trace v3** (2019 release, publicly available). Kofi validates this dataset. Zhi's Clovers simulator can replay it. Alternatively, the Azure Public Dataset (Resource Central) for a second validation source.

### What Makes This Novel
No existing paper combines all three: (1) predictive admission via Random Forest on tenant history + (2) DRF fairness with temporal prediction extension + (3) cgroups-enforced SLA guarantee with Atropos-style fallback. Chaudhari calls for this exact combination. You build it.
