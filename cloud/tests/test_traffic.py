"""Two forklifts sharing lanes: reservations, waiting, replanning, giving up."""

import os

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-2")

import traffic  # noqa: E402
from fakes import install, paths  # noqa: E402


def test_window_is_current_plus_lookahead(monkeypatch):
    tables, locks, sent = install(monkeypatch, [{"forklift_id": "fl-01", "node": "dock_A"}])
    assert traffic.start_route("fl-01", "slot_A4", "t1")
    p = paths(sent, "fl-01")[-1]
    assert [n["id"] for n in p["nodes"]] == ["dock_A", "j1", "j3"] and p["partial"]
    assert set(locks.held()) == {"dock_A", "j1", "j3"}


def test_already_there(monkeypatch):
    install(monkeypatch, [{"forklift_id": "fl-01", "node": "j1"}])
    assert traffic.start_route("fl-01", "j1", "t1") is False


def test_parked_forklift_is_avoided(monkeypatch):
    tables, locks, sent = install(monkeypatch, [{"forklift_id": "fl-01", "node": "dock_A"},
                                                {"forklift_id": "fl-02", "node": "j3"}])
    locks.acquire("j3", "fl-02")  # fl-02 parked at j3
    traffic.start_route("fl-01", "slot_A2", "t1")
    route = tables["FORKLIFTS_TABLE"].items["fl-01"]["route"]
    assert "j3" not in route and route[-1] == "slot_A2"   # goes round via j5/j2/j4/j6


def test_blocked_waits_then_extends_when_lane_frees(monkeypatch):
    tables, locks, sent = install(monkeypatch, [{"forklift_id": "fl-01", "node": "j1"}])
    locks.acquire("dock_A", "fl-02")  # someone sitting on the only way in
    traffic.start_route("fl-01", "dock_A", "t1")
    assert paths(sent, "fl-01") == []                              # nothing drivable yet
    assert tables["FORKLIFTS_TABLE"].items["fl-01"]["blocked_since"]
    locks.release("dock_A", "fl-02")
    traffic.on_blocked("fl-01")
    p = paths(sent, "fl-01")[-1]
    assert [n["id"] for n in p["nodes"]] == ["j1", "dock_A"] and not p["partial"]


def test_replan_after_5s(monkeypatch):
    tables, locks, sent = install(monkeypatch, [{"forklift_id": "fl-01", "node": "j1"}])
    traffic.start_route("fl-01", "j4", "t1")       # route j1 j3 j6 j4 (or j1 j5 j2 j4)
    route = tables["FORKLIFTS_TABLE"].items["fl-01"]["route"]
    # Another forklift grabs the node right after our window.
    victim = route[3] if len(route) > 3 else route[2]
    for n in route[1:]:
        locks.release(n, "fl-01")
    locks.acquire(route[1], "fl-02")
    tables["FORKLIFTS_TABLE"].items["fl-01"].update(window_end=None, blocked_since=1, blocked_first=traffic.now_ms())
    traffic.on_blocked("fl-01")
    new = tables["FORKLIFTS_TABLE"].items["fl-01"]["route"]
    assert route[1] not in new and new[-1] == "j4", (route, new, victim)


def test_give_up_after_45s(monkeypatch):
    tables, locks, sent = install(monkeypatch, [{"forklift_id": "fl-01", "node": "j2"}])
    locks.acquire("dock_B", "fl-02")
    traffic.start_route("fl-01", "dock_B", "t1")
    tables["FORKLIFTS_TABLE"].items["fl-01"]["blocked_first"] = 1
    err = traffic.on_blocked("fl-01")
    assert err and "fl-02" in err
    assert "route" not in tables["FORKLIFTS_TABLE"].items["fl-01"]


def test_renew_never_resurrects_released_locks(monkeypatch):
    tables, locks, _ = install(monkeypatch, [{"forklift_id": "fl-01", "node": "dock_A"}])
    traffic.start_route("fl-01", "slot_A4", "t1")
    tables["FORKLIFTS_TABLE"].items["fl-01"]["node"] = "j1"
    traffic.on_node_reached("fl-01", "j1")       # releases dock_A
    traffic.renew("fl-01", "j1")                 # telemetry heartbeat
    assert "dock_A" not in locks.held()


