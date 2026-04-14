# Optimal Shared Memory Utilization with Service Level Guarantees in Multi-Tenant Clusters

## Problem Statement and Motivation

### The DRAM Cost Crisis

The landscape of cloud computing infrastructure is undergoing a significant transformation driven by fundamental shifts in the semiconductor manufacturing sector. Supplies of conventional DRAM are shrinking as leading semiconductor manufacturers, including Samsung, SK Hynix, and Micron, divert production capacity toward premium high-bandwidth memory (HBM) for AI data centers (S&P Global, 2026). This strategic pivot is creating substantial downstream effects across the technology ecosystem. Conventional DRAM remains essential for servers, personal computers, mobile devices, and a broad range of consumer electronics. The redirection of operational capacity is driving up prices for conventional DRAM at an unprecedented rate. Industry analysts project that prices for traditional DRAM will increase between 54% and 116% year-over-year across all major manufacturers (S&P Global, 2026).

This dramatic price escalation is fundamentally reshaping cloud infrastructure costs and budgeting strategies (Voicu, 2026). DRAM constitutes a key component of cloud service infrastructure, and the rising costs create immediate pressure on cloud service providers (CSPs) to maintain profitability. As prices continue to climb, cloud service customers will scrutinize cost-optimized alternatives more carefully, intensifying competition among providers (Voicu, 2026). This environment creates a strong business imperative for CSPs to reduce operational costs while simultaneously providing attractive pricing to consumers of their services.

The economic challenge for CSPs is two-fold. First, expanding physical DRAM capacity has become increasingly costly, directly impacting capital expenditures. Second, failure to optimally utilize existing DRAM resources represents a significant opportunity cost in an environment where every gigabyte carries substantial financial weight. This situation necessitates sophisticated approaches to memory resource management that can extract maximum value from available infrastructure.

### Overcommitment as a Utilization Strategy

To address the challenge of optimal DRAM utilization, CSPs employ several techniques, among which overcommitment stands as a prominent strategy. Overcommitment, also referred to in the literature as oversubscription, overbooking, or multiplexing (Zhi, 2025), occurs when the total allocated amount of a resource type—in this case, DRAM—for processes exceeds the available capacity of that resource on the physical machine (PM), also known as a node.

However, despite the theoretical potential of overcommitment strategies, current cloud computing infrastructure operates at surprisingly low utilization levels. Studies indicate that global cloud computing resources operate at an average DRAM utilization of just 45%, with substantial portions remaining idle (Wang & Yang, 2025). This dramatic underutilization represents a significant economic inefficiency, particularly in the context of rising DRAM costs. The primary reason CSPs maintain such conservative utilization levels is the risk of SLA violations. When memory resources become overcommitted and multiple workloads peak simultaneously, the resulting performance degradation or service disruptions can breach contractual SLA commitments, leading to financial penalties, customer dissatisfaction, and reputational damage.

This conservative approach creates a direct tension between cost efficiency and reliability. CSPs prefer to maintain excess capacity headroom as a safety buffer against unpredictable demand spikes, effectively trading capital efficiency for reduced SLA violation risk. The reluctance to adopt aggressive overcommitment strategies reflects a lack of confidence in existing resource management tools to accurately predict and prevent overload conditions before they impact service quality.

This observation leads to a critical insight regarding the relationship between model reliability and production adoption: **there exists a strong positive correlation between the confidence CSPs have in an overcommitment model's ability to prevent SLA violations and their willingness to deploy that model in production environments with higher utilization targets.** A highly reliable predictive admission control model that demonstrably reduces SLA violation risk would enable CSPs to safely increase memory utilization beyond current conservative levels. Conversely, a model with uncertain accuracy or prone to false negatives (failing to detect impending overloads) would see limited production adoption regardless of its theoretical benefits, as CSPs cannot afford the business risk of SLA breaches.

