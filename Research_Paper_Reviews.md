# Research Paper Reviews — Capstone Focus: Scheduling, Utilization, Fairness, SLA

**Focus:** Multi-tenant workload scheduling | Improving utilization | Maintaining fairness and SLA | Running jobs (VMs + containers) | No ballooning/swapping/termination | Overcommitment only with accurate prediction

---

## Quick Context: Kubernetes, VMs, Containers, and Jobs

**Kubernetes** orchestrates **containers** (via pods), not VMs. But real cloud deployments layer both:
- The physical cluster runs **VMs** as worker nodes (e.g., AWS EC2, Azure VMs, GCP instances)
- Inside those VMs, Kubernetes runs **containers** (your actual workloads)
- On top of containers, you can submit **jobs** (batch work) or **services** (long-running)

So: **VM = the node | Container = the workload unit | Job = a scheduled unit of work**

Your instructor saying "containers and not just VMs" means the scheduling problem spans all three layers. This is exactly the scope the papers below address. Multi-tenancy means multiple teams sharing this infrastructure simultaneously.

---

## Papers

---

### 1. Chaudhari (2025) — Multi-Tenant AI Workload Scheduling on Kubernetes

**What it's about:**  
A synthesis paper reviewing why Kubernetes's default scheduler fails for AI/ML workloads. Covers three scheduling strategies: gang scheduling (run only when all resources available), topology-aware placement (put related workers on same rack/NVLink), and predictive resource management. Proposes a 4-component architecture: Workload Classifier → Fairness Engine → Topology Optimizer → Priority Queue. All numbers are borrowed from cited papers — no original experiments.

**The Gap:**  
The framework was never built or evaluated. Numbers are from other papers. There's no admission control, no overcommitment handling, and no SLA violation prevention mechanism. The fairness engine and predictive management are described but not designed in detail.

**How to build your capstone on it:**  
Use it as the roadmap and build one or more of the components. The most impactful angle: implement the Fairness Engine + Predictive Resource Management as a working scheduler plugin on Kubernetes. Your differentiation is that you add safe overcommitment with accurate prediction — something this paper has no answer for.

| Has Math Optimization Model | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| No | No (cites others) | No — conceptual only |

---

### 2. Jiang Zhi (2025) — A Study on Overcommitment in Cloud Providers

**What it's about:**  
A master's thesis. Zhi builds two tools: (1) a data collector that pulls real workload traces from Google Borg, Azure, and Alibaba Cloud, and (2) a cloud simulator called **Clovers** that lets you test overcommitment policies without real infrastructure. He tests machine-centric overcommitment (overcommit at the physical machine level) vs. container-centric overcommitment (overcommit at the container level). He compares these against real traces and measures QoS impact.

Key finding: overcommitment works, but current policies are blunt — they either waste resources or violate QoS unpredictably. Scheduling algorithms are treated separately and compared. Containers and VMs both appear.

**The Gap:**  
The simulator has no ML-based prediction — overcommitment policies are static or rule-based. No predictive model to decide whether to accept a workload. No fairness analysis. The future directions section explicitly lists ML-based overcommitment policies, clustering-based policies, and comparison of scheduling algorithms as open problems.

**How to build your capstone on it:**  
Zhi's Clovers simulator is a ready-made evaluation environment. You can extend it with an ML-based admission policy that predicts utilization before accepting a workload. Use his Google Borg / Alibaba traces as your dataset. Target his open research questions directly.

| Has Math Optimization Model | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| Basic formulation (overcommit ratio) | No | Yes — Clovers simulator built in Python, real trace data available |

---

### 3. Reidys et al. / Coach (ASPLOS 2025) — All-Resource Oversubscription Using Temporal Patterns

**What it's about:**  
A Microsoft Research paper analyzing 1 million+ Azure VMs. Key finding: many VMs have complementary usage patterns (one peaks at noon, another at night). Coach exploits this by creating a new VM type called **CoachVM**, which splits each resource into a guaranteed portion + an oversubscribed portion. The scheduler co-locates VMs whose peak patterns don't overlap. Memory is the hardest to manage, so they focus there. Result: **~26% more VMs hosted** with minimal performance degradation. Uses long-term predictions and monitoring to detect/prevent contention.

