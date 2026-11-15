"""Turn-then-drive waypoint follower: spin in place, then drive dead straight.

Every command is one of exactly two shapes:
  * spin in place:    (-s, s) or (s, -s)
  * straight line:    (v, v * RIGHT_TRIM), forward or backward
Never a curve and never a mecanum strafe. Easier to predict on a small table with a
pole beside it than pure pursuit's arcs.

When the next waypoint is behind the forklift (and ALLOW_REVERSE is on), it backs up
to it instead of turning round, so a two-point route shuttles with almost no turning.

Border (optional): the camera's view shrunk by a margin, as a world polygon for each
forklift tag at that tag's height (see border_from_camera). The forklift may be
anywhere as long as AT LEAST ONE of its tags (TAG_MOUNTS) is inside its polygon.
Before every move the follower predicts where the tags will go:
  * a spin swings the chassis about its measured pivot (PIVOT_FWD_M ahead of centre),
    so the tags are checked every few degrees along the arc;
  * a straight leg is checked every LEG_CHECK_STEP_M.
It tries face-forward / face-backward and short-way / long-way spins, least turning
first, and takes the first whose whole move keeps a tag in view. If none does, it
doesn't move and reports mode "blocked". If every tag is ever found outside the border
(drift), it stops ("border").

Steering point: the chassis turns about a point PIVOT_FWD_M ahead of its centre (it is
front-heavy), so that point, not the centre, is what's steered to the waypoints. A spin
then really is "in place" for navigation: the bearing to the target doesn't move while
it turns. Steering the centre instead made the centre swing past a nearby target during
a re-aim and the follower chase it round by 135 deg (real run 8).

Same interface as pure_pursuit.WaypointFollower (waypoints, index, laps, finished,
target, update), so the runner and overlay don't care which one they get.
"""

from __future__ import annotations

import math

from . import config

SPIN_CHECK_STEP = math.radians(5)
LEG_CHECK_STEP_M = 0.02


def wrap(a: float) -> float:
    """Wrap an angle to -pi..pi."""
    return (a + math.pi) % (2 * math.pi) - math.pi


def body_to_world(x, y, theta, fwd, left):
    c, s = math.cos(theta), math.sin(theta)
    return x + fwd * c - left * s, y + fwd * s + left * c


def tag_at(x, y, theta):
    """World position of the forklift tag for a chassis pose."""
    return body_to_world(x, y, theta, config.TAG_OFFSET_FORWARD_M, config.TAG_OFFSET_LEFT_M)


def tags_at(x, y, theta, fid=None):
    """World position of each of forklift `fid`'s tags (default the hero) for a chassis
    pose: {tag: (x, y)}."""
    fid = fid or config.HERO_FORKLIFT
    return {t: body_to_world(x, y, theta, m["fwd"], m["left"]) for t, m in config.TAG_MOUNTS.items()
            if config.FORKLIFT_TAGS.get(t) == fid}


def in_view(border, x, y, theta, fid=None) -> bool:
    """Would the camera still see forklift `fid` at this pose? True if ANY of its tags is
    inside its border. `border` is {tag: polygon} (border_from_camera), a single polygon
    (tag 13 only, older callers/tests), or None (no limit)."""
    if border is None:
        return True
    if isinstance(border, dict):
        pts = tags_at(x, y, theta, fid)
        return any(inside(poly, pts[t]) for t, poly in border.items() if t in pts)
    return inside(border, tag_at(x, y, theta))


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
    """The camera frame inset by margin_px, as a world polygon for each forklift tag
    (each at its own height): {tag: polygon}. None if the localizer has no table map yet."""
    if localizer.H is None:
        return None
    m = config.TAG_BORDER_MARGIN_PX if margin_px is None else margin_px
    corners = [(m, m), (width - m, m), (width - m, height - m), (m, height - m)]
    return {t: [tuple(localizer.to_world((float(u), float(v)), mount["height"])) for u, v in corners]
            for t, mount in config.TAG_MOUNTS.items()}