Therefore, our research recognizes that technical sophistication alone is insufficient—the model must achieve a level of reliability and predictive accuracy that inspires operational confidence. The success of our solution will be measured not only by its utilization improvements in controlled experiments but by the confidence intervals it provides around SLA compliance, enabling CSPs to make informed risk-adjusted decisions about deployment in production clusters.

The rationale behind memory overcommitment rests on a key observation about workload behavior: individual workloads typically do not simultaneously peak in their resource demands. By leveraging this statistical multiplexing property, CSPs can achieve higher utilization rates and improved cost efficiency (Waldspurger, 2002). The VMware ESX Server implementation demonstrated early success with memory overcommitment through techniques such as ballooning, which allows the hypervisor to reclaim pages that the guest operating system considers least valuable, and content-based page sharing, which eliminates redundancy across virtual machines (Waldspurger, 2002).

In our capstone project, we focus specifically on the DRAM resource type, Virtual Machine (VM) processes, and DRAM utilization on a single node. Utilization strategies can be implemented at the single-node level by overcommitting DRAM allocations to VMs beyond what physically exists on the node. This approach, when properly managed, can significantly improve resource efficiency without degrading service quality. Our node-level predictive admission control model aims to provide the reliability guarantees necessary to move from the current 45% average utilization to substantially higher levels while maintaining or improving SLA compliance rates.

## The Multi-Objective Challenge

### Competing Objectives in Memory Overcommitment

While memory overcommitment offers clear benefits in terms of resource utilization, it introduces a complex set of competing objectives that must be carefully balanced. In online shared clusters, all tasks running on a node compete for the same limited pool of DRAM. When memory is overcommitted, the system operates under the assumption that workload demands will not peak concurrently. However, when multiple workloads attempt to peak simultaneously, the system faces critical decisions that can impact both performance and service reliability.

If aggregate memory demand exceeds physical capacity, the system is forced into one of two undesirable scenarios. First, it may resort to swapping memory contents to slower storage media, which drastically reduces performance and increases latency for affected workloads. The performance degradation from swapping can be severe, as the speed differential between DRAM and even fast solid-state storage spans orders of magnitude. Second, the system may terminate or throttle processes, directly affecting the predetermined service level agreements (SLAs) committed to tenants (Blagodurov et al., n.d.). Neither option is acceptable in a production environment where both performance and reliability guarantees must be maintained.

This situation creates a multi-objective business challenge encompassing several critical dimensions:

1. **High Utilization:** Maximizing the productive use of expensive DRAM resources to improve return on infrastructure investment and reduce the need for capacity expansion.

2. **Service Level Guarantees:** Meeting predetermined SLA commitments to tenants, ensuring that performance targets are consistently achieved even under varying load conditions (Blagodurov et al., n.d.).

3. **Fairness Among Tenants:** Ensuring equitable resource allocation across tenants with potentially heterogeneous workload characteristics and resource demands (Ghodsi et al., 2011). The Dominant Resource Fairness (DRF) framework provides a mathematically rigorous approach to fairness that generalizes max-min fairness to multiple resource types, ensuring that resource allocation satisfies properties such as sharing incentive, strategy-proofness, envy-freeness, and Pareto efficiency.

4. **Workload Isolation:** Preventing disruptive spillover effects where a spike in resource demand from one workload negatively impacts the performance of other workloads sharing the same physical infrastructure. Effective isolation mechanisms are essential for maintaining predictable performance in multi-tenant environments (Mishra & Kulkarni, 2018).

### Dynamic Nature of the Problem

The challenge is further complicated by the dynamic and often unpredictable nature of workload behavior. Recent research on Microsoft Azure's production workloads has demonstrated that while certain VM behaviors exhibit consistency over multiple lifetimes, making historical data valuable for prediction, workload patterns can still vary significantly (Cortez et al., 2017). This variability necessitates adaptive resource management strategies that can respond to changing conditions in real-time while maintaining service guarantees.

