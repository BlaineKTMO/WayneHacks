"""Tests that run without a camera or Free-WiLi. Run:  uv run pytest edge"""

import math

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


def test_backs_up_to_target_behind():
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
    f = WaypointFollower([(0.13, 0.13), (0.40, 0.13)], loop=True)
    left, right = f.update(0.159, 0.071 + 0.03, 0.09)
    assert f.index == 1


# A border box (tag positions, metres) for the planner tests.
BOX = [(0.0, 0.0), (0.5, 0.0), (0.5, 0.4), (0.0, 0.4)]


def test_spin_check_follows_the_tag_round_the_pivot():
    """The tag sits just right of the pivot. With the border's bottom edge a hair below
    the tag, a clockwise spin dips the tag under it; an anticlockwise one lifts it."""
    from fleet_edge.turn_drive import TurnDriveFollower, tag_at
    x, y, th = 0.19, 0.20, 0.0
    tag_y = tag_at(x, y, th)[1]
    box = [(0.0, tag_y - 0.0003), (0.6, tag_y - 0.0003), (0.6, 0.6), (0.0, 0.6)]
    f = TurnDriveFollower([(0.5, 0.3)], border=box)
    assert not f.spin_is_safe(x, y, th, -math.radians(30))
    assert f.spin_is_safe(x, y, th, math.radians(30))


def test_planner_turns_the_safe_way():
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
