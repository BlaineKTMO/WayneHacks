import pytest

from fleet_shared import topics
from fleet_shared.graph import Graph, edge_id
from fleet_shared.messages import Event, Path, Pose, Telemetry


def test_topics():
    assert topics.pose("fl-01") == "fleet/fl-01/pose"
    assert topics.forklift_from_topic("fleet/fl-02/event") == "fl-02"
    with pytest.raises(ValueError):
        topics.pose("forklift1")


def test_message_roundtrip():
    p = Pose(x=0.1, y=0.2, theta=1.0)
    assert Pose.from_json(p.to_json()) == p
    with pytest.raises(ValueError):
        Telemetry.from_dict({"battery_v": 7, "motor_a": 0, "state": "FLYING"})
    with pytest.raises(ValueError):
        Event.from_dict({"type": "done", "bogus": 1})
    Path.from_dict({"task_id": "t1", "nodes": [{"id": "j1", "x": 0, "y": 0}], "action": "pick"})


def test_graph_astar():
    g = Graph.load()
    path = g.astar("dock_A", "slot_A4")
    assert path[0] == "dock_A" and path[-1] == "slot_A4"
    # Block the direct lane and it must route around
    blocked = g.astar("dock_A", "j3", blocked_edges=[edge_id("j1", "j3")])
    assert blocked is not None and edge_id("j1", "j3") not in {edge_id(a, b) for a, b in zip(blocked, blocked[1:], strict=False)}
    assert g.nearest_node(0.11, 0.21) == "dock_A"


def test_pallets_on_real_nodes():
    g = Graph.load()
    for pallet, node in g.pallets.items():
        assert node in g.nodes, pallet


def test_routes_never_pass_through_slots_or_docks():
    g = Graph.load()
    for a in g.nodes:
        for b in g.nodes:
            if a == b:
                continue
            route = g.astar(a, b)
            assert route is not None, f"{a}->{b} unreachable"
            assert all(g.nodes[n].type == "junction" for n in route[1:-1]), route


def test_safety_guard_stops_and_resumes():
    from fleet_shared.safety import Guard, Hazards
    h, g = Hazards(), Guard()
    pose, ahead = (0.4, 0.2, 0.0), [{"x": 0.9, "y": 0.2}]
    assert g.update(pose, ahead, h) is None
    h.on_detections({"model_version": "virtual-demo", "objects": [{"cls": "person", "x": 0.65, "y": 0.25}]})
    assert g.update(pose, ahead, h) == "stop"          # person on the lane ahead
    assert g.update(pose, ahead, h) is None            # still holding
    h.on_detections({"model_version": "virtual-demo", "objects": []})
    assert g.update(pose, ahead, h) == "clear"
    h.on_detections({"model_version": "virtual-demo", "objects": [{"cls": "person", "x": 0.65, "y": 0.6}]})
    assert g.update(pose, ahead, h) is None            # far from the path: keep driving


def test_table_map_is_valid_and_inside_the_safe_rectangle(monkeypatch):
    monkeypatch.setenv("WAREHOUSE_MAP", "table")
    g = Graph.load()
    assert "j1" in g.nodes and g.data["size"]["width"] < 1
    for n in g.nodes.values():   # Ring 1 clearance search: x 0.12-0.42, y 0.14-0.20
        assert 0.12 <= n.x <= 0.42 and 0.14 <= n.y <= 0.20, n
    for a in g.nodes:
        for b in g.nodes:
            if a != b:
                r = g.astar(a, b)
                assert r and all(g.nodes[n].type == "junction" for n in r[1:-1])
    for node in g.pallets.values():
        assert node in g.nodes
