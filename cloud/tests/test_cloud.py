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


def test_shadow_planner_scored_and_counted(monkeypatch):
    import dispatch
    from fakes import FakeTable
    from fleet_shared.sim import simulate
    from fleet_shared.graph import Graph

    g = Graph.load()
    pallets = {"pallet_3": "slot_A3"}
    fl = {"forklift_id": "fl-01", "node": "dock_A"}
    good = {"type": "Sequence", "children": [{"type": "GoTo", "node": "slot_A3"}, {"type": "Pick", "pallet": "pallet_3"},
                                             {"type": "GoTo", "node": "dock_B"}, {"type": "Drop", "slot": "dock_B"}]}
    snap = dispatch.sim_snapshot(fl, pallets, [fl])
    prod_cost = simulate({"type": "Sequence", "children": [{"type": "GoTo", "node": "slot_A4"}] + good["children"]}, snap, g).cost

    planners = FakeTable("version", [{"version": "v2", "stage": "shadow"}])
    planners.update_item = lambda **kw: planners.items["v2"].update(counted=True)
    sent = []
    monkeypatch.setattr(dispatch, "table", lambda name: planners)
    monkeypatch.setattr(dispatch, "publish", lambda t, p: sent.append((t, p)))
    monkeypatch.setattr(dispatch, "make_plan", lambda *a, **k: (good, fl))
    res = dispatch.run_shadows({"task_id": "t9"}, [{"version": "v2"}], "move pallet 3 to dock B", pallets, [fl], prod_cost, snap)
    assert res[0]["win"] and res[0]["sim_cost"] < prod_cost
    assert sent[0][0] == "tasks/t9/shadow" and planners.items["v2"]["counted"]


def test_pick_forklift_availability():
    import dispatch
    now = dispatch.now_ms()
    fl = [{"forklift_id": "fl-01", "node": "dock_A", "state": "STOPPED", "last_seen": now},      # stopped, no task: OK
          {"forklift_id": "fl-02", "node": "j6", "state": "DRIVING", "last_seen": now},          # telemetry lag: OK
          {"forklift_id": "fl-03", "node": "j1", "state": "IDLE", "current_task": "t1", "last_seen": now},
          {"forklift_id": "fl-04", "node": "j2", "state": "FAULT", "last_seen": now}]
    pool = {dispatch.pick_forklift([f], "slot_A3") and f["forklift_id"] for f in fl} - {None}
    assert pool == {"fl-01", "fl-02"}
