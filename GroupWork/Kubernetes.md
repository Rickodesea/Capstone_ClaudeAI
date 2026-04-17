  What You Already Know

  In your old design, you had:

  Physical Server (node)
      └── VMs running on it (managed by hypervisor like KVM/Xen)
              └── Your admission control decides: can this node take another VM?

  The cluster manager (like Google Borg, or what you were designing) sits above the nodes and decides which node gets which VM.

  ---
  What Kubernetes Is

  Kubernetes is not a cloud provider. It's a workload scheduler and manager that runs on top of whatever infrastructure you give it — cloud or bare metal.

  It doesn't manage VMs. It manages containers (think: a lighter, faster version of a VM — just a process with isolation, no full OS).

  Cloud Provider (AWS / GCP / Azure / Linode)
      └── Gives you: Virtual Machines (your nodes)
              └── Kubernetes runs ON those VMs
                      └── Kubernetes schedules: Containers (pods)
                              └── Your app runs inside a container

  ---
  How It Applies to AWS, GCP, Azure, Linode

  Every cloud provider has a managed Kubernetes service:

  ┌──────────┬──────────────────────────────────┐
  │ Provider │        Kubernetes Service        │
  ├──────────┼──────────────────────────────────┤
  │ AWS      │ EKS (Elastic Kubernetes Service) │
  ├──────────┼──────────────────────────────────┤
  │ GCP      │ GKE (Google Kubernetes Engine)   │
  ├──────────┼──────────────────────────────────┤
  │ Azure    │ AKS (Azure Kubernetes Service)   │
  ├──────────┼──────────────────────────────────┤
  │ Linode   │ LKE (Linode Kubernetes Engine)   │
  └──────────┴──────────────────────────────────┘

  You tell AWS: "give me 5 VMs." AWS spins them up. Then Kubernetes takes over those 5 VMs and treats them as a cluster of nodes. From that point, you don't talk to AWS to run your app —
  you talk to Kubernetes.

  ---
  The Layers Explained

  ┌─────────────────────────────────────┐
  │  CLOUD PROVIDER (AWS / GCP / Azure) │  ← Sells you VMs, storage, networking
  │                                     │     You pay per VM per hour
  └────────────────┬────────────────────┘
                   │  gives you
                   ▼
  ┌─────────────────────────────────────┐
  │  VIRTUAL MACHINES (Nodes)           │  ← These are your cluster nodes
  │  e.g. 10 x m5.xlarge on AWS        │     Each has CPU, RAM, disk
  │                                     │     Kubernetes sees these as "workers"
  └────────────────┬────────────────────┘
                   │  managed by
                   ▼
  ┌─────────────────────────────────────┐
  │  KUBERNETES                         │  ← The cluster manager / scheduler
  │  (control plane + worker nodes)     │     Decides which container goes where
  │                                     │     Handles failures, restarts, scaling
  └────────────────┬────────────────────┘
                   │  runs
                   ▼
  ┌─────────────────────────────────────┐
  │  CONTAINERS (Pods)                  │  ← Your actual workloads
  │  e.g. ML training job, web server  │     Lightweight, start in seconds
  │                                     │     Multiple can share one VM/node
  └─────────────────────────────────────┘

  ---
  How It's Different From Your Old Design

  ┌────────────────────────────────────────────────────┬────────────────────────────────────────────────────┐
  │                  Your Old Design                   │                     Kubernetes                     │
  ├────────────────────────────────────────────────────┼────────────────────────────────────────────────────┤
  │ Unit of work = VM                                  │ Unit of work = Container (pod)                     │
  ├────────────────────────────────────────────────────┼────────────────────────────────────────────────────┤
  │ Admission control = can this node take another VM? │ Scheduler = which node has room for this pod?      │
  ├────────────────────────────────────────────────────┼────────────────────────────────────────────────────┤
  │ VM takes minutes to start                          │ Container starts in seconds                        │
  ├────────────────────────────────────────────────────┼────────────────────────────────────────────────────┤
  │ You designed node-level memory prediction          │ Kubernetes schedules based on requested CPU/memory │
  ├────────────────────────────────────────────────────┼────────────────────────────────────────────────────┤
  │ One layer (nodes + VMs)                            │ Two layers (VMs as nodes, containers as workloads) │
  └────────────────────────────────────────────────────┴────────────────────────────────────────────────────┘

  The core scheduling problem is the same — which node gets this workload, is there enough room, will SLAs be met — but the unit being scheduled is a container, not a VM.

  ---
  Is Kubernetes a Cloud Manager?

  Not exactly. Think of it this way:

  - Cloud provider = the landlord (owns the building, rents you rooms/VMs)
  - Kubernetes = the property manager (decides which tenant goes in which room, handles complaints, evicts if needed)
  - Your app = the tenant

  Kubernetes doesn't know or care whether those VMs are on AWS or a server in your basement. It just sees nodes with resources and schedules work onto them.

  ---
  Bottom Line for Your Capstone

  Your admission control idea still applies — Kubernetes has its own scheduler, but it's basic. It schedules based on requested resources, not predicted actual usage. That's the gap. Your
  capstone can be:

  ▎ A smarter Kubernetes scheduler plugin that uses workload history to predict real utilization and makes admission decisions that improve utilization while maintaining SLA — something
  ▎ the default Kubernetes scheduler cannot do.

  Same problem you were solving before, just at the container level instead of the VM level.