Modern approaches to this problem increasingly leverage machine learning techniques for workload prediction and resource allocation optimization. For instance, intelligent resource allocation frameworks utilizing Long Short-Term Memory (LSTM) networks for demand prediction and Deep Q-Networks (DQN) for dynamic scheduling have demonstrated substantial improvements, enhancing resource utilization by 32.5%, reducing average response time by 43.3%, and lowering operational costs by 26.6% in production environments (Wang & Yang, 2025).

## Proposed Solution Architecture

### Overview: Decoupling Bin Packing from Admission Control

Our proposed solution introduces a novel architecture that decouples the traditional VM bin packing problem from node-level admission control decisions. While VM bin packing—the problem of efficiently assigning VMs to physical nodes to optimize resource utilization—remains a critical function of cluster management systems, our research does not focus on the bin packing algorithms themselves. Instead, we recognize that existing cluster managers employ various sophisticated bin packing strategies to determine which node should host a new VM based on factors such as resource availability, network topology, power consumption, and load balancing objectives.

Our contribution centers on a **node-level predictive admission control model** that serves as a consultant to the cluster manager. This model provides intelligent yes/no decisions regarding VM admission based on predicted memory usage patterns and overload risks. By positioning our model as an advisory layer that communicates with the cluster manager's bin packing mechanism, we create a system architecture that is agnostic to the specific bin packing algorithm employed while adding a crucial layer of temporal awareness and overload prevention.

### System Architecture Components

The proposed system architecture consists of three primary components operating in a coordinated workflow:

#### 1. Cluster Manager with Bin Packing

The cluster manager maintains overall responsibility for VM placement decisions across the cluster. When a request arrives to provision a new VM, the cluster manager uses its bin packing algorithm to identify a candidate node that appears suitable based on current resource availability and other placement criteria. The specific bin packing strategy—whether first-fit, best-fit, worst-fit, or more sophisticated algorithms considering multiple dimensions—is not constrained by our approach.

#### 2. Node-Level Predictive Admission Control Model

Each physical node in the cluster hosts an instance of our predictive admission control model. This model maintains two complementary prediction mechanisms:

**Generalized Prediction Model:** This model operates using trend-based forecasting similar to the approach described in the Coach paper (Reidys et al., n.d.), leveraging characteristics such as VM size, resource allocation, and temporal usage patterns observed across similar VM types. The generalized model provides predictions for new VMs where no specific historical data exists. It identifies typical memory usage patterns based on VM configuration parameters and applies learned temporal patterns to forecast when peak demands are likely to occur.

**Curated Prediction Model:** For VMs that have been running on the node for sufficient time to establish a behavioral history, the system develops VM-specific curated models. These models learn the unique memory usage patterns of individual VMs, capturing application-specific behaviors, periodic workload cycles, and response to external triggers (Cortez et al., 2017). The curated model provides more accurate predictions than the generalized model by exploiting the consistency that many VMs exhibit across their lifetimes.

The system employs the generalized model when a VM is newly admitted to a node, then progressively transitions to the curated model as sufficient historical data accumulates. This dual-model approach balances the need for immediate predictive capability with the advantages of personalized forecasting.

#### 3. Communication Protocol Between Components

When the cluster manager selects a candidate node for VM placement, it queries the node's admission control model with the proposed VM's characteristics. The model evaluates whether accepting the new VM would create unacceptable overload risk by analyzing:

- Current memory commitments to existing VMs
- Predicted peak memory demands of existing VMs over a forecast horizon
- Predicted memory demand of the candidate VM (using the generalized model)
- Potential temporal overlap of peak demands that could exceed physical capacity
- SLA requirements and priorities of all affected VMs

Based on this analysis, the model returns a binary decision: accept or reject. If the model rejects the VM, the cluster manager must consult its bin packing algorithm to identify an alternative node and repeat the consultation process with that node's model. This continues until either a node accepts the VM or no suitable node can be found in the cluster.

### VM Admission Control Workflow

