"""Tests that run without a camera or Free-WiLi. Run:  uv run pytest edge"""

import math
import time

from fleet_edge import config
from fleet_edge.freewili_link import SimLink, format_stop, format_wheels, parse_line
from fleet_edge.turn_drive import TurnDriveFollower as WaypointFollower


def test_protocol():
    assert format_wheels(150, -42.4) == "W,100,-42\n"
    assert format_stop("lost,tag") == "S,lost tag\n"
    t = parse_line("T,DRIVING,7.41\r\n")
    assert t.state == "DRIVING" and t.battery_v == 7.41
    assert parse_line("garbage") is None
    assert parse_line("T,IDLE,notanumber") is None


def test_turns_toward_target():
    f = WaypointFollower([(1.0, 1.0)], loop=False)
    left, right = f.update(0.5, 0.5, 0.0)       # target is up-left -> spin left in place
    assert right > 0 and left == -right


def test_drives_straight_when_lined_up():
    f = WaypointFollower([(1.0, 0.0)], loop=False)
    left, right = f.update(0.0, 0.0, 0.0)
    assert left > 0 and right > 0 and right == max(round(left * config.RIGHT_TRIM), config.MIN_SPEED)


def test_backs_up_to_target_behind(monkeypatch):
    monkeypatch.setattr(config, "ALLOW_REVERSE", True)
    f = WaypointFollower([(0.0, 0.0)], loop=False)
    left, right = f.update(0.5, 0.0, 0.0)       # target dead behind -> reverse, no turn
    assert left < 0 and right < 0


def test_sim_completes_laps():
    """The simulated forklift should finish the configured loop in reasonable time."""
    link = SimLink(x=config.WAYPOINTS[0][0], y=config.WAYPOINTS[0][1] - 0.05, theta=0.0)
    f = WaypointFollower(config.WAYPOINTS, loop=True)
    import time
    t = 0.0
    dt = 1 / config.COMMAND_RATE_HZ
    # Drive the sim with a fake clock so the test is fast.
    clock = [0.0]
    time_monotonic = time.monotonic
    time.monotonic = lambda: clock[0]
    try:
        link._t = 0.0
        while f.laps < 1 and t < 120:
            l, r = f.update(link.x, link.y, link.theta)
            # Only ever a pure spin or a (trimmed) straight line: never a deliberate curve.
            assert l == -r or (l * r > 0), f"curved command {l},{r}"
            link.wheels(l, r)
            clock[0] += dt
            t += dt
    finally:
        time.monotonic = time_monotonic
    assert f.laps >= 1, f"didn't finish a lap in {t:.0f}s, stuck at wp {f.index} pos=({link.x:.2f},{link.y:.2f})"
    assert math.isfinite(link.x)


def test_skips_waypoint_it_is_already_at():
    """Standing 6 cm from the first waypoint: head for the next one, don't spin back."""
    f = WaypointFollower([(0.24, 0.13), (0.47, 0.13)], loop=True)
    # Waypoints are pivot positions; the pivot is PIVOT_FWD_M ahead of the centre.
    cx = 0.24 + 0.03 - config.PIVOT_FWD_M
    left, right = f.update(cx, 0.10, 0.09)
    assert f.index == 1


# A border box (tag positions, metres) for the planner tests.
BOX = [(0.0, 0.0), (0.5, 0.0), (0.5, 0.4), (0.0, 0.4)]


def _fork_mast_geometry(monkeypatch):
    """Geometry these planner tests were written for: tag just right of a front pivot."""
    monkeypatch.setattr(config, "TAG_OFFSET_FORWARD_M", 0.1175)
    monkeypatch.setattr(config, "TAG_OFFSET_LEFT_M", -0.054)
    monkeypatch.setattr(config, "PIVOT_FWD_M", 0.11)


def test_spin_check_follows_the_tag_round_the_pivot(monkeypatch):
    _fork_mast_geometry(monkeypatch)
    """The tag sits just right of the pivot. With the border's bottom edge a hair below
    the tag, a clockwise spin dips the tag under it; an anticlockwise one lifts it."""
    from fleet_edge.turn_drive import TurnDriveFollower, tag_at
    x, y, th = 0.19, 0.20, 0.0
    tag_y = tag_at(x, y, th)[1]
    box = [(0.0, tag_y - 0.0003), (0.6, tag_y - 0.0003), (0.6, 0.6), (0.0, 0.6)]
    f = TurnDriveFollower([(0.5, 0.3)], border=box)
    assert not f.spin_is_safe(x, y, th, -math.radians(30))
    assert f.spin_is_safe(x, y, th, math.radians(30))


