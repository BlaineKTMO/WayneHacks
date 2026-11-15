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
