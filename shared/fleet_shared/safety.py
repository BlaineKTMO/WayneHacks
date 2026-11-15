"""Edge safety guard: stop a forklift when a person is in or near its path. Runs on the edge
(bridge.py and the simulated fleet), never in the cloud, so it works when the network doesn't.

    hazards = Hazards()                 # one per edge process
    hazards.on_detections(msg_dict)     # every warehouse/detections message
    guard = Guard()                     # one per forklift
    action = guard.update(pose, upcoming_nodes, hazards)   # "stop" | "clear" | None
"""

from __future__ import annotations

import math
import time

PERSON_RADIUS_M = 0.12      # stop if a person is this close to the forklift or its next stretch
LOOKAHEAD_NODES = 2         # how much of the route ahead counts as "in the path"
STALE_S = 1.5               # real detections older than this are ignored (virtual ones persist)
CLASSES = {"person", "obstacle"}


def _seg_dist(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    if dx == dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


class Hazards:
    """Latest detections per source (model_version). 'virtual-*' sources (demo) never go stale."""

    def __init__(self):
        self.by_source: dict[str, tuple[float, list[dict]]] = {}

    def on_detections(self, msg: dict) -> None:
        objs = [o for o in msg.get("objects", []) if o.get("cls") in CLASSES]
        self.by_source[msg.get("model_version", "?")] = (time.monotonic(), objs)

    def active(self) -> list[dict]:
        now = time.monotonic()
        out = []
        for src, (t, objs) in self.by_source.items():
            if src.startswith("virtual") or now - t < STALE_S:
                out += objs
        return out


class Guard:
    def __init__(self):
        self.holding = False

    def update(self, pose: tuple[float, float, float] | None, upcoming: list[dict], hazards: Hazards) -> str | None:
        """pose = (x, y, theta); upcoming = next route nodes [{x, y}, ...]. Returns a transition or None."""
        danger = pose is not None and self.in_path(pose, upcoming, hazards.active())
        if danger and not self.holding:
            self.holding = True
            return "stop"
        if self.holding and not danger:
            self.holding = False
            return "clear"
        return None

    @staticmethod
    def in_path(pose, upcoming: list[dict], people: list[dict]) -> bool:
        x, y = pose[0], pose[1]
        pts = [(x, y)] + [(n["x"], n["y"]) for n in upcoming[:LOOKAHEAD_NODES]]
        for p in people:
            if math.hypot(p["x"] - x, p["y"] - y) < PERSON_RADIUS_M:
                return True
            for (ax, ay), (bx, by) in zip(pts, pts[1:]):  # noqa: B905
                if _seg_dist(p["x"], p["y"], ax, ay, bx, by) < PERSON_RADIUS_M:
                    return True
        return False
