  Proposal Draft

  Title: Optimal Shared Memory Utilization with Service Level Guarantees in Multi-Tenant Clusters

  (Working Title — see Suggested Topic Sentences section for alternatives)

  ---

  ## Pointers  (scope, platform, assumptions)

  **Platform:**  We focus on Kubernetes as our cluster management platform. It is the standard for multi-tenant workloads in production cloud systems, is well-documented, and has a formal Scheduling Framework that lets us plug in custom logic without modifying Kubernetes itself.

  **Assumptions:**
  1. All workloads are containerized Kubernetes Jobs or Deployments — no VM-level scheduling.
  2. Google Cluster Trace (optionally v3) is our primary dataset.
  3. Simulation is the primary evaluation method — no live cluster required.
  4. Memory is the primary scarce resource driving SLA violations — CPU throttling slows jobs down, memory OOM kills terminate them.
  5. Tenants over-declare memory requests as a safety buffer — actual usage is consistently lower, and that gap is the overcommitment opportunity.

  **Scope boundary:**  We are not building a full cloud platform like AWS. We build the scheduling decision model (prediction + optimizer (prescription)) and evaluate it via simulation.

  **Possible simplification:**  Start with memory as the single constrained resource and CPU as secondary. This matches the group's original angle and keeps the math tractable for capstone scope. (Or use CPU and Memory together which is common.)

  **Additive peaks insight:**  Model each job's memory usage as a pulse (ramps up, plateaus, ramps down). Total memory on a node = sum of all active pulses. Admission decision: will adding this new pulse cause the sum to cross the node's memory limit at any point? If yes → deny or delay.

  **K8s hook points:**  Our scheduler plugs into three Kubernetes phases: (1) Admission Webhook — ML-based admit/deny before scheduling; (2) Score phase — DRF fairness + temporal co-location ranking; (3) Runtime Controller — Prometheus-driven cgroup adjustments every 5 seconds.

  ---

  ## Suggested Topic Sentences

  **Option 1 — Preferred (captures the two-model pipeline and all three outcomes):**
  "By using the output of a predictive model as the input of an optimization model that is constrained by available resources and SLA requirements, we can optimally schedule workloads on a cluster such that resource idleness is kept minimal and fairness to tenants is high to result in low operational cost and high client satisfaction."

  Option 2:
  "This project designs an optimized predictive scheduling for multi-tenant Kubernetes clusters that closes the gap between declared resource requests and actual memory usage, enabling safe overcommitment while enforcing per-tenant SLA compliance and fairness."

  Option 3:
  "We propose an optimization-based scheduling model for multi-tenant Kubernetes clusters that focuses on shared memory utilization as the primary constraint, using machine learning to dynamically predict resource requirements, maximize cluster utilization, minimize SLA violations, and maintain fairness across tenants."

  Option 4:
  "Cloud clusters waste up to 40–60% of available memory because schedulers rely on declared requests rather than actual usage — this project builds a smarter Kubernetes scheduler that uses tenant workload history to predict real memory demand, safely pack more jobs onto each node, and prevent SLA violations before they occur."

  (Working Topic Sentence)

  ---

  ## Suggested Hypothesis

  We hypothesize that a Kubernetes scheduler augmented with (1) machine learning-based prescriptive scheduling, and an optimization model that incorporates (2) Dominant Resource Fairness-aware placement, and (3) dynamic cgroup-based SLA enforcement will achieve significantly higher cluster memory utilization than the default Kubernetes scheduler while maintaining SLA compliance rates above 95% and reducing inter-tenant fairness inequality, as measured by Gini coefficient and dominant share variance, on workloads replayed from the Google Cluster Trace v3.

  Specifically, we expect:
  - Cluster memory utilization to increase from ~45–60% (default K8s) to ≥ 85%
  - SLA violation rate to remain below 5% — matching or improving on Priya (2025)'s benchmark
  - Gini coefficient across tenants to decrease from ~0.25 toward ~0.10 — matching Alatawi (2025)'s range
  - Prediction accuracy (MAPE) below 5% using Random Forest on tenant history features

  (Working Hypothesis — is it required?)

  ---

  ## Suggested Introduction

  Cloud infrastructure costs are escalating on multiple fronts. Conventional DRAM supplies are contracting as manufacturers redirect production toward high-bandwidth memory for AI accelerators, with prices projected to rise 54–116% year-over-year (S&P Global, 2026). The operational cost of leaving compute and memory resources idle compounds these pressures. This creates a compelling imperative for cloud providers: extract significantly more value from existing hardware before buying more. The bottleneck is not hardware availability — it is the scheduler.

  Current cluster memory utilization in production environments averages between 40% and 60%, meaning that on any given node, nearly half its memory sits idle while tenants wait for their jobs to start (Chaudhari, 2025). This waste exists not because hardware is unavailable, but because schedulers are conservative. The default Kubernetes scheduler places workloads based solely on declared resource requests and current availability, with no awareness of how much memory jobs will actually use, when co-located jobs will peak, or whether admitting a new job will cause a neighbor's SLA to be violated in the next thirty minutes. The result is predictable: either the scheduler over-admits and memory contention causes OOM kills, or it under-admits and expensive hardware idles.

  Multi-tenancy sharpens the problem. When multiple teams share the same cluster, a single greedy tenant can monopolize memory, causing others to queue arbitrarily long. Kubernetes has no native fairness mechanism — it processes requests in arrival order without regard to whether one tenant has already consumed a disproportionate share of cluster memory. Latency-sensitive jobs compete against batch workloads with no systematic prioritization, and memory violations are discovered after the fact rather than prevented proactively.

  This project addresses the gap between what the Kubernetes scheduler currently does and what a production multi-tenant cluster requires. The gap is precise: no existing system combines prescriptive scheduling, multi-resource fairness, and runtime SLA enforcement into a single coherent Kubernetes scheduler. Chaudhari (2025) describes the need for exactly this combination — a Workload Classifier, Fairness Engine, and Predictive Resource Manager — but the framework exists only as a proposal with no implementation or evaluation. We build it.

  The proposed system is a custom Kubernetes scheduler plugin with two integrated analytical components: a Predictive Model and an Optimization Model (Prescriptive). The Predictive Model trains a Random Forest on tenant workload history from the Google Cluster Trace to predict the actual peak memory utilization of each submitted job. Tenants routinely over-declare memory requests as a safety buffer; the actual usage is consistently lower. The model learns this gap per tenant. The Optimization Model then takes those predictions as inputs and decides whether and where to place each job, applying Dominant Resource Fairness (Ghodsi et al., 2011) to ensure no tenant monopolizes memory, and enforcing SLA deadline constraints before any placement is confirmed. A runtime enforcement layer assigns memory priority weights to pods via Kubernetes QoS classes and dynamic cgroup controls.

  The system is evaluated against the Google Cluster Trace v3 using discrete-event simulation, comparing against the default Kubernetes scheduler and a static DRF baseline. Target outcomes are cluster memory utilization above 85%, SLA compliance above 95%, and inter-tenant fairness variance below 10%.

  (Working — to be made more concise)

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

  - P95 memory peak `m̂_j^peak` — worst-case usage for the optimization model's memory safety check
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

  **Model:** Random Forest Regressor (Doukha 2025: MAPE 2.65% vs LSTM's 17.43%). Preprocessing: Savitzky-Golay smoothing + min-max normalization on the Google Cluster Trace v3 (Kofi 2025 pipeline). Output scaled by a 5% uncertainty buffer before being fed into the optimization model.

  **Temporal profile generation:** Model each job's memory usage as a trapezoid pulse — ramp up for 10% of predicted duration, plateau for 80%, ramp down for 10%. The total memory on any node at time `t` is the sum of all active pulses. Decision check: will adding this new pulse cause the sum to exceed `α · RAM_n` at any point during the job's lifetime?

  **Drift handling:** Retrain weekly or whenever prediction MAPE on recent jobs exceeds 8%. This directly addresses the model drift gap Perera (2025) identifies as unsolved in the RL scheduling literature.

  ---

  ## Brief Literature Review

  21 papers reviewed. Table below summarizes each paper's contribution, the gap it addresses, what we take from it, and what kind of model or algorithm it uses.

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
  | 18 | Zhao et al. (2021) | Formal admission control + profit optimization for AaaS; deadline + budget SLA constraints | Optimization model formalization | Admission algorithm structure; profit = utilization framing; SLA constraint formalization | Scheduler optimizer: admission control + profit LP |
  | 19 | Verma et al. — Borg (2015) | Google's large-scale cluster manager; overcommitment via workload sharing; 20–30% more machines needed without sharing | Foundational industry context; overcommitment justification | Overcommitment rationale at scale; shared cluster architecture reference; prior art for our K8s approach | None — engineering framework |
  | 20 | Delimitrou & Kozyrakis — Quasar (2014) | Performance-driven (QoS-aware) scheduling; ML-based workload profiling replaces static reservation | Resource underutilization problem (< 20% CPU, ~40% memory in static systems) | QoS-aware allocation framing; ML profiling to estimate actual needs vs. declared; allocation + assignment joint optimization | Prediction: collaborative filtering (workload profiling) |
  | 21 | Lo et al. — Heracles (2015) | Memory bandwidth as primary co-location bottleneck; dynamic feedback control achieves ~90% utilization while maintaining SLA | Memory-as-primary-constraint justification | Memory contention evidence supporting our memory-first model; dynamic feedback control reference | Scheduler optimizer: feedback-based dynamic control |

  ---

  ## Note on Team Draft Contributions

  The team's initial proposal draft established several important framing decisions that are carried through in this document: (1) memory as the primary constraint in a multi-tenant cluster setting; (2) the scope boundary — we are not building a full cloud platform like AWS but a scheduling model and simulation; (3) the core objective tuple: maximize utilization, minimize SLA violations, maintain fairness; and (4) the use of mathematical optimization and real cluster data (Google Cluster Trace) as the analytical approach. Those anchor points shape every section below.

  ---

  ## Basic Idea  (simple explanation for the team)

  Imagine a shared office building where multiple companies rent desk space. Each company tells the building manager "I need 20 desks," but on any given day they actually use 12. The building manager, following the rules, keeps 20 desks reserved per company — so half the building sits empty even when other companies are waiting for a single desk. To make things worse, some companies have urgent meetings (latency-sensitive jobs) while others are doing background filing (batch jobs), but they all wait in the same queue with no priority distinction.

  Now imagine a smarter building manager who: (1) looks at how much desk space each company has historically used — not just what they asked for — and reserves only what they will likely actually need; (2) gives priority to the company that has been waiting longest or has used the least space so far; and (3) guarantees that companies with urgent meetings always get their desks on time, even if the filing team has to wait.

  That is what this project builds — but for cloud computing. The "building" is a Kubernetes cluster. The "companies" are tenants. The "desks" are memory (and CPU). The smarter building manager is our custom Kubernetes scheduler. We test it by replaying real workload data from Google's production cluster through a simulation — no live cloud account needed.

  ---

  ## Key Concepts

  Definitions for team members who are new to cluster scheduling.

  **Multi-Tenant Cluster**
  A shared pool of servers where multiple organizations ("tenants") submit workloads. They share the same physical resources but expect isolation and fairness. Example: a university research cluster shared by several research groups.

  **Kubernetes**
  The dominant open-source cluster manager — same category as Google Borg, Apache Mesos. It accepts workload submissions, decides which server runs each one, and keeps them running. Think of it as the operating system for a cluster. It does not care whether the servers underneath are on AWS, GCP, or a university datacenter.

  **Pod / Container**
  The unit of work in Kubernetes. A container holds one application and its dependencies, starts in seconds, and shares the host's OS kernel. A pod is one or more containers scheduled together. When we say "job," we mean a pod submitted by a tenant.

  **Scheduler vs. Optimizer — what is the difference?**
  The SCHEDULER is the mechanism: the Kubernetes component that assigns pods to nodes (it answers "which server runs this job?"). The OPTIMIZER is the decision logic: the mathematical/ML model that tells the scheduler which choice is best (it answers "given all options, what is the best placement?"). In our project, we build the optimizer — a prediction model + fairness model + SLA enforcement logic — and plug it into Kubernetes as a scheduler plugin. The scheduler runs our optimizer's recommendations.

  **Predictive Model vs. Optimization Model (Prescriptive) — the two things we build**
  These are the two analytical components at the heart of this project. The **Predictive Model** (a Random Forest) answers: "How much memory will this job actually use, and when?" It is trained on historical workload data. The **Optimization Model** (Prescriptive) takes those predictions as inputs and answers: "Should we admit this job right now, and if so, on which node?" It applies mathematical constraints (memory capacity, fairness bounds, SLA deadlines) to make that decision optimally. In data analytics terminology: the Predictive Model is *predictive analytics*; the Optimization Model is *prescriptive analytics* (also called operations research or decision optimization).

  **Admission Control**
  The front-door decision point: "Should we accept this job right now?" This is what the Optimization Model executes — it takes the Predictive Model's output and applies all constraints to decide admit, queue, or deny. The term "admission control" comes from the Kubernetes and networking literature. In our project, our Optimization Model IS the admission control logic. If the predicted memory usage would overflow any available node, or violate a fairness bound, the job is queued.

  **Resource Overcommitment**
  Accepting more workloads than the server's declared capacity on paper, relying on the fact that not all jobs will peak simultaneously. Safe when done with accurate prediction. Dangerous without it.

  **SLA (Service Level Agreement)**
  A formal contract stating what level of service a tenant is guaranteed — e.g., "your job completes within X minutes" or "less than 5 minutes of downtime per month." Violating the SLA has financial and contractual consequences. Our system prevents violations proactively, not reactively.

  **Memory OOM Kill**
  When a container exceeds its memory limit, the Linux kernel kills it immediately — no warning. This is fundamentally different from CPU throttling, which just slows the job down. Memory violations are fatal and directly violate SLAs. This is why memory is the primary constraint in our model.

  **Dominant Resource Fairness (DRF)**
  A fairness algorithm from Ghodsi et al. (2011). For each tenant, find which resource (CPU or memory) they consume the largest fraction of across the cluster. DRF ensures no tenant's dominant share grows disproportionately compared to others. Proven properties: no tenant prefers another's allocation, everyone gets their fair share, the system cannot be gamed by lying about needs.

  **Gini Coefficient**
  A number between 0 and 1 measuring inequality in resource distribution across tenants. 0 = perfect equality. 1 = total inequality. Alatawi (2025) reduces it from 0.25 to 0.10 — that improvement range is our target.

  **Google Cluster Trace v3**
  A publicly available dataset from Google containing real job records from their production cluster: arrival times, declared resource requests, actual measured usage, job duration, tenant IDs, priority levels. This is our training and simulation dataset.
