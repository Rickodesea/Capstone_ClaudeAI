"""
Pipeline/pa_instance_export.py
────────────────────────────────
Export Plan-Ahead MISOCP instances for the professor to run independently.

Exports two instances matching the computational time analysis parameters:
  T=256, N=256 — largest instance that Gurobi solved to OPT (755s, 477k vars)
  T=256, N=512 — first instance that crashed with OOM (1.06M vars)

Files written to Pipeline/timing_data/instances/:
  pa_T256_N256.lp          — Gurobi LP format (load with model.read())
  pa_T256_N512.lp          — same (OOM instance — may not solve on all machines)
  pa_T256_N256_params.json — raw parameter dict (sets, demands, capacities)
  pa_T256_N512_params.json — same
  README.txt               — run instructions for professor

Run:
  cd Pipeline/
  python pa_instance_export.py
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "Realtime"))
sys.path.insert(0, str(_ROOT / "PlanAhead"))

from plan_ahead_data      import build_synthetic_data, make_gurobi_env
from plan_ahead_optimizer import build_model

# ── Match exact parameters used in computational_time_analysis.py ──────────────
SEED          = 42
PA_FIXED_PERIODS  = 4
PA_USAGE_MIN      = 0.5
PA_USAGE_MAX      = 2.5
PA_ALWAYS_FRAC    = 0.5
PA_EXCL_FRAC      = 0.20
PA_MIP_GAP        = 0.01

INSTANCES = [
    {"n_tenants": 256, "n_nodes": 256, "label": "T256_N256",
     "note": "Largest solved to OPT (755 s, 477k vars)"},
    {"n_tenants": 256, "n_nodes": 512, "label": "T256_N512",
     "note": "First OOM crash (1.06M vars — may not solve on all machines)"},
]

OUT_DIR = Path(__file__).parent / "timing_data" / "instances"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _scale_params(n_tenants: int, n_nodes: int) -> dict:
    """Reproduce the same param scaling used in time_pa() in computational_time_analysis.py."""
    min_cap = (n_tenants * PA_USAGE_MAX) / max(1, n_nodes) * 1.3
    cap     = max(10.0, min_cap)
    cap     = min(cap, 500.0)
    usage_max = min(PA_USAGE_MAX, cap * 0.70)
    usage_min = min(PA_USAGE_MIN, usage_max * 0.50)
    n_always = max(1, int(n_nodes * PA_ALWAYS_FRAC))
    n_excl   = min(max(1, int(n_tenants * PA_EXCL_FRAC)), n_tenants - 1)
    return dict(
        seed=SEED, n_tenants=n_tenants, n_nodes=n_nodes, n_intervals=PA_FIXED_PERIODS,
        node_capacity=cap, n_always_available=n_always, n_exclusive=n_excl,
        tenant_usage_min=usage_min, tenant_usage_max=usage_max,
        sigma_frac=0.20, epsilon=0.10, min_machines_per_tenant=1,
    )


def _json_safe(obj):
    """Convert dict with tuple keys / numpy values to JSON-serialisable form."""
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(x) for x in obj]
    if hasattr(obj, "item"):
        return obj.item()
    return obj


def export_instance(cfg: dict) -> None:
    n_t, n_n = cfg["n_tenants"], cfg["n_nodes"]
    label    = cfg["label"]
    print(f"\n  Exporting {label}  ({n_t} tenants, {n_n} nodes)  …")

    params = _scale_params(n_t, n_n)
    P = build_synthetic_data(**params)

    # Save parameter dict as JSON (for professor to inspect/load)
    json_path = OUT_DIR / f"pa_{label}_params.json"
    summary = {
        "n_tenants":    n_t,
        "n_nodes":      n_n,
        "n_periods":    PA_FIXED_PERIODS,
        "node_capacity": params["node_capacity"],
        "n_always":     params["n_always_available"],
        "n_exclusive":  params["n_exclusive"],
        "usage_min":    params["tenant_usage_min"],
        "usage_max":    params["tenant_usage_max"],
        "note":         cfg["note"],
        "sets": {
            "T":   P["T"],
            "T_e": P["T_e"],
            "T_s": P["T_s"],
            "M":   P["M"],
            "M_a": P["M_a"],
            "M_b": P["M_b"],
            "H":   P["H"],
        },
        "u_sample_first10": {
            str(k): v for k, v in list(P["u"].items())[:10]
        },
    }
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(_json_safe(summary), fh, indent=2)
    print(f"    Params JSON → {json_path}")

    # Build Gurobi model and write .lp file
    lp_path = OUT_DIR / f"pa_{label}.lp"
    print(f"    Building Gurobi model …")
    env = make_gurobi_env()
    try:
        t0 = time.perf_counter()
        model, _ = build_model(P, env, use_socp=True)
        build_s = time.perf_counter() - t0
        n_vars    = model.NumVars
        n_constrs = model.NumConstrs + model.NumQConstrs
        print(f"    Built in {build_s:.1f}s  |  vars={n_vars:,}  constrs={n_constrs:,}")
        model.write(str(lp_path))
        print(f"    LP file → {lp_path}")
        model.dispose()
    except Exception as exc:
        print(f"    [WARN] Could not build model: {exc}")
        print(f"    (This is expected for OOM instances on machines with < 16 GB RAM)")
    finally:
        try:
            env.dispose()
        except Exception:
            pass


def write_readme() -> None:
    readme = OUT_DIR / "README.txt"
    content = """\
