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

| Optimization / Model Type | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| None — conceptual framework | No (cites others) | No — conceptual framework only |

---

### 2. Jiang Zhi (2025) — A Study on Overcommitment in Cloud Providers

**What it's about:**
A master's thesis building two tools: (1) a data collector that pulls real workload traces from **Google Borg, Azure, and Alibaba Cloud**, and (2) a cloud simulator called **Clovers** that lets you test overcommitment and scheduling policies without real infrastructure. Tests machine-centric overcommitment (at the physical machine level) vs. container-centric overcommitment (at the container level). Compares scheduling algorithms on real trace data and measures QoS impact.

Key finding: current overcommitment policies are static and blunt — they either waste resources or violate QoS unpredictably. Explicitly treats containers as a first-class scheduling unit alongside VMs.

**The Gap:**
No ML-based prediction — overcommitment policies are static or rule-based. No predictive admission control. No fairness analysis across tenants. The future directions section explicitly names ML-based overcommitment policies and clustering-based scheduling as open problems.

**How to build your capstone on it:**
Clovers is a ready-made evaluation environment. Extend it with your ML admission policy (Random Forest predicting P95 utilization before admitting a job). Use Zhi's Google Borg and Alibaba traces as your dataset. You are directly targeting the open research questions he lists. This gives your capstone an evaluation environment + real dataset without running a live cluster.

| Optimization / Model Type | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| None — rule-based simulator (overcommit ratio only) | No | Yes — Clovers simulator (Python), real trace data from Google Borg/Azure/Alibaba |

---

### 3. Reidys et al. / Coach (ASPLOS 2025) — All-Resource Oversubscription Using Temporal Patterns

**What it's about:**
A Microsoft Research paper analyzing 1 million+ Azure workloads. Core finding: many containers and services have **complementary temporal usage patterns** — one peaks at noon, another peaks at night. Coach exploits this by co-locating workloads whose peaks don't overlap, yielding ~26% more workloads hosted with minimal degradation. Introduces a new workload type that splits each resource into a guaranteed base + oversubscribed burst allocation. Uses long-term predictions to detect/prevent contention.

**The Gap:**
Coach handles oversubscription without guaranteeing inter-tenant fairness. The co-location policy is greedy — it picks complementary pairs but doesn't account for which tenant gets the oversubscribed slots equitably. SLA compliance is measured but not formally enforced. The temporal prediction model is not open-sourced.

**How to build your capstone on it:**
Coach is your overcommitment strategy. Predict each job's temporal usage pattern from historical submissions. When placing a new job, prefer nodes where running jobs have off-peak patterns at the expected runtime of the new job. Add DRF fairness constraints (from Paper #4) so no tenant is systematically denied the oversubscribed slots. Your improvement over Coach: explicit multi-tenant fairness guarantees alongside the temporal co-location strategy.

| Optimization / Model Type | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| Prediction: temporal pattern forecasting (co-location heuristic) | Yes — temporal pattern prediction, long-term forecasting | Partial — CoachVM mechanics described, evaluated on Azure traces |

---

### 4. Ghodsi et al. (2011) — Dominant Resource Fairness (DRF)

**What it's about:**
The foundational multi-resource fair scheduling algorithm. The problem: how do you fairly allocate CPU, memory, and other resources to tenants with heterogeneous demands? DRF identifies each user's **dominant resource** (whichever resource they consume the highest fraction of) and equalizes dominant shares across all users. Formally proven to satisfy: sharing incentive, strategy-proofness, Pareto efficiency, and envy-freeness. Implemented in **Apache Mesos**. Beats Hadoop's slot-based fair scheduler by up to 66% on large job completion times using Facebook production traces.

**The Gap:**
DRF assumes static resource pools. It does not account for temporal variation — if a tenant's dominant resource shifts between morning (CPU-heavy) and night (memory-heavy), DRF doesn't adapt proactively. No prediction component. Does not handle latency-sensitive SLA deadlines separately from batch workloads.

**How to build your capstone on it:**
DRF is your **fairness backbone**. Use it as the fairness algorithm in your scheduler's Score phase. Extend it with time-awareness: instead of computing dominant share on current observed usage, compute it on **predicted future usage** over the job's expected runtime. This gives you Predictive DRF — no one in your paper set has combined DRF with temporal prediction. Directly addresses your "maintain fairness" requirement.

| Optimization / Model Type | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| Fairness algorithm: LP-proven dominant resource equalization | No | Yes — Algorithm 1 (pseudocode), implemented in Mesos, Facebook trace evaluation |

---

### 5. Cortez et al. / Resource Central (SOSP 2017) — Workload Prediction at Azure

**What it's about:**
Microsoft Research builds **Resource Central (RC)**, a system that collects container and VM telemetry from Azure and trains ML models to predict: average CPU utilization, P95 CPU utilization, workload lifetime, and workload class (interactive vs. batch). Uses **Random Forest** and **XGBoost** classifiers. Accuracy: 79–90% depending on metric. Key insight: workloads from the same subscription (tenant) behave consistently, so tenant history is a strong predictor. Shows that prediction-informed oversubscription prevents CPU exhaustion while reducing scheduling failures by 65% vs. baseline.

