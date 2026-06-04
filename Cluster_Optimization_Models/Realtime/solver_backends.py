"""
Realtime/solver_backends.py
────────────────────────────
Solver-agnostic backend abstraction for the real-time placement MILP.

The MILP formulation is identical across all backends:
  Variables : x[j,n] ∈ {0,1}   1 = job j placed on machine n
  Objective : max Σ_{j,n} ω_{t(j)} · P̂_j^mem · u_n^mem · σ_n^consolid · x[j,n]
  C1        : Σ_n x[j,n] ≤ 1             ∀ j   (one machine per job)
  C2        : Σ_j P̂_j^mem · x[j,n] ≤ R[n]  ∀ n   (effective capacity)
  C4        : x[j,n] = 0  if CPU(j) > CPU(n)       (CPU fitment, upper-bound=0)

Why GLOP is excluded
─────────────────────
  GLOP is an LP solver. It relaxes x[j,n] ∈ {0,1} to x[j,n] ∈ [0,1]
  and finds a fractional optimum. The old "> 0.5" rounding trick does not
  solve the integer program — it discards optimality guarantees and can
  violate capacity constraints under fractional solutions.
  All backends in this file produce certified integer 1/0 assignments.

Supported backends
──────────────────
  "CBC"    OR-Tools CBC   — open-source branch-and-bound MILP
  "SCIP"   OR-Tools SCIP  — open-source MILP (often faster than CBC on tight problems)
  "GUROBI" Gurobi         — commercial best-in-class (requires WLS credentials in PlanAhead/.env)
  "HIGHS"  HiGHS          — open-source, often fastest free MIP solver (pip install highspy)

Usage
─────
  from solver_backends import precompute, get_backend

  data    = precompute(jobs, nodes, W_t, K)
  backend = get_backend("SCIP")
  result  = backend.solve(data, time_limit_ms=10_000)
  # result: dict[job_id -> node_id | None]
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from simulation_data import (
    Job, NodeState,
    compute_violation_rate,
    compute_available_capacity,
    compute_remaining_avail,
    compute_remaining_eff,
    compute_utilization_weight,
    compute_node_weight,
    compute_omega,
    K_WINDOW, NUM_TENANTS,
)


# ═══════════════════════════════════════════════════════════════════════════════
# § SHARED MODEL DATA
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ModelData:
    """Precomputed, solver-agnostic inputs for the placement MILP."""
    jobs:   list[Job]
    nodes:  list[NodeState]
    R:      dict[int, float]   # effective capacity per node (RHS of C2)
    u_mem:  dict[int, float]   # utilization weight per node ∈ [1,2]
    w_node: dict[int, float]   # consolidation weight per node
    omega:  dict[int, float]   # per-tenant fairness weight


def precompute(
    jobs:  list[Job],
    nodes: list[NodeState],
    W_t:   dict[int, float],
    K:     int = K_WINDOW,
) -> ModelData:
    """Compute solver-agnostic MILP inputs from raw simulation state."""
    v_bar   = {n.node_id: compute_violation_rate(n.overflow_history, K) for n in nodes}
    m_cap   = {n.node_id: compute_available_capacity(n)                  for n in nodes}
    r_avail = {n.node_id: compute_remaining_avail(n, m_cap[n.node_id])  for n in nodes}
    R       = {n.node_id: compute_remaining_eff(r_avail[n.node_id], v_bar[n.node_id]) for n in nodes}
    u_mem   = {n.node_id: compute_utilization_weight(n)                  for n in nodes}
    w_node  = {n.node_id: compute_node_weight(i, len(nodes))             for i, n in enumerate(nodes)}

    all_ten = sorted({j.tenant_id for j in jobs}) if jobs else list(range(NUM_TENANTS))
    omega_r = compute_omega({t: W_t.get(t, 0.0) for t in all_ten})
    omega   = {t: omega_r.get(t, 1.0) for t in all_ten}

    return ModelData(jobs=jobs, nodes=nodes, R=R, u_mem=u_mem, w_node=w_node, omega=omega)


# ═══════════════════════════════════════════════════════════════════════════════
# § BACKEND PROTOCOL
# ═══════════════════════════════════════════════════════════════════════════════

class SolverBackend(Protocol):
    """Interface every backend must implement."""
    def solve(self, data: ModelData, time_limit_ms: int) -> dict[str, int | None]:
        """Return job_id → node_id (int) if placed, None if unscheduled."""
        ...


# ═══════════════════════════════════════════════════════════════════════════════
# § OR-TOOLS BACKEND  (CBC / SCIP)
# ═══════════════════════════════════════════════════════════════════════════════

class OrtoolsBackend:
    """
    OR-Tools pywraplp backend for exact integer MILP.

    Supports "CBC" and "SCIP".  Both declare x[j,n] as IntVar ∈ {0,1}
    and solve to certified integer optimality (or feasibility within the
    time limit).
    """

    _INTEGER_IDS = {"CBC", "SCIP"}

    def __init__(self, solver_id: str = "CBC") -> None:
        sid = solver_id.upper()
        if sid not in self._INTEGER_IDS:
            raise ValueError(
                f"OrtoolsBackend: '{solver_id}' is not an integer solver. "
                f"GLOP is an LP relaxation and does not guarantee 1/0 values. "
                f"Choose: {sorted(self._INTEGER_IDS)}"
            )
        self.solver_id = sid

    def solve(self, data: ModelData, time_limit_ms: int) -> dict[str, int | None]:
        from ortools.linear_solver import pywraplp

        jobs, nodes = data.jobs, data.nodes
        if not jobs or not nodes:
            return {j.job_id: None for j in jobs}

        solver = pywraplp.Solver.CreateSolver(self.solver_id)
        if solver is None:
            raise RuntimeError(
                f"OR-Tools: solver '{self.solver_id}' not available. "
                "Install ortools: pip install ortools"
            )
        solver.set_time_limit(time_limit_ms)

        # Decision variables — integer {0,1}
        x: dict[tuple, pywraplp.Variable] = {}
        for j in jobs:
            for n in nodes:
                ub = 1 if j.pred_cpu_p95 <= n.cpu_cores else 0   # C4: CPU fitment
                x[j.job_id, n.node_id] = solver.IntVar(0, ub, f"x_{j.job_id}_{n.node_id}")

        # Objective
        obj = solver.Objective()
        for j in jobs:
            w = data.omega.get(j.tenant_id, 1.0)
            for n in nodes:
                obj.SetCoefficient(
                    x[j.job_id, n.node_id],
                    w * j.pred_mem_mb * data.u_mem[n.node_id] * data.w_node[n.node_id],
                )
        obj.SetMaximization()

        # C1: at most one machine per job
        for j in jobs:
            ct = solver.Constraint(0.0, 1.0, f"c1_{j.job_id}")
            for n in nodes:
                ct.SetCoefficient(x[j.job_id, n.node_id], 1.0)

        # C2: machine effective memory capacity
        for n in nodes:
            ct = solver.Constraint(0.0, data.R[n.node_id], f"c2_{n.node_id}")
            for j in jobs:
                ct.SetCoefficient(x[j.job_id, n.node_id], j.pred_mem_mb)

        status = solver.Solve()
        if status not in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
            return {j.job_id: None for j in jobs}

        return {
            j.job_id: next(
                (n.node_id for n in nodes if x[j.job_id, n.node_id].solution_value() > 0.5),
                None,
            )
            for j in jobs
        }


# ═══════════════════════════════════════════════════════════════════════════════
# § GUROBI BACKEND
# ═══════════════════════════════════════════════════════════════════════════════

def _load_gurobi_credentials() -> None:
    """Read WLS credentials from PlanAhead/.env into os.environ (idempotent)."""
    env_file = Path(__file__).resolve().parent.parent / "PlanAhead" / ".env"
    if not env_file.exists():
        return
    with open(env_file, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def _make_gurobi_env():
    """Return a Gurobi WLS Env using credentials from PlanAhead/.env."""
    import gurobipy as gp
    _load_gurobi_credentials()
    return gp.Env(params={
        "WLSACCESSID": os.environ.get("WLSACCESSID", ""),
        "WLSSECRET":   os.environ.get("WLSSECRET",   ""),
        "LICENSEID":   int(os.environ.get("LICENSEID", "0")),
    })


class GurobiBackend:
    """
    Gurobi exact integer MILP backend.

    Creates one Gurobi Env per solve() call — fully thread-safe for
    concurrent analysis runs.  Variables are declared as GRB.BINARY,
    guaranteeing integer 1/0 assignments.
    """

    def solve(self, data: ModelData, time_limit_ms: int) -> dict[str, int | None]:
        try:
            import gurobipy as gp
            from gurobipy import GRB
        except ImportError:
            raise RuntimeError(
                "gurobipy is not installed. "
                "Install via: pip install gurobipy"
            )

        jobs, nodes = data.jobs, data.nodes
        if not jobs or not nodes:
            return {j.job_id: None for j in jobs}

        env = _make_gurobi_env()
        try:
            model = gp.Model(env=env)
            model.Params.OutputFlag   = 0
            model.Params.LogToConsole = 0
            model.Params.TimeLimit    = time_limit_ms / 1000.0

            # Decision variables — binary
            x: dict[tuple, "gp.Var"] = {}
            for j in jobs:
                for n in nodes:
                    ub = 1.0 if j.pred_cpu_p95 <= n.cpu_cores else 0.0
                    x[j.job_id, n.node_id] = model.addVar(
                        vtype=GRB.BINARY, ub=ub, name=f"x_{j.job_id}_{n.node_id}",
                    )
            model.update()

            # Objective
            obj_expr = gp.LinExpr()
            for j in jobs:
                w = data.omega.get(j.tenant_id, 1.0)
                for n in nodes:
                    obj_expr += (
                        w * j.pred_mem_mb * data.u_mem[n.node_id] * data.w_node[n.node_id]
                        * x[j.job_id, n.node_id]
                    )
            model.setObjective(obj_expr, GRB.MAXIMIZE)

            # C1: one machine per job
            for j in jobs:
                model.addConstr(
                    gp.quicksum(x[j.job_id, n.node_id] for n in nodes) <= 1,
                    name=f"c1_{j.job_id}",
                )

            # C2: effective memory capacity
            for n in nodes:
                model.addConstr(
                    gp.quicksum(j.pred_mem_mb * x[j.job_id, n.node_id] for j in jobs)
                    <= data.R[n.node_id],
                    name=f"c2_{n.node_id}",
                )

            model.optimize()

            sc = model.Status
            if sc not in (GRB.OPTIMAL, GRB.TIME_LIMIT, GRB.SUBOPTIMAL):
                model.dispose()
                env.dispose()
                return {j.job_id: None for j in jobs}

            result = {}
            for j in jobs:
                result[j.job_id] = next(
                    (n.node_id for n in nodes if x[j.job_id, n.node_id].X > 0.5),
                    None,
                )
            model.dispose()
            env.dispose()
            return result

        except Exception:
            try:
                env.dispose()
            except Exception:
                pass
            return {j.job_id: None for j in jobs}


# ═══════════════════════════════════════════════════════════════════════════════
# § HIGHS BACKEND
# ═══════════════════════════════════════════════════════════════════════════════

class HiGHSBackend:
    """
    HiGHS exact integer MILP backend via highspy.

    Install: pip install highspy

    Declares x[j,n] as binary (integer with bounds [0,1]) — guaranteed 1/0
    assignments.  HiGHS is often the fastest free MIP solver for moderate
    problem sizes.
    """

    def solve(self, data: ModelData, time_limit_ms: int) -> dict[str, int | None]:
        try:
            import highspy
        except ImportError:
            raise RuntimeError(
                "highspy is not installed. Install via: pip install highspy"
            )

        jobs, nodes = data.jobs, data.nodes
        if not jobs or not nodes:
            return {j.job_id: None for j in jobs}

        # Resolve version-stable enum constants
        _MS   = highspy.HighsModelStatus
        _VT   = highspy.HighsVarType
        _kInt = _VT.kInteger

        h = highspy.Highs()
        h.setOptionValue("output_flag", False)
        h.setOptionValue("time_limit", time_limit_ms / 1000.0)
        h.changeObjectiveSense(highspy.ObjSense.kMaximize)

        # Decision variables — binary (integer, bounds [0, ub])
        idx: dict[tuple, int] = {}
        col = 0
        for j in jobs:
            w = data.omega.get(j.tenant_id, 1.0)
            for n in nodes:
                ub = 1.0 if j.pred_cpu_p95 <= n.cpu_cores else 0.0   # C4: CPU fitment
                cost = w * j.pred_mem_mb * data.u_mem[n.node_id] * data.w_node[n.node_id]
                h.addVar(0.0, ub)
                h.changeColCost(col, cost)
                h.changeColIntegrality(col, _kInt)
                idx[j.job_id, n.node_id] = col
                col += 1

        # C1: Σ_n x[j,n] ≤ 1  ∀ j
        for j in jobs:
            cols = [idx[j.job_id, n.node_id] for n in nodes]
            h.addRow(0.0, 1.0, len(cols), cols, [1.0] * len(cols))

        # C2: Σ_j P̂_j^mem · x[j,n] ≤ R[n]  ∀ n
        for n in nodes:
            cols = [idx[j.job_id, n.node_id] for j in jobs]
            vals = [j.pred_mem_mb for j in jobs]
            h.addRow(0.0, data.R[n.node_id], len(cols), cols, vals)

        h.run()

        model_status = h.getModelStatus()
        ok = {_MS.kOptimal, _MS.kObjectiveBound, _MS.kSolutionLimit, _MS.kTimeLimit}
        if model_status not in ok:
            return {j.job_id: None for j in jobs}

        sol = h.getSolution()
        if not sol.col_value:
            return {j.job_id: None for j in jobs}

        col_vals = sol.col_value
        return {
            j.job_id: next(
                (n.node_id for n in nodes if col_vals[idx[j.job_id, n.node_id]] > 0.5),
                None,
            )
            for j in jobs
        }


# ═══════════════════════════════════════════════════════════════════════════════
# § REGISTRY
# ═══════════════════════════════════════════════════════════════════════════════

AVAILABLE_BACKENDS: list[str] = ["CBC", "SCIP", "GUROBI", "HIGHS"]


def get_backend(name: str) -> SolverBackend:
    """
    Return a backend instance by name (case-insensitive).

    Valid: "CBC", "SCIP", "GUROBI", "HIGHS"

    Raises ValueError for unknown names, and explicitly rejects "GLOP"
    with an explanation (LP relaxation — no integer guarantee).
    """
    u = name.upper()
    if u == "GLOP":
        raise ValueError(
            "GLOP is an LP relaxation and does not produce integer 1/0 "
            "placement decisions. The professor confirmed this is not valid. "
            "Use CBC, SCIP, GUROBI, or HIGHS instead."
        )
    if u in ("CBC", "SCIP"):
        return OrtoolsBackend(u)
    if u == "GUROBI":
        return GurobiBackend()
    if u == "HIGHS":
        return HiGHSBackend()
    raise ValueError(
        f"Unknown solver backend: '{name}'. "
        f"Available backends: {AVAILABLE_BACKENDS}"
    )