Plan-Ahead MISOCP — Professor's Test Instances
================================================

Files
─────
  pa_T256_N256.lp           Gurobi model (LP format) — largest solved instance
  pa_T256_N256_params.json  Parameter summary for T=256, N=256

  pa_T256_N512.lp           Gurobi model (LP format) — first OOM crash instance
  pa_T256_N512_params.json  Parameter summary for T=256, N=512

Model overview
──────────────
  T=256 tenants, N=256 (or 512) machines, P=4 planning periods.
  MISOCP with Cantelli probabilistic capacity constraint.
  Variables include e[i,n,h], y[i,n,h], f[i,n,h], z_on[n], sigma, mix[n,h].
  Variable count: ~477k (T=N=256) and ~1.06M (T=256, N=512).

How to run in Python
────────────────────
  import gurobipy as gp

  # Load WLS credentials (same .env as PlanAhead/)
  env = gp.Env(params={
      "WLSACCESSID": "<your-id>",
      "WLSSECRET":   "<your-secret>",
      "LICENSEID":   <your-id>,
  })

  model = gp.read("pa_T256_N256.lp", env=env)
  model.Params.TimeLimit = 900      # 15-minute cap
  model.Params.MIPGap    = 0.01     # 1% gap
  model.Params.Threads   = 8        # adjust to your machine
  model.optimize()

  print("Status :", model.Status)
  print("ObjVal :", model.ObjVal if model.SolCount > 0 else "no solution")
  print("MIPGap :", model.MIPGap if model.SolCount > 0 else "—")

Expected runtimes (from our runs)
──────────────────────────────────
  T=256, N=256 → OPT in ~756 s (12.6 min) on an 8-core machine
  T=256, N=512 → OOM crash (1.06M vars exceeds Gurobi's memory budget)
                 May solve on a high-RAM machine (≥ 64 GB recommended)

Benchmark comparison
────────────────────
  This confirms the O(T × N × P) variable growth: doubling N doubles variables
  and causes OOM at T=256, N=512.  Decomposition (Benders, column generation)
  is needed to scale beyond this point.
"""
    with open(readme, "w", encoding="utf-8") as fh:
        fh.write(content)
    print(f"\n  README → {readme}")


def main() -> None:
    print("═" * 70)
    print("  Plan-Ahead Instance Export")
    print("═" * 70)
    for cfg in INSTANCES:
        export_instance(cfg)
    write_readme()
    print(f"\n  All files written to: {OUT_DIR}")
    print()


if __name__ == "__main__":
    main()