class TurnDriveFollower:
    def __init__(self, waypoints: list[tuple[float, float]], loop: bool = True, border=None,
                 fid: str | None = None):
        if not waypoints:
            raise ValueError("need at least one waypoint")
        self.waypoints = waypoints
        self.loop = loop
        self.border = border    # {tag: polygon} one tag must stay in (or one polygon, or None)
        self.fid = fid or config.HERO_FORKLIFT   # whose tags count for the border
        self.index = 0          # waypoint we're driving to
        self.laps = 0
        self.finished = False
        self.driving = False    # False = turning (or about to), True = on a straight leg
        self.plan = None        # (reverse, turn_dir) chosen for the current turn
        self.spin_from = None   # where the current spin started (drift guard)
        self.drifts = 0         # drift re-plans on the way to the current waypoint
        self.moved = False      # any motion commanded yet (SKIP_NEAR_M only applies before)
        self.mode = "turn"      # "turn", "forward", "reverse", "done", "blocked", "border", "drift", "drift_stop"

    @property
    def target(self) -> tuple[float, float]:
        return self.waypoints[self.index]

    # ------------------------------------------------------------- planning
    def spin_is_safe(self, x, y, theta, delta) -> bool:
        """Does a tag stay inside the border all the way through a spin of delta rad?"""
        steps = max(1, math.ceil(abs(delta) / SPIN_CHECK_STEP))
        return all(in_view(self.border, *spin_pose(x, y, theta, delta * i / steps), self.fid)
                   for i in range(1, steps + 1))

    def move_is_safe(self, x, y, theta, delta, reverse, tx, ty) -> bool:
        """Spin by delta about the pivot, then drive straight to (tx, ty): does the tag
        stay inside the border the whole way?"""
        if self.border is None:
            return True
        if not self.spin_is_safe(x, y, theta, delta):
            return False
        px, py = body_to_world(x, y, theta, config.PIVOT_FWD_M, 0.0)   # unmoved by the spin
        heading = math.atan2(ty - py, tx - px) + (math.pi if reverse else 0.0)
        # The leg ends with the PIVOT on the target, so the centre sits behind it.
        cx = tx - config.PIVOT_FWD_M * math.cos(heading)
        cy = ty - config.PIVOT_FWD_M * math.sin(heading)
        # Sample the leg: with several tags, the one in view at the start may not be the
        # one in view at the end, so checking only the end is no longer enough.
        sx, sy, _ = spin_pose(x, y, theta, delta)
        steps = max(1, math.ceil(math.hypot(cx - sx, cy - sy) / LEG_CHECK_STEP_M))
        return all(in_view(self.border, sx + (cx - sx) * i / steps, sy + (cy - sy) * i / steps, heading, self.fid)
                   for i in range(1, steps + 1))

    def choose_plan(self, x, y, theta, tx, ty):
        """Least-turning (reverse, turn_dir) that keeps the tag inside, or None.
        (x, y, theta) is the chassis CENTRE; the bearing is taken from the pivot."""
        px, py = body_to_world(x, y, theta, config.PIVOT_FWD_M, 0.0)
        bearing = math.atan2(ty - py, tx - px)
        options = []
        for reverse in ([False, True] if config.ALLOW_REVERSE else [False]):
            err = wrap(bearing + (math.pi if reverse else 0.0) - theta)
            options.append((abs(err), reverse, err))
            if abs(err) > config.ALIGN_TOL_RAD:      # the long way round
                long_way = err - math.copysign(2 * math.pi, err)
                options.append((abs(long_way), reverse, long_way))
        for _, reverse, delta in sorted(options, key=lambda o: o[0]):
            if config.SPIN_LEFT_ONLY and delta < -config.ALIGN_TOL_RAD:
                continue        # clockwise spins slide on this chassis; take the long way left
            if self.move_is_safe(x, y, theta, delta, reverse, tx, ty):
                return reverse, (1 if delta >= 0 else -1)
        return None

    # ------------------------------------------------------------- control
    def update(self, x: float, y: float, theta: float) -> tuple[int, int]:
        """One control step. Returns (left, right)."""
        cmd = self._update(x, y, theta)
        if cmd != (0, 0):
            self.moved = True
        return cmd

    def _update(self, x: float, y: float, theta: float) -> tuple[int, int]:
        if self.finished:
            if self.mode != "drift_stop":
                self.mode = "done"
            return 0, 0
        if not in_view(self.border, x, y, theta, self.fid):
            self.mode, self.driving, self.plan = "border", False, None
            return 0, 0
        cx, cy = x, y                                    # chassis centre (for the border)
        x, y = body_to_world(cx, cy, theta, config.PIVOT_FWD_M, 0.0)   # steer the pivot
        tx, ty = self.target
        dist = math.hypot(tx - x, ty - y)
        # Skip a waypoint we're already standing near at the very start: spinning up to
        # 90 deg to travel a few cm swings the front-mounted tag around for nothing. Only
        # before the first move: mid-run, a re-aim near a waypoint must still reach it
        # (run 7 turned back 38 mm short because this applied there too).
        close_enough = not self.moved and dist < config.SKIP_NEAR_M
        if dist < config.ARRIVE_RADIUS_M or close_enough or self._drew_level(x, y, theta, tx, ty):
            self._advance()
            self.driving, self.plan = False, None     # always re-plan for a new waypoint
            self.drifts = 0
            if self.finished:
                self.mode = "done"
                return 0, 0
            tx, ty = self.target
            dist = math.hypot(tx - x, ty - y)

        if self.driving:
            reverse = self.mode == "reverse"
            err = wrap(math.atan2(ty - y, tx - x) + (math.pi if reverse else 0.0) - theta)
            # Drifted too far on a straight leg: stop and re-plan the turn. Not in the last
            # NO_REALIGN_M: up close a 2-3 cm sideways miss looks like a big angle, and the
            # leg finishes fine without it (_drew_level accepts ARRIVE_SIDE_M sideways).
            if abs(err) > config.REALIGN_RAD and dist >= config.NO_REALIGN_M:
                self.driving, self.plan = False, None
            else:
                return self._straight(dist, reverse)

        # Spin drift guard: a spin should leave the turning point where it was. If it has
        # slid SPIN_DRIFT_M (a wheel slipping, a tugged cable), stop and re-plan from where
        # it really is, so the border check runs again. Real loop 2 slid 35 cm in one spin.
        if self.plan is not None and self.spin_from is not None and \
                math.hypot(x - self.spin_from[0], y - self.spin_from[1]) > config.SPIN_DRIFT_M:
            self.plan, self.spin_from = None, None
            self.drifts += 1
            # Re-planning keeps spinning the same way, so a chassis that slides keeps
            # creeping (real loop 5: 15 cm toward the pole). Give up after MAX_DRIFTS.
            self.mode = "drift_stop" if self.drifts > config.MAX_DRIFTS else "drift"
            if self.mode == "drift_stop":
                self.finished = True
            return 0, 0
        # Turning: pick (once per turn) which way to face and which way to spin.
        if self.plan is None:
            self.spin_from = (x, y)
            self.plan = self.choose_plan(cx, cy, theta, tx, ty)
            if self.plan is None:
                self.mode = "blocked"
                return 0, 0
        reverse, turn_dir = self.plan
        err = wrap(math.atan2(ty - y, tx - x) + (math.pi if reverse else 0.0) - theta)
        if abs(err) <= config.ALIGN_TOL_RAD:
            self.driving, self.plan, self.spin_from = True, None, None
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