**The Gap:**  
Coach handles VM-level oversubscription. It does not address job/container scheduling fairness. The scheduling policy is greedy (co-locate complementary patterns) — no fairness guarantees between tenants. SLA compliance is measured but not formally enforced. The prediction model for temporal patterns is described but not open-sourced.

**How to build your capstone on it:**  
Coach proves temporal-pattern-based overcommitment is valid and safe. Adopt the core idea: predict complementary patterns and co-locate workloads. Add fairness constraints (using DRF) so no tenant is starved. Your improvement: a scheduler that does what Coach does but with explicit SLA compliance guarantees and multi-tenant fairness, not just utilization.

| Has Math Optimization Model | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| No formal optimization | Yes — temporal pattern prediction, long-term forecasting | Partial — describes CoachVM mechanics, evaluation on Azure traces |

---

### 4. Ghodsi et al. (2011) — Dominant Resource Fairness (DRF)

**What it's about:**  
The foundational paper on multi-resource fair scheduling. The problem: how do you fairly allocate CPU, memory, and other resources to tenants with heterogeneous demands? DRF generalizes max-min fairness: each user's dominant resource (the one they're using the highest percentage of) is equalized across users. Formally proven to satisfy four key properties: sharing incentive, strategy-proofness, Pareto efficiency, and envy-freeness. Implemented in **Apache Mesos**. Beats slot-based Hadoop fair scheduler by up to 66% on large job completion times using Facebook production traces.

**The Gap:**  
DRF assumes static resource pools with no overcommitment. It does not account for temporal variation in demand — if a user's dominant resource shifts over time, DRF doesn't adapt proactively. No predictive component. Does not handle SLA deadlines or latency-sensitive workloads separately.

**How to build your capstone on it:**  
DRF is your fairness backbone. Use it as the fairness algorithm in your scheduler. Extend it with time-awareness: instead of computing dominant share on current usage, compute it on predicted future usage. This gives you a **predictive DRF** that stays fair even as workloads shift. No one has combined DRF + temporal prediction in the literature you have.

| Has Math Optimization Model | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| Yes — formal LP formulation and proofs | No | Yes — Algorithm 1 (pseudocode), implemented in Mesos, Facebook trace evaluation |

---

### 5. Cortez et al. / Resource Central (SOSP 2017) — Workload Prediction at Azure

**What it's about:**  
Microsoft Research builds **Resource Central (RC)**, a system that collects VM telemetry from Azure and trains ML models to predict: average CPU utilization, P95 CPU utilization, VM lifetime, deployment size, and workload class (interactive vs. batch). Uses **Random Forest** and **XGBoost** classifiers. Accuracy: 79–90% depending on metric. Key insight: VMs from the same subscription behave consistently, so subscription history is a strong predictor. Shows that prediction-informed oversubscription prevents CPU exhaustion while reducing scheduling failures by 65% vs. baseline.

**The Gap:**  
RC only oversubscribes CPU, not memory or network. Prediction happens at VM creation time, not dynamically. Does not enforce fairness — it's a scheduling helper, not a fair allocator. No gang scheduling or topology awareness. Memory-specific oversubscription is explicitly left as future work.

**How to build your capstone on it:**  
RC gives you the prediction architecture. Use subscription history + VM size as features. Train a model that predicts P95 utilization for the resources you care about. Plug this prediction into your scheduler's admission decision: only admit a new workload if predicted combined utilization stays below a safe threshold. This is exactly the "overcommitment only with accurate prediction" requirement.

| Has Math Optimization Model | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| No | Yes — Random Forest + XGBoost, 79–90% accuracy | Yes — full training pipeline described, Azure dataset publicly available |

---

### 6. Blagodurov et al. — Maximizing Server Utilization While Meeting Critical SLAs

