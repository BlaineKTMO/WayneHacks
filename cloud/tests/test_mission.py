"""Missions on the mission map: plan moves, run the whole fleet until every pallet ships."""

import importlib
import os

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-2")


import pytest


def _reload():
    import mission
    import route_planner
    import traffic
    import tree_runner
    for mod in (traffic, tree_runner, route_planner, mission):
        importlib.reload(mod)     # GRAPH is loaded at import time
    return mission, traffic, tree_runner


@pytest.fixture
def mission_map():
    os.environ["WAREHOUSE_MAP"] = "mission"
    yield _reload()
    os.environ["WAREHOUSE_MAP"] = "demo"
    _reload()                     # leave the demo map loaded for the other tests


def load(monkeypatch):
    return _reload()


def test_build_moves_round_robin(mission_map):
    mission, *_ = mission_map
    pallets = dict(mission.GRAPH.pallets)
    moves = mission.build_moves({"A": ["dock_1"], "B": ["dock_2", "dock_3"]}, pallets)
    assert len(moves) == 6
    assert [m["dock"] for m in moves if m["slot"].startswith("slot_B")] == ["dock_2", "dock_3", "dock_2"]
    with pytest.raises(ValueError):
        mission.build_moves({"A": ["j1"]}, pallets)            # not a dock
    with pytest.raises(ValueError):
        mission.build_moves({"A": ["dock_1"]}, {})            # empty aisle


def test_mission_runs_to_completion(monkeypatch, mission_map):
    mission, traffic, tree_runner = mission_map
    from fakes import FakeTable, install
    now = mission.now_ms()
    tables, locks, sent = install(monkeypatch, [
        {"forklift_id": "fl-01", "node": "xA", "last_seen": now},
        {"forklift_id": "fl-02", "node": "xB", "last_seen": now}],
        [{"pallet_id": p, "node": n} for p, n in mission.GRAPH.pallets.items()])
    tables["MISSIONS_TABLE"] = FakeTable("mission_id")
    for mod in (mission,):
        monkeypatch.setattr(mod, "table", lambda name: tables[name])
        monkeypatch.setattr(mod, "publish", lambda topic, payload: sent.append((topic, payload)))

    s = mission.start({"A": ["dock_1", "dock_2"]})
    assert s["total"] == 3 and s["running"] == 2                      # both forklifts busy right away

    F = tables["FORKLIFTS_TABLE"].items
    for _ in range(40):                                               # drive each running task step by step
        busy = [fid for fid, f in F.items() if f.get("current_task")]
        if not busy:
            break
        for fid in busy:
            f = F[fid]
            route = f.get("route")
            if route and len(route) > 1:                              # still driving: hop to the end
                for n in route[1:]:
                    f["node"] = n
                    traffic.on_node_reached(fid, n)
                traffic.on_done(fid, f["node"])
            else:
                traffic.on_done(fid, f["node"])
            tree_runner.on_event(fid, f["current_task"], "done")
            f["last_seen"] = mission.now_ms()
    m = mission.get(s["mission_id"])
    assert m["status"] == "done", mission.summary(m)
    shipped = {p["pallet_id"]: p["node"] for p in tables["PALLETS_TABLE"].items.values()}
    assert all(shipped[mv["pallet"]] == f"shipped:{mv['dock']}" for mv in m["moves"])
    assert mission.summary(m)["per_dock"]["dock_1"]["shipped"] == 2


def test_retry_of_a_carried_pallet_goes_to_the_same_forklift(monkeypatch, mission_map):
    mission, traffic, tree_runner = mission_map
    from fakes import FakeTable, install
    now = mission.now_ms()
    tables, locks, sent = install(monkeypatch, [
        {"forklift_id": "fl-01", "node": "c1", "last_seen": now},
        {"forklift_id": "fl-02", "node": "xA", "last_seen": now}],
        [{"pallet_id": "pallet_7", "node": "on:fl-01"}])
    tables["MISSIONS_TABLE"] = FakeTable("mission_id")
    monkeypatch.setattr(mission, "table", lambda name: tables[name])
    monkeypatch.setattr(mission, "publish", lambda t, p: sent.append((t, p)))
    tables["MISSIONS_TABLE"].put_item({"mission_id": "m-1", "status": "running", "rev": 0, "moves": [
        {"pallet": "pallet_7", "slot": "slot_C1", "dock": "dock_4", "state": "pending", "task_id": None,
         "forklift": None, "attempts": 2}]})
    assert mission.dispatch_next("m-1") == 1
    mv = tables["MISSIONS_TABLE"].items["m-1"]["moves"][0]
    assert mv["forklift"] == "fl-01"                                   # the one carrying it
    steps = [s["type"] for s in tables["TASKS_TABLE"].items[mv["task_id"]]["tree"]["children"]]
    assert steps == ["GoTo", "Drop"]                                   # no second Pick