**The Gap:**
RC predicts at workload creation time — no dynamic re-prediction as conditions change. Does not enforce fairness — it's a scheduling helper, not a fair allocator. No gang scheduling or topology awareness.

**How to build your capstone on it:**
RC is your **prediction architecture**. Use tenant/subscription history as your primary feature. Train Random Forest to predict P95 utilization per job. Plug predictions into your admission decision: only admit a new job if the predicted combined utilization of all co-located jobs stays below a safe threshold. This is the "overcommitment only with accurate prediction" requirement made concrete. Their public Azure dataset can be used directly.

| Optimization / Model Type | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| None (scheduling heuristic) | Yes — Prediction model: Random Forest + XGBoost (P95 utilization), 79–90% accuracy | Yes — full training pipeline, Azure dataset publicly available |

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

| Optimization / Model Type | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| Scheduler optimizer: cgroups weight equations (dynamic priority control) | No | Yes — working prototype on KVM/Linux, real workload experiments (RUBiS, Wikipedia benchmark) |

---

### 7. Wang & Yang (2025) — Intelligent Resource Allocation via LSTM + DQN

**What it's about:**
End-to-end cloud resource allocation system on **Kubernetes** combining **LSTM** for demand prediction and **Deep Q-Network (DQN)** for scheduling decisions. LSTM uses a 12-hour sliding window to predict demand 30 minutes ahead. DQN decides expand/contract/migrate actions for running pods. Deployed with a custom Kubernetes controller. Results: 32.5% better utilization, 43.3% reduction in response time, 26.6% cost reduction. Tested on a 208-core Kubernetes cluster with real e-commerce workloads.

**The Gap:**
No fairness mechanism — the system optimizes global metrics, ignoring per-tenant equity. DQN training requires 10 TB of data and 4.5 hours to train. No formal SLA enforcement — compliance is tracked but not guaranteed. Multi-tenant isolation is absent.

**How to build your capstone on it:**
This paper gives you the Kubernetes deployment blueprint. Simpler approach for your capstone: use LSTM only for prediction (they specify 2–3 LSTM layers, 128 neurons, 12-hour window, 30-minute prediction horizon — you can reproduce this exactly), and replace DQN with a rule-based admission decision guided by the prediction + DRF fairness score. Wang & Yang is your Kubernetes integration reference. You are adding what they left out: multi-tenant fairness and formal SLA enforcement.

| Optimization / Model Type | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| Scheduler optimizer: RL reward function (DQN) + Prediction model (LSTM) | Yes — Prediction model: LSTM (RMSE 0.086, MAPE 7.2%) | Yes — full architecture, Kubernetes setup, training dataset described |

---

### 8. Doukha & Ez-zahout (2025) — Enhanced VM Resource Optimization via ML

**What it's about:**
Head-to-head comparison of **Random Forest vs. LSTM** for predicting CPU utilization. Uses Prometheus for real-time monitoring and Grafana for visualization. Tested on actual container/VM workloads. Result: **Random Forest wins clearly** — MAPE 2.65% vs LSTM's 17.43%. Random Forest handles abrupt workload changes better and requires far less data. LSTM struggles without massive datasets and careful hyperparameter tuning.

**The Gap:**
Single-resource focus (CPU only). No integration with an actual scheduler. No multi-tenant setting. Monitoring is reactive, not predictive for admission control.

**How to build your capstone on it:**
This is your **model selection justification**. When you build the prediction component, use Random Forest as your primary model — not LSTM — unless you have a very large dataset. This paper provides the rigorous comparison that justifies that choice. Also establishes your evaluation metrics: use MSE and MAPE to report your prediction accuracy, exactly as they do. Prometheus + Grafana is your standard monitoring stack — they're both natively available in Kubernetes clusters.

| Optimization / Model Type | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| None (comparison study only) | Yes — Prediction comparison: RF (MAPE 2.65%) vs LSTM (MAPE 17.43%) | Yes — PROXMOX environment, Prometheus/Grafana, MSE/MAPE evaluation |

---

### 9. Hu et al. / Atropos (SOSP 2025) — Targeted Task Cancellation for Overload

**What it's about:**
When a system is overloaded, existing approaches kill victim requests (those waiting in queue — lowest priority). Atropos identifies the **culprit request** — the one holding a resource and blocking everyone else — and cancels that instead. Continuously monitors resource usage per executing job. Result: higher SLO attainment with fewer total cancellations. Integrates with 6 large-scale applications, tested across 16 overload scenarios.

**The Gap:**
Application-level, not cluster-level. Focuses on within-application resource contention (e.g., lock contention), not cross-tenant scheduling competition. No fairness model. Cancellation is reactive — not preventive.

**How to build your capstone on it:**
This is your **overload fallback mechanism**. Admission control prevents overload 95% of the time. For the remaining cases — when prediction is wrong and overload occurs — instead of blindly killing the lowest-priority tenant's jobs (unfair), identify the culprit workload causing contention and throttle or preempt it. This is consistent with your "no termination" constraint: you're not doing blind termination, you're doing targeted intervention based on who is causing the problem. Admit control first; Atropos-style response second.

| Optimization / Model Type | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| None — monitoring-based detection (no optimizer) | No — reactive monitoring only | Yes — implementation in 6 real systems, 16 benchmarked overload scenarios |

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

