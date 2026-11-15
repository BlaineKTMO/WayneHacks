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


def test_any_forklift_tag_in_view_is_enough(monkeypatch):
    """Two tags: the follower keeps going while EITHER is inside its border, stops only
    when both are out, and plans moves that keep at least one in view."""
    from fleet_edge import config
    from fleet_edge.turn_drive import TurnDriveFollower, in_view, tags_at
    monkeypatch.setattr(config, "TAG_MOUNTS", {
        13: dict(height=0.08, fwd=-0.08, left=0.0, heading=0.0),
        16: dict(height=0.10, fwd=0.08, left=0.0, heading=0.0)})
    box = [(0.0, 0.0), (0.20, 0.0), (0.20, 0.30), (0.0, 0.30)]   # x <= 0.20 is in view
    border = {13: box, 16: box}
    # Facing +x at x=0.20: tag 13 (behind) at 0.12 is in, tag 16 (ahead) at 0.28 is out.
    pts = tags_at(0.20, 0.15, 0.0)
    assert pts[13][0] < 0.20 < pts[16][0]
    assert in_view(border, 0.20, 0.15, 0.0)
    assert not in_view(border, 0.30, 0.15, 0.0)          # both tags out
    f = TurnDriveFollower([(0.24, 0.15)], loop=False, border=border)
    f.update(0.20, 0.15, 0.0)
    assert f.mode != "border"
    f = TurnDriveFollower([(0.24, 0.15)], loop=False, border=border)
    assert f.update(0.30, 0.15, 0.0) == (0, 0) and f.mode == "border"
    # A leg that runs every tag out of view part-way is refused even if the end is fine
    # for a different tag (13 in at the start, out in the middle; 16 only in at the end).
    gap = {13: [(0.0, 0.0), (0.10, 0.0), (0.10, 0.3), (0.0, 0.3)],
           16: [(0.40, 0.0), (0.60, 0.0), (0.60, 0.3), (0.40, 0.3)]}
    f = TurnDriveFollower([(0.40, 0.15)], loop=False, border=gap)
    assert not f.move_is_safe(0.10, 0.15, 0.0, 0.0, False, 0.40, 0.15)


def test_radio_hub_routes_lines_per_forklift():
    """One base station, two forklifts: commands carry "<id>:", telemetry is routed back
    by its prefix (forklift 1 may answer unprefixed)."""
    from fleet_edge.freewili_link import RadioHub, RadioLink, split_addr
    assert split_addr("2:T,IDLE,0.00") == (2, "T,IDLE,0.00")
    assert split_addr("T,IDLE,0.00") == (1, "T,IDLE,0.00")
    hub = RadioHub("loop://")
    one, two = RadioLink(hub, 1), RadioLink(hub, 2)
    two.wheels(45, 38)
    one.stop("x")
    assert hub.ser.read(100) == b"2:W,45,38\n1:S,x\n"
    hub.ser.write(b"T,DRIVING,0.00\n2:T,IDLE,0.00\n2:T,NO_RADIO,0.00\n")
    assert one.poll().state == "DRIVING"
    assert two.poll().state == "NO_RADIO"
    two.close()
    one.close()
    assert not hub.ser.is_open


def test_border_counts_only_this_forklifts_tags(monkeypatch):
    """fl-02's tags in view must not keep fl-01 'in view'."""
    from fleet_edge import config
    from fleet_edge.turn_drive import in_view, tags_at
    monkeypatch.setattr(config, "FORKLIFT_TAGS", {13: "fl-01", 20: "fl-02"})
    monkeypatch.setattr(config, "TAG_MOUNTS", {
        13: dict(height=0.08, fwd=0.0, left=0.0, heading=0.0),
        20: dict(height=0.08, fwd=0.5, left=0.0, heading=0.0)})
    assert set(tags_at(0, 0, 0, "fl-01")) == {13} and set(tags_at(0, 0, 0, "fl-02")) == {20}
    box = [(0.4, -0.1), (0.6, -0.1), (0.6, 0.1), (0.4, 0.1)]    # only x = 0.5 is in view
    border = {13: box, 20: box}
    assert not in_view(border, 0, 0, 0, "fl-01")
    assert in_view(border, 0, 0, 0, "fl-02")
