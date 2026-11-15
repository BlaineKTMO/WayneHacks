"""Run a whole pick-and-drop task through tree_runner with fake DynamoDB and MQTT."""

import os

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-2")

import route_planner  # noqa: E402
import tree_runner  # noqa: E402
from fleet_shared.behavior_tree import with_status  # noqa: E402


class FakeTable:
    def __init__(self, key, items):
        self.key, self.items = key, {i[key]: dict(i) for i in items}

    def get_item(self, Key):
        k = Key[self.key]
        return {"Item": dict(self.items[k])} if k in self.items else {}

    def put_item(self, Item):
        self.items[Item[self.key]] = dict(Item)

    def scan(self, **_):
        return {"Items": [dict(v) for v in self.items.values()]}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeNames=None, ExpressionAttributeValues=None, **_):
        item = self.items.setdefault(Key[self.key], dict(Key))
        if UpdateExpression.startswith("REMOVE"):
            item.pop(UpdateExpression.split()[1], None)
            return {}
        for part in UpdateExpression[4:].split(","):
            name, val = (s.strip() for s in part.split("="))
            item[(ExpressionAttributeNames or {}).get(name, name)] = ExpressionAttributeValues[val]
        return {}


def setup(monkeypatch):
    tables = {
        "FORKLIFTS_TABLE": FakeTable("forklift_id", [{"forklift_id": "fl-01", "node": "dock_A"}]),
        "TASKS_TABLE": FakeTable("task_id", []),
        "PALLETS_TABLE": FakeTable("pallet_id", [{"pallet_id": "pallet_3", "node": "slot_A3"}]),
    }
    sent = []
    for mod in (tree_runner, route_planner):
        monkeypatch.setattr(mod, "table", lambda name: tables[name])
    monkeypatch.setattr(tree_runner, "publish", lambda topic, payload: sent.append((topic, payload)))
    return tables, sent


def arrive(tables, node):
    tables["FORKLIFTS_TABLE"].items["fl-01"]["node"] = node


def test_full_move(monkeypatch):
    tables, sent = setup(monkeypatch)
    tree = with_status({"type": "Sequence", "children": [
        {"type": "GoTo", "node": "slot_A3"}, {"type": "Pick", "pallet": "pallet_3"},
        {"type": "GoTo", "node": "dock_B"}, {"type": "Drop", "slot": "dock_B"}]})
    task = {"task_id": "t1", "text": "move pallet 3 to dock B", "status": "running", "tree": tree}
    tables["TASKS_TABLE"].put_item(task)

    tree_runner.start(task, "fl-01", tree)
    paths = [p for t, p in sent if t == "fleet/fl-01/path"]
    assert paths[-1]["nodes"][-1]["id"] == "slot_A3"

    arrive(tables, "slot_A3"); tree_runner.on_event("fl-01", "t1", "done")   # reached pallet
    assert [p for t, p in sent if t == "fleet/fl-01/path"][-1]["action"] == "pick"
    tree_runner.on_event("fl-01", "t1", "done")                                # picked
    assert tables["PALLETS_TABLE"].items["pallet_3"]["node"] == "on:fl-01"
    arrive(tables, "dock_B"); tree_runner.on_event("fl-01", "t1", "done")     # at dock
    tree_runner.on_event("fl-01", "t1", "done")                                # dropped

    assert tables["PALLETS_TABLE"].items["pallet_3"]["node"] == "dock_B"
    assert tables["TASKS_TABLE"].items["t1"]["status"] == "done"
    assert sent[-1][0] == "tasks/t1/status" and sent[-1][1]["state"] == "done"


def test_fault_fails_task(monkeypatch):
    tables, sent = setup(monkeypatch)
    tree = with_status({"type": "Sequence", "children": [{"type": "GoTo", "node": "j3"}]})
    task = {"task_id": "t2", "status": "running", "tree": tree}
    tables["TASKS_TABLE"].put_item(task)
    tree_runner.start(task, "fl-01", tree)
    tree_runner.on_event("fl-01", "t2", "fault")
    assert tables["TASKS_TABLE"].items["t2"]["status"] == "failed"
    assert "fault" in sent[-1][1]["error"]


def test_retry_then_fallback(monkeypatch):
    tables, _ = setup(monkeypatch)
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