The following diagram illustrates the VM admission control process:

```
┌─────────────────────────────────────────────────────────────────┐
│                     VM Admission Control Flow                    │
└─────────────────────────────────────────────────────────────────┘

    ┌─────────────────┐
    │  New VM Request │
    └────────┬────────┘
             │
             ▼
    ┌────────────────────────┐
    │   Cluster Manager      │
    │   Bin Packing Engine   │
    │  Selects Candidate     │
    │        Node            │
    └────────┬───────────────┘
             │
             ▼
    ┌────────────────────────────────────────────┐
    │  Query Node's Admission Control Model      │
    │  - Pass VM characteristics                 │
    │  - Request admission decision              │
    └────────┬───────────────────────────────────┘
             │
             ▼
    ┌─────────────────────────────────────────────┐
    │   Node-Level Prediction & Analysis          │
    │                                              │
    │   1. Use Generalized Model for new VM       │
    │      - Predict memory demand pattern        │
    │      - Identify likely peak times           │
    │                                              │
    │   2. Use Curated Models for existing VMs    │
    │      - Forecast memory usage timeline       │
    │      - Identify existing peak periods       │
    │                                              │
    │   3. Temporal Conflict Analysis             │
    │      - Check for overlapping peaks          │
    │      - Calculate total demand at each time  │
    │      - Compare against physical capacity    │
    │                                              │
    │   4. SLA Impact Assessment                  │
    │      - Evaluate risk to SLA compliance      │
    │      - Consider priority levels             │
    └────────┬────────────────────────────────────┘
             │
             ▼
    ┌────────────────────┐
    │  Decision:         │
    │  Accept or Reject? │
    └────┬───────────┬───┘
         │           │
    Accept│           │Reject
         │           │
         ▼           ▼
┌────────────┐   ┌──────────────────────┐
│  Admit VM  │   │  Return to Cluster   │
│  to Node   │   │  Manager to Select   │
│            │   │  Alternative Node    │
└─────┬──────┘   └──────────┬───────────┘
      │                     │
      │                     │
      ▼                     ▼
┌────────────────┐   ┌─────────────────┐
│ Start Curated  │   │  Try Next Node  │
│ Model Learning │   │  from Bin Pack  │
│ for New VM     │   │   Candidates    │
└────────────────┘   └─────────────────┘
```

### Continuous Monitoring and Overload Management

Beyond the admission control phase, our model continuously monitors the memory usage of all VMs on its node and maintains updated predictions of future demand. This ongoing analysis enables proactive overload management through several mechanisms:

#### Overload Prediction and Early Reclamation

When the model predicts an upcoming overload condition—a period when aggregate memory demand will exceed or approach physical capacity—it initiates early reclamation processes. Reclamation techniques may include:

- **Ballooning:** Requesting guest operating systems to release memory pages they consider less critical (Waldspurger, 2002)
- **Page sharing:** Identifying and consolidating duplicate memory pages across VMs
- **Compression:** Applying memory compression techniques to reduce physical memory footprint

These reclamation mechanisms operate transparently and aim to prevent overload without impacting VM performance or requiring drastic interventions.

#### Mitigation Strategies for Impending Overload

If reclamation proves insufficient and the model detects that the system is trending toward an overload state that will violate SLA commitments, more aggressive mitigation strategies become necessary. While our capstone research focuses primarily on the predictive and decision-making framework, we acknowledge that mitigation strategies form an important part of the complete system. These strategies include:

**Memory Swapping** (Future Work): Selectively swapping less-critical memory pages to storage. While swapping degrades performance, strategic application to lower-priority VMs may preserve SLAs for critical workloads. Our current research scope treats swapping mechanisms as future work beyond the core predictive model.

**Selective VM Termination:** When overload cannot be avoided through reclamation or swapping, the system may need to terminate VMs to preserve physical capacity for higher-priority workloads. This decision is governed by SLA priorities, where VMs with lower SLA requirements become candidates for termination before those with stricter guarantees.

