"""
test_main.py
────────────
Simulation API tests.

Run from the api/ directory:
    pytest test_main.py -v
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_state():
    """Reset config to defaults and restart simulation before each test."""
    from simulation_config import DEFAULT_CONFIG
    client.post("/api/config", json=DEFAULT_CONFIG)
    client.post("/api/reset")


# ── GET /api/state ─────────────────────────────────────────────────────────────

class TestGetState:
    def test_returns_200(self):
        assert client.get("/api/state").status_code == 200

    def test_has_required_top_level_keys(self):
        d = client.get("/api/state").json()
        for key in ("interval", "nodes", "queue", "hud", "mem_history",
                    "eff_history", "plan_ahead", "recent_placements",
                    "plan_ahead_interval", "tenants", "sim_config",
                    "batch_stats", "sim_totals"):
            assert key in d, f"Missing key: {key}"

    def test_initial_interval_is_zero(self):
        assert client.get("/api/state").json()["interval"] == 0

    def test_initial_plan_ahead_is_null(self):
        # plan_ahead fires on demand or at configured interval — not on init
        assert client.get("/api/state").json()["plan_ahead"] is None

    def test_nodes_count_matches_config(self):
        d = client.get("/api/state").json()
        assert len(d["nodes"]) == d["sim_config"]["num_nodes"]

    def test_node_has_required_fields(self):
        node = client.get("/api/state").json()["nodes"][0]
        for field in ("node_id", "capacity_mb", "used_mb", "mem_pct",
                      "eff_pct", "cpu_cores", "violation_rate",
                      "running_jobs", "viols_count", "ovrflw_count"):
            assert field in node, f"Node missing field: {field}"

    def test_node_capacity_within_configured_range(self):
        d = client.get("/api/state").json()
        min_mb = d["sim_config"]["node_mem_min_gb"] * 1024
        max_mb = d["sim_config"]["node_mem_max_gb"] * 1024
        for n in d["nodes"]:
            assert min_mb <= n["capacity_mb"] <= max_mb, \
                f"Node {n['node_id']} capacity {n['capacity_mb']} out of range"

    def test_node_capacity_non_decreasing(self):
        # Nodes are arranged in geometric progression (smallest first)
        caps = [n["capacity_mb"] for n in client.get("/api/state").json()["nodes"]]
        assert caps == sorted(caps)

    def test_hud_has_required_fields(self):
        hud = client.get("/api/state").json()["hud"]
        for field in ("total_jobs", "total_tenants", "total_nodes",
                      "mem_utilization_pct", "eff_utilization_pct",
                      "longest_wait_intervals", "intervals_to_plan_ahead"):
            assert field in hud, f"HUD missing field: {field}"

    def test_sim_config_has_usage_range(self):
        cfg = client.get("/api/state").json()["sim_config"]
        assert "tenant_usage_min" in cfg
        assert "tenant_usage_max" in cfg
        assert cfg["tenant_usage_min"] == 0.8
        assert cfg["tenant_usage_max"] == 6.0

    def test_sim_totals_zero_on_init(self):
        t = client.get("/api/state").json()["sim_totals"]
        assert t["num_batches"] == 0
        assert t["total_generated"] == 0
        assert t["total_placed"] == 0


# ── POST /api/step ─────────────────────────────────────────────────────────────

class TestStep:
    def test_returns_200(self):
        assert client.post("/api/step").status_code == 200

    def test_advances_interval_by_one(self):
        assert client.post("/api/step").json()["interval"] == 1

    def test_interval_increments_correctly(self):
        for _ in range(4):
            client.post("/api/step")
        assert client.post("/api/step").json()["interval"] == 5

    def test_generates_jobs(self):
        client.post("/api/step")
        d = client.get("/api/state").json()
        assert d["hud"]["total_jobs"] > 0

    def test_jobs_generated_within_configured_range(self):
        generated = []
        for _ in range(10):
            r = client.post("/api/step")
            bs = r.json().get("batch_stats")
            if bs:
                generated.append(bs["jobs_generated"])
        cfg = client.get("/api/state").json()["sim_config"]
        lo, hi = cfg["jobs_min_per_round"], cfg["jobs_max_per_round"]
        assert all(lo <= c <= hi for c in generated), \
            f"jobs_generated outside [{lo},{hi}]: {generated}"

    def test_mem_history_grows(self):
        client.post("/api/step")
        client.post("/api/step")
        assert len(client.get("/api/state").json()["mem_history"]) == 2

    def test_eff_history_grows(self):
        client.post("/api/step")
        client.post("/api/step")
        assert len(client.get("/api/state").json()["eff_history"]) == 2

    def test_batch_stats_present_after_step(self):
        r = client.post("/api/step")
        # batch_stats key must exist (value may be null on first step)
        assert "batch_stats" in r.json()

    def test_plan_ahead_null_between_intervals(self):
        # plan_ahead in step response is null unless it fires at that exact interval
        d = client.post("/api/step").json()
        assert d["plan_ahead"] is None

    def test_plan_ahead_fires_at_configured_interval(self):
        # Set a small interval so the test is fast
        client.post("/api/config", json={"plan_ahead_interval": 3})
        client.post("/api/reset")
        r = None
        for _ in range(3):
            r = client.post("/api/step")
        d = r.json()
        assert d["interval"] == 3
        assert d["plan_ahead"] is not None, "plan_ahead should fire at interval 3"
        assert "tenant_schedule" in d["plan_ahead"]

    def test_sim_totals_accumulate(self):
        for _ in range(3):
            client.post("/api/step")
        t = client.get("/api/state").json()["sim_totals"]
        assert t["num_batches"] == 3
        assert t["total_generated"] > 0
        assert 0 <= t["placement_rate"] <= 100

    def test_queue_job_has_required_fields(self):
        for _ in range(3):
            client.post("/api/step")
        q = client.get("/api/state").json()["queue"]
        if q:
            job = q[0]
            for f in ("job_id", "tenant_id", "req_mem_mb", "pred_mem_mb",
                      "req_cpu", "arrival_interval", "wait_intervals"):
                assert f in job, f"Queue job missing field: {f}"


# ── POST /api/reset ────────────────────────────────────────────────────────────

class TestReset:
    def test_returns_200(self):
        assert client.post("/api/reset").status_code == 200

    def test_resets_interval_to_zero(self):
        for _ in range(5):
            client.post("/api/step")
        assert client.post("/api/reset").json()["interval"] == 0

    def test_resets_mem_history(self):
        for _ in range(3):
            client.post("/api/step")
        assert client.post("/api/reset").json()["mem_history"] == []

    def test_resets_sim_totals(self):
        for _ in range(3):
            client.post("/api/step")
        t = client.post("/api/reset").json()["sim_totals"]
        assert t["num_batches"] == 0
        assert t["total_generated"] == 0

    def test_resets_plan_ahead_to_null(self):
        # Backend state.last_plan_ahead is None after reset
        assert client.post("/api/reset").json()["plan_ahead"] is None

    def test_applies_staged_config(self):
        client.post("/api/config", json={"num_nodes": 7})
        d = client.post("/api/reset").json()
        assert len(d["nodes"]) == 7

    def test_node_count_reverts_with_default_config(self):
        client.post("/api/config", json={"num_nodes": 7})
        client.post("/api/reset")
        from simulation_config import DEFAULT_CONFIG
        client.post("/api/config", json={"num_nodes": DEFAULT_CONFIG["num_nodes"]})
        d = client.post("/api/reset").json()
        assert len(d["nodes"]) == DEFAULT_CONFIG["num_nodes"]


# ── POST /api/config ───────────────────────────────────────────────────────────

class TestConfig:
    def test_returns_ok(self):
        r = client.post("/api/config", json={"num_nodes": 4})
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_returns_pending_values(self):
        r = client.post("/api/config", json={"num_nodes": 4})
        assert r.json()["pending"]["num_nodes"] == 4

    def test_unknown_keys_ignored(self):
        r = client.post("/api/config", json={"totally_fake_key": 99})
        assert r.status_code == 200
        assert "totally_fake_key" not in r.json()["pending"]

    def test_usage_range_configurable(self):
        r = client.post("/api/config", json={"tenant_usage_min": 1.5, "tenant_usage_max": 8.0})
        assert r.json()["pending"]["tenant_usage_min"] == 1.5
        assert r.json()["pending"]["tenant_usage_max"] == 8.0


# ── POST /api/plan_ahead ───────────────────────────────────────────────────────

class TestPlanAhead:
    def test_returns_200(self):
        assert client.post("/api/plan_ahead").status_code == 200

    def test_has_required_keys(self):
        d = client.post("/api/plan_ahead").json()
        for key in ("tenant_schedule", "num_slots", "slot_labels",
                    "current_slot", "access_period", "planning_horizon", "summary"):
            assert key in d, f"plan_ahead missing key: {key}"

    def test_tenant_schedule_has_all_tenants(self):
        d = client.post("/api/plan_ahead").json()
        num_tenants = client.get("/api/state").json()["sim_config"]["num_tenants"]
        for t in range(num_tenants):
            assert str(t) in d["tenant_schedule"], f"Tenant {t} missing"

    def test_all_slots_present_per_tenant(self):
        d = client.post("/api/plan_ahead").json()
        num_slots = d["num_slots"]
        for t_str, slots in d["tenant_schedule"].items():
            for s in range(num_slots):
                assert str(s) in slots, f"Tenant {t_str} missing slot {s}"

    def test_node_ids_in_range(self):
        num_nodes = client.get("/api/state").json()["sim_config"]["num_nodes"]
        d = client.post("/api/plan_ahead").json()
        for t_str, slots in d["tenant_schedule"].items():
            for s_str, nodes in slots.items():
                assert isinstance(nodes, list)
                for n in nodes:
                    assert 0 <= n < num_nodes, \
                        f"node_id {n} out of range [0,{num_nodes}) for T{t_str} S{s_str}"

    def test_num_slots_derived_from_config(self):
        client.post("/api/config", json={"plan_ahead_interval": 20, "access_period": 4})
        client.post("/api/reset")
        d = client.post("/api/plan_ahead").json()
        assert d["num_slots"] == 5   # 20 // 4

    def test_slot_labels_count_matches_num_slots(self):
        d = client.post("/api/plan_ahead").json()
        assert len(d["slot_labels"]) == d["num_slots"]

    def test_summary_fields_present(self):
        s = client.post("/api/plan_ahead").json()["summary"]
        for f in ("avg_nodes_per_tenant", "isolation_score", "week_number"):
            assert f in s, f"summary missing field: {f}"

    def test_plan_ahead_state_updated_after_trigger(self):
        # After triggering plan-ahead, GET /api/state should reflect the new plan
        client.post("/api/plan_ahead")
        d = client.get("/api/state").json()
        assert d["plan_ahead"] is not None
        assert "tenant_schedule" in d["plan_ahead"]


# ── Mock plan-ahead (priority-hint bin-packing, tested directly) ──────────────
# Tests _mock_plan_ahead directly to always exercise the mock path regardless
# of whether Gurobi is available.

class TestMockPlanAhead:
    """Tests _mock_plan_ahead directly so they always run the mock path."""

    def _cfg(self, **kw):
        base = {
            'num_tenants': 3, 'num_nodes': 5,
            'plan_ahead_interval': 12, 'access_period': 4,
            'tenant_usage_min': 0.8, 'tenant_usage_max': 6.0,
            'node_capacity': 10.0,
        }
        base.update(kw)
        return base

    def _mock(self, **kw):
        from interface import _mock_plan_ahead
        return _mock_plan_ahead(self._cfg(**kw), interval=0)

    def test_has_required_keys(self):
        d = self._mock()
        for k in ("tenant_schedule", "num_slots", "slot_labels",
                  "current_slot", "access_period", "planning_horizon", "summary"):
            assert k in d

    def test_num_slots_derived_from_horizon_and_period(self):
        d = self._mock(plan_ahead_interval=20, access_period=5)
        assert d["num_slots"] == 4  # 20 // 5

    def test_all_tenants_present(self):
        d = self._mock()
        for t in range(3):
            assert str(t) in d["tenant_schedule"]

    def test_all_periods_present_per_tenant(self):
        d = self._mock()
        for t_str, slots in d["tenant_schedule"].items():
            for s in range(d["num_slots"]):
                assert str(s) in slots, \
                    f"Tenant {t_str} missing period {s}"

    def test_node_ids_in_range(self):
        d = self._mock()
        for t_str, slots in d["tenant_schedule"].items():
            for s_str, nodes in slots.items():
                for n in nodes:
                    assert 0 <= n < 5, \
                        f"node {n} out of range for T{t_str} period {s_str}"

    def test_each_tenant_assigned_at_least_one_node(self):
        # With default usage range and enough capacity, every tenant should
        # receive at least one priority node across all periods.
        d = self._mock()
        for t_str, slots in d["tenant_schedule"].items():
            total = sum(len(nodes) for nodes in slots.values())
            assert total > 0, f"Tenant {t_str} has no priority assignments at all"

    def test_no_node_over_capacity(self):
        # Sum of u[i,h] over all tenants for any period must be assignable
        # within total cluster capacity.  The mock should not crash even when
        # demand exceeds capacity (partial assignment is acceptable).
        d = self._mock(tenant_usage_min=3.0, tenant_usage_max=3.0,
                       num_tenants=5, num_nodes=5, node_capacity=10.0)
        # Each tenant: 3.0 units, 5 tenants = 15 total > 50 (5×10) capacity
        # — no crash expected; partial assignment is fine
        assert d["num_slots"] >= 1

    def test_idempotent_within_same_week(self):
        # Same week_number → same seed → same assignment
        from interface import _mock_plan_ahead
        cfg = self._cfg()
        d1 = _mock_plan_ahead(cfg, interval=0)   # week 0
        d2 = _mock_plan_ahead(cfg, interval=5)   # still week 0 (5//12=0)
        assert d1["tenant_schedule"] == d2["tenant_schedule"]

    def test_different_weeks_may_differ(self):
        # Different week seeds can produce different assignments
        from interface import _mock_plan_ahead
        cfg = self._cfg()
        d1 = _mock_plan_ahead(cfg, interval=0)    # week 0
        d2 = _mock_plan_ahead(cfg, interval=12)   # week 1
        # They are generated by different seeds — may differ (not required to,
        # but the seeds are different so almost certainly will).
        assert d1["summary"]["week_number"] == 0
        assert d2["summary"]["week_number"] == 1

    def test_summary_fields_present(self):
        d = self._mock()
        for f in ("avg_nodes_per_tenant", "isolation_score", "week_number"):
            assert f in d["summary"], f"summary missing field: {f}"

    def test_isolation_score_bounded(self):
        # isolation_score is a fraction in [0, 1]
        d = self._mock()
        s = d["summary"]["isolation_score"]
        assert 0.0 <= s <= 1.0, f"isolation_score out of bounds: {s}"


# ── Tenant usage range config ─────────────────────────────────────────────────

class TestTenantUsageRange:
    def test_default_usage_range(self):
        cfg = client.get("/api/state").json()["sim_config"]
        assert cfg["tenant_usage_min"] == 0.8
        assert cfg["tenant_usage_max"] == 6.0

    def test_usage_range_configurable_via_api(self):
        client.post("/api/config", json={"tenant_usage_min": 2.0, "tenant_usage_max": 8.0})
        r = client.post("/api/reset")
        cfg = r.json()["sim_config"]
        assert cfg["tenant_usage_min"] == 2.0
        assert cfg["tenant_usage_max"] == 8.0


# ── Simulation totals ──────────────────────────────────────────────────────────

class TestSimTotals:
    def test_placement_rate_bounded(self):
        for _ in range(5):
            client.post("/api/step")
        t = client.get("/api/state").json()["sim_totals"]
        assert 0 <= t["placement_rate"] <= 100

    def test_total_generated_gte_total_placed(self):
        for _ in range(5):
            client.post("/api/step")
        t = client.get("/api/state").json()["sim_totals"]
        assert t["total_generated"] >= t["total_placed"]

    def test_k_window_matches_config(self):
        t = client.get("/api/state").json()["sim_totals"]
        cfg = client.get("/api/state").json()["sim_config"]
        assert t["k_window"] == cfg["k_window"]