def test_concurrent_route_updates_are_not_lost(monkeypatch):
    """Two node_reached events processed 'at once': the later write must not undo the earlier one."""
    tables, locks, _ = install(monkeypatch, [{"forklift_id": "fl-01", "node": "dock_A"}])
    traffic.start_route("fl-01", "slot_A4", "t1")
    stale = dict(tables["FORKLIFTS_TABLE"].items["fl-01"])   # what a parallel Lambda read earlier
    traffic.on_node_reached("fl-01", "j1")
    real_read = traffic._forklift
    reads = iter([stale])
    monkeypatch.setattr(traffic, "_forklift", lambda fid: next(reads, None) or real_read(fid))
    traffic.on_node_reached("fl-01", "j3")                    # first attempt uses the stale read -> conflict -> retry
    assert tables["FORKLIFTS_TABLE"].items["fl-01"]["route"][0] == "j3"
    assert "j1" not in locks.held() and "dock_A" not in locks.held()


def test_idle_forklift_makes_way(monkeypatch):
    tables, locks, sent = install(monkeypatch, [{"forklift_id": "fl-01", "node": "dock_A"},
                                                {"forklift_id": "fl-02", "node": "dock_B"}])
    locks.acquire("dock_B", "fl-02")              # fl-02 parked on our destination, idle
    traffic.start_route("fl-01", "dock_B", "t1")
    route = tables["FORKLIFTS_TABLE"].items["fl-01"]["route"]
    moved = tables["FORKLIFTS_TABLE"].items["fl-02"].get("route")
    assert moved and moved[0] == "dock_B" and moved[-1] not in route   # moved off our way, before we arrive
    assert tables["FORKLIFTS_TABLE"].items["fl-02"]["route_task"] == "yield-fl-01"


def test_busy_forklift_is_not_moved(monkeypatch):
    tables, locks, _ = install(monkeypatch, [{"forklift_id": "fl-01", "node": "j2"},
                                             {"forklift_id": "fl-02", "node": "dock_B", "current_task": "t9"}])
    locks.acquire("dock_B", "fl-02")
    traffic.start_route("fl-01", "dock_B", "t1")
    traffic.on_blocked("fl-01")
    assert "route" not in tables["FORKLIFTS_TABLE"].items["fl-02"]


def test_heartbeat_retries_a_forklift_that_never_got_a_path(monkeypatch):
    """Blocked before its first move: no path, so no 'blocked' events. Telemetry must retry it."""
    tables, locks, sent = install(monkeypatch, [{"forklift_id": "fl-01", "node": "j1"}])
    locks.acquire("j3", "fl-02")
    locks.acquire("j5", "fl-02")                         # both ways out of j1 taken
    traffic.start_route("fl-01", "slot_A3", "t1")
    assert paths(sent, "fl-01") == []                   # stuck, nothing sent
    assert traffic.retry_if_stuck("fl-01") is None      # heartbeat: still blocked, no error yet
    locks.release("j3", "fl-02")
    traffic.retry_if_stuck("fl-01")                     # next heartbeat: lane free -> path goes out
    assert paths(sent, "fl-01") and paths(sent, "fl-01")[-1]["nodes"][0]["id"] == "j1"
    tables["FORKLIFTS_TABLE"].items["fl-01"].update(blocked_first=1, blocked_since=1)
    locks.acquire("j3", "fl-02")
    tables["FORKLIFTS_TABLE"].items["fl-01"]["window_end"] = None
    tables["FORKLIFTS_TABLE"].items["fl-01"]["route"] = ["j1", "j3", "slot_A3"]
    for n in ("j3",):
        locks.release(n, "fl-01")
    locks.acquire("j3", "fl-02")
    assert traffic.retry_if_stuck("fl-01")              # stuck 45 s+ -> gives up with a reason
