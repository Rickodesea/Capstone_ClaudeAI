# Optimal Shared Memory Utilization with Service Level Guarantees in Multi-Tenant Clusters

## Problem Statement and Motivation

### The DRAM Cost Crisis

The landscape of cloud computing infrastructure is undergoing a significant transformation driven by fundamental shifts in the semiconductor manufacturing sector. Supplies of conventional DRAM are shrinking as leading semiconductor manufacturers, including Samsung, SK Hynix, and Micron, divert production capacity toward premium high-bandwidth memory (HBM) for AI data centers (S&P Global, 2026). This strategic pivot is creating substantial downstream effects across the technology ecosystem. Conventional DRAM remains essential for servers, personal computers, mobile devices, and a broad range of consumer electronics. The redirection of operational capacity is driving up prices for conventional DRAM at an unprecedented rate. Industry analysts project that prices for traditional DRAM will increase between 54% and 116% year-over-year across all major manufacturers (S&P Global, 2026).

This dramatic price escalation is fundamentally reshaping cloud infrastructure costs and budgeting strategies (Voicu, 2026). DRAM constitutes a key component of cloud service infrastructure, and the rising costs create immediate pressure on cloud service providers (CSPs) to maintain profitability. As prices continue to climb, cloud service customers will scrutinize cost-optimized alternatives more carefully, intensifying competition among providers (Voicu, 2026). This environment creates a strong business imperative for CSPs to reduce operational costs while simultaneously providing attractive pricing to consumers of their services.

The economic challenge for CSPs is two-fold. First, expanding physical DRAM capacity has become increasingly costly, directly impacting capital expenditures. Second, failure to optimally utilize existing DRAM resources represents a significant opportunity cost in an environment where every gigabyte carries substantial financial weight. This situation necessitates sophisticated approaches to memory resource management that can extract maximum value from available infrastructure.

### Overcommitment as a Utilization Strategy

To address the challenge of optimal DRAM utilization, CSPs employ several techniques, among which overcommitment stands as a prominent strategy. Overcommitment, also referred to in the literature as oversubscription, overbooking, or multiplexing (Zhi, 2025), occurs when the total allocated amount of a resource type—in this case, DRAM—for processes exceeds the available capacity of that resource on the physical machine (PM), also known as a node.

The rationale behind memory overcommitment rests on a key observation about workload behavior: individual workloads typically do not simultaneously peak in their resource demands. By leveraging this statistical multiplexing property, CSPs can achieve higher utilization rates and improved cost efficiency (Waldspurger, 2002). The VMware ESX Server implementation demonstrated early success with memory overcommitment through techniques such as ballooning, which allows the hypervisor to reclaim pages that the guest operating system considers least valuable, and content-based page sharing, which eliminates redundancy across virtual machines (Waldspurger, 2002).

In our capstone project, we focus specifically on the DRAM resource type, Virtual Machine (VM) processes, and DRAM utilization on a single node. Utilization strategies can be implemented at the single-node level by overcommitting DRAM allocations to VMs beyond what physically exists on the node. This approach, when properly managed, can significantly improve resource efficiency without degrading service quality.

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

## Proposed Solution Approach

### Mathematical Programming Framework

This research will address the memory overcommitment problem through mathematical programming techniques, employing operations research and prescriptive analytical tools to develop an optimization framework. The approach recognizes that optimal memory allocation represents a multi-objective optimization problem requiring careful balancing of competing goals.

The solution framework will integrate several key components:

**Workload Characterization and Prediction:** Building on established research demonstrating that VM behavioral patterns exhibit temporal consistency (Cortez et al., 2017), we will develop predictive models to forecast memory demand patterns. These predictions will inform overcommitment decisions by identifying periods when aggressive oversubscription can be safely employed versus periods requiring more conservative allocation strategies.

**Dynamic Memory Balancing:** Drawing from the VMMB (Virtual Machine Memory Balancer) approach (Min et al., 2012), the solution will incorporate mechanisms to dynamically monitor memory demand with low overhead and periodically rebalance memory allocations among VMs based on current demand and QoS requirements. This dynamic adjustment capability is essential for responding to workload variations while maintaining service guarantees.

**Fairness-Aware Allocation:** The allocation mechanism will incorporate principles from Dominant Resource Fairness (Ghodsi et al., 2011) to ensure equitable resource distribution among tenants with heterogeneous demands. This framework provides theoretical guarantees on fairness properties while enabling efficient resource utilization.

**SLA-Aware Prioritization:** Following the weight-based collocation management approach (Blagodurov et al., n.d.), the solution will implement priority mechanisms that ensure critical workloads receive preferential access to memory resources during contention scenarios, maintaining SLA compliance even under high utilization levels.

### Integration of Complementary Techniques

