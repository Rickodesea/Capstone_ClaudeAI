  Proposal Draft

  Title: Predictive Multi-Tenant Job Scheduling in Kubernetes: Maximizing Utilization While Preserving Fairness and SLA Compliance

  ---

  ## Basic Idea

  Imagine a shared office building where multiple companies rent desk space. Each company tells the building manager "I need 20 desks," but on any given day they only actually use 12. The building manager, following the rules, keeps 20 desks reserved per company — so half the building sits empty even when other companies are waiting for a single desk. To make things worse, some companies have urgent meetings (latency-sensitive jobs) while others are just doing background filing (batch jobs), but they all wait in the same queue with no priority distinction.

  Now imagine a smarter building manager who: (1) looks at how much desk space each company has historically used — not just what they asked for — and reserves only what they will likely actually need; (2) when multiple companies need desks at the same time, gives priority to the one that has been waiting longest or has used the least space so far; and (3) guarantees that companies with urgent meetings always get their desks on time, even if it means the filing team has to wait a little.

  That is what this project builds — but for cloud computing. The "building" is a Kubernetes cluster (a pool of servers). The "companies" are tenants (teams or organizations sharing those servers). The "desks" are CPU and memory. The "smarter building manager" is our custom Kubernetes scheduler.

  We are not building a cloud platform like AWS. We are building the decision-making model that sits inside a Kubernetes cluster and makes smarter admission and placement decisions using machine learning and optimization. We test it by replaying real workload data from Google's production cluster through a simulation.

  ---

  ## Concepts

  **Multi-Tenant Cluster**
  A shared pool of servers where multiple organizations ("tenants") submit their workloads. They share the same physical resources but expect isolation and fairness. Example: a university research cluster shared by multiple research groups.

  **Kubernetes**
  The dominant open-source cluster manager — same category as Google Borg, Apache Mesos. It accepts workload submissions, decides which server runs each one, and keeps them running. Think of it as the operating system for a cluster. It does not know or care whether the servers underneath are on AWS, GCP, or a university datacenter.

  **Pod / Container**
  The unit of work in Kubernetes. A container is like a lightweight virtual machine — it holds one application and its dependencies, starts in seconds, and shares the host's OS kernel. A pod is one or more containers that are scheduled together. When we say "job," we mean a pod or a group of pods submitted by a tenant.

  **Scheduler**
  The component inside Kubernetes that decides *which server (node) runs which pod*. It answers: "Given this pod's requirements and current cluster state, which node is the best fit?" The default Kubernetes scheduler is simple — it only looks at what resources are available right now, not what will be available in 30 minutes.

  **Optimizer vs. Scheduler — what is the difference?**
  This is where most confusion starts. The scheduler is the *mechanism* (the Kubernetes component that does the placement). The optimizer is the *decision logic* (the mathematical model or ML model that tells the scheduler which choice is best). In our project, we are building the optimizer — a prediction model + fairness model + SLA enforcement logic — and plugging it into Kubernetes as a scheduler plugin. So the scheduler runs our optimizer's recommendations. They work together: optimizer says "Node 3 is the best and safest choice," scheduler executes that decision.

  **Admission Control**
  The front door check that happens *before* the scheduler even sees a job. Admission control asks: "Should we accept this job at all right now?" If the answer is no (e.g., predicted memory usage would overflow the cluster), the job goes into a queue. Our predictive admission control layer is what makes safe overcommitment possible — we only admit a job when the model predicts the node can handle it.

  **Resource Overcommitment**
  Accepting more workloads than the server's declared capacity on paper, relying on the fact that not all jobs will peak simultaneously. Safe when done with accurate prediction (we know Job A peaks at 2pm and Job B peaks at 8pm, so they can share the same node). Dangerous without prediction (you accept both without knowing they both spike at noon — OOM kill happens).

  **SLA (Service Level Agreement)**
  A formal contract stating what level of service a tenant is guaranteed. In our context: "your job will complete within X minutes" or "your service will experience less than 5 minutes of downtime per month." Violating the SLA has legal and financial consequences. Our system is designed to prevent violations proactively, not just react after they occur.

  **Memory OOM Kill**
  When a container exceeds its memory limit, the Linux kernel kills it immediately — no warning. This is fundamentally different from CPU throttling, which just slows the job down. Memory violations are fatal and directly violate SLAs. This is why memory is the primary constraint in our model: an OOM kill means the job is gone and must restart from scratch.

  **Dominant Resource Fairness (DRF)**
  A fairness algorithm from Ghodsi et al. (2011). For each tenant, find their "dominant resource" — whichever resource (CPU or memory) they consume the largest fraction of across the cluster. DRF ensures that no tenant's dominant share grows disproportionately compared to others. It is the fairness backbone of our scheduler. Proven properties: no tenant prefers another tenant's allocation, everyone gets their fair share, and the system cannot be gamed by lying about needs.

  **Gini Coefficient**
  A number between 0 and 1 measuring inequality in resource distribution across tenants. 0 = perfect equality (every tenant gets exactly the same proportion). 1 = total inequality (one tenant gets everything). A well-designed fair scheduler should keep this number low. Alatawi (2025) shows their system reduces it from 0.25 to 0.10 — that improvement range is our target.

  **Google Cluster Trace v3**
  A publicly available dataset from Google containing real job submission records from their production cluster: job arrival times, declared resource requests, actual measured usage, job duration, tenant IDs, priority levels. This is our training and simulation dataset. Multiple papers (Kofi, Jiang Zhi) have validated it as suitable for exactly this type of research.

  ---

  ## Pointers

  - We can focus on Kubernetes as our cluster management platform. It is the standard deployment environment for multi-tenant workloads in production cloud systems, is well-documented, and has a formal extension point (the Scheduling Framework) that lets us plug in custom logic without modifying Kubernetes itself.

  - We will assume: (1) all workloads are containerized and submitted as Kubernetes Jobs or Deployments — no VM-level scheduling; (2) Google Cluster Trace v3 is our primary dataset; (3) simulation is the primary evaluation method — a discrete-event simulator replays workloads, no live cluster required; (4) memory is the primary scarce resource driving SLA violations — CPU throttling slows jobs down, memory OOM kills terminate them; (5) tenants over-declare memory requests as a safety buffer — actual usage is consistently lower than declared, and that gap is the overcommitment opportunity.

  - We will not build a full cloud system like AWS. We build the scheduling model / scheduler plugin and evaluate it via simulation on real trace data.

  - One thing we could keep simple: rather than predicting all resources at once, we can start with memory as the single constrained resource and CPU as secondary. This matches the group's original angle and keeps the math tractable for a capstone scope.

  - The additive peaks idea is powerful and explainable: if we model each job's memory usage as a pulse (ramps up, plateaus, ramps down), the total memory on a node is the sum of all active pulses. Admitting a new job means adding one more pulse. We predict whether the sum crosses the node's memory limit at any point during the job's lifetime. If it does, we deny or delay admission.

  - For the Kubernetes platform specifically, our scheduler hooks into three phases: (1) PreFilter/Admission Webhook — runs before scheduling, enforces tenant quotas, makes the ML-based admit/deny decision; (2) Score — ranks candidate nodes using DRF fairness + temporal co-location score; (3) Runtime Controller — adjusts cgroups weights every few seconds based on live Prometheus metrics.

  - A possible scope simplification: we can assume a single cluster (not multi-cluster), single cloud provider, and Linux containers only. This is consistent with the Google Cluster Trace dataset and avoids Windows VM complexity.

  ---

  ## Suggested Topic Sentences

  Option 1 (technical, precise):
  "This project designs a predictive scheduling and admission control system for multi-tenant Kubernetes clusters that closes the gap between declared resource requests and actual memory usage, enabling safe overcommitment while enforcing per-tenant SLA compliance and fairness."

  Option 2 (optimization framing, matches group's angle):
  "We propose an optimization-based scheduling model for multi-tenant Kubernetes clusters that focuses on shared memory utilization as the primary constraint, using machine learning to dynamically allocate resources, maximize cluster utilization, minimize SLA violations, and maintain fairness across tenants."

  Option 3 (problem-first, accessible):
  "Cloud clusters waste up to 40–60% of available memory because schedulers rely on declared requests rather than actual usage — this project builds a smarter Kubernetes scheduler that uses tenant workload history to predict real memory demand, safely pack more jobs onto each node, and prevent SLA violations before they occur."

  ---

  ## Suggested Hypothesis

  We hypothesize that a Kubernetes scheduler augmented with (1) machine learning-based predictive admission control, (2) Dominant Resource Fairness-aware placement, and (3) dynamic cgroup-based SLA enforcement will achieve significantly higher cluster memory utilization than the default Kubernetes scheduler while maintaining SLA compliance rates above 95% and reducing inter-tenant fairness inequality, as measured by Gini coefficient and dominant share variance, on workloads replayed from the Google Cluster Trace v3.

  Specifically, we expect:
  - Cluster memory utilization to increase from ~45–60% (default K8s baseline) to ≥ 85%
  - SLA violation rate to remain below 5% (matching or improving on Priya 2025's benchmark)
  - Gini coefficient across tenants to decrease from ~0.25 toward ~0.10 (matching Alatawi 2025's improvement range)
  - Prediction accuracy (MAPE) below 5% using Random Forest on tenant history features

  ---

  ## Suggested Introduction

  Cloud infrastructure costs are escalating on multiple fronts. Conventional DRAM supplies are contracting as manufacturers redirect production toward high-bandwidth memory for AI accelerators, with prices projected to rise 54–116% year-over-year (S&P Global, 2026). The operational cost of leaving compute and memory resources idle compounds these pressures. This creates a compelling imperative for cloud providers: extract significantly more value from existing hardware before buying more. The bottleneck is not hardware availability — it is the scheduler.

  Current cluster memory utilization in production environments averages between 40% and 60%, meaning that on any given node, nearly half its memory sits idle while tenants wait for their jobs to start (Chaudhari, 2025). This waste exists not because hardware is unavailable, but because schedulers are conservative. The default Kubernetes scheduler places workloads based solely on declared resource requests and current availability, with no awareness of how much memory jobs will actually use, when co-located jobs will peak, or whether admitting a new job will cause a neighbor's SLA to be violated in the next thirty minutes. The result is predictable: either the scheduler over-admits and memory contention causes OOM kills, or it under-admits and expensive hardware idles.

  Multi-tenancy sharpens the problem. When multiple teams share the same cluster, a single greedy tenant can monopolize memory, causing others to queue arbitrarily long. Kubernetes has no native fairness mechanism — it processes requests in arrival order without regard to whether one tenant has already consumed a disproportionate share of cluster memory. Latency-sensitive jobs compete against batch workloads with no systematic prioritization, and memory violations are discovered after the fact rather than prevented at admission time.

  This project addresses the gap between what the Kubernetes scheduler currently does and what a production multi-tenant cluster requires. The gap is precise: no existing system combines predictive admission control, multi-resource fairness, and runtime SLA enforcement into a single coherent Kubernetes scheduler. Chaudhari (2025) describes the need for exactly this combination — a Workload Classifier, Fairness Engine, and Predictive Resource Manager — but the framework exists only as a proposal with no implementation or evaluation. We build it.

  The proposed system is a custom Kubernetes scheduler plugin with three integrated components. First, a predictive admission layer trains a Random Forest model on tenant workload history from the Google Cluster Trace to predict the actual peak memory utilization of each submitted job. Tenants routinely over-declare memory requests as a safety buffer; the actual usage is consistently lower. The model learns this gap per tenant. A new job is admitted only if the predicted combined memory usage of all co-located jobs stays below a safe threshold. Second, a fairness-aware placement layer applies Dominant Resource Fairness (Ghodsi et al., 2011) to rank candidate nodes during scheduling. The tenant with the smallest share of their dominant resource gets priority for the next scheduling slot, ensuring no team waits disproportionately long regardless of workload type. Third, a runtime enforcement layer assigns memory priority weights to pods via Kubernetes QoS classes and dynamic cgroup controls. Critical latency-sensitive jobs receive guaranteed memory access; batch jobs operate on remaining capacity and are dynamically throttled when a co-located critical job approaches its SLA boundary.

  The system is evaluated against the Google Cluster Trace v3 using discrete-event simulation, comparing against the default Kubernetes scheduler and a static DRF baseline. Target outcomes are cluster memory utilization above 85%, SLA compliance above 95%, and inter-tenant fairness variance below 10%.

  ---

  ## Suggested Optimization Model Design

  *Adapting Kovalenko & Zhdanova (2024) as the structural foundation, extended with DRF fairness, Random Forest prediction, and SLA deadline constraints.*

  Kovalenko & Zhdanova (2024) provide one of the few Kubernetes scheduling papers that writes down actual mathematical constraints — typed variables, objectives, and hard bounds — rather than describing goals in prose. Their discrete combinatorial model assigns pods to nodes across a time horizon and is the closest existing formulation to what our capstone needs. We take it as the structural skeleton and make four targeted extensions.

  **What we keep from Kovalenko:**
  - Binary pod-to-node assignment variable: x_{n,j} ∈ {0,1}
  - Per-node resource capacity constraints (CPU and memory per time period)
  - Multi-objective structure (utilization + penalty terms)
  - Discrete time horizon T (each period = ~5-minute snapshot)

  **What we change:**

  - Declared requests → predicted utilization. Replace static `cp, mp` with RF-predicted values `ĉ_j(t), m̂_j(t)` from our Random Forest model. This is the core enabler of safe overcommitment — scheduling on predictions rather than inflated declarations.
  - Add DRF fairness constraint. Define each tenant's dominant share `d_k = max(memory share, CPU share)`. Constrain `d_k ≤ (1/|K|) + ε` so no tenant's dominant share exceeds the fair-share baseline by more than a tunable tolerance `ε`.
  - Add SLA deadline constraint. If a job is placed, it must complete before its deadline: `T_j^start + dur_j ≤ D_j`. Jobs that cannot meet their deadline on any available node are queued, not placed.
  - Add temporal peak-time safety check. Rather than enforcing the memory capacity constraint at every time period equally, enforce it specifically at the RF-predicted peak time `t*_j` of each job — the moment its memory usage is highest. This is tighter and more efficient than Kovalenko's uniform period check.
  - Remove server power on/off objective. Not applicable to our Kubernetes context — nodes are pre-provisioned VMs managed by the cloud provider, not turned on/off by the scheduler.

  **Condensed objective function:**

  > maximize: w₁ · (avg memory utilization across nodes) − w₂ · (SLA violation penalty) − w₃ · (dominant share spread across tenants)

  Suggested starting weights: w₁ = 0.5 (utilization priority), w₂ = 0.35 (SLA protection), w₃ = 0.15 (fairness).

  **For capstone simulation:** The full model is NP-hard (binary assignment + multi-objective). Implement as a greedy sequential admission algorithm: for each incoming job, check constraints in order — memory safety at predicted peak → CPU safety → fairness bound → SLA deadline → if all pass, admit; otherwise, queue. This is how production schedulers work and is tractable at simulation scale.

  ---

  ## Suggested Prediction Model Design

  *Built on Resource Central (Cortez 2017) as the architecture, Random Forest justified by Doukha (2025), preprocessing from Kofi (2025).*

  The optimization model above needs `m̂_j(t)` and `ĉ_j(t)` — predicted memory and CPU usage of each job at each point in time. The prediction model computes these before the scheduler makes its placement decision.

  **Three things to predict:**

  - P95 memory peak `m̂_j^peak` — worst-case usage for the admission gate check (C1)
  - Temporal usage profile `m̂_j(t)` — the shape of usage over the job's lifetime, used for temporal co-location scoring and peak conflict detection
  - Job duration `dur_j` — when resources free up, used for the SLA deadline check and queue time estimation

  **Key features (Random Forest inputs):**

  - Tenant's historical actual/declared memory ratio — the single most predictive feature (per Resource Central: tenant history dominates job-level features)
  - Tenant's historical actual/declared CPU ratio
  - Declared memory request and CPU request (job-level)
  - Job priority class (BestEffort / Burstable / Guaranteed)
  - Hour of day and day of week (temporal usage cycle features)
  - Current node memory utilization
  - Hours until the peak of the largest job currently running on the candidate node

  **Model:** Random Forest Regressor (Doukha 2025: MAPE 2.65% vs LSTM's 17.43%). Preprocessing: Savitzky-Golay smoothing + min-max normalization on the Google Cluster Trace v3 (Kofi 2025 pipeline). Output scaled by a 5% uncertainty buffer before being fed into admission constraints.

  **Temporal profile generation:** Model each job's memory usage as a trapezoid pulse — ramp up for 10% of predicted duration, plateau for 80%, ramp down for 10%. The total memory on any node at time `t` is the sum of all active pulses. Admission check: will adding this new pulse cause the sum to exceed `α · RAM_n` at any point during the job's lifetime?

  **Drift handling:** Retrain weekly or whenever prediction MAPE on recent jobs exceeds 8%. This directly addresses the model drift gap Perera (2025) identifies as unsolved in the RL scheduling literature.

  ---

  ## Brief Literature Review

  | # | Paper | Core Contribution | Gap It Addresses | What We Could Include | Model Type |
  |---|---|---|---|---|---|
  | 1 | Chaudhari (2025) | K8s fails for AI/ML; proposes 4-component framework — never implemented or evaluated | Gap anchor: defines what we build | Architecture blueprint; utilization baselines from Tables 1 & 2 (60% → 92%) | None — conceptual framework |
  | 2 | Jiang Zhi (2025) | Overcommitment study on real traces; Clovers Python simulator; Google/Azure/Alibaba datasets | Simulation and evaluation environment | Clovers simulator design or our own equivalent; trace datasets | None — rule-based simulator |
  | 3 | Coach — Reidys (2025) | Co-locate workloads with non-overlapping temporal usage peaks; ~26% more workloads on same hardware | Safe overcommitment strategy | Temporal co-location scoring: prefer nodes whose running jobs peak at different times | Prediction: temporal pattern forecasting |
  | 4 | DRF — Ghodsi (2011) | Dominant Resource Fairness: formal multi-resource fair allocation with proven properties | Fairness backbone of the scheduler | DRF algorithm as our Score phase; extend with predicted utilization instead of declared requests | Fairness algorithm: LP-proven dominance equalization |
  | 5 | Resource Central — Cortez (2017) | Random Forest predicts P95 CPU/memory from tenant subscription history; 79–90% accuracy; 65% fewer failures | Prediction architecture | RF model; tenant history as primary feature; Azure dataset as secondary validation | Prediction model: Random Forest (P95 utilization) |
  | 6 | Blagodurov et al. | cgroups CPU weights for critical vs batch co-location; near-100% server utilization with SLA maintained | Runtime SLA enforcement layer | Dynamic cgroups weight controller adjusting every 5 seconds; critical = Guaranteed QoS class | Scheduler optimizer: cgroups weight equations |
  | 7 | Wang & Yang (2025) | LSTM+DQN on Kubernetes; 32.5% better utilization, 43.3% latency reduction on 208-core cluster | K8s deployment model reference | Kubernetes scheduler plugin architecture; LSTM+DQN as performance comparison baseline | Prediction (LSTM) + RL scheduler (DQN) |
  | 8 | Doukha (2025) | Random Forest clearly beats LSTM (MAPE 2.65% vs 17.43%) on CPU/memory prediction | Model selection justification | Random Forest as primary predictor; MSE and MAPE as evaluation metrics | Prediction comparison: RF vs LSTM |
  | 9 | Atropos — Hu (2025) | Target the culprit workload causing overload, not the innocent victim; fewer total cancellations | Overload fallback mechanism | Targeted throttling of culprit pod when prediction fails and overload occurs | None — monitoring-based detection |
  | 10 | Priya (2025) | Full QoS-aware K8s: SloPolicy CRD + scheduler plugin + Prometheus feedback loop; 45% P99 reduction, <5% SLA violation | Most directly applicable architecture | SloPolicy CRD design; scheduler plugin structure; Prometheus monitoring loop; evaluation benchmark | Scheduler optimizer: QoS scoring function |
  | 11 | Kofi (2025) | LSTM on Google Cluster Trace v3; R²=0.99 with Savitzky-Golay + min-max normalization preprocessing | Dataset validation and preprocessing | Google Cluster Trace v3; preprocessing pipeline before training; R²=0.99 as accuracy benchmark | Prediction model: LSTM (R²=0.99) |
  | 12 | Perera (2025/2026) | RL schedulers suffer model drift and interpretability issues in production | Model choice justification | Drift detection / periodic retraining step; Random Forest interpretability argument vs RL | None — review / landscape paper |
  | 13 | Pinnapareddy (2025) | Right-sizing, bin packing, autoscaling in K8s; cost and sustainability framing | Problem motivation; tooling | Kubecost for per-tenant resource cost attribution in evaluation; bin packing framing | None — practitioner analysis |
  | 14 | Patchamatla | K8s on OpenStack; VM-hosted vs bare-metal containers; scheduler coordination gap | Deployment context | Deployment architecture reference for private cloud / OpenStack environments | None — experimental comparison |
  | 15 | Liu & Guitart (2025) | In-node group-aware scheduling + Dynamic Resource Controller (DRC); 242–319% throughput gain | In-node enforcement after placement | DRC concept for in-node cgroup coordination; post-placement resource rebalancing within a node | Scheduler optimizer: in-node cgroup assignment |
  | 16 | Kovalenko & Zhdanova (2024) | Formal discrete optimization model for K8s: explicit objectives + constraints | Mathematical formalization backbone | Objective function structure; constraint formalization (CPU/memory per node, pod assignment variables) | Scheduler optimizer: discrete combinatorial LP |
  | 17 | Alatawi (2025) | RL (MDP) adaptive resource allocation; Gini coefficient fairness; 98% SLA compliance; 50% latency reduction | Fairness metric + RL comparison baseline | Gini coefficient as fairness metric alongside DRF; RL as baseline to argue against in model choice | RL policy model: MDP-based allocation |
  | 18 | Zhao et al. (2021) | Formal admission control + profit optimization for AaaS; deadline + budget SLA constraints | Admission control formalization | Admission algorithm structure; profit = utilization framing; SLA constraint formalization | Scheduler optimizer: admission control + profit LP |