**What it's about:**  
Collocates critical (interactive, latency-sensitive) workloads with non-critical (batch, HPC) workloads on the same server. Uses **Linux cgroups CPU weights** to give critical workloads priority access. Static weights: critical VMs get weight 10,000, batch gets weight 2. Dynamic weights: controller adjusts every 5 seconds based on SLA attainment. Result: server runs at **near 100% utilization** while critical workload SLAs remain satisfied. Two scenarios tested: fairness mode and isolation mode.

**The Gap:**  
This is CPU-only and single-server. No cluster-wide scheduler. No admission control — it assumes you've already decided to co-locate, just manages the weights reactively. The dynamic model is a simple proportional feedback controller; no ML or prediction. Does not consider multi-resource fairness.

**How to build your capstone on it:**  
The weight-based priority model is your enforcement mechanism. When your scheduler admits new workloads, assign them CPU weights dynamically based on their SLA class. Combine this with predictive admission (from RC above) to decide when collocation is safe, then use cgroups weights to ensure SLA compliance during execution. You get admission control + enforcement in one system.

| Has Math Optimization Model | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| Yes — Equations 1–3 for dynamic weight calculation | No | Yes — working prototype on KVM/Linux, real workload experiments (RUBiS, Wiki) |

---

### 7. Wang & Yang (2025) — Intelligent Resource Allocation via LSTM + DQN

**What it's about:**  
Proposes an end-to-end cloud resource allocation system combining **LSTM** for demand prediction and **Deep Q-Network (DQN)** for dynamic scheduling decisions. Deployed on **Kubernetes** with a custom controller. LSTM uses a 12-hour sliding window to predict demand 30 minutes ahead. DQN decides expand/contract/migrate actions. Results: 32.5% better utilization, 43.3% reduction in response time, 26.6% cost reduction. Tested on a 208-core Kubernetes cluster using real e-commerce workloads.

**The Gap:**  
No fairness mechanism — the system optimizes global metrics, not per-tenant equity. The DQN training requires large datasets (10TB used) and long training time (4.5 hours). No formal SLA enforcement — SLA compliance is tracked but not guaranteed. Multi-tenant isolation is absent.

**How to build your capstone on it:**  
This gives you the RL-based scheduling approach on Kubernetes. Simpler version for your capstone: use LSTM for prediction (they give you architecture details — 2-3 layers, 128 neurons, 12-hour window) and replace DQN with a rule-based admission decision guided by the prediction. Add fairness by incorporating DRF into the scheduling reward function.

| Has Math Optimization Model | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| Yes — Equations 1–2 (objective function, reward) | Yes — LSTM (RMSE 0.086, MAPE 7.2%) | Yes — full architecture, Kubernetes setup, dataset described |

---

### 8. Doukha & Ez-zahout (2025) — Enhanced VM Resource Optimization

**What it's about:**  
Compares **Random Forest vs. LSTM** for predicting VM CPU utilization. Uses Prometheus for real-time monitoring and Grafana for visualization. Tests both on actual VM workloads. Result: **Random Forest wins clearly** — MAPE 2.65% vs. LSTM's 17.43%. Random Forest handles abrupt workload changes better. LSTM struggles without massive datasets and hyperparameter tuning.

**The Gap:**  
Single-VM focus — no multi-tenant scheduling. Prediction is only for CPU, not memory or network. No integration with an actual scheduler. The monitoring system is reactive, not predictive in terms of admission control.

**How to build your capstone on it:**  
Critical model selection evidence. When you build your prediction component, use Random Forest as your primary model (not LSTM) unless you have a very large dataset. This paper gives you the justification. Prometheus + Grafana is your monitoring stack — they're standard in Kubernetes. Use their evaluation methodology (MSE, MAPE) to measure your own prediction accuracy.

| Has Math Optimization Model | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| No | Yes — RF (MAPE 2.65%), LSTM (MAPE 17.43%) | Yes — PROXMOX environment, Prometheus/Grafana, MSE/MAPE evaluation |