def test_planner_turns_the_safe_way(monkeypatch):
    _fork_mast_geometry(monkeypatch)
    """Same spot, target up-right: the short way (anticlockwise) is safe and chosen; a
    target down-right needs a clockwise spin, which is unsafe, so it goes the long way."""
    from fleet_edge.turn_drive import TurnDriveFollower, tag_at
    x, y, th = 0.19, 0.20, 0.0
    tag_y = tag_at(x, y, th)[1]
    box = [(-1.0, tag_y - 0.0003), (2.0, tag_y - 0.0003), (2.0, 2.0), (-1.0, 2.0)]
    f = TurnDriveFollower([(0.6, 0.5)], border=box)
    f.update(x, y, th)
    assert f.plan is not None and f.plan[1] > 0
    # Down-right target: forward needs clockwise (unsafe); backing up to it needs a
    # big turn the other way. Whatever it picks, it must not spin clockwise.
    f = TurnDriveFollower([(0.6, 0.15)], border=box)
    left, right = f.update(x, y, th)
    assert f.plan is None or f.plan[1] > 0 or f.mode == "blocked"


def test_blocked_when_no_safe_move():
    from fleet_edge.turn_drive import TurnDriveFollower
    tiny = [(0.30, 0.0), (0.40, 0.0), (0.40, 0.10), (0.30, 0.10)]   # tag can barely move
    f = TurnDriveFollower([(0.0, 0.3)], loop=False, border=tiny)
    from fleet_edge.turn_drive import tag_at
    # place the chassis so its tag sits in the middle of the tiny box
    tx, ty = tag_at(0, 0, 0)
    assert f.update(0.35 - tx, 0.05 - ty, 0.0) == (0, 0) and f.mode == "blocked"


def test_stops_if_tag_outside_border():
    from fleet_edge.turn_drive import TurnDriveFollower
    f = TurnDriveFollower([(0.3, 0.2)], loop=False, border=BOX)
    assert f.update(0.6, 0.2, 0.0) == (0, 0) and f.mode == "border"


def test_reaims_near_waypoint_still_reach_it():
    """Mid-run, re-aiming 4 cm short of a waypoint must not count it as reached."""
    f = WaypointFollower([(0.13, 0.13), (0.36, 0.13)], loop=True)
    f.index, f.moved = 1, True
    f.update(0.32, 0.10, 0.0)            # 37 deg off-heading, 5 cm away: turn, don't skip
    assert f.index == 1 and f.mode == "turn"


def test_spin_drift_guard_stops_and_replans():
    from fleet_edge.turn_drive import TurnDriveFollower
    # (target up-left: a left spin either way)
    f = TurnDriveFollower([(0.0, 0.5)], loop=False)
    assert f.update(0.2, 0.2, 0.0)[0] < 0            # target up-left: spinning left
    l, r = f.update(0.2 + config.SPIN_DRIFT_M + 0.01, 0.2, 0.2)   # slid 5 cm mid-spin
    assert (l, r) == (0, 0) and f.mode == "drift"
    assert f.update(0.25, 0.2, 0.2) != (0, 0)        # re-planned from the new spot


def test_left_only_takes_the_long_way(monkeypatch):
    from fleet_edge.turn_drive import TurnDriveFollower
    monkeypatch.setattr(config, "SPIN_LEFT_ONLY", True)
    f = TurnDriveFollower([(0.5, 0.0)], loop=False)
    l, r = f.update(0.2, 0.3, 0.0)           # target down-right: short way is clockwise
    assert l < 0 < r and f.plan[1] > 0       # spins left (the long way) instead


def test_drift_stop_after_repeated_slides():
    from fleet_edge.turn_drive import TurnDriveFollower
    f = TurnDriveFollower([(0.0, 0.5)], loop=False)
    x = 0.2
    for _ in range(config.MAX_DRIFTS + 1):
        f.update(x, 0.2, 0.0)                         # start a spin
        x += config.SPIN_DRIFT_M + 0.01
        f.update(x, 0.2, 0.0)                         # slid: drift
    assert f.mode == "drift_stop" and f.update(x, 0.2, 0.0) == (0, 0)