---

ubernetes — All Your Questions Answered

  1. Is Kubernetes at the same layer as a cluster manager?

  Yes — Kubernetes is a cluster manager. Same category as Google Borg, Apache Mesos, and HashiCorp Nomad. All four do the same thing: accept workloads, decide which machine runs them, and
  keep them healthy. Borg was Google's internal system built in the 2000s. Kubernetes is essentially the open-source version of Borg, released by Google in 2014.

  ---
  2. Node = physical device AND a VM?

  Both. A "node" in Kubernetes is any machine that can run containers. That machine could be:
  - A bare-metal server — a physical box in a datacenter
  - A VM — an EC2 instance, an Azure VM, a Linode VPS

  Kubernetes doesn't care which. It just sees a machine with CPU and RAM. You register it as a node and Kubernetes starts scheduling containers onto it.

  ---
  3. Kubernetes vs GitHub Actions — what is the difference?

  They operate at completely different layers and solve different problems:

  ┌─────────────────┬─────────────────────────────────────────────────────────────┬─────────────────────────────────────────┐
  │                 │                         Kubernetes                          │             GitHub Actions              │
  ├─────────────────┼─────────────────────────────────────────────────────────────┼─────────────────────────────────────────┤
  │ What it is      │ Infrastructure management system                            │ CI/CD automation platform               │
  ├─────────────────┼─────────────────────────────────────────────────────────────┼─────────────────────────────────────────┤
  │ Always running? │ Yes — continuously managing your cluster                    │ No — runs pipelines triggered by events │
  ├─────────────────┼─────────────────────────────────────────────────────────────┼─────────────────────────────────────────┤
  │ What it manages │ Where containers run, how many replicas, networking, health │ Build → test → deploy pipelines         │
  ├─────────────────┼─────────────────────────────────────────────────────────────┼─────────────────────────────────────────┤
  │ Triggered by    │ Desired state declarations (YAML files)                     │ Code pushes, PRs, schedules             │
  ├─────────────────┼─────────────────────────────────────────────────────────────┼─────────────────────────────────────────┤
  │ Lives on        │ Your cluster (or managed by cloud)                          │ GitHub's servers                        │
  └─────────────────┴─────────────────────────────────────────────────────────────┴─────────────────────────────────────────┘

  Analogy: GitHub Actions is the factory assembly line that builds and ships the product. Kubernetes is the warehouse and delivery truck system that the shipped product runs on. They
  connect — GitHub Actions can deploy TO Kubernetes as one of its steps — but they're not the same thing.

  ---
  4. How would your capstone model apply to Kubernetes?

  Your scheduler becomes a custom Kubernetes Scheduler Plugin. Kubernetes has a built-in extension called the https://kubernetes.io/docs/concepts/scheduling-eviction/scheduling-framework/
  — it defines phases you can hook into:

  Pod submitted → PreFilter (admission check) → Filter (rule out nodes) → Score (rank nodes) → Bind (assign pod to node)

  Your capstone plugs in here:

  - PreFilter phase — your Random Forest prediction model runs. Given the new pod's resource request + current cluster state, predict what P95 utilization would be if admitted. If
  predicted utilization exceeds your threshold → reject the pod (send it to a pending queue or deny it).
  - Score phase — your DRF fairness model scores each candidate node. Nodes where the requesting tenant's dominant resource share is lowest score highest. Add a temporal complementarity
  bonus for nodes whose running workloads have off-peak patterns.
  - Bind phase — pod gets assigned to the highest-scoring node.

  Additionally, a custom Admission Webhook (a K8s extension point that runs before the scheduler even sees the pod) can enforce namespace-level quotas and tenant fairness caps before
  anything reaches the scheduler.

  This is exactly how Wang & Yang deployed their LSTM+DQN system in Paper #7. You would follow the same architecture.

  ---
  5. How is Kubernetes deployed so it can see all nodes?

  Kubernetes has two kinds of nodes: control plane nodes and worker nodes.

  Control Plane (master nodes)
  ├── API Server        ← all commands flow through here
  ├── etcd              ← distributed database storing all cluster state
  ├── Scheduler         ← decides which node gets each pod
  └── Controller Manager ← watches state, ensures desired matches actual

  Worker Nodes (your machines/VMs)
  ├── kubelet           ← agent that registers the node with the API Server
  ├── kube-proxy        ← handles networking
  └── Container runtime ← (Docker / containerd) actually runs containers

  The key: nodes come TO the control plane — not the other way around. When a VM boots and you install Kubernetes on it, the kubelet daemon starts and registers itself with the API Server.
   From that point, the API Server knows about the node and the Scheduler can place pods on it.

  The API Server is the single entry point for everything. When you run kubectl apply -f mypod.yaml, it goes to the API Server → stored in etcd → Scheduler reads the unbound pod → picks a
  node → kubelet on that node creates the container.

  In managed Kubernetes (EKS on AWS, AKS on Azure, GKE on GCP, LKE on Linode), the cloud provider runs the control plane for you on their infrastructure. You only provision worker nodes.
  That's the typical cloud deployment.

  ---
  6. CSP products — VM vs managed Kubernetes

  Cloud Provider (AWS / Azure / GCP / Linode)
  ├── Raw VMs          (EC2 / Azure VMs / GCP Compute / Linode VPS)
  │                    — you buy a computer, manage everything yourself
  ├── Managed K8s      (EKS / AKS / GKE / LKE)
  │                    — CSP manages the control plane; you add worker VMs
  ├── Databases        (RDS, Azure SQL, Cloud SQL)
  ├── Storage          (S3, Azure Blob, GCS)
  └── Other services   (load balancers, CDN, queues, AI APIs, etc.)

  Buying a VM and using managed Kubernetes are separate transactions. When you buy a VM from Linode, you get a computer with an IP address. When you use LKE (Linode Kubernetes Engine),
  Linode runs the control plane behind the scenes and provisions VMs as your worker nodes — those VMs just appear in your cluster automatically.

  For your capstone: if you were to prototype this for real, you'd use a managed K8s service so you don't have to manage etcd and the control plane yourself. Your custom scheduler plugin
  and admission webhook would be deployed as pods running inside the cluster.

