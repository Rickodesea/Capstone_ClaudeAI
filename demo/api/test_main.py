"""
test_main.py
────────────
Backend smoke tests for the demo API.

Run with:
    cd demo/api
    pytest test_main.py -v
"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

import pytest
from fastapi.testclient import TestClient
from main import app
from plan_ahead_mock import generate_plan_ahead

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_before_each():
    """Ensure a clean simulation state before every test."""
    client.post("/api/reset")


# ── Endpoint: GET /api/state ────────────────────────────────────────────────

class TestGetState:
    def test_returns_200(self):
        r = client.get("/api/state")
        assert r.status_code == 200

    def test_has_required_top_level_keys(self):
        d = client.get("/api/state").json()
        for key in ("interval", "nodes", "queue", "hud", "mem_history",
                    "plan_ahead", "recent_placements", "plan_ahead_interval"):
            assert key in d, f"Missing key: {key}"

    def test_initial_interval_is_zero(self):
        d = client.get("/api/state").json()
        assert d["interval"] == 0

    def test_initial_plan_ahead_is_not_null(self):
        d = client.get("/api/state").json()
        assert d["plan_ahead"] is not None
        assert "heatmap" in d["plan_ahead"]

    def test_nodes_present(self):
        d = client.get("/api/state").json()
        assert len(d["nodes"]) > 0

    def test_node_has_required_fields(self):
        node = client.get("/api/state").json()["nodes"][0]
        for field in ("node_id", "capacity_mb", "used_mb", "mem_pct",
                      "eff_pct", "cpu_cores", "violation_rate", "running_jobs"):
            assert field in node, f"Node missing field: {field}"

    def test_hud_has_required_fields(self):
        hud = client.get("/api/state").json()["hud"]
        for field in ("total_jobs", "total_tenants", "total_nodes",
                      "mem_utilization_pct", "longest_wait_intervals",
                      "intervals_to_plan_ahead"):
            assert field in hud, f"HUD missing field: {field}"


# ── Endpoint: POST /api/step ─────────────────────────────────────────────────

class TestStep:
    def test_returns_200(self):
        assert client.post("/api/step").status_code == 200

    def test_advances_interval(self):
        r = client.post("/api/step")
        assert r.json()["interval"] == 1

    def test_interval_increments_each_step(self):
        client.post("/api/step")
        client.post("/api/step")
        d = client.post("/api/step").json()
        assert d["interval"] == 3

    def test_generates_jobs(self):
        d = client.post("/api/step").json()
        assert d["hud"]["total_jobs"] > 0

    def test_mem_history_grows(self):
        client.post("/api/step")
        d = client.post("/api/step").json()
        assert len(d["mem_history"]) >= 1

    def test_queue_job_has_cpu_field(self):
        # Run a few steps so there are queued jobs
        for _ in range(3):
            client.post("/api/step")
        d = client.get("/api/state").json()
        if d["queue"]:
            job = d["queue"][0]
            assert "req_cpu" in job, "Queue job missing req_cpu"

    def test_plan_ahead_not_null_at_correct_interval(self):
        from main import PLAN_AHEAD_INTERVAL
        for _ in range(PLAN_AHEAD_INTERVAL):
            r = client.post("/api/step")
        d = r.json()
        assert d["plan_ahead"] is not None, "Plan-ahead should fire at PLAN_AHEAD_INTERVAL"

    def test_plan_ahead_null_between_triggers(self):
        d = client.post("/api/step").json()
        assert d["plan_ahead"] is None


# ── Endpoint: POST /api/reset ─────────────────────────────────────────────────

class TestReset:
    def test_returns_200(self):
        assert client.post("/api/reset").status_code == 200

    def test_resets_interval_to_zero(self):
        client.post("/api/step")
        client.post("/api/step")
        d = client.post("/api/reset").json()
        assert d["interval"] == 0

    def test_resets_mem_history(self):
        client.post("/api/step")
        d = client.post("/api/reset").json()
        assert d["mem_history"] == []

    def test_resets_plan_ahead(self):
        d = client.post("/api/reset").json()
        assert d["plan_ahead"] is not None
        assert "heatmap" in d["plan_ahead"]


# ── plan_ahead_mock unit tests ────────────────────────────────────────────────

class TestPlanAheadMock:
    def test_heatmap_cell_count(self):
        result = generate_plan_ahead(3, 5, 50)
        assert len(result["heatmap"]) == 15  # 3 tenants × 5 nodes

    def test_heatmap_has_required_fields(self):
        cell = generate_plan_ahead(3, 5, 50)["heatmap"][0]
        for f in ("tenant_id", "node_id", "intensity", "authorized"):
            assert f in cell

    def test_intensity_in_range(self):
        for cell in generate_plan_ahead(3, 5, 50)["heatmap"]:
            assert 0.0 <= cell["intensity"] <= 1.0

    def test_all_tenants_have_at_least_one_node(self):
        result = generate_plan_ahead(3, 5, 100)
        access = result["tenant_node_access"]
        for t in ("0", "1", "2"):
            assert len(access.get(t, [])) >= 1

    def test_summary_fields(self):
        s = generate_plan_ahead(3, 5, 50)["summary"]
        assert "avg_nodes_per_tenant" in s
        assert "isolation_score" in s
        assert "week_number" in s

    def test_different_intervals_produce_different_data(self):
        r1 = generate_plan_ahead(3, 5, 50)
        r2 = generate_plan_ahead(3, 5, 200)
        # Different week seeds should produce different access maps
        assert r1["tenant_node_access"] != r2["tenant_node_access"] or \
               r1["summary"]["week_number"] != r2["summary"]["week_number"]
