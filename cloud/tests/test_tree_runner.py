"""Run whole behavior-tree tasks through tree_runner + traffic with fake AWS."""

import os

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-2")

import traffic  # noqa: E402
import tree_runner  # noqa: E402
from fakes import install, last_status, paths  # noqa: E402
from fleet_shared import topics  # noqa: E402
from fleet_shared.behavior_tree import with_status  # noqa: E402


def drive_to_end(tables, fid, sent):
    """Pretend the forklift drives its windowed route: report node_reached until the route is done."""
    for _ in range(20):
        route = tables["FORKLIFTS_TABLE"].items[fid].get("route")
        if not route or len(route) == 1:
            break
        nxt = route[1]
        tables["FORKLIFTS_TABLE"].items[fid]["node"] = nxt
        traffic.on_node_reached(fid, nxt)
    node = tables["FORKLIFTS_TABLE"].items[fid]["node"]
    traffic.on_done(fid, node)
    return node


def test_full_move(monkeypatch):
    tables, locks, sent = install(monkeypatch, [{"forklift_id": "fl-01", "node": "dock_A"}],
                                  [{"pallet_id": "pallet_3", "node": "slot_A3"}])
    tree = with_status({"type": "Sequence", "children": [
        {"type": "GoTo", "node": "slot_A3"}, {"type": "Pick", "pallet": "pallet_3"},
        {"type": "GoTo", "node": "dock_B"}, {"type": "Drop", "slot": "dock_B"}]})
    task = {"task_id": "t1", "text": "move pallet 3 to dock B", "status": "running", "tree": tree}
    tables["TASKS_TABLE"].put_item(task)

    tree_runner.start(task, "fl-01", tree)
    first = paths(sent, "fl-01")[0]
    assert first["partial"] is True and len(first["nodes"]) == 3  # current + 2 reserved ahead

    assert drive_to_end(tables, "fl-01", sent) == "slot_A3"
    tree_runner.on_event("fl-01", "t1", "done")                       # reached pallet
    assert paths(sent, "fl-01")[-1]["action"] == "pick"
    tree_runner.on_event("fl-01", "t1", "done")                       # picked
    assert tables["PALLETS_TABLE"].items["pallet_3"]["node"] == "on:fl-01"
    assert drive_to_end(tables, "fl-01", sent) == "dock_B"
    tree_runner.on_event("fl-01", "t1", "done")                       # at dock
    tree_runner.on_event("fl-01", "t1", "done")                       # dropped

    assert tables["PALLETS_TABLE"].items["pallet_3"]["node"] == "dock_B"
    assert tables["TASKS_TABLE"].items["t1"]["status"] == "done"
    assert last_status(sent, "t1")["state"] == "done"
    assert tables["EPISODES_TABLE"].items["t1"]["success"] is True          # graded episode recorded
    assert any(k.endswith("/t1.json") for k in tables["_s3"])
    assert [p for t, p in sent if t == topics.task_outcome("t1")][-1]["success"] is True
    assert locks.held() == {"dock_B": "fl-01"}  # only the parking spot stays reserved


def test_fault_fails_task(monkeypatch):
    tables, _, sent = install(monkeypatch, [{"forklift_id": "fl-01", "node": "dock_A"}])
    tree = with_status({"type": "Sequence", "children": [{"type": "GoTo", "node": "j3"}]})
    task = {"task_id": "t2", "status": "running", "tree": tree}
    tables["TASKS_TABLE"].put_item(task)
    tree_runner.start(task, "fl-01", tree)
    tree_runner.on_event("fl-01", "t2", "fault")
    assert tables["TASKS_TABLE"].items["t2"]["status"] == "failed"
    assert "fault" in last_status(sent, "t2")["error"]
    assert tables["EPISODES_TABLE"].items["t2"]["success"] is False


def test_retry_then_fallback(monkeypatch):
    tables, _, _ = install(monkeypatch, [{"forklift_id": "fl-01", "node": "dock_A"}])
    tree = with_status({"type": "Fallback", "children": [
        {"type": "Retry", "n": 2, "child": {"type": "GoTo", "node": "j3"}},
        {"type": "GoTo", "node": "j2"}]})
    task = {"task_id": "t3", "status": "running", "tree": tree}
    tables["TASKS_TABLE"].put_item(task)
    tree_runner.start(task, "fl-01", tree)
    tree_runner.on_event("fl-01", "t3", "fault")   # attempt 1 fails -> retried
    tree_runner.on_event("fl-01", "t3", "fault")   # attempt 2 fails -> fallback to j2
    t = tables["TASKS_TABLE"].items["t3"]["tree"]
    assert t["children"][0]["status"] == "failure" and t["children"][1]["status"] == "running"
