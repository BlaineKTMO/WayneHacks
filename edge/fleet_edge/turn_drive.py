"""Turn-then-drive waypoint follower: spin in place, then drive dead straight.

Every command is one of exactly two shapes:
  * spin in place:    (-s, s) or (s, -s)
  * straight line:    (v, v * RIGHT_TRIM), forward or backward
Never a curve and never a mecanum strafe. Easier to predict on a small table with a
pole beside it than pure pursuit's arcs.

When the next waypoint is behind the forklift (and ALLOW_REVERSE is on), it backs up
to it instead of turning round, so a two-point route shuttles with almost no turning.

Border (optional): a convex polygon, in world metres at the forklift tag's height, that
the tag must stay inside, normally the camera's view shrunk by a margin (see
border_from_camera). Before every move the follower predicts where the tag will go:
  * a spin swings the chassis about its measured pivot (PIVOT_FWD_M ahead of centre),
    so the tag is checked every few degrees along the arc;
  * a straight leg moves it in a line, so checking the end is enough (the border is
    convex and the start is inside).
It tries face-forward / face-backward and short-way / long-way spins, least turning
first, and takes the first whose whole move keeps the tag inside. If none does, it
doesn't move and reports mode "blocked". If the tag is ever found outside the border
(drift), it stops ("border").

Same interface as pure_pursuit.WaypointFollower (waypoints, index, laps, finished,
target, update), so the runner and overlay don't care which one they get.
"""

from __future__ import annotations

import math

from . import config

SPIN_CHECK_STEP = math.radians(5)


def wrap(a: float) -> float:
    """Wrap an angle to -pi..pi."""
    return (a + math.pi) % (2 * math.pi) - math.pi


def body_to_world(x, y, theta, fwd, left):
    c, s = math.cos(theta), math.sin(theta)
    return x + fwd * c - left * s, y + fwd * s + left * c


def tag_at(x, y, theta):
    """World position of the forklift tag for a chassis pose."""
    return body_to_world(x, y, theta, config.TAG_OFFSET_FORWARD_M, config.TAG_OFFSET_LEFT_M)


def spin_pose(x, y, theta, delta):
    """Chassis pose after spinning by `delta` rad about the measured pivot."""
    px, py = body_to_world(x, y, theta, config.PIVOT_FWD_M, 0.0)
    th = theta + delta
    return px - config.PIVOT_FWD_M * math.cos(th), py - config.PIVOT_FWD_M * math.sin(th), th


def inside(poly, p) -> bool:
    """Point in convex polygon (either winding). No polygon = no limit."""
    if poly is None:
        return True
    sign = 0
    n = len(poly)
    for i in range(n):
        (ax, ay), (bx, by) = poly[i], poly[(i + 1) % n]
        cross = (bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax)
        if cross != 0:
            if sign == 0:
                sign = 1 if cross > 0 else -1
            elif (cross > 0) != (sign > 0):
                return False
    return True


def border_from_camera(localizer, width, height, margin_px=None):
    """The camera frame inset by margin_px, as a world polygon at the tag's height.
    None if the localizer has no table map yet."""
    if localizer.H is None:
        return None
    m = config.TAG_BORDER_MARGIN_PX if margin_px is None else margin_px
    corners = [(m, m), (width - m, m), (width - m, height - m), (m, height - m)]
    return [tuple(localizer.to_world((float(u), float(v)), config.FORKLIFT_TAG_HEIGHT_M))
            for u, v in corners]