---

  1. Can I simulate in Clovers? How do I test on Google data? Will I have to simulate?

  Yes, almost certainly you will simulate — and that's fine for a capstone. Here's how it works:

  Google Cluster Trace v3 is publicly downloadable (Google released it for research). It's a CSV/Parquet dataset of real job submissions to Google's production cluster: job arrival time,
  resource request (CPU, RAM), actual peak usage, priority, duration, tenant ID. You download it to your laptop.

  Clovers (Jiang Zhi's simulator) is a Python program that reads trace data and replays jobs through a simulated cluster. You can configure cluster size, number of nodes, scheduling
  algorithm. You run your scheduler code against the trace and measure what happens — utilization, SLA violations, wait times. No live Kubernetes needed.

  Other options if you want to test actual Kubernetes:
  - kind (Kubernetes in Docker) — runs a full K8s cluster on your laptop in Docker containers. Completely local.
  - minikube — similar, single-node K8s cluster locally.
  - For your capstone, Clovers + Google Trace is probably sufficient and much simpler.

  Bottom line: Download Google Cluster Trace → replay through Clovers with your scheduler → measure metrics. You don't need a real cloud account or live cluster.

  ---
  2. Coach — what am I predicting in job scheduling?

  This is the most important conceptual question. Let me be precise.

  In Kubernetes, when a tenant submits a job, they specify a resource request: "I need 4 CPU cores and 8 GB RAM." This is the declaration. But the actual usage of that job is almost always
   lower — tenants over-request as a safety buffer. A job that requests 8 GB might only use 5 GB at peak.

  So there are three things worth predicting:

  ┌───────────────────────────────────────┬─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
  │            What to predict            │                                                               Why it matters                                                                │
  ├───────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ Actual peak usage vs. declared        │ If actual peak is lower, you can safely fit more jobs on the node than declared requests suggest. This is the overcommitment opportunity.   │
  │ request                               │                                                                                                                                             │
  ├───────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ Job duration (when will it finish)    │ Determines when the node's resources free up for the next tenant. "Your job will start in ~12 minutes" vs. "your job will start in ~45      │
  │                                       │ minutes."                                                                                                                                   │
  ├───────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ Temporal shape of usage (when does it │ Coach's insight — if Job A peaks at 2 PM and Job B peaks at 8 PM, you can co-locate them on the same node safely even if their combined     │
  │  peak?)                               │ declared requests exceed capacity.                                                                                                          │
  └───────────────────────────────────────┴─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

  So what you're predicting is: given this job's resource request and tenant history, what will the actual usage profile look like, and when will it conflict with other jobs on the same
  node?

  You're not predicting "will the container die." You're predicting "if I place this job here, will the node become overloaded, and will the other tenant's SLA be violated?"

  ---
  3. Does DRF apply? Do I know resource needs upfront for containers?

  Yes — and yes, you DO know resource needs upfront. This is one of Kubernetes's design requirements. Every pod submission includes a resource spec:

  resources:
    requests:
      cpu: "4"
      memory: "8Gi"
    limits:
      cpu: "8"
      memory: "16Gi"

  DRF takes the request values and computes each tenant's dominant share against total cluster capacity. If the cluster has 100 CPUs and 200 GB RAM, and Tenant A's jobs collectively use 20
   CPUs (20% of CPU) and 10 GB RAM (5% of memory), their dominant share is 20% (CPU). DRF ensures no tenant's dominant share exceeds another's by more than is necessary. Fully applicable.

  ---
  Does Resource Central (Cortez) apply?

  Yes, with one reframe. Cortez predicts actual CPU usage from declared requests + tenant history. In Kubernetes, tenants over-request. A tenant who always requests 8 GB but only uses 4 GB
   peak has a consistent pattern. Resource Central trains on that history: "This tenant's jobs historically use 52% of what they request." That 52% figure is what you use for scheduling,
  not the declared 100%. This is how safe overcommitment works — you're filling the gap between declared requests and actual usage.

  ---
  Blagodurov — how to implement in Kubernetes?

  Kubernetes already has a three-tier QoS system built on cgroups:

  ┌───────────────┬──────────────────────┬────────────────────────────────────────────┐
  │ K8s QoS Class │   When it applies    │              cgroups behavior              │
  ├───────────────┼──────────────────────┼────────────────────────────────────────────┤
  │ Guaranteed    │ request == limit     │ Never throttled or preempted first         │
  ├───────────────┼──────────────────────┼────────────────────────────────────────────┤
  │ Burstable     │ request < limit      │ Can burst, may be throttled under pressure │
  ├───────────────┼──────────────────────┼────────────────────────────────────────────┤
  │ BestEffort    │ no request/limit set │ First to be throttled or preempted         │
  └───────────────┴──────────────────────┴────────────────────────────────────────────┘

  You implement Blagodurov's dynamic weight model by:
  1. Tagging each tenant's jobs with a priority/SLA class (critical vs. batch)
  2. Setting Guaranteed QoS for critical jobs (they always get their declared CPU)
  3. Setting Burstable/BestEffort for batch jobs (they use what's left over)
  4. Running a controller that watches Prometheus metrics. If a critical job's P99 latency exceeds its SLO target, the controller lowers the CPU limit of co-located batch jobs —
  effectively adjusting weights dynamically.

  The controller is a small Kubernetes operator (a program that watches K8s events and takes action). This is your enforcement layer on top of the admission + scheduling logic.

  ---
  Wang & Yang — why prediction in scheduling? I understood it for VM overcommitment but not here.

  Great question. The core insight is the same — you just shift the question slightly.

  In VM overcommitment: "If I deploy this VM, will memory pressure cause existing VMs to crash?"

  In job scheduling: "If I schedule this job NOW, will the nodes become overloaded in 30 minutes when this job ramps up alongside the other jobs already running?"

  The problem is that a node looks healthy at scheduling time but becomes overloaded later. Example:

  - 9:00 AM: Node has 70% CPU used. You admit a new job that requests 20% CPU. Total: 90%. Looks fine.
  - 9:15 AM: Three other jobs that were admitted earlier all hit their peak simultaneously. Node is now at 110%. SLA violations.

  Wang's LSTM predicts: "In 30 minutes, this node will be at 95% CPU based on the ramp-up patterns of currently running jobs." That forecast at scheduling time prevents you from placing
  the new job there — even though the node looks available right now.

  So Wang's prediction answers: "Is this node actually available 30 minutes from now, not just right now?" Your scheduler only places jobs on nodes where predicted future utilization stays
   below your threshold. This is proactive admission control rather than reactive damage control.

  ---
  Kofi (LSTM) — what use is prediction to scheduling?

  Same as Wang but from the other direction — Kofi shows how accurately you can predict workload demand from historical traces. The scheduling application:

  1. Admission control: Predict that if this job is admitted, the node's CPU will hit 92% in 20 minutes → deny admission, queue the job for 10 minutes until another job finishes.
  2. Queue time estimation: Tell the waiting tenant "estimated wait: 12 minutes" based on predicted job completion times of currently running jobs.
  3. Proactive autoscaling: Predict that cluster-wide demand will spike in 45 minutes → provision additional capacity now (if autoscaling is in scope).

  Without prediction, the scheduler only reacts to what's happening now. With prediction, it acts on what's about to happen.

    ---
  If all nodes are occupied, do waiting jobs violate fairness and SLA?

  Yes — exactly right. When all nodes are at declared-request capacity, new jobs go into a Pending state in Kubernetes. They sit in a queue with no guarantee of when they start. This
  violates:

  - Fairness — if Tenant A submitted 50 batch jobs before Tenant B submitted one critical job, Tenant B waits 2 hours with no recourse
  - SLA — if the SLA says "job starts within 5 minutes" and the job waits 45 minutes, that's a direct violation

  But here's the key nuance: nodes may not actually be full. Tenants over-declare resource requests as a safety buffer. A node where declared requests sum to 100% might only be at 60%
  actual CPU usage. The default scheduler sees "full" and blocks new jobs. Your scheduler looks past declarations to actual usage — and finds room. That's the overcommitment opportunity.

  ---
  Does the scheduler launch the job immediately or say "this job starts in 15 minutes"?

  Kubernetes today does neither elegantly — it either places the job immediately or marks it Pending and keeps trying. It has no concept of "I'll schedule you in 15 minutes when Node 3
  frees up."

  This is a real gap. HPC schedulers like SLURM do exactly what you're describing — it's called backfill scheduling. SLURM predicts when running jobs will finish, reserves those slots for
  waiting jobs, and tells users "your job starts at 14:32." Kubernetes doesn't do this natively.

  Your capstone can add it: predict job duration → know when resources free up → pre-assign waiting jobs to those future slots → tell the tenant an estimated start time. This is novel in
  the Kubernetes context and directly addresses the SLA violation problem.

  ---
  Can the scheduler predict that current jobs will end soon and schedule proactively?

  Yes — and this is one of the most impactful things prediction enables. You need two predictions working together:

  1. How long will running jobs take? (job duration prediction — Resource Central does this)
  2. How much will they use until they end? (utilization prediction — Wang, Kofi)

  With both, your scheduler can say: "Job X on Node 4 will finish in ~10 minutes, freeing 6 CPUs and 12 GB RAM. Tenant B's waiting job fits exactly there. Bind it to Node 4 now, mark it as
   starting in ~10 minutes." This turns idle waiting time into scheduled future slots.

  ---
  When a user submits — schedule for future or wait until available?

  In the real world, both patterns exist:

  - Queue and wait (Kubernetes default): job sits Pending, scheduler checks every few seconds and places it when space opens. No time estimate given.
  - Future reservation (SLURM, HPC): scheduler assigns a specific future slot. "Your job starts at 14:32 on Node 4." Tenant knows when to expect it.
  - Speculative placement (your capstone's angle): admit the job now, bind it to a predicted-free node, enforce that the currently running jobs don't exceed predicted usage before that
  slot opens.

  For your capstone, the most practical approach: predict job completion times for running jobs, build a simple time-forward reservation queue, and report estimated start times. This alone
   is a meaningful improvement over vanilla Kubernetes.

  ---
  What actually happens when a node is over capacity in the real world?

  Three different things depending on which resource:

  ┌──────────────────────┬─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
  │       Resource       │                                                       Over limit behavior                                                       │
  ├──────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ CPU                  │ Container is throttled — slowed down but not killed. Latency spikes. SLA violated silently.                                     │
  ├──────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ Memory               │ Container is OOM-killed — the OS kills the process immediately. The container restarts (or fails permanently).                  │
  ├──────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ Node memory pressure │ kubelet evicts pods — lowest priority first (BestEffort, then Burstable). Evicted pod may reschedule elsewhere or stay Pending. │
  └──────────────────────┴─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

  So yes — memory over-limit means the job dies. CPU over-limit means the job gets very slow. Neither is acceptable under an SLA. The goal of your scheduler is to make these events
  essentially impossible by never committing to a placement the prediction says will cause them.

  ---
  GitHub Actions — stateless so failure doesn't matter?

  Partially right. GHA jobs are ephemeral and usually idempotent — if a CI pipeline fails, you rerun it. No production state is lost. But "doesn't matter" overstates it — failure still
  costs time and delays deployments.

  The real difference: GHA has no SLA in the traditional sense. Nobody promises your build finishes in under 5 minutes. Kubernetes production workloads — a web service, an ML inference
  endpoint, a database job — absolutely have SLAs. If that container gets OOM-killed and a user's transaction fails, there are real consequences (money, contracts, user trust). That's why
  the enforcement matters.

  ---
  Can I run Kubernetes on my laptop? VMs too?

  Yes — no VMs needed. Your options:

  ┌──────────────────────┬──────────────────────────────────────────────┬──────────────┬───────────────────────────────────────────┐
  │         Tool         │                 Resource use                 │  Setup time  │                 Good for                  │
  ├──────────────────────┼──────────────────────────────────────────────┼──────────────┼───────────────────────────────────────────┤
  │ kind (K8s in Docker) │ ~2–4 GB RAM, uses Docker containers as nodes │ < 1 minute   │ Testing scheduler plugins, small clusters │
  ├──────────────────────┼──────────────────────────────────────────────┼──────────────┼───────────────────────────────────────────┤
  │ minikube             │ ~2–4 GB RAM, single node                     │ ~2 minutes   │ Learning, simple experiments              │
  ├──────────────────────┼──────────────────────────────────────────────┼──────────────┼───────────────────────────────────────────┤
  │ k3s                  │ ~512 MB RAM                                  │ < 30 seconds │ Very lightweight K8s                      │
  └──────────────────────┴──────────────────────────────────────────────┴──────────────┴───────────────────────────────────────────┘

  On a 16 GB laptop, kind with 4–5 nodes runs fine. The "nodes" are just Docker containers pretending to be machines — no actual VMs. You can deploy your custom scheduler plugin to it and
  watch it make decisions in real time.

  However — for replaying millions of jobs from the Google Cluster Trace, you do NOT want to actually launch containers. That's where simulation comes in. kind is for testing that your
  code runs correctly on a small example. The simulator is for evaluating performance at scale.

  ---
  Should I write my own simulator instead of Clovers?

  Writing your own is genuinely reasonable and probably the right call for a capstone. Clovers is comprehensive but complex — learning someone else's codebase takes time, and you'd be
  constrained by its design decisions.

  A simple scheduler simulator is maybe 300–500 lines of Python:

  # Core data structures
  cluster = {node_id: {cpu: 32, mem: 128, jobs: []}}
  job_queue = PriorityQueue()  # incoming jobs sorted by arrival time
  timeline = EventQueue()      # job completions scheduled as future events

  # Main loop
  while timeline or job_queue:
      event = next event (job arrival or job completion)
      if job_arrival:
          node = your_scheduler.place(job, cluster)  # YOUR CODE HERE
          if node:
              assign job to node, schedule completion event
          else:
              job_queue.push(job)  # pending
      if job_completion:
          free resources on node
          try to place pending jobs from queue
      record metrics (utilization, wait time, SLA violations)

  The advantage: you fully understand every line. You can swap in any scheduling algorithm in one function. You control exactly what gets measured. The Google Cluster Trace gives you real
  job arrival times, resource requests, and actual usage data to feed in.

  ---
  Can Kubernetes see how much CPU/memory a running container uses? Does Google Trace log it?

  Yes on both.

  Kubernetes tracking: The Linux kernel tracks CPU and memory usage per container via cgroups automatically — no extra work needed. The kubelet reads this and exposes it. Add Prometheus
  and you get per-pod CPU/memory sampled every 15 seconds. kubectl top pods shows live usage right now.

  Google Cluster Trace: Yes — it logs both declared requests AND actual measured usage at regular sample intervals. The dataset includes:
  - resource_request: what the job declared (CPU, memory)
  - average_usage: mean observed CPU and memory during execution
  - maximum_usage: peak observed
  - Job start/end timestamps
  - Machine ID, priority class, tenant/task group

  This is the exact training data you need. Actual usage vs. declared request per tenant, over time. That ratio — how much tenants actually use vs. what they claim — is your primary
  prediction signal.

  ---
  Can prediction be "what a tenant consistently requests"?

  Yes — and this is actually the strongest signal, per Resource Central. Tenants have habits:

  - "Tenant A always requests 8 CPUs but peaks at 5.1 in practice"
  - "Tenant B's ML jobs always request 32 GB memory and use 28 GB"
  - "Tenant C's batch jobs declare 4-hour runtime but finish in 90 minutes on average"

  Resource Central found tenant/subscription history was more predictive than job size, time of day, or workload type. So a feature as simple as "this tenant's historical request-to-actual
   ratio" is a powerful input to your model.

  ---
  Multiple prediction layers — cluster-level, tenant-level, job-type — combined?

  Yes — this is a strong design. Think of it as a hierarchy of corrections:

  Base prediction:  cluster-level pattern (peak hours, daily cycle)
     + correction:  tenant-specific ratio (Tenant A uses 65% of what they request)
     + correction:  job-type adjustment (ML training jobs ramp up slowly, then plateau)
     + correction:  current context (3 other big jobs just started on this node)
     = final predicted utilization

  This is interpretable (you can explain each component), composable (add or remove layers), and directly addresses Perera's model interpretability gap. No existing paper in your set does
  this layered approach — Coach works at cluster level, Resource Central works at tenant level, but nobody combines them explicitly as additive corrections.

  ---
  Additive peaks technique — does it apply to spikes?

  Yes, and this is a genuinely good idea. The standard version of what you're describing is called STL decomposition (Seasonal-Trend-Residual) — decompose any time series into:

  - Trend component: the slow-moving baseline (cluster grows over months)
  - Seasonal component: repeating patterns (weekday peaks, weekend lulls)
  - Residual component: unpredictable bursts and spikes

  Your insight about "doesn't have to be waves, can just add peaks" is valid and actually more flexible. Instead of assuming sinusoidal patterns, you model peaks as pulse shapes — a job
  starts, ramps up, plateaus, finishes. Each job is a pulse. The total cluster load at any time is the sum of all active pulses. A new job arrival adds one more pulse to the sum.

  This framing is powerful for scheduling because:
  1. You predict the shape of the new job's pulse (from tenant history)
  2. You look at the current sum of active pulses on each node
  3. You ask: "if I add this new pulse to Node 3's sum, does the combined signal exceed my threshold at any point during the job's lifetime?"
  4. If no — admit. If yes — find a different node or delay.

  This is your additive technique applied to admission control. It's interpretable, computationally cheap, and novel in this context. You should use it.

  ---

  ● The optimization model is the decision-making core. Prediction is the oracle that tells the optimizer what is real. Let me explain the relationship first, then write the full flowchart
  document.

  ---
  How Optimization and Prediction Connect

  The scheduler's job is fundamentally a constrained optimization problem: given a new job, decide where to place it (or when) such that utilization is maximized while fairness and SLA
  constraints hold. The challenge is that you cannot optimize against declared resource requests — tenants over-declare. You need to optimize against actual predicted usage.

  Prediction feeds optimization as follows:

  ┌──────────────────────────────────────────────────────────┬───────────────────────────────────────────────────────┐
  │                    Optimization needs                    │                  Prediction provides                  │
  ├──────────────────────────────────────────────────────────┼───────────────────────────────────────────────────────┤
  │ "How much will this job actually use?"                   │ Predicted P95 CPU/memory per job                      │
  ├──────────────────────────────────────────────────────────┼───────────────────────────────────────────────────────┤
  │ "When will running jobs finish?"                         │ Predicted job duration                                │
  ├──────────────────────────────────────────────────────────┼───────────────────────────────────────────────────────┤
  │ "Is this node safe to overcommit?"                       │ Overcommit ratio α (derived from prediction accuracy) │
  ├──────────────────────────────────────────────────────────┼───────────────────────────────────────────────────────┤
  │ "Will this job peak at the same time as neighbors?"      │ Temporal usage shape (your additive peaks)            │
  ├──────────────────────────────────────────────────────────┼───────────────────────────────────────────────────────┤
  │ "What dominant share will this tenant have if admitted?" │ Predicted resource share post-placement               │
  └──────────────────────────────────────────────────────────┴───────────────────────────────────────────────────────┘

  Without prediction, the optimizer must be conservative and use declared requests — which wastes 30–40% of capacity. With prediction, the optimizer can pack more jobs safely because it
  knows actual usage.

  ---
  Scheduling Gate — Yes, You Can Override the Retry Loop

  Kubernetes 1.26+ introduced Scheduling Gates. A pod can be created in a held state — the scheduler never sees it until you explicitly release it. Your controller:

  1. Receives job submission
  2. Predicts when a slot opens (e.g., Node 4 frees up in 12 minutes)
  3. Creates the pod with a schedulingGate: hold-for-slot label → scheduler ignores it
  4. Sets a timer for T+12 minutes
  5. At T+12, removes the gate → pod enters the scheduler queue → immediately placed on the now-free node

  Why this is faster than the retry loop: Default Kubernetes retries every ~15 seconds with an exponential backoff, wasting scheduler cycles and causing jitter. Your approach: one precise
  release at the predicted moment. No wasted checks. The tenant gets a concrete start time estimate. Multiple jobs can be gated to different future times, turning the scheduler into a
  reservation system.