#### Termination Policy and Priority Framework

The termination mechanism operates according to a priority-based framework:

**SLA-Based Prioritization:** VMs are classified according to their SLA requirements. Lower-priority VMs (those with more lenient performance guarantees or best-effort service levels) are designated for potential termination before higher-priority VMs (those with strict performance SLAs or guaranteed service levels).

**Termination Granularity:** The system supports multiple levels of termination granularity. While our capstone treats VMs as black boxes, existing research has demonstrated that selective termination of individual processes within a VM can mitigate overload while preserving partial VM functionality (Hu et al., 2025). However, our primary approach focuses on whole-VM termination to maintain architectural simplicity and avoid guest OS introspection requirements.

**Termination Groups:** VMs designated for potential termination are organized into termination groups based on their priority levels. When termination becomes necessary, the system selects from the lowest-priority group first, proceeding to higher-priority groups only if sufficient capacity cannot be reclaimed from lower priorities.

**Cluster Manager Coordination:** When a VM is terminated due to overload prediction, we assume the cluster manager will relaunch the terminated VM on an alternative node within a specified threshold time. This assumption aligns with standard cloud platform behavior where VM failures trigger automatic recovery procedures. The admission control model on the alternative node will evaluate whether it can safely host the relaunched VM based on its own predictive analysis.

### Overload Management Workflow

The following diagram illustrates the continuous monitoring and overload management process:

```
┌──────────────────────────────────────────────────────────────────┐
│              Continuous Monitoring & Overload Management         │
└──────────────────────────────────────────────────────────────────┘

         ┌─────────────────────────┐
         │  Continuous Monitoring  │
         │  Loop (Per Node)        │
         └────────┬────────────────┘
                  │
                  ▼
         ┌──────────────────────────────────┐
         │  Update Predictions for All VMs  │
         │  - Curated models for existing   │
         │  - Adjust for observed behavior  │
         │  - Forecast next N hours         │
         └────────┬─────────────────────────┘
                  │
                  ▼
         ┌───────────────────────────┐
         │  Aggregate Memory Demand  │
         │  Forecast Across All VMs  │
         └────────┬──────────────────┘
                  │
                  ▼
         ┌───────────────────────┐
         │  Predict Overload?    │
         └────┬───────────┬──────┘
              │           │
          No  │           │ Yes
              │           │
              ▼           ▼
    ┌──────────────┐  ┌────────────────────────┐
    │  Continue    │  │  Calculate Time Until  │
    │  Monitoring  │  │  Predicted Overload    │
    └──────────────┘  └──────────┬─────────────┘
                                 │
                                 ▼
                      ┌──────────────────────────┐
                      │  Initiate Early          │
                      │  Reclamation:            │
                      │  - Ballooning            │
                      │  - Page Sharing          │
                      │  - Compression           │
                      └──────────┬───────────────┘
                                 │
                                 ▼
                      ┌──────────────────────┐
                      │  Reclamation         │
                      │  Sufficient?         │
                      └────┬──────────┬──────┘
                           │          │
                       Yes │          │ No
                           │          │
                           ▼          ▼
                  ┌──────────────┐  ┌────────────────────────┐
                  │  Continue    │  │  Trending to Overload  │
                  │  Monitoring  │  │  Despite Reclamation   │
                  └──────────────┘  └──────────┬─────────────┘
                                               │
                                               ▼
                                    ┌──────────────────────────┐
                                    │  Evaluate SLA Priorities │
                                    │  and Termination Options │
                                    └──────────┬───────────────┘
                                               │
                                               ▼
                                    ┌──────────────────────────┐
                                    │  Select VMs from Lowest  │
                                    │  Priority Termination    │
                                    │  Group                   │
                                    └──────────┬───────────────┘
                                               │
                                               ▼
                                    ┌──────────────────────────┐
                                    │  Terminate Selected VMs  │
                                    └──────────┬───────────────┘
                                               │
                                               ▼
                                    ┌──────────────────────────┐
                                    │  Notify Cluster Manager  │
                                    │  - Terminated VM IDs     │
                                    │  - Request Relaunch      │
                                    └──────────┬───────────────┘
                                               │
                                               ▼
                                    ┌──────────────────────────┐
                                    │  Cluster Manager         │
                                    │  Relaunches VMs on       │
                                    │  Alternative Nodes       │
                                    │  (Within Threshold Time) │
                                    └──────────────────────────┘
```