class TurnDriveFollower:
    def __init__(self, waypoints: list[tuple[float, float]], loop: bool = True, border=None):
        if not waypoints:
            raise ValueError("need at least one waypoint")
        self.waypoints = waypoints
        self.loop = loop
        self.border = border    # convex polygon the tag must stay in, or None
        self.index = 0          # waypoint we're driving to
        self.laps = 0
        self.finished = False
        self.driving = False    # False = turning (or about to), True = on a straight leg
        self.plan = None        # (reverse, turn_dir) chosen for the current turn
        self.mode = "turn"      # "turn", "forward", "reverse", "done", "blocked", "border"

    @property
    def target(self) -> tuple[float, float]:
        return self.waypoints[self.index]

    # ------------------------------------------------------------- planning
    def spin_is_safe(self, x, y, theta, delta) -> bool:
        """Does the tag stay inside the border all the way through a spin of delta rad?"""
        steps = max(1, math.ceil(abs(delta) / SPIN_CHECK_STEP))
        return all(inside(self.border, tag_at(*spin_pose(x, y, theta, delta * i / steps)))
                   for i in range(1, steps + 1))

    def move_is_safe(self, x, y, theta, delta, reverse, tx, ty) -> bool:
        """Spin by delta about the pivot, then drive straight to (tx, ty): does the tag
        stay inside the border the whole way?"""
        if self.border is None:
            return True
        if not self.spin_is_safe(x, y, theta, delta):
            return False
        sx, sy, _ = spin_pose(x, y, theta, delta)
        heading = math.atan2(ty - sy, tx - sx) + (math.pi if reverse else 0.0)
        return inside(self.border, tag_at(tx, ty, heading))

    def choose_plan(self, x, y, theta, tx, ty):
        """Least-turning (reverse, turn_dir) that keeps the tag inside, or None."""
        bearing = math.atan2(ty - y, tx - x)
        options = []
        for reverse in ([False, True] if config.ALLOW_REVERSE else [False]):
            err = wrap(bearing + (math.pi if reverse else 0.0) - theta)
            options.append((abs(err), reverse, err))
            if abs(err) > config.ALIGN_TOL_RAD:      # the long way round
                long_way = err - math.copysign(2 * math.pi, err)
                options.append((abs(long_way), reverse, long_way))
        for _, reverse, delta in sorted(options, key=lambda o: o[0]):
            if self.move_is_safe(x, y, theta, delta, reverse, tx, ty):
                return reverse, (1 if delta >= 0 else -1)
        return None

    # ------------------------------------------------------------- control
    def update(self, x: float, y: float, theta: float) -> tuple[int, int]:
        """One control step. Returns (left, right)."""
        if self.finished:
            self.mode = "done"
            return 0, 0
        if not inside(self.border, tag_at(x, y, theta)):
            self.mode, self.driving, self.plan = "border", False, None
            return 0, 0
        tx, ty = self.target
        dist = math.hypot(tx - x, ty - y)
        # Also skip a waypoint we're already near before setting off: spinning up to 90 deg
        # to travel a few cm swings the front-mounted tag around for nothing.
        close_enough = not self.driving and dist < config.SKIP_NEAR_M
        if dist < config.ARRIVE_RADIUS_M or close_enough or self._drew_level(x, y, theta, tx, ty):
            self._advance()
            self.driving, self.plan = False, None     # always re-plan for a new waypoint
            if self.finished:
                self.mode = "done"
                return 0, 0
            tx, ty = self.target
            dist = math.hypot(tx - x, ty - y)

        if self.driving:
            reverse = self.mode == "reverse"
            err = wrap(math.atan2(ty - y, tx - x) + (math.pi if reverse else 0.0) - theta)
            # Drifted too far on a straight leg: stop and re-plan the turn. Not in the last
            # SLOWDOWN_M: up close a 1-2 cm sideways miss looks like a big angle.
            if abs(err) > config.REALIGN_RAD and dist >= config.SLOWDOWN_M:
                self.driving, self.plan = False, None
            else:
                return self._straight(dist, reverse)

        # Turning: pick (once per turn) which way to face and which way to spin.
        if self.plan is None:
            self.plan = self.choose_plan(x, y, theta, tx, ty)
            if self.plan is None:
                self.mode = "blocked"
                return 0, 0
        reverse, turn_dir = self.plan
        err = wrap(math.atan2(ty - y, tx - x) + (math.pi if reverse else 0.0) - theta)
        if abs(err) <= config.ALIGN_TOL_RAD:
            self.driving, self.plan = True, None
            self.mode = "reverse" if reverse else "forward"
            return self._straight(dist, reverse)
        # Remaining angle the chosen way round; slow down as it lines up so the camera
        # lag doesn't make it overshoot.
        remaining = err if (err > 0) == (turn_dir > 0) else err + turn_dir * 2 * math.pi
        frac = min(1.0, abs(remaining) / math.radians(30))
        s = round(config.TURN_MIN_SPEED + (config.TURN_SPEED - config.TURN_MIN_SPEED) * frac)
        self.mode = "turn"
        return (-s, s) if turn_dir > 0 else (s, -s)     # turn_dir > 0: anticlockwise (left)

    @staticmethod
    def _straight(dist, reverse):
        # Same speed both sides: a straight line. Ease off near the target.
        # RIGHT_TRIM evens out the stronger right side; never trim below MIN_SPEED (stall).
        v = max(config.MAX_SPEED * min(1.0, dist / config.SLOWDOWN_M), config.MIN_SPEED)
        left, right = round(v), round(max(v * config.RIGHT_TRIM, config.MIN_SPEED))
        return (-left, -right) if reverse else (left, right)

    def _drew_level(self, x, y, theta, tx, ty) -> bool:
        """On a straight leg, the waypoint is now beside or behind the direction of travel
        and close sideways: count it as reached rather than spinning back for it."""
        if not self.driving:
            return False
        dx, dy = tx - x, ty - y
        ahead = dx * math.cos(theta) + dy * math.sin(theta)       # + = in front of the chassis
        side = abs(-dx * math.sin(theta) + dy * math.cos(theta))
        if self.mode == "reverse":
            ahead = -ahead
        return ahead <= 0.0 and side < config.ARRIVE_SIDE_M

    def _advance(self) -> None:
        self.index += 1
        if self.index >= len(self.waypoints):
            self.laps += 1
            if self.loop:
                self.index = 0
            else:
                self.index -= 1
                self.finished = True