| Optimization / Model Type | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| Scheduler optimizer: QoS scoring function (partial) | No — reactive feedback only | Yes — Kubernetes scheduler plugin implementation, Prometheus integration, microservices benchmark |

---

### 11. Kofi (2025) — Data-Driven Cloud Workload Optimization Using ML for Proactive Resource Management

**What it's about:**
Trains an **LSTM model on the Google Cluster Trace dataset** for proactive resource management. Pipeline: raw trace data → logarithmic scaling + Savitzky-Golay filtering + min-max normalization → LSTM training. Predicts workload count, CPU usage, and RAM usage simultaneously. Evaluation: R² = 0.99, MSE = 13,934 (workload), 128.89 (CPU), 131.29 (RAM), RMSLE = 0.14–0.16. Compared against VAMBig, SVM, and SATCN baselines — LSTM outperforms all.

Key contribution: the preprocessing pipeline (filtering + normalization) is what makes the LSTM achieve near-perfect accuracy. The raw trace data without preprocessing gives much worse results.

**The Gap:**
Single-model approach — no fairness component, no admission control, no multi-tenant framework. Predicts at a cluster aggregate level, not per-tenant or per-job. No Kubernetes integration. Future work explicitly lists multi-cloud and federated environments as extensions.

**How to build your capstone on it:**
Two contributions you can take directly: (1) **Preprocessing pipeline** — apply Savitzky-Golay filtering and min-max normalization to your Google Cluster Trace data before training. This directly improves your prediction accuracy. (2) **Dataset validation** — this paper confirms that the Google Cluster Trace is a viable, well-studied dataset for this exact type of prediction task. Use their R² = 0.99 as a benchmark for your own model accuracy. Note that if you use Random Forest (based on Doukha's comparison), you should report your own R² to compare against Kofi's LSTM baseline.

| Optimization / Model Type | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| None (no scheduling optimizer) | Yes — Prediction model: LSTM (R²=0.99, RMSLE 0.14–0.16) | Yes — Google Cluster Trace dataset, preprocessing pipeline fully described, evaluation methodology |

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

| Optimization / Model Type | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| None — review / landscape paper | No — synthesizes existing work | No — synthesizes existing work |

---

### 13. Pinnapareddy (2025) — Cloud Cost Optimization and Sustainability in Kubernetes

**What it's about:**
Examines resource management strategies in Kubernetes from cost and sustainability angles. Covers: **right-sizing** (matching pod resource requests to actual usage), **autoscaling** (HPA, VPA, KEDA), **cost-efficient scheduling** (bin packing via node affinity and taints), and **multi-cluster orchestration** (distributing workloads across regions/clouds to minimize cost). Practical tooling covered: Kubecost (resource cost attribution per namespace/tenant), Kyverno (policy enforcement), Open Policy Agent (admission control for compliance).

**The Gap:**
Cost optimization focus — not multi-tenant fairness or SLA enforcement. Autoscaling recommendations are reactive (trigger thresholds), not predictive. No ML models. Multi-cluster scheduling is discussed conceptually, not with a concrete algorithm.

**How to build your capstone on it:**
Two supporting contributions: (1) **Bin packing framing** — your scheduler's placement optimization (DRF + temporal co-location) is a principled form of bin packing. Pinnapareddy confirms this is a standard Kubernetes technique; your contribution is making it fairness-aware and predictive. (2) **Evaluation tooling** — Kubecost can be used in your experimental setup to measure per-tenant resource consumption and cost attribution, giving you a second evaluation dimension beyond utilization and SLA. Cite as motivation: his finding that unmanaged K8s leads to over-provisioning and waste is the problem your scheduler addresses.

| Optimization / Model Type | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| None — practitioner analysis | No — no predictive model | No — practitioner analysis, no experiments |

---

### 14. Patchamatla — Optimizing Kubernetes-Based Multi-Tenant Container Environments in OpenStack for Scalable AI Workflows

**What it's about:**
Examines running Kubernetes on top of OpenStack (open-source private cloud IaaS) for AI workloads. Compares three deployment models: **bare-metal containers**, **VM-hosted containers**, and **pure VMs**. Evaluates GPU scheduling efficiency, workload isolation, and resource utilization. Key finding: VM-hosted containers (K8s on OpenStack VMs) balances isolation and utilization better than pure VMs, but bare-metal containers achieve the best raw performance. OpenStack's Nova scheduler and Kubernetes scheduler need coordination — running them independently causes suboptimal placement.

**The Gap:**
No ML-based scheduling. Scheduler coordination between OpenStack and Kubernetes is manual/rule-based. No fairness guarantees across tenants. No predictive admission control.

**How to build your capstone on it:**
Contextual relevance — validates that multi-tenant K8s deployments are a real production concern and that the scheduling layer matters for AI workloads. If your capstone targets a cloud environment where nodes are OpenStack VMs (which is common in private clouds at universities, enterprises, and research institutions), this paper describes exactly your deployment context. The scheduler coordination gap it identifies (OpenStack Nova + Kubernetes Scheduler not talking to each other) is a problem your admission control layer would address — since your ML admission step can take node-level VM capacity into account before submitting jobs to Kubernetes.

| Optimization / Model Type | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| None — experimental comparison | No — no predictive model | Yes — comparative experiments across bare-metal, VM-hosted, and pure VM configurations |

---

---

### 15. Liu & Guitart (2025) — Dynamic In-node Group-Aware Scheduling for Multi-Tenant ML Services on Kubernetes

**What it's about:**
Addresses a gap that all prior Kubernetes scheduling work has ignored: **in-node scheduling**. After the cluster scheduler picks a node for a pod, Kubernetes relies on Linux cgroups and the Completely Fair Scheduler (CFS) to distribute resources within the node — but CFS has no awareness of container groups or multi-tenant ML services. Liu & Guitart introduce a **Dynamic Resource Controller (DRC)** that monitors container groups on a node and dynamically adjusts cgroup settings based on group membership. For ML services partitioned into multiple containers, DRC enables CPU/memory affinity within the node. Results: DRC achieves 242–319% throughput improvement over single-container baselines, and 44% faster makespan.

**The Gap:**
No predictive admission control — DRC reacts to conditions after placement. No fairness model across tenants. Focused purely on in-node reallocation, not the upstream scheduling decision. No historical workload prediction or overcommitment strategy.

**How to build your capstone on it:**
This is your **in-node enforcement complement**. After your scheduler places a pod on a node (via predictive admission + DRF scoring), DRC-style group-aware cgroup adjustment handles fine-grained resource sharing within the node. Your SloPolicy CRD (from Priya) defines tenant SLO requirements; DRC-style enforcement translates those into dynamic cgroup CPU/memory weight updates. You add what Liu & Guitart assume is given: the smart placement decision that gets the right pods onto the right nodes before DRC runs.

| Optimization / Model Type | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| Scheduler optimizer: in-node cgroup assignment (group-aware, reactive) | No — reactive cgroup adaptation | Yes — full Kubernetes implementation, cgroup benchmarks, ML workload experiments (MobileNet, ResNet50, VGG16) |

---

### 16. Kovalenko & Zhdanova (2024) — Dynamic Mathematical Model for Resource Management and Scheduling in Kubernetes

**What it's about:**
Creates a **formal discrete combinatorial optimization model** for Kubernetes scheduling. Key insight: most Kubernetes scheduling papers describe objectives but not constraints — this paper provides explicit mathematical formalization of both. Objectives: (1) minimize average shared hosting servers running, (2) maximize average resource utilization, (3) minimize node power state transitions, (4) minimize spillover resource use by dedicated-server customers on shared nodes. The model is "dynamic" because it incorporates a time parameter T. Distinguishes between dedicated servers and shared hosting servers, directly encoding the cloud business rules a provider must follow.

**The Gap:**
No ML/predictive component — the model operates on declared resource requests, not predicted actual usage. No tenant fairness mechanism. No SLA enforcement or deadline constraints. No simulation evaluation.

**How to build your capstone on it:**
This is your **mathematical formalization backbone**. Kovalenko provides the formal constraint structure (CPU/memory constraints per node, pod-to-node assignment variables, server on/off state variables) that your optimization model should adopt. Extend it with: (1) replace declared requests with **predicted utilization** from your Random Forest model, (2) add **DRF fairness constraints**, and (3) add **SLA deadline constraints** (job must complete by T_deadline). The result is a more complete optimization model than any single prior paper provides.

| Optimization / Model Type | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| Scheduler optimizer: discrete combinatorial LP (pod-to-node assignment + server on/off) | No — no predictive model | No — theoretical model only |

---

### 17. Alatawi (2025) — Optimizing Multitenancy: Adaptive Resource Allocation in Serverless via Reinforcement Learning

**What it's about:**
Builds an RL-based adaptive resource allocation framework for **serverless cloud environments** (function-as-a-service). Models resource allocation as a **Markov Decision Process (MDP)** with state variables: latency, resource utilization, energy consumption. Reward function balances throughput, latency, energy, and fairness. Uses the **Gini coefficient** as the fairness metric. Results: 50% latency reduction (250ms → 120ms), 38.9% throughput increase (180 → 250 tasks/s), SLA compliance > 98%, Gini coefficient from 0.25 to 0.10. Under burst loads, SLO success rate is 94%.

**The Gap:**
Serverless (function invocations) context — not containerized persistent jobs or batch workloads. RL training requires substantial data and compute. No gang scheduling or topology awareness. Under burst loads, performance drops below the nominal 98% SLA compliance. No trace-based historical prediction.

**How to build your capstone on it:**
Two contributions: (1) **Gini coefficient as your fairness metric** — adopt Gini alongside DRF dominant share variance as dual fairness measurements. Target: Gini from ~0.25 to ~0.10 to match Alatawi's improvement. (2) **Comparison baseline** — frame your Random Forest + DRF approach as achieving similar or better results without RL's training complexity, interpretability issues, and burst-load instability (which Perera independently critiques). This directly justifies your design choice.

| Optimization / Model Type | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| RL policy model: MDP-based resource allocation (reward-driven) | No — RL learns policy dynamically (not a separate prediction model) | Yes — simulation experiments, Gini/latency/throughput metrics |

---

### 18. Zhao et al. (2021) — SLA-Based Profit Optimization Resource Scheduling for AaaS Platforms

**What it's about:**
Formal admission control and resource scheduling system for **Analytics-as-a-Service (AaaS)** platforms. Proposes two algorithms: (1) **scalable admission control** — formal accept/reject decision based on SLA constraints (query deadline D_q and budget B_q), and (2) **profit optimization scheduling** — dynamically provisions cloud VMs to execute admitted queries while maximizing provider profit. Uses a data splitting method to process large datasets efficiently. Evaluations show significantly higher admission rates, profits, and SLA compliance vs. state-of-the-art baselines.

**The Gap:**
AaaS-specific context (SQL analytics queries, not general container workloads). Profit maximization as primary goal vs. utilization + fairness. No ML prediction. No DRF fairness. VM-based (not container-based). No temporal usage pattern exploitation.

**How to build your capstone on it:**
Two contributions: (1) **Admission control formalization** — Zhao's formal accept/reject decision (admit if job completes within deadline D_q and resource budget B_q given current cluster state) maps directly to your Admission Webhook. Reframe: "query deadline" → "job SLA deadline", "query budget" → "tenant resource quota". The admission algorithm structure transfers directly. (2) **Utilization = Profit framing** — Zhao maximizes provider profit; maximizing cluster utilization is the equivalent objective. This framing makes your scheduler compelling to cloud provider stakeholders.

| Optimization / Model Type | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| Scheduler optimizer: admission control LP + profit maximization LP | No — no predictive model | Yes — simulation with real cloud workloads, comparison vs. state-of-the-art baselines |

---

## Capstone Optimization Model Design

*Built on Kovalenko & Zhdanova (2024) as the structural base, extended with DRF fairness, Random Forest prediction, and SLA constraints.*

---

### Why Kovalenko Is the Right Starting Point

Most Kubernetes scheduling papers describe what they want to achieve (maximize utilization, minimize violations) without writing down the actual mathematical constraints the system must satisfy. Kovalenko & Zhdanova (2024) is one of the few papers that does the opposite: it explicitly formalizes the discrete combinatorial structure of the pod-to-node assignment problem with typed variables, multi-objective criteria, and hard constraints. That formalism is the scaffolding your capstone's optimization model needs.

The original model targets a cloud provider managing both dedicated servers and shared multi-tenant servers. Its objectives and constraint categories translate almost directly to our Kubernetes multi-tenant scheduling problem — we just replace VMs with pods, servers with nodes, and extend the variables to carry prediction outputs and fairness terms.

---

### Kovalenko's Original Model — Summary

**Parameters (inputs to the model):**
- `S` — number of cluster nodes (servers)
- `U` — number of tenants (customers)
- `P` — number of pods to schedule
- `T` — discrete time horizon; each period `t` is a snapshot where cluster state is held constant
- `cs'`, `ms'` — CPU and memory capacity of node `s`
- `cp`, `mp` — declared CPU and memory request of pod `p`
- `dus,t` — whether node `s` is dedicated to a specific tenant at time `t`

**Decision Variables:**
- `x_{s,p,t} ∈ {0,1}` — 1 if pod `p` is running on node `s` at time `t`
- `y_{s,t} ∈ {0,1}` — 1 if node `s` is powered on at time `t`

**Objectives (multi-criteria, may conflict):**
1. Minimize average number of shared nodes powered on
2. Maximize average resource utilization on shared nodes
3. Minimize number of node power-state transitions (on→off, off→on)
4. Minimize spillover: dedicated-server tenants consuming shared node resources

**Constraints:**
- `Σ_p x_{s,p,t} · cp ≤ cs'` — CPU capacity per node per time period
- `Σ_p x_{s,p,t} · mp ≤ ms'` — Memory capacity per node per time period
- `Σ_s x_{s,p,t} ≤ 1` — Each pod on at most one node at any time
- `x_{s,p,t} ≤ y_{s,t}` — Pod can only run on a powered-on node

---

### Our Capstone Adaptation — What We Change and Why

| Kovalenko Element | Our Adaptation | Reason |
|---|---|---|
| Declared resource requests `cp`, `mp` | Replace with **predicted utilization** `ĉ_j(t)`, `m̂_j(t)` from Random Forest | Tenants over-declare; actual usage is 30–50% lower. Scheduling on declarations wastes capacity. |
| Server power on/off objective | **Remove** — not applicable to Kubernetes context | K8s nodes are pre-provisioned VMs; node power management is the cloud provider's responsibility, not our scheduler's. |
| No fairness objective | **Add DRF dominant share equalization** as fairness constraint | The original model has no fairness component; our multi-tenant setting requires it. |
| No SLA deadline constraint | **Add deadline constraint**: `T_j^start + T_j^duration ≤ T_j^deadline` | SLA compliance is a primary goal; Kovalenko's model has no notion of job deadlines. |
| Static resource values | **Add time-indexed predictions**: `m̂_j(t)` varies across the job's lifetime | Captures temporal peaks — the overcommitment opportunity is specifically about non-simultaneous peaks. |
| Single resource check per period | **Add peak-time safety check**: enforce constraint at predicted peak `t*`, not just current snapshot | A node may look safe now but overflow at the predicted peak in 30 minutes. |
| No tenant grouping | **Add tenant sets** `T_k = {jobs belonging to tenant k}` | DRF requires tracking per-tenant resource consumption to compute dominant shares. |

---

### Sample Refactored Model for Our Capstone

#### Parameters

```
N          — set of cluster nodes (indexed n)
J          — set of submitted jobs/pods (indexed j)
K          — set of tenants (indexed k)
T_k        — set of jobs belonging to tenant k
T          — discrete time horizon (indexed t); each period ≈ 5-minute window
RAM_n      — physical memory capacity of node n  (GB)
CPU_n      — CPU capacity of node n  (cores)
m̂_j(t)    — RF-predicted memory usage of job j at time t  (GB)
ĉ_j(t)    — RF-predicted CPU usage of job j at time t  (cores)
t*_j       — predicted peak time of job j  (when m̂_j is maximized)
D_j        — SLA deadline of job j  (absolute timestamp)
dur_j      — predicted duration of job j  (from Resource Central)
α          — safety overcommitment ratio (e.g., 0.90)
ε          — maximum allowable fairness gap between tenants
RAM_total  — sum of RAM_n across all nodes
CPU_total  — sum of CPU_n across all nodes
```

#### Decision Variables

```
x_{n,j} ∈ {0,1}     — 1 if job j is assigned to node n  (primary placement variable)
q_j ∈ {0,1}         — 1 if job j is queued (not yet placed)
d_k ∈ [0,1]         — dominant resource share of tenant k
```

#### Objective Function (weighted multi-objective)

```
maximize:
  w₁ · (1/|N|) · Σ_n [ Σ_j x_{n,j} · m̂_j(t) / RAM_n ]        — (1) memory utilization
  - w₂ · Σ_j max(0, T_j^start + dur_j - D_j)                    — (2) SLA violation penalty
  - w₃ · (max_k d_k - min_k d_k)                                  — (3) fairness (minimize Gini-like spread)
```

where `w₁, w₂, w₃` are tunable weights (suggested starting values: `w₁=0.5, w₂=0.35, w₃=0.15`).

#### Constraints

```
(C1)  Memory safety (per node, at predicted peak):
      Σ_j x_{n,j} · m̂_j(t*_j)  ≤  α · RAM_n          ∀ n

(C2)  CPU safety (per node, per time period):
      Σ_j x_{n,j} · ĉ_j(t)     ≤  α · CPU_n           ∀ n, t

(C3)  Single-node assignment (each job placed at most once):
      Σ_n x_{n,j} + q_j = 1                             ∀ j

(C4)  DRF dominant share definition:
      d_k = max(
                Σ_{j∈T_k} Σ_n x_{n,j} · m̂_j / RAM_total,
                Σ_{j∈T_k} Σ_n x_{n,j} · ĉ_j / CPU_total
               )                                          ∀ k

(C5)  Fairness bound (no tenant's dominant share exceeds fair share by more than ε):
      d_k  ≤  (1/|K|) + ε                               ∀ k

(C6)  SLA deadline (if job is placed, it must complete before deadline):
      x_{n,j} = 1  →  T_j^start + dur_j ≤ D_j           ∀ j, n

(C7)  Temporal co-location safety (non-overlapping peaks preferred):
      Σ_j x_{n,j} · m̂_j(t)  ≤  α · RAM_n               ∀ n, ∀ t ∈ [t_j^start, t_j^end]

(C8)  Binary and non-negativity:
      x_{n,j} ∈ {0,1},   q_j ∈ {0,1},   d_k ≥ 0        ∀ n, j, k
```

---

### Key Novel Contributions vs. Kovalenko

1. **Predictive terms in constraints** — `m̂_j(t)` and `ĉ_j(t)` replace static declared values `mp`, `cp`. This is the core enabler of safe overcommitment. No existing Kubernetes optimization model does this.

2. **DRF fairness as constraint C4–C5** — Kovalenko has no fairness objective. Adding dominant share tracking and bounding makes our model the first to combine formal scheduling optimization with proven multi-resource fairness.

3. **Peak-time constraint C1 vs. Kovalenko's period-based constraint** — Kovalenko checks each time period equally. We specifically enforce the constraint at `t*_j` (the RF-predicted peak), which is tighter and more efficient than checking all periods uniformly.

4. **SLA deadline as hard constraint C6** — Kovalenko's model has no concept of job deadlines. C6 ensures the model only places a job if it can complete on time, making admission control mathematically grounded.

5. **Temporal co-location as C7** — Derived from Coach (Reidys 2025): the constraint enforces that the sum of all running jobs' predicted memory at any point in time stays within capacity, not just at the snapshot moment of scheduling.

---

### Practical Notes for Capstone Scope

- **C1 and C7 are the most important constraints to implement first.** They directly prevent OOM kills and are what make overcommitment safe.
- **C5 (fairness bound)** can start with a relaxed `ε` (e.g., 0.15) and tighten as the model matures.
- **The multi-objective function** can be simplified for initial simulation: treat w₂ (SLA) as a hard constraint (never violate) and maximize only w₁ (utilization) with w₃ (fairness) as a secondary sort criterion.
- **The problem is NP-hard in its full form** (binary assignment + multi-objective). For simulation, use a greedy sequential admission algorithm that checks constraints in order: C1 → C2 → C4/C5 → C6 → admit or queue. This is tractable and mirrors how real schedulers work.

---

## Capstone Prediction Model Design

*Built on Resource Central (Cortez 2017) as the primary architecture, with Random Forest justified by Doukha (2025) and preprocessing from Kofi (2025).*

---

### What We Are Predicting and Why

The optimization model in the section above needs `m̂_j(t)` and `ĉ_j(t)` — predicted memory and CPU usage of each job at each time step. The prediction model is what computes these values before the scheduler makes its placement decision.

There are three distinct things worth predicting:

| Prediction Target | Why It Matters | Used In |
|---|---|---|
| **P95 memory peak** `m̂_j^peak` | The worst-case usage that determines node safety — if predicted peak fits, actual peak almost certainly fits | Admission gate: C1 check |
| **Temporal usage profile** `m̂_j(t)` | The shape of usage over time — enables temporal co-location (C7) and peak conflict detection | Temporal scoring: C7 check, Coach co-location |
| **Job duration** `dur_j` | When resources free up — enables proactive future-slot reservation and C6 SLA check | SLA check (C6), queue time estimation |

---

### Feature Set (what the Random Forest model uses as inputs)

```
Tenant-level features (most predictive — per Resource Central):
  - tenant_request_ratio_mem    : tenant's historical (actual/declared) memory ratio
  - tenant_request_ratio_cpu    : tenant's historical (actual/declared) CPU ratio
  - tenant_job_duration_p50     : tenant's median job duration (minutes)
  - tenant_job_duration_p90     : tenant's 90th percentile job duration

Job-level features:
  - declared_mem_gb             : declared memory request (GB)
  - declared_cpu_cores          : declared CPU request (cores)
  - job_priority_class          : 0=BestEffort, 1=Burstable, 2=Guaranteed
  - job_type                    : batch=0, service=1, ML_training=2

Temporal features:
  - hour_of_day                 : 0–23 (captures daily usage cycles)
  - day_of_week                 : 0–6 (captures weekly patterns)
  - cluster_utilization_now     : current cluster-wide memory utilization %

Co-location features:
  - node_current_mem_util       : current memory utilization % on candidate node
  - node_peak_offset_hours      : hours until the largest running job on the node peaks
```

---

### Model Architecture

```
Input: feature vector (above)
       ↓
Preprocessing: Savitzky-Golay smoothing + min-max normalization  ← Kofi (2025) pipeline
       ↓
Random Forest Regressor                                           ← Doukha (2025): RF MAPE 2.65%
  - n_estimators = 200
  - max_depth = 15
  - min_samples_leaf = 5
  - Output 1: P95 predicted memory peak (GB)
  - Output 2: P95 predicted CPU peak (cores)
  - Output 3: predicted job duration (minutes)
       ↓
Post-processing:
  - Scale output by (1 + uncertainty_buffer)                     ← e.g., 1.05 for 5% safety margin
  - Generate temporal profile: pulse shape m̂_j(t) using
    predicted peak value + predicted duration
    m̂_j(t) = m̂_j^peak · f(t - t_start, dur_j)
    where f is a trapezoid: ramp up 10%, plateau 80%, ramp down 10%
       ↓
Output: m̂_j(t), ĉ_j(t), dur_j  →  fed into optimization model constraints C1–C7
```

---

### Training and Evaluation

**Dataset:** Google Cluster Trace v3 — contains declared requests AND actual measured usage per job, per tenant, with timestamps. This is exactly the training signal needed.

**Train/test split:** Replay-based — use weeks 1–3 for training, week 4 for evaluation. This mimics the real deployment scenario where the model is trained on history and predicts future jobs.

**Evaluation metrics (per Doukha 2025):**
- MAPE (Mean Absolute Percentage Error) — target < 5%
- MSE (Mean Squared Error)
- R² — target ≥ 0.95 (Kofi baseline: 0.99)

**Drift detection (per Perera 2025):** Retrain every N days (e.g., weekly) or when prediction MAPE on recent jobs exceeds threshold (e.g., 8%). This directly addresses the model drift gap Perera identifies as unsolved in the literature.

---

### Prediction Model Selection: Why Random Forest, and What Else Could Be Used

#### What the Papers Actually Used

Across all 18 papers reviewed, four distinct prediction approaches appear:

| Model | Papers | What Was Predicted | Accuracy |
|---|---|---|---|
| **Random Forest** | Doukha 2025, Resource Central (Cortez 2017) | P95 memory/CPU utilization | MAPE 2.65% (Doukha), 79–90% accuracy (Resource Central) |
| **XGBoost** | Resource Central (Cortez 2017) | P95 utilization | ~79–90% accuracy (used alongside RF) |
| **LSTM** | Wang & Yang 2025, Doukha 2025, Kofi 2025 | Resource usage time-series, job duration | MAPE 7.2% (Wang), MAPE 17.43% (Doukha), R²=0.99 (Kofi) |
| **Temporal heuristic** | Coach (Reidys 2025) | Usage shape / co-location compatibility | Not quantified — pattern-matching, not regression |

No paper used: neural networks beyond LSTM, gradient boosting variants (LightGBM, CatBoost), Gaussian Processes, or transformer-based time-series models.

---

#### Is Random Forest the Best Choice Here?

**Short answer: Yes, for a capstone scope — with one important caveat.**

**Arguments for Random Forest:**

1. **Doukha 2025 directly compared RF vs LSTM on the same task** (cloud resource utilization prediction) and RF won: MAPE 2.65% vs MAPE 17.43%. This is the most directly relevant comparison in the literature.
2. **Interpretability** — RF feature importances tell you which inputs drive the prediction. For a research capstone, explaining *why* the model admitted or rejected a job is valuable.
3. **Tabular data is RF's home turf** — the Google Cluster Trace and Azure trace datasets are tabular (rows = jobs, columns = features). LSTMs are optimized for sequential raw time-series; RF is optimized for structured tabular features, which is what we have.
4. **No sequence padding / variable-length headaches** — LSTM requires fixed-length input windows; job histories vary wildly in length. RF handles this cleanly with aggregated features (P50, P90, ratio).
5. **Fast inference** — a placement decision needs to happen in milliseconds. RF inference is a lookup through decision trees; LSTM requires a forward pass through recurrent layers.
6. **Lower data requirement** — LSTM needs significantly more examples to converge. RF generalizes well from smaller tenant history windows.

**The caveat — what RF cannot do:**

RF predicts a single value (scalar peak or duration). It cannot predict the *shape* of usage over time in a data-driven way. That is why the trapezoid pulse model is used as a post-processing step to generate `m̂_j(t)`. The trapezoid is a simplification; if temporal profile shape turns out to vary significantly across job types, this is a weakness.

---

#### Alternative and Complementary Models

**1. XGBoost / LightGBM (gradient boosted trees)**
- Same paradigm as RF but often higher accuracy on tabular data with less tuning
- Resource Central already validates this: RF and XGBoost were used together, both at 79–90%
- **Could replace RF** or be run in ensemble; LightGBM is faster to train than XGBoost on large datasets
- **Recommendation:** worth running as a comparison baseline; negligible implementation cost

**2. LSTM (Long Short-Term Memory)**
- Used by Wang & Yang (MAPE 7.2%), Kofi (R²=0.99), but *lost* to RF in Doukha's head-to-head
- Best suited for predicting the *temporal usage profile* `m̂_j(t)` directly (i.e., the full curve, not just the peak scalar)
- **Could complement RF** in a two-stage pipeline: RF predicts the peak + duration, LSTM predicts the shape of the curve between start and peak
- Main tradeoff: significantly more complex to train, tune, and explain; needs sequential history per tenant

**3. Gaussian Process Regression (GPR)**
- Not used by any paper reviewed
- Key advantage: provides *uncertainty estimates* natively — instead of a point prediction `m̂_j^peak`, you get a distribution `N(μ, σ²)`
- This would make the safety margin in the optimization model principled: instead of `m̂ · 1.05`, you could use `μ + 2σ` (a 95% confidence upper bound)
- Main tradeoff: does not scale well to large feature sets; computationally expensive at prediction time
- **Could complement RF** as a calibration layer on top: use RF for the mean estimate, GPR to model residual uncertainty

**4. Quantile Regression (as a wrapper)**
- Not a separate model — any tree-based model (RF, XGBoost) can be configured to output quantiles directly
- Instead of predicting the mean `m̂`, predict the P90 or P95 directly as the training target
- This is actually closer to what Resource Central does: they predict P95 utilization, not mean
- **Recommendation:** use quantile regression framing on top of RF — output the P95 directly rather than mean + post-hoc scaling

**5. Temporal Fusion Transformer (TFT)**
- State-of-the-art for multi-horizon time-series forecasting (predicts the full temporal profile)
- Not used in any paper reviewed; represents cutting-edge literature beyond capstone scope
- **Not recommended** for capstone unless the team has significant ML engineering background

---

#### Recommended Approach for the Capstone

```
Primary model:   Random Forest (or LightGBM)
                 → predicts P95 peak memory, P95 peak CPU, job duration
                 → justified by Doukha 2025 head-to-head comparison

Enhancement 1:   Quantile Regression framing
                 → train RF to directly minimize quantile loss at P90/P95
                 → avoids the ad-hoc ×1.05 safety buffer

Enhancement 2:   XGBoost as comparison baseline
                 → run both; report which wins on Google Cluster Trace holdout
                 → validates model selection in the paper

Optional:        LSTM for temporal profile shape (if scope allows)
                 → predict the usage curve shape, not just the peak
                 → replaces the trapezoid heuristic with a data-driven shape
                 → cite Wang & Yang (2025) and Kofi (2025) as prior art
```

The core argument for Random Forest remains: it is the only model in this literature body that was directly compared to LSTM on the same task and won by a significant margin (MAPE 2.65% vs 17.43%). The alternatives are worth acknowledging and comparing against, but RF is the defensible primary choice.

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
| **Core** | Liu & Guitart 2025 (In-node DRC) | In-node enforcement layer — DRC-style cgroup coordination after placement; complements Blagodurov |
| **Core** | Kovalenko & Zhdanova 2024 (Math Model) | Formal mathematical backbone — discrete optimization structure with explicit constraints to extend with prediction + DRF |
| **Support** | Alatawi 2025 (RL Serverless) | Gini coefficient as fairness metric; RL comparison baseline to contrast your RF+DRF approach against |
| **Support** | Zhao et al. 2021 (AaaS Admission) | Formal admission control formalization; profit=utilization framing for cloud provider stakeholders |

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
