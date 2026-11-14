"""Unit tests for Lambda logic that don't need AWS (DynamoDB/IoT are stubbed)."""

import os

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-2")

import route_planner  # noqa: E402


def test_current_node_prefers_reported_node():
    assert route_planner.current_node({"node": "j2", "pose": {"x": 0.1, "y": 0.2}}) == "j2"


def test_current_node_falls_back_to_pose():
    assert route_planner.current_node({"pose": {"x": 0.89, "y": 0.21}}) == "j3"
    assert route_planner.current_node({}) is None


def test_plan(monkeypatch):
    class FakeTable:
        def get_item(self, Key):
            return {"Item": {"forklift_id": Key["forklift_id"], "node": "dock_A"}}

    monkeypatch.setattr(route_planner, "table", lambda _name: FakeTable())
    path = route_planner.plan("fl-01", "slot_A3", "t1")
    ids = [n["id"] for n in path["nodes"]]
    assert ids == ["dock_A", "j1", "j3", "slot_A3"]


def test_api_routes(monkeypatch):
    import api

    class FakeTable:
        def scan(self, **_):
            return {"Items": [{"forklift_id": "fl-02"}, {"forklift_id": "fl-01"}]}

    monkeypatch.setattr(api, "table", lambda _name: FakeTable())
    r = api.handler({"routeKey": "GET /graph"}, None)
    assert r["statusCode"] == 200 and "nodes" in r["body"]
    r = api.handler({"routeKey": "GET /forklifts"}, None)
    assert r["body"].index("fl-01") < r["body"].index("fl-02")
    assert api.handler({"routeKey": "GET /nope"}, None)["statusCode"] == 404