---

### 9. Hu et al. / Atropos (SOSP 2025) — Targeted Task Cancellation for Overload

**What it's about:**  
When a system is overloaded, existing approaches drop victim requests (ones waiting in queue). Atropos identifies the **culprit request** — the one holding a resource and blocking everyone else — and cancels that instead. Continuously monitors resource usage per executing request. Result: higher SLO attainment with fewer total request drops. Integrates with 6 large-scale applications, tested across 16 overload scenarios.

**The Gap:**  
This is application-level, not cluster-level. Focuses on within-application overload (e.g., database lock contention), not cross-tenant resource competition. No fairness model. Cancellation is the response — not prevention.

**How to build your capstone on it:**  
If your scheduler admits too aggressively and overload occurs, Atropos's approach gives you a principled overload response: instead of killing the lowest-priority tenant's VMs (unfair), identify the culprit workload causing contention and throttle/preempt that. This is your fallback mechanism — admission control prevents overload 95% of the time, and Atropos-style targeted preemption handles the rest. Aligns with your "no termination" rule if you interpret it as "no blind termination."

| Has Math Optimization Model | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| No | No (monitoring-based detection) | Yes — implementation in 6 real systems, 16 benchmarked overload scenarios |

---

### 10. Min et al. / VMMB (2012) — Virtual Machine Memory Balancing

**What it's about:**  
Monitors memory demand across VMs using LRU histograms, then dynamically re-balances memory allocation without modifying the guest OS. Detects memory pressure by tracking page reclamation rates. Up to **3.6x performance improvement** for memory-starved VMs with under 1% monitoring overhead.

**The Gap:**  
VM-level only — no container or job awareness. Works reactively, not predictively. No fairness model across tenants. No SLA enforcement.

**How to build your capstone on it:**  
Largely superseded by Coach and Resource Central for your purposes. If your capstone includes memory as a scheduled resource, VMMB's LRU histogram technique is how you measure real memory demand without guest OS modification. Use it as a background reference for how memory pressure detection works, but it's not a core building block for a scheduling system.

| Has Math Optimization Model | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| No | No — reactive monitoring | Yes — Linux/Xen implementation, synthetic and realistic workloads |

---

### 11. Mishra & Kulkarni (2018) — Survey of Memory Management in Virtualized Systems

**What it's about:**  
A comprehensive survey covering: shadow paging, direct paging, nested paging (hardware MMU virtualization), memory ballooning, content-based page sharing, memory compression, and swap. Covers dual-control challenges (guest OS + hypervisor both managing memory). Good background reference.

**The Gap:**  
2018 survey — pre-container-native era. Doesn't cover cgroup-based memory management in Kubernetes. No scheduler integration.

**How to build your capstone on it:**  
Background reading only. Use it to understand how memory virtualization works under the hood so you can speak credibly about why memory is harder to schedule than CPU. Not a direct building block.

| Has Math Optimization Model | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| No | No | No — survey paper |

---

### 12. Wu & Zhao — Performance Modeling of VM Live Migration

**What it's about:**  
Builds a **regression model** to predict VM migration time based on application behavior and available CPU at source/destination. Experiments on Xen. Result: migration time is significantly affected by how much CPU the hypervisor gets for migration. Coefficient of determination R² > 90% — the model is accurate.

**The Gap:**  
Migration-time model only. Doesn't address when to migrate, fairness, or admission control. Single-VM focus.

**How to build your capstone on it:**  
If your scheduler includes live VM migration as a rebalancing mechanism, you need to account for migration cost. Use this model to predict migration overhead before deciding to migrate. Prevents a scheduler from triggering expensive migrations that cause more SLA violations than they solve. Relevant only if migration is part of your scheduling strategy.

| Has Math Optimization Model | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| No | Yes — regression model (R² > 0.9) | Yes — Xen experiments, profiling methodology described |

---

### 13. Mao & Humphrey — VM Startup Time Study

