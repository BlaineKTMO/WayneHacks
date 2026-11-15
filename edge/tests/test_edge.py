"""Tests that run without a camera or Free-WiLi. Run:  uv run pytest edge"""

import math
import time

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