def test_bridge_unit_drives_partial_then_reports_blocked():
    """A partial route ends in 'blocked' (not 'done'); a full route ends in 'done'."""
    from fleet_edge.bridge import Unit
    from fleet_shared.graph import Graph
    from fleet_shared.messages import Path

    g = Graph.load()
    sent = []
    link = SimLink(x=g.nodes["dock_A"].x, y=g.nodes["dock_A"].y, theta=0.0)
    u = Unit("fl-01", link, g, lambda topic, msg: sent.append((topic, msg)))
    clock = [0.0]
    time_monotonic = time.monotonic
    time.monotonic = lambda: clock[0]
    link._t = 0.0

    def run(path, steps=4000):
        u.on_message("path", Path(task_id="t", nodes=g.to_path_nodes(path), partial=False).to_json().encode()
                     if path[-1] == "j3" else Path(task_id="t", nodes=g.to_path_nodes(path), partial=True).to_json().encode())
        for _ in range(steps):
            clock[0] += 0.05
            u.tick(clock[0], (link.x, link.y, link.theta))
            if u.follower is None and u.inbox.empty():
                break
    try:
        u.tick(0.0, (link.x, link.y, link.theta))
        run(["dock_A", "j1"])                    # partial
        events = [m.type for t, m in sent if t.endswith("/event")]
        assert events[-1] == "blocked" and "node_reached" in events and u.state == "BLOCKED"
        run(["j1", "j3"])                        # final
        events = [m.type for t, m in sent if t.endswith("/event")]
        assert events[-1] == "done" and u.node == "j3" and u.state == "IDLE"
        u.on_message("stop", b'{"reason":"person"}')
        u.tick(clock[0] + 0.05, (link.x, link.y, link.theta))
        assert u.state == "STOPPED"
    finally:
        time.monotonic = time_monotonic


def test_bridge_with_turn_drive_reaches_the_node():
    """The real-hardware follower (turn-then-drive) plugged into the bridge drives a route
    and reports node_reached + done, never a curve."""
    from fleet_edge.bridge import Unit
    from fleet_shared.graph import Graph
    from fleet_shared.messages import Path

    g = Graph.load()
    sent = []
    link = SimLink(x=g.nodes["dock_A"].x, y=g.nodes["dock_A"].y, theta=0.0)
    u = Unit("fl-01", link, g, lambda topic, msg: sent.append((topic, msg)),
             follower_factory=lambda wps: WaypointFollower(wps, loop=False))
    clock = [0.0]
    time_monotonic = time.monotonic
    time.monotonic = lambda: clock[0]
    link._t = 0.0
    try:
        u.tick(0.0, (link.x, link.y, link.theta))
        u.on_message("path", Path(task_id="t", nodes=g.to_path_nodes(["dock_A", "j1"])).to_json().encode())
        for _ in range(4000):
            clock[0] += 0.05
            u.tick(clock[0], (link.x, link.y, link.theta))
            assert link.left == -link.right or link.left * link.right >= 0
            if u.follower is None:
                break
        events = [m.type for t, m in sent if t.endswith("/event")]
        assert events[-1] == "done" and u.node == "j1" and u.state == "IDLE"
    finally:
        time.monotonic = time_monotonic


def test_bridge_abandons_route_when_follower_refuses():
    """turn_drive says every move would lose the tag: the bridge stops and reports fault."""
    from fleet_edge.bridge import Unit
    from fleet_shared.graph import Graph
    from fleet_shared.messages import Path

    g = Graph.load()
    sent = []
    link = SimLink(x=0.2, y=0.2, theta=0.0)
    tiny = [(0.30, 0.0), (0.31, 0.0), (0.31, 0.01), (0.30, 0.01)]   # no move keeps the tag inside
    u = Unit("fl-01", link, g, lambda topic, msg: sent.append((topic, msg)),
             follower_factory=lambda wps: WaypointFollower(wps, loop=False, border=tiny))
    u.tick(0.0, (0.2, 0.2, 0.0))
    u.on_message("path", Path(task_id="t", nodes=g.to_path_nodes(["dock_A", "j1"])).to_json().encode())
    u.tick(0.05, (0.2, 0.2, 0.0))
    events = [m.type for t, m in sent if t.endswith("/event")]
    assert events[-1] == "fault" and u.state == "STOPPED" and u.follower is None


def test_bridge_pick_runs_fork_then_reports_done():
    from fleet_edge.bridge import Unit
    from fleet_shared.graph import Graph
    from fleet_shared.messages import Path

    g = Graph.load()
    sent, forks = [], []
    link = SimLink(x=g.nodes["slot_A3"].x, y=g.nodes["slot_A3"].y, theta=0.0)
    link.fork = lambda speed, ms: forks.append((speed, ms))
    u = Unit("fl-01", link, g, lambda topic, msg: sent.append((topic, msg)))
    u.tick(0.0, (link.x, link.y, link.theta))
    u.node = "slot_A3"
    u.on_message("path", Path(task_id="t", nodes=g.to_path_nodes(["slot_A3"]), action="pick").to_json().encode())
    u.tick(0.05, (link.x, link.y, link.theta))
    assert forks and forks[0][0] > 0                                  # lifting
    assert not [m for t, m in sent if t.endswith("/event") and m.type == "done"]   # not done yet
    u.tick(0.05 + config.FORK_LIFT_MS / 1000 + 0.01, (link.x, link.y, link.theta))
    assert [m for t, m in sent if t.endswith("/event")][-1].type == "done"
