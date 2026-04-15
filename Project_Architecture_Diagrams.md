# Project Architecture & Design Diagrams
**Node-Level Predictive Admission Control for Memory Overcommitment**
DAMO 699 Capstone — Spring 2026

---

## 1. System Overview

```mermaid
graph TD
    CM["🖥️ Cluster Manager\n(Bin Packing Engine)"]
    VM_REQ["New VM Request"]
    NODE["Physical Node\n(Our Model Lives Here)"]
    OPT["Optimization &\nAdmission Control Model"]
    GEN["Generalized\nPrediction Model"]
    CUR["Curated\nPrediction Model"]
    VMs["Running VMs\n(VM1 … VMn)"]
    RAM["Physical RAM"]

    VM_REQ --> CM
    CM -->|"Query: Accept VM?"| NODE
    NODE -->|"YES / NO"| CM
    NODE --> OPT
    OPT --> GEN
    OPT --> CUR
    OPT --> VMs
    VMs --> RAM

    style CM fill:#003A52,color:#00B4D8,stroke:#00B4D8
    style NODE fill:#1A2E44,color:#CAE9FF,stroke:#00B4D8
    style OPT fill:#003A52,color:#FFD166,stroke:#FFD166
    style GEN fill:#0D2A1A,color:#4CAF50,stroke:#4CAF50
    style CUR fill:#2A1A0D,color:#E0876A,stroke:#E0876A
    style RAM fill:#1A3A5C,color:#00B4D8,stroke:#00B4D8
```

---

## 2. Admission Control Flow

```mermaid
flowchart TD
    A["New VM Request"] --> B["Cluster Manager\nselects candidate node"]
    B --> C["Query node's\nAdmission Control Model"]
    C --> D["Node Analysis\n───────────────\n• Generalized model → predict new VM wave\n• Curated models → forecast existing VM waves\n• Compute M_new(t) = M(t) + m_j(t)\n• Check: max M_new(t) ≤ RAM_physical?\n• Check: SLA budgets still safe?"]
    D --> E{Accept?}
    E -->|YES| F["Admit VM\nStart curated model\nlearning for VM j"]
    E -->|NO| G["Reject\nReturn to Cluster Manager"]
    G --> H["Try next candidate node\nfrom bin-pack results"]
    H --> C

    style A fill:#00B4D8,color:#0D1B2A
    style E fill:#1A2E44,color:#FFEE58,stroke:#FFEE58
    style F fill:#1B4A22,color:#4CAF50,stroke:#4CAF50
    style G fill:#4A1B1B,color:#EF5350,stroke:#EF5350
```

---

## 3. Wave-Based Memory Demand Model

```mermaid
graph LR
    subgraph "Per-VM Wave  m_i(t)"
        V1["VM 1\nHigh spike\nhigh amplitude"]
        V2["VM 2\nStable use\nflatline wave"]
        V3["VM 3\nModerate\nmedium amplitude"]
    end

    subgraph "Superposition"
        SUM["M(t) = m_1(t) + m_2(t) + m_3(t)\n(additive — closed form)"]
    end

    subgraph "Detection"
        PEAK["Find t* where M(t*) > RAM_physical"]
        ACT["Act at  t_act = t* − τ_lead"]
    end

    V1 --> SUM
    V2 --> SUM
    V3 --> SUM
    SUM --> PEAK
    PEAK --> ACT

    style SUM fill:#003A52,color:#FFD166,stroke:#FFD166
    style PEAK fill:#4A1B1B,color:#EF5350,stroke:#EF5350
    style ACT fill:#1A2E44,color:#00B4D8,stroke:#00B4D8
```

### Wave Formula

| Symbol | Meaning |
|--------|---------|
| `m_i(t)` | Memory demand of VM i at time t |
| `A_i0` | Baseline (mean) usage — "flatline" component |
| `A_ik` | Amplitude of k-th harmonic — spike height |
| `f_k` | Frequency (e.g., 1/86400 for daily cycle) |
| `φ_ik` | Phase offset — when within period VM peaks |
| `M(t)` | Total node demand = Σ m_i(t) |
| `RAM_physical` | Physical RAM ceiling |
| `t*` | First future time M(t*) > RAM_physical |
| `τ_lead` | Lead time — how far before t* to act |

---

## 4. Continuous Monitoring & Overload Response

```mermaid
flowchart TD
    MON["Continuous Monitoring Loop\n(update waves, recompute M(t))"]
    CHECK{"M(t) exceeds\nRAM_physical\nin forecast window?"}
    CONT["Continue monitoring"]
    LIFE{"Spike VM's\nlifetime ends\nbefore t*?"}
    SKIP["No action needed\n(VM will be gone)"]
    SHARE["Trigger Memory Sharing\n(ballooning abstraction)\nPull memory from low-demand VMs\ngive to spike-bound VMs"]
    SUFF{"Sharing\nsufficient?"}
    TERM_CHECK["Evaluate SLA budgets\nB_i = D_max − D_i\nFilter: B_i > τ only"]
    TERM["Terminate VM with\nlargest B_i\n(most budget remaining)"]
    NOTIFY["Notify Cluster Manager\nRelaunch on another node\nwithin threshold τ"]

    MON --> CHECK
    CHECK -->|No| CONT --> MON
    CHECK -->|Yes at t*| LIFE
    LIFE -->|Yes| SKIP --> MON
    LIFE -->|No| SHARE
    SHARE --> SUFF
    SUFF -->|Yes| CONT
    SUFF -->|No| TERM_CHECK
    TERM_CHECK --> TERM
    TERM --> NOTIFY
    NOTIFY --> MON

    style CHECK fill:#1A2E44,color:#FFEE58,stroke:#FFEE58
    style SHARE fill:#003A52,color:#FFD166,stroke:#FFD166
    style TERM fill:#4A1B1B,color:#EF5350,stroke:#EF5350
    style NOTIFY fill:#1A2E44,color:#A8DADC,stroke:#A8DADC
```

