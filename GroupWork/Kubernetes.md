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
