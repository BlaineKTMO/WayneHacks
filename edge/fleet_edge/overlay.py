"""Draw the demo overlay: waypoints, path, current target, forklift heading, status text."""

from __future__ import annotations

import math

import cv2
import numpy as np

from . import config

GREEN, RED, YELLOW, WHITE, CYAN = (80, 220, 80), (60, 60, 230), (0, 220, 255), (255, 255, 255), (230, 200, 60)


class WorldCanvas:
    """Maps world metres onto an image. Used in --sim (blank table) and for real frames
    when we only have a homography (pass world_to_px)."""

    def __init__(self, world_to_px=None, scale=600, margin=40):
        w, h = 1.5, 1.0
        self.scale, self.margin = scale, margin
        self.size = (int(h * scale + 2 * margin), int(w * scale + 2 * margin))
        self.world_to_px = world_to_px or (
            lambda x, y: (int(margin + x * scale), int(self.size[0] - margin - y * scale)))

    def blank(self) -> np.ndarray:
        img = np.full((*self.size, 3), 35, np.uint8)
        for cx, cy in config.CORNER_TAGS.values():
            cv2.rectangle(img, self.world_to_px(cx - .02, cy - .02), self.world_to_px(cx + .02, cy + .02), WHITE, 1)
        return img


def draw(img, canvas: WorldCanvas, pose, follower, status: str, ok: bool):
    P = canvas.world_to_px
    wps = follower.waypoints
    for i, (x, y) in enumerate(wps):
        a, b = P(x, y), P(*wps[(i + 1) % len(wps)])
        cv2.line(img, a, b, CYAN, 1)
        cv2.circle(img, a, 6, YELLOW if i == follower.index else CYAN, -1 if i == follower.index else 1)
    if pose is not None:
        c = P(pose.x, pose.y)
        tip = P(pose.x + 0.06 * math.cos(pose.theta), pose.y + 0.06 * math.sin(pose.theta))
        cv2.circle(img, c, 10, GREEN if ok else RED, 2)
        cv2.arrowedLine(img, c, tip, GREEN if ok else RED, 2, tipLength=0.4)
        cv2.line(img, c, P(*follower.target), YELLOW, 1)
    cv2.putText(img, status, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, GREEN if ok else RED, 2)
    cv2.putText(img, f"lap {follower.laps}/{config.LAPS}  wp {follower.index}", (10, 50),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, WHITE, 1)
    return img