### Dual Prediction Model Architecture

The following diagram illustrates how the generalized and curated prediction models work together:

```
┌──────────────────────────────────────────────────────────────────┐
│                  Dual Prediction Model System                     │
└──────────────────────────────────────────────────────────────────┘

  ┌─────────────────────────────────────────────────────────────┐
  │                    New VM Arrives at Node                    │
  └────────────────────────────┬────────────────────────────────┘
                               │
                               ▼
              ┌────────────────────────────────┐
              │  Initialize Generalized Model  │
              │                                 │
              │  Inputs:                        │
              │  - VM size (CPU, RAM allocated) │
              │  - VM type/class                │
              │  - Deployment configuration     │
              │                                 │
              │  Based on:                      │
              │  - Historical patterns from     │
              │    similar VM profiles          │
              │  - Temporal usage trends        │
              │  - Typical peak/trough cycles   │
              └────────────────┬───────────────┘
                               │
                               ▼
              ┌────────────────────────────────┐
              │  VM Runs on Node               │
              │  Collecting Actual Usage Data  │
              └────────┬───────────────────────┘
                       │
                       │ Time passes...
                       │ Data accumulates...
                       │
                       ▼
              ┌─────────────────────────────────┐
              │  Sufficient History Collected?  │
              │  (e.g., 7+ days of data)        │
              └────────┬────────────────┬───────┘
                   No  │                │ Yes
                       │                │
           ┌───────────┘                └────────────┐
           │                                         │
           ▼                                         ▼
  ┌──────────────────┐                ┌──────────────────────────┐
  │  Continue Using  │                │  Train Curated Model     │
  │  Generalized     │                │                          │
  │  Model           │                │  VM-Specific Features:   │
  │                  │                │  - Actual usage patterns │
  │  + Refine with   │                │  - Observed peak times   │
  │    recent data   │                │  - Response to events    │
  └────────┬─────────┘                │  - Application cycles    │
           │                          │  - Seasonal variations   │
           │                          └────────┬─────────────────┘
           │                                   │
           │                                   ▼
           │                          ┌────────────────────────────┐
           │                          │  Gradual Transition:       │
           │                          │  Blend Generalized +       │
           │                          │  Curated Predictions       │
           │                          │  (Increase curated weight) │
           │                          └────────┬───────────────────┘
           │                                   │
           │                                   ▼
           │                          ┌────────────────────────────┐
           │                          │  Full Switch to Curated    │
           │                          │  Model for This VM         │
           │                          │                            │
           │                          │  - Higher accuracy         │
           │                          │  - VM-specific insights    │
           │                          │  - Adaptive to changes     │
           │                          └────────┬───────────────────┘
           │                                   │
           └───────────────┬───────────────────┘
                           │
                           ▼
              ┌─────────────────────────────┐
              │  Continuous Model Updates   │
              │                             │
              │  - Retrain periodically     │
              │  - Adapt to behavior drift  │
              │  - Incorporate new patterns │
              └─────────────────────────────┘

  Legend:
  ═══════════════════════════════════════════════════════
  Generalized Model: Uses cross-VM trends & patterns
  Curated Model: Learns individual VM behavior
  Hybrid Period: Weighted combination of both models
```

### Mathematical Programming Framework

The core of our solution employs mathematical programming techniques to formalize the admission control and overload management decisions. The optimization framework addresses the multi-objective nature of the problem through a set of constraints and objective functions:

**Decision Variables:**
- Binary admission decision for candidate VM
- Memory allocation levels for existing VMs
- Reclamation target amounts per VM
- Termination decisions for lower-priority VMs

**Constraints:**
- Physical memory capacity limits
- SLA performance guarantees for each priority tier
- Minimum memory allocation thresholds
- Temporal peak demand predictions
- Fairness requirements across tenant VMs

**Objective Functions:**
- Maximize aggregate memory utilization
- Minimize SLA violation risk
- Minimize VM terminations
- Maximize fairness metrics (e.g., Dominant Resource Fairness)

The mathematical program is solved repeatedly: during admission decisions, during continuous monitoring when reclamation is triggered, and when evaluating termination scenarios. The solution employs prescriptive analytics to recommend specific actions (admit/reject, reclamation amounts, termination candidates) that optimize the multi-objective criteria while respecting all constraints.

### Integration of Complementary Techniques

The solution synthesizes techniques from multiple research domains:

- **Temporal pattern exploitation** for identifying safe oversubscription opportunities across different time periods, as demonstrated in all-resource oversubscription research (Reidys et al., n.d.)
- **Real-time monitoring and adaptive control** to detect emerging resource pressure and trigger preventive actions before SLA violations occur (Doukha & Ez-zahout, 2025)
- **Memory management optimizations** such as page sharing and intelligent caching to reduce effective memory pressure while supporting higher degrees of overcommitment (Krishnaiah & Rao, 2025)
- **Workload characterization** based on Microsoft Azure production studies demonstrating behavioral consistency across VM lifetimes (Cortez et al., 2017)
- **Fairness-aware allocation** incorporating Dominant Resource Fairness principles to ensure equitable treatment of heterogeneous workloads (Ghodsi et al., 2011)

### Expected Outcomes and Contributions

The proposed research aims to develop a comprehensive framework for memory overcommitment that demonstrably improves upon current practices by:

1. **Improving Admission Decisions:** Providing the cluster manager with informed guidance on VM placement that accounts for temporal demand patterns rather than merely instantaneous resource availability.

2. **Reducing SLA Violations:** Proactively detecting and mitigating overload conditions before they impact performance, maintaining higher SLA compliance rates even under aggressive overcommitment policies.

3. **Increasing Utilization Efficiency:** Enabling safer overcommitment through accurate prediction, allowing CSPs to extract more value from existing DRAM infrastructure without proportional increases in violation risk.

4. **Providing Fairness Guarantees:** Ensuring that resource allocation and termination decisions respect fairness principles, preventing systematic disadvantage to particular tenant workloads.

5. **Enabling Autonomous Operation:** Operating as a largely autonomous system that requires minimal manual intervention, relying on learned models and mathematical optimization to make real-time decisions.

By addressing this multi-faceted problem through rigorous mathematical programming techniques informed by empirical workload studies and established theoretical frameworks, this research will contribute practical tools for CSPs navigating the challenging economic environment created by rising DRAM costs while maintaining competitive service quality. The decoupled architecture—separating bin packing from admission control—ensures that our contributions can integrate with diverse cluster management platforms without requiring fundamental changes to their placement algorithms.

## Research Scope and Limitations

To maintain focused scope, our capstone research explicitly defines several boundaries:

**In Scope:**
- Node-level predictive admission control model
- Dual prediction models (generalized and curated)
- VM admission decision framework
- Overload prediction and early reclamation triggering
- SLA-based termination prioritization framework
- Communication protocol between cluster manager and node models

**Out of Scope (Future Work):**
- Specific bin packing algorithm design or optimization
- Detailed swap management strategies
- Process-level termination within VMs (treated as black boxes)
- Multi-node coordination and VM migration
- Network and I/O resource management
- Cross-datacenter resource optimization

This scoping ensures that our research delivers depth in the critical area of predictive memory overcommitment while acknowledging complementary problems that merit separate investigation.

