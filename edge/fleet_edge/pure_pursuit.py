"""Pure pursuit for a 2-wheel (differential drive) forklift.

Given where the forklift is (x, y, theta) and where it should go next, return
(left, right) wheel commands in -100..100. No hardware here, so it's easy to unit test.
"""

from __future__ import annotations

import math

from . import config


def wrap(a: float) -> float:
    """Wrap an angle to -pi..pi."""
    return (a + math.pi) % (2 * math.pi) - math.pi


class WaypointFollower:
    def __init__(self, waypoints: list[tuple[float, float]], loop: bool = True):
        if not waypoints:
            raise ValueError("need at least one waypoint")
        self.waypoints = waypoints
        self.loop = loop
        self.index = 0          # waypoint we're driving to
        self.laps = 0
        self.finished = False

    @property
    def target(self) -> tuple[float, float]:
        return self.waypoints[self.index]

    def update(self, x: float, y: float, theta: float) -> tuple[int, int]:
        """One control step. Returns (left, right)."""
        if self.finished:
            return 0, 0
        tx, ty = self.target
        dist = math.hypot(tx - x, ty - y)

        if dist < config.ARRIVE_RADIUS_M:
            self._advance()
            if self.finished:
                return 0, 0
            tx, ty = self.target
            dist = math.hypot(tx - x, ty - y)

        # Angle between where we're facing and where the target is.
        alpha = wrap(math.atan2(ty - y, tx - x) - theta)

        # Way off-heading: spin in place first.
        if abs(alpha) > config.TURN_IN_PLACE_RAD:
            s = config.MIN_SPEED + 5
            return (-s, s) if alpha > 0 else (s, -s)

        # Pure pursuit curvature: k = 2 sin(alpha) / L.
        lookahead = max(config.LOOKAHEAD_M, min(dist, 3 * config.LOOKAHEAD_M))
        k = 2 * math.sin(alpha) / lookahead
        # Slow down near the target so we don't overshoot the 3 cm radius.
        v = config.MAX_SPEED * min(1.0, dist / (3 * config.LOOKAHEAD_M))
        v = max(v, config.MIN_SPEED)
        left = v * (1 - k * config.WHEEL_BASE_M / 2)
        right = v * (1 + k * config.WHEEL_BASE_M / 2)
        # Keep the ratio but don't exceed 100.
        m = max(abs(left), abs(right), 100) / 100
        return round(left / m), round(right / m)

    def _advance(self) -> None:
        self.index += 1
        if self.index >= len(self.waypoints):
            self.laps += 1
            if self.loop:
                self.index = 0
            else:
                self.index -= 1
                self.finished = True