The solution will synthesize techniques from multiple domains:

- **Temporal pattern exploitation** for identifying safe oversubscription opportunities across different time periods, as demonstrated in all-resource oversubscription research (Reidys et al., n.d.)
- **Real-time monitoring and adaptive control** to detect emerging resource pressure and trigger preventive actions before SLA violations occur (Doukha & Ez-zahout, 2025)
- **Memory management optimizations** such as page sharing and intelligent caching to reduce effective memory pressure while supporting higher degrees of overcommitment (Krishnaiah & Rao, 2025)

### Expected Outcomes and Contributions

The proposed research aims to develop a comprehensive framework for memory overcommitment that demonstrably improves upon current practices by:

1. Achieving higher average memory utilization rates while maintaining strict SLA compliance
2. Providing provable fairness guarantees for resource allocation among heterogeneous tenants
3. Reducing the frequency and severity of performance degradation incidents due to memory contention
4. Enabling more cost-effective cloud infrastructure operation through improved resource efficiency

By addressing this multi-faceted problem through rigorous mathematical programming techniques informed by empirical workload studies and established theoretical frameworks, this research will contribute practical tools for CSPs navigating the challenging economic environment created by rising DRAM costs while maintaining competitive service quality.

## References

Blagodurov, S., Gmach, D., Arlitt, M., Chen, Y., Hyser, C., & Fedorova, A. (n.d.). Maximizing server utilization while meeting critical SLAs via weight-based collocation management. Simon Fraser University & Hewlett-Packard Laboratories.

Cortez, E., Bonde, A., Muzio, A., Russinovich, M., Fontoura, M., & Bianchini, R. (2017). Resource Central: Understanding and predicting workloads for improved resource management in large cloud platforms. In *Proceedings of the 26th Symposium on Operating Systems Principles* (SOSP '17). Association for Computing Machinery. https://doi.org/10.1145/3132747.3132772

Doukha, R., & Ez-zahout, A. (2025). Enhanced virtual machine resource optimization in cloud computing using real-time monitoring and predictive modeling. *International Journal of Advanced Computer Science and Applications, 16*(2), 658-673.

Ghodsi, A., Zaharia, M., Hindman, B., Konwinski, A., Shenker, S., & Stoica, I. (2011). Dominant resource fairness: Fair allocation of multiple resource types. In *Proceedings of the 8th USENIX Symposium on Networked Systems Design and Implementation* (NSDI '11). USENIX Association.

Krishnaiah, V. V. J. R., & Rao, B. S. (2025). Optimizing server and memory utilization in cloud computing through virtualization and caching. Koneru Lakshmaiah Education Foundation.

Min, C., Kim, I., Kim, T., & Eom, Y. I. (2012). VMMB: Virtual machine memory balancing for unmodified operating systems. *Journal of Grid Computing, 10*(1), 69-84. https://doi.org/10.1007/s10723-012-9209-4

Mishra, D., & Kulkarni, P. (2018). A survey of memory management techniques in virtualized systems. *Computer Science Review, 29*, 56-73. https://doi.org/10.1016/j.cosrev.2018.06.001

Reidys, B., Zardoshti, P., Goiri, Í., Irvene, C., Berger, D. S., Ma, H., Arya, K., Cortez, E., Stark, T., Bak, E., Iyigun, M., Novaković, S., Hsu, L., Trueba, K., Pan, A., & Bansal, C. (n.d.). Coach: Exploiting temporal patterns for all-resource oversubscription in cloud platforms. Microsoft & collaborating institutions.

S&P Global. (2026, January). AI memory boom squeezes legacy DRAM supply, pushing prices higher. *S&P Global Market Intelligence.* Retrieved April 13, 2026, from https://www.spglobal.com/market-intelligence/en/news-insights/research/2026/01/ai-memory-boom-squeezes-legacy-dram-supply-pushing-prices-higher

Voicu, C. (2026). AI memory crisis: How the HBM boom is reshaping cloud costs. *N2WS Blog.* Retrieved April 13, 2026, from https://n2ws.com/blog/ai-memory-crisis

Waldspurger, C. A. (2002). Memory resource management in VMware ESX server. In *Proceedings of the Fifth Symposium on Operating Systems Design and Implementation* (OSDI '02). USENIX Association. [Best paper award]

Wang, Y., & Yang, X. (2025). Intelligent resource allocation optimization for cloud computing via machine learning. *Advances in Computer, Signals and Systems, 9*(1), 55-73. https://doi.org/10.23977/acss.2025.090109

Zhi, J. (2025). *A study on overcommitment in cloud providers* [Master's dissertation, University of São Paulo]. Institute of Mathematics and Statistics. https://doi.org/10.11606/D.45.2025.tde-20082025-120108