---

## 5. Dual Prediction Model — Transition Timeline

```mermaid
gantt
    title Prediction Model Lifecycle per VM
    dateFormat  X
    axisFormat  Day %s

    section Generalized Model
    Active (new VM, no history)          :active, gen, 0, 7
    Blended with curated                 :active, blend, 7, 14

    section Curated Model
    Training begins                      :crit, train, 7, 10
    Blended (increasing weight)          :active, blend2, 10, 14
    Fully active                         :done, cur, 14, 30
```

```mermaid
flowchart LR
    A["VM Admitted\nDay 0"] -->|"No history"| B["Generalized Model\n(cross-VM trends,\nCoach-style patterns)"]
    B -->|"7+ days data"| C["Hybrid Period\n(weighted blend)"]
    C -->|"Sufficient data"| D["Curated Model\n(per-VM wave fit,\nlearned φ, A, f)"]
    D -->|"Ongoing"| E["Continuous\nRetraining"]

    style B fill:#0D2A1A,color:#4CAF50,stroke:#4CAF50
    style C fill:#1A2E44,color:#FFEE58,stroke:#FFEE58
    style D fill:#2A1A0D,color:#E0876A,stroke:#E0876A
    style E fill:#1A2E44,color:#00B4D8,stroke:#00B4D8
```

---

## 6. SLA Downtime Budget Framework

```mermaid
graph TD
    ALPHA["α = SLA uptime %\ne.g. 99.99%\n(Akamai standard for all compute VMs)"]
    DMAX["D_max = (1 − α) × T_month\n= (1 − 0.9999) × 43,200 min\n≈ 4.32 min/month per VM"]
    BUDGET["Remaining budget for VM i:\nB_i = D_max − D_i\n(D_i = cumulative downtime this month)"]
    ELIGIBLE{"B_i > τ\n(relaunch threshold)?"}
    CAN["VM eligible for termination\nPriority: highest B_i first"]
    CANNOT["VM protected —\ncannot be terminated\nthis month"]

    ALPHA --> DMAX --> BUDGET --> ELIGIBLE
    ELIGIBLE -->|Yes| CAN
    ELIGIBLE -->|No| CANNOT

    style ALPHA fill:#003A52,color:#FFD166,stroke:#FFD166
    style DMAX fill:#1A2E44,color:#FFEE58,stroke:#FFEE58
    style CAN fill:#1B4A22,color:#4CAF50,stroke:#4CAF50
    style CANNOT fill:#4A1B1B,color:#EF5350,stroke:#EF5350
```

---

## 7. Simulation Architecture

```mermaid
flowchart TD
    subgraph DATA ["Data Sources"]
        REAL["Azure VM Public Trace\n(Cortez et al. 2017)\nReal lifecycle + utilization data"]
        SYN["Synthetic Data\nStress-test edge cases:\nsimultaneous peaks, adversarial sequences"]
    end

    subgraph SIM ["Simulation Engine"]
        STUB["Cluster Manager Stub\nGenerates VM admission requests\nfrom empirical arrival distributions"]
        NODE_MODEL["Node Model\n────────────────\nDual prediction models\nWave superposition M(t)\nAdmission control logic\nMemory sharing trigger\nSLA budget-aware termination"]
        TRUTH["Ground Truth Evaluator\nCompares predicted vs actual demand\nMeasures SLA violations"]
    end

    subgraph METRICS ["Evaluation Metrics"]
        M1["DRAM Utilization\n(target: ~80% vs baseline ~45%)"]
        M2["SLA Violation Rate\n(D_i > D_max events)"]
        M3["Terminations/month"]
        M4["Prediction Accuracy\n(MAE / RMSE)"]
        M5["Admission Acceptance Rate"]
    end

    DATA --> STUB
    STUB --> NODE_MODEL
    NODE_MODEL --> TRUTH
    TRUTH --> METRICS

    style NODE_MODEL fill:#003A52,color:#FFD166,stroke:#FFD166
    style REAL fill:#1A2E44,color:#00B4D8,stroke:#00B4D8
    style SYN fill:#1A2E44,color:#A8DADC,stroke:#A8DADC
```

---

## 8. Component Summary

| Component | Role | Simplification Applied |
|-----------|------|----------------------|
| Cluster Manager | Sends VM placement requests to node | Black box — internal bin-packing not modeled |
| Generalized Prediction Model | Forecasts new VM wave using cross-VM trends | Based on VM size/type features (Coach-style) |
| Curated Prediction Model | Learns per-VM wave parameters over time | Transitions from generalized after 7+ days |
| Wave Superposition Engine | Computes M(t) = Σ m_i(t) | Treats each VM as a sinusoidal waveform |
| Admission Control | Checks max M_new(t) ≤ RAM_physical | Binary accept/reject per node |
| Memory Sharing | Redistributes memory before predicted spike | Ballooning abstracted — mechanics not re-derived |
| SLA Budget Tracker | Tracks D_i, enforces B_i > τ for termination | Single α for all VMs (Akamai model) |
| Termination Selector | Picks VM with largest B_i | Whole-VM only — no process-level introspection |
| Cluster Manager Coordinator | Notified to relaunch terminated VM | Fixed relaunch threshold τ assumed |
