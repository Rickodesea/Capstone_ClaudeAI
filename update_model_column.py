"""
Updates the per-paper mini-tables in Research_Paper_Reviews_Jobs.md:
  - Renames column header "Has Math Optimization Model" → "Optimization / Model Type"
  - Replaces each paper's model column value with a specific description
"""
import re

path = r"C:\Users\alric\Documents\Spring2026\Capstone_ClaudeAI\Research_Paper_Reviews_Jobs.md"
with open(path, encoding='utf-8') as f:
    text = f.read()

# ── 1. Rename all column headers ──────────────────────────────────────────────
text = text.replace(
    "| Has Math Optimization Model | Has Predictive Model | Training / Implementation Details |",
    "| Optimization / Model Type | Has Predictive Model | Training / Implementation Details |"
)
text = text.replace(
    "|---|---|---|",  # only hits the table separator lines — they stay the same
    "|---|---|---|"
)

# ── 2. Per-paper model column value replacements ──────────────────────────────
# Format: (old_model_cell_value, new_value)
# We target the FIRST column cell of each paper's data row.

replacements = [
    # Paper 1 — Chaudhari
    ("| No | No (cites others) | No — conceptual framework only |",
     "| None — conceptual framework | No (cites others) | No — conceptual framework only |"),
    # Paper 2 — Jiang Zhi
    ("| Basic formulation (overcommit ratio) | No | Yes — Clovers simulator (Python), real trace data from Google Borg/Azure/Alibaba |",
     "| None — rule-based simulator (overcommit ratio only) | No | Yes — Clovers simulator (Python), real trace data from Google Borg/Azure/Alibaba |"),
    # Paper 3 — Coach
    ("| No formal optimization | Yes — temporal pattern prediction, long-term forecasting | Partial — CoachVM mechanics described, evaluated on Azure traces |",
     "| Prediction: temporal pattern forecasting (co-location heuristic) | Yes — temporal pattern prediction, long-term forecasting | Partial — CoachVM mechanics described, evaluated on Azure traces |"),
    # Paper 4 — DRF
    ("| Yes — formal LP formulation and proofs | No | Yes — Algorithm 1 (pseudocode), implemented in Mesos, Facebook trace evaluation |",
     "| Fairness algorithm: LP-proven dominant resource equalization | No | Yes — Algorithm 1 (pseudocode), implemented in Mesos, Facebook trace evaluation |"),
    # Paper 5 — Resource Central
    ("| No | Yes — Random Forest + XGBoost, 79–90% accuracy | Yes — full training pipeline, Azure dataset publicly available |",
     "| None (scheduling heuristic) | Yes — Prediction model: Random Forest + XGBoost (P95 utilization), 79–90% accuracy | Yes — full training pipeline, Azure dataset publicly available |"),
    # Paper 6 — Blagodurov
    ("| Yes — Equations 1–3 for dynamic weight calculation | No | Yes — working prototype on KVM/Linux, real workload experiments (RUBiS, Wikipedia benchmark) |",
     "| Scheduler optimizer: cgroups weight equations (dynamic priority control) | No | Yes — working prototype on KVM/Linux, real workload experiments (RUBiS, Wikipedia benchmark) |"),
    # Paper 7 — Wang & Yang
    ("| Yes — Equations 1–2 (objective function, reward) | Yes — LSTM (RMSE 0.086, MAPE 7.2%) | Yes — full architecture, Kubernetes setup, training dataset described |",
     "| Scheduler optimizer: RL reward function (DQN) + Prediction model (LSTM) | Yes — Prediction model: LSTM (RMSE 0.086, MAPE 7.2%) | Yes — full architecture, Kubernetes setup, training dataset described |"),
    # Paper 8 — Doukha
    ("| No | Yes — RF (MAPE 2.65%), LSTM (MAPE 17.43%) | Yes — PROXMOX environment, Prometheus/Grafana, MSE/MAPE evaluation |",
     "| None (comparison study only) | Yes — Prediction comparison: RF (MAPE 2.65%) vs LSTM (MAPE 17.43%) | Yes — PROXMOX environment, Prometheus/Grafana, MSE/MAPE evaluation |"),
    # Paper 9 — Atropos
    ("| No | No (monitoring-based detection) | Yes — implementation in 6 real systems, 16 benchmarked overload scenarios |",
     "| None — monitoring-based detection (no optimizer) | No — reactive monitoring only | Yes — implementation in 6 real systems, 16 benchmarked overload scenarios |"),
    # Paper 10 — Priya
    ("| Partial — QoS scoring function | No — reactive feedback only | Yes — Kubernetes scheduler plugin implementation, Prometheus integration, microservices benchmark |",
     "| Scheduler optimizer: QoS scoring function (partial) | No — reactive feedback only | Yes — Kubernetes scheduler plugin implementation, Prometheus integration, microservices benchmark |"),
    # Paper 11 — Kofi
    ("| No | Yes — LSTM (R²=0.99, RMSLE 0.14–0.16) | Yes — Google Cluster Trace dataset, preprocessing pipeline fully described, evaluation methodology |",
     "| None (no scheduling optimizer) | Yes — Prediction model: LSTM (R²=0.99, RMSLE 0.14–0.16) | Yes — Google Cluster Trace dataset, preprocessing pipeline fully described, evaluation methodology |"),
    # Paper 12 — Perera
    ("| No | No — review paper | No — synthesizes existing work |",
     "| None — review / landscape paper | No — synthesizes existing work | No — synthesizes existing work |"),
    # Paper 13 — Pinnapareddy
    ("| No | No | No — practitioner analysis, no experiments |",
     "| None — practitioner analysis | No — no predictive model | No — practitioner analysis, no experiments |"),
    # Paper 14 — Patchamatla
    ("| No | No | Yes — comparative experiments across bare-metal, VM-hosted, and pure VM configurations |",
     "| None — experimental comparison | No — no predictive model | Yes — comparative experiments across bare-metal, VM-hosted, and pure VM configurations |"),
    # Paper 15 — Liu & Guitart
    ("| No | No — reactive cgroup adaptation | Yes — full Kubernetes implementation, cgroup benchmarks, ML workload experiments (MobileNet, ResNet50, VGG16) |",
     "| Scheduler optimizer: in-node cgroup assignment (group-aware, reactive) | No — reactive cgroup adaptation | Yes — full Kubernetes implementation, cgroup benchmarks, ML workload experiments (MobileNet, ResNet50, VGG16) |"),
    # Paper 16 — Kovalenko
    ("| Yes — full discrete optimization with explicit constraints and multi-objective structure | No | No — theoretical model only |",
     "| Scheduler optimizer: discrete combinatorial LP (pod-to-node assignment + server on/off) | No — no predictive model | No — theoretical model only |"),
    # Paper 17 — Alatawi
    ("| Yes — MDP formulation with formal reward function | No — RL learns policy dynamically | Yes — simulation experiments, Gini/latency/throughput metrics |",
     "| RL policy model: MDP-based resource allocation (reward-driven) | No — RL learns policy dynamically (not a separate prediction model) | Yes — simulation experiments, Gini/latency/throughput metrics |"),
    # Paper 18 — Zhao
    ("| Yes — formal admission control + profit optimization LP | No | Yes — simulation with real cloud workloads, comparison vs. state-of-the-art baselines |",
     "| Scheduler optimizer: admission control LP + profit maximization LP | No — no predictive model | Yes — simulation with real cloud workloads, comparison vs. state-of-the-art baselines |"),
]

for old, new in replacements:
    text = text.replace(old, new, 1)

with open(path, 'w', encoding='utf-8') as f:
    f.write(text)

print("Research_Paper_Reviews_Jobs.md updated.")
