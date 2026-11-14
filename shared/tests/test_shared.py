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
    assert blocked is not None and edge_id("j1", "j3") not in {edge_id(a, b) for a, b in zip(blocked, blocked[1:])}
    assert g.nearest_node(0.11, 0.21) == "dock_A"


def test_pallets_on_real_nodes():
    g = Graph.load()
    for pallet, node in g.pallets.items():
        assert node in g.nodes, pallet