## References

Blagodurov, S., Gmach, D., Arlitt, M., Chen, Y., Hyser, C., & Fedorova, A. (n.d.). Maximizing server utilization while meeting critical SLAs via weight-based collocation management. Simon Fraser University & Hewlett-Packard Laboratories.

Cortez, E., Bonde, A., Muzio, A., Russinovich, M., Fontoura, M., & Bianchini, R. (2017). Resource Central: Understanding and predicting workloads for improved resource management in large cloud platforms. In *Proceedings of the 26th Symposium on Operating Systems Principles* (SOSP '17). Association for Computing Machinery. https://doi.org/10.1145/3132747.3132772

Doukha, R., & Ez-zahout, A. (2025). Enhanced virtual machine resource optimization in cloud computing using real-time monitoring and predictive modeling. *International Journal of Advanced Computer Science and Applications, 16*(2), 658-673.

Ghodsi, A., Zaharia, M., Hindman, B., Konwinski, A., Shenker, S., & Stoica, I. (2011). Dominant resource fairness: Fair allocation of multiple resource types. In *Proceedings of the 8th USENIX Symposium on Networked Systems Design and Implementation* (NSDI '11). USENIX Association.

Hu, Y., Zhang, Z., Liu, Y., Gu, Y., Lei, S., Kasikci, B., & Huang, P. (2025). Mitigating application resource overload with targeted task cancellation. In *Proceedings of the 31st ACM SIGOPS Symposium on Operating Systems Principles* (SOSP '25). Association for Computing Machinery.

Krishnaiah, V. V. J. R., & Rao, B. S. (2025). Optimizing server and memory utilization in cloud computing through virtualization and caching. Koneru Lakshmaiah Education Foundation. https://doi.org/10.22541/au.174593665.52735213/v1

Min, C., Kim, I., Kim, T., & Eom, Y. I. (2012). VMMB: Virtual machine memory balancing for unmodified operating systems. *Journal of Grid Computing, 10*(1), 69-84. https://doi.org/10.1007/s10723-012-9209-4

Mishra, D., & Kulkarni, P. (2018). A survey of memory management techniques in virtualized systems. *Computer Science Review, 29*, 56-73. https://doi.org/10.1016/j.cosrev.2018.06.001

Reidys, B., Zardoshti, P., Goiri, Í., Irvene, C., Berger, D. S., Ma, H., Arya, K., Cortez, E., Stark, T., Bak, E., Iyigun, M., Novaković, S., Hsu, L., Trueba, K., Pan, A., & Bansal, C. (n.d.). Coach: Exploiting temporal patterns for all-resource oversubscription in cloud platforms. Microsoft & collaborating institutions.

S&P Global. (2026, January). AI memory boom squeezes legacy DRAM supply, pushing prices higher. *S&P Global Market Intelligence.* Retrieved April 13, 2026, from https://www.spglobal.com/market-intelligence/en/news-insights/research/2026/01/ai-memory-boom-squeezes-legacy-dram-supply-pushing-prices-higher

Voicu, C. (2026). AI memory crisis: How the HBM boom is reshaping cloud costs. *N2WS Blog.* Retrieved April 13, 2026, from https://n2ws.com/blog/ai-memory-crisis

Waldspurger, C. A. (2002). Memory resource management in VMware ESX server. In *Proceedings of the Fifth Symposium on Operating Systems Design and Implementation* (OSDI '02). USENIX Association. [Best paper award]

Wang, Y., & Yang, X. (2025). Intelligent resource allocation optimization for cloud computing via machine learning. *Advances in Computer, Signals and Systems, 9*(1), 55-73. https://doi.org/10.23977/acss.2025.090109

Zhi, J. (2025). *A study on overcommitment in cloud providers* [Master's dissertation, University of São Paulo]. Institute of Mathematics and Statistics. https://doi.org/10.11606/D.45.2025.tde-20082025-120108
