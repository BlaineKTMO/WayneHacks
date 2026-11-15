"""Tests that run without a camera or Free-WiLi. Run:  uv run pytest edge"""

import math

from fleet_edge import config
from fleet_edge.freewili_link import SimLink, format_stop, format_wheels, parse_line
from fleet_edge.pure_pursuit import WaypointFollower


def test_protocol():
    assert format_wheels(150, -42.4) == "W,100,-42\n"
    assert format_stop("lost,tag") == "S,lost tag\n"
    t = parse_line("T,DRIVING,7.41\r\n")
    assert t.state == "DRIVING" and t.battery_v == 7.41
    assert parse_line("garbage") is None
    assert parse_line("T,IDLE,notanumber") is None


def test_turns_toward_target():
    f = WaypointFollower([(1.0, 1.0)], loop=False)
    left, right = f.update(0.5, 0.5, 0.0)       # target is up-left -> turn left
    assert right > left


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
            left, right = f.update(link.x, link.y, link.theta)
            link.wheels(left, right)
            clock[0] += dt
            t += dt
    finally:
        time.monotonic = time_monotonic
    assert f.laps >= 1, f"didn't finish a lap in {t:.0f}s, stuck at wp {f.index} pos=({link.x:.2f},{link.y:.2f})"
    assert math.isfinite(link.x)
