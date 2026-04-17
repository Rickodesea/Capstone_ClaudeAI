  Proposal Draft

  Title: Predictive Multi-Tenant Job Scheduling in Kubernetes: Maximizing Utilization While Preserving Fairness and SLA Compliance

  ---
  Cloud infrastructure costs are escalating on multiple fronts. Conventional DRAM supplies are contracting as manufacturers redirect production toward high-bandwidth memory for AI
  accelerators, with prices projected to rise 54–116% year-over-year (S&P Global, 2026). GPU hardware remains scarce and expensive, and the operational cost of leaving compute, memory, and
   networking resources idle compounds these pressures. This environment creates a compelling business imperative for cloud service providers: extract significantly more value from
  existing hardware before buying more. The path to that goal is not better hardware — it is better scheduling.

  Current cluster utilization in production cloud environments averages between 40% and 60%, meaning that on any given node, nearly half its resources sit idle while tenants wait for their
   jobs to start (Chaudhari, 2025). This waste exists not because hardware is unavailable, but because schedulers are conservative. The default Kubernetes scheduler places jobs based
  solely on declared resource requests and current availability, with no awareness of how much resources jobs will actually use, when co-located jobs will peak, or whether admitting a new
  job will cause a neighbor's SLA to be violated in the next thirty minutes. The result is predictable: either the scheduler over-admits and SLA violations occur, or it under-admits and
  expensive hardware sits idle.

  Multi-tenancy compounds the problem. When multiple teams share the same cluster, a single greedy tenant can monopolize resources, causing others to wait arbitrarily long for their jobs
  to start. The Kubernetes scheduler has no fairness mechanism — it processes requests in order without regard to whether one tenant has consumed a disproportionate share of cluster
  resources. Tenants with latency-sensitive jobs compete against batch workloads with no systematic prioritization, and SLA violations are discovered after the fact rather than prevented
  at admission time.

  This capstone addresses the gap between what the Kubernetes scheduler currently does and what a production multi-tenant cluster actually requires. The gap is precise: no existing system
  combines predictive admission control, multi-resource fairness, and runtime SLA enforcement into a single coherent Kubernetes scheduler. Chaudhari (2025) describes the need for exactly
  this combination — a Workload Classifier, Fairness Engine, and Predictive Resource Manager working together — but the framework exists only as a proposal with no implementation. We build
   it.

  The proposed system is a custom Kubernetes scheduler plugin with three integrated components. First, a predictive admission layer trains a Random Forest model on tenant workload history
  from the Google Cluster Trace to predict the actual peak utilization of each submitted job. Because tenants routinely over-declare resource requests as a safety buffer, the actual usage
  is consistently lower than the declaration. The model learns this gap per tenant. A new job is admitted only if the predicted combined utilization of all co-located jobs stays below a
  safe threshold, preventing the scheduler from committing resources it cannot actually deliver. Second, a fairness-aware placement layer applies Dominant Resource Fairness (Ghodsi et al.,2011) to rank candidate nodes during scheduling. The tenant with the smallest share of their dominant resource gets priority for the next scheduling slot, ensuring no team waits
  disproportionately long regardless of workload type. This extends DRF with temporal awareness: dominant shares are computed against predicted future utilization over the job's expected
  runtime, not just the current snapshot. Third, a runtime enforcement layer assigns CPU priority weights to pods via Kubernetes QoS classes and dynamic cgroup controls, based on the
  approach of Blagodurov et al. Critical latency-sensitive jobs receive guaranteed CPU access; batch jobs operate on remaining capacity and are dynamically throttled when a co-located
  critical job approaches its SLA boundary.

  The system is evaluated against the Google Cluster Trace v3 using the Clovers simulation framework (Jiang Zhi, 2025), comparing against the default Kubernetes scheduler and the static
  DRF baseline. Target outcomes are cluster utilization above 85%, SLA compliance above 95%, and inter-tenant fairness variance below 10%.