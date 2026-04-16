  Team Brief — Chaudhari (2025) Paper Summary

  What the Paper Is About

  This paper is about why Kubernetes's default scheduler fails for AI/ML workloads, and what strategies fix that.

  Kubernetes was built for web apps — stateless, lightweight, and simple to schedule. But AI training jobs are completely different: they need GPUs, they need all their resources at once,
  they're sensitive to which machines they land on, and multiple teams share the same cluster. The default scheduler doesn't handle any of that well.

  The author identifies three core problems and three strategies to solve them:

  The Problems:
  - GPU fragmentation — clusters have mixed GPU types (RTX, A100, H100) and poor placement wastes them. Bad scheduling can degrade training performance by up to 70%
  - Fairness violations — research teams, production teams, and data science teams all have different usage patterns. Quota-based limits don't work when hyperparameter tuning jobs run for
  days and starve everyone else
  - Unpredictable performance — ignoring network topology between nodes can slow training down by 200–300%

  The Strategies:
  - Gang scheduling — don't start a job until ALL its required resources are available. Prevents deadlocks where partial allocations block everyone
  - Topology-aware placement — put workers that communicate heavily on the same rack or same NVLink group to cut network overhead
  - Predictive resource management — use ML to forecast demand and pre-allocate before contention happens

  The paper then proposes a 4-component architecture: Workload Classifier → Fairness Engine → Topology Optimizer → Priority Queue.

  ---
  Which Scheduler Has the Best Results?

  From the paper's own tables (these numbers come from cited papers, not original experiments):

  For Utilization:

  ┌───────────────────────┬─────────────────┐
  │       Approach        │ GPU Utilization │
  ├───────────────────────┼─────────────────┤
  │ No topology awareness │ 60%             │
  ├───────────────────────┼─────────────────┤
  │ Network-aware         │ 85%             │
  ├───────────────────────┼─────────────────┤
  │ Topology-optimized    │ 90%             │
  ├───────────────────────┼─────────────────┤
  │ Dynamic gang sizing   │ 90%             │
  └───────────────────────┴─────────────────┘

  For Fairness + Completion Time:

  ┌─────────────────────┬─────────────┬────────────────────────────┬───────────────────┐
  │      Approach       │ Utilization │ Job Completion Improvement │ Failure Reduction │
  ├─────────────────────┼─────────────┼────────────────────────────┼───────────────────┤
  │ Traditional FCFS    │ 65%         │ 5%                         │ 10%               │
  ├─────────────────────┼─────────────┼────────────────────────────┼───────────────────┤
  │ Gang scheduling     │ 85%         │ 40%                        │ 60%               │
  ├─────────────────────┼─────────────┼────────────────────────────┼───────────────────┤
  │ Dynamic gang sizing │ 90%         │ 35%                        │ 45%               │
  └─────────────────────┴─────────────┴────────────────────────────┴───────────────────┘

  Bottom line: Topology-optimized placement and dynamic gang sizing both hit 90% utilization. Together they're the strongest combination. The paper's combined architecture claims 92%
  efficiency and 67% overall improvement.

  ---
  Should You Care About Security?

  No. The author mentions security in the first two paragraphs as motivation — something like "AI workloads need secure environments." Then the rest of the paper never mentions security
  again. It was padding to cite a cloud security survey. Your capstone focus is scheduling. Ignore it.

  ---
  The Gap — What Your Capstone Can Build On

  The paper is honest about one thing: it's all conceptual. There are no real experiments — every number is borrowed from other papers. The proposed 4-component framework (Workload
  Classifier, Fairness Engine, Topology Optimizer, Priority Queue) is never actually built or tested.

  The gap is: this framework exists only on paper.

  Angles you can target for the capstone:

  1. Implement and evaluate gang scheduling vs. dynamic gang sizing on a real (or simulated) multi-tenant cluster — the paper never does this with its own data
  2. Build the Fairness Engine component — the paper says it improves fairness by 35% but gives no concrete algorithm. You can design and test one
  3. Topology-aware placement for a specific workload type — pick one (e.g., distributed training or inference serving) and implement topology-aware scheduling with measurable results
  4. Chase the cited papers — References [3], [4], [5], [6] are the actual research papers behind the numbers. Those are worth reading. They will have gaps you can extend

  The best cited papers to look at:
  - [5] Gao et al. (2022) — Deep Learning Workload Scheduling in GPU Datacenters — likely the richest source, covers gang scheduling taxonomy
  - [3] Sharma et al. (2024) — GPU Cluster Scheduling for Network-Sensitive Deep Learning — the source for the 70% degradation and topology numbers
  - [6] Harlap et al. (2018) PipeDream — the pipeline parallelism paper, older but foundational