**What it's about:**  
Empirical study of VM startup time across EC2, Azure, and Rackspace. Factors analyzed: time of day, OS image size, instance type, datacenter location, batch size. Spot instances have longer and more variable startup times than on-demand. Startup can range from seconds to minutes depending on load.

**The Gap:**  
Empirical observations only — no model or algorithm. 2012 data (cloud startup times have improved, but variance remains).

**How to build your capstone on it:**  
Use startup time as a scheduling constraint. If your scheduler decides to launch new VMs as part of workload placement, account for startup delay when making SLA promises. A job that needs a VM in 30 seconds cannot be scheduled on a new VM that takes 3 minutes to boot. Important for admission control timing.

| Has Math Optimization Model | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| No | No | No — empirical measurement study |

---

### 14. Krishnaiah & Rao (2025) — Optimizing Server and Memory Utilization via Coherent Caching

**What it's about:**  
Proposes coherent caching as a solution to memory overload — store frequently accessed memory pages in a network-accessible cache rather than swapping to disk. Addresses two problems: memory overload in VMs and server underutilization from over-provisioning.

**The Gap:**  
Unreviewed preprint (April 2025). The caching mechanism addresses symptoms (overload recovery), not prevention. No scheduler. No ML. No fairness.

**How to build your capstone on it:**  
Not relevant to your new focus on scheduling. Skip it.

| Has Math Optimization Model | Has Predictive Model | Training / Implementation Details |
|---|---|---|
| No | No | Minimal |

---

### 15. CPU and RAM Energy-Based SLA-Aware

**Status:** File appears to contain no content. Skip — cannot analyze.

---

## Summary: Which Papers Matter Most for Your Capstone

| Tier | Paper | Why |
|---|---|---|
| **Must use** | DRF (Ghodsi 2011) | Your fairness algorithm — formal, proven, implementable |
| **Must use** | Resource Central (Cortez 2017) | Your prediction architecture — Random Forest, real Azure data, safe oversubscription |
| **Must use** | Coach (Reidys 2025) | Your overcommitment strategy — temporal patterns, complementary colocation |
| **Core** | Blagodurov | Your SLA enforcement mechanism — cgroups weights, dynamic prioritization |
| **Core** | Wang & Yang 2025 | Kubernetes deployment model, LSTM+DQN baseline to compare against |
| **Core** | Chaudhari 2025 | Your instructor's assigned paper — gap is the unbuilt framework you're building |
| **Support** | Jiang Zhi 2025 | Simulator + real traces for evaluation; also covers containers |
| **Support** | Doukha 2025 | Use Random Forest, not LSTM — evidence for model selection |
| **Support** | Atropos 2025 | Overload handling if admission control fails |
| **Reference** | VM Migration Time | Use only if migration is in your design |
| **Reference** | VM Startup Time | Use only if VM launch is part of scheduling |
| **Skip** | VMMB, Mishra Survey, Krishnaiah | Memory-focused, not relevant to scheduling pivot |

---

## Recommended Capstone Direction (Based on All Papers)

Build a **multi-tenant cluster scheduler** that does three things:

1. **Predict before admitting** (Resource Central approach): Train a Random Forest on workload history to predict P95 utilization. Only admit a new workload if predicted combined utilization stays below your threshold. No guessing — prediction must exceed accuracy threshold or you deny admission.

2. **Schedule fairly** (DRF approach): Use Dominant Resource Fairness to decide which tenant gets scheduled next when resources are contested. No tenant can starve another.

3. **Collocate intelligently** (Coach approach): Among all schedulable options, prefer placing workloads whose temporal usage patterns complement each other (different peak times). This increases utilization without overloading.

This approach directly fills the gap in Chaudhari's paper (which proposes these components but never builds them), builds on proven methods from Resource Central (prediction) and DRF (fairness), and adds the temporal co-location insight from Coach.

**Evaluation target:** Utilization ≥ 85%, SLA compliance ≥ 95%, fairness variance between tenants < 10%, measured against a simulated cluster using Jiang Zhi's Clovers simulator or Google Borg traces.
