"""Camera frame -> forklift poses in metres.

Pipeline: undistort -> detect AprilTags -> corner tags give a homography (pixels -> metres)
-> each forklift tag becomes (x, y, theta) -> smooth with a simple filter.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from . import config


@dataclass
class TagSighting:
    tag_id: int
    center_px: tuple[float, float]
    corners_px: np.ndarray       # 4x2, order: as returned by the detector
    heading_px: float            # tag "up" direction in image, radians


@dataclass
class ForkliftPose:
    forklift_id: str
    x: float
    y: float
    theta: float
    conf: float
    t: float                     # time.monotonic() when seen


class Camera:
    """Webcam with optional undistortion from step2_calibrate."""

    def __init__(self, index: int = config.CAMERA_INDEX):
        self.cap = cv2.VideoCapture(index)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAMERA_WIDTH)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAMERA_HEIGHT)
        if not self.cap.isOpened():
            raise RuntimeError(f"cannot open camera {index}; try CAMERA_INDEX = 1 in config.py")
        self.K = self.dist = None
        calib = Path(config.CALIBRATION_FILE)
        if calib.exists():
            d = np.load(calib)
            self.K, self.dist = d["K"], d["dist"]
        else:
            print(f"[camera] no calibration at {calib}; running without undistortion")

    def read(self) -> np.ndarray:
        ok, frame = self.cap.read()
        if not ok:
            raise RuntimeError("camera read failed (unplugged?)")
        if self.K is not None:
            frame = cv2.undistort(frame, self.K, self.dist)
        return frame

    def close(self):
        self.cap.release()


class TagDetector:
    def __init__(self):
        from pupil_apriltags import Detector
        self.det = Detector(families=config.TAG_FAMILY, quad_decimate=1.0)

    def detect(self, frame: np.ndarray) -> list[TagSighting]:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        out = []
        for d in self.det.detect(gray):
            c = d.corners  # (4,2): bottom-left, bottom-right, top-right, top-left of the tag
            # Tag "up" = from bottom edge midpoint to top edge midpoint.
            bottom, top = (c[0] + c[1]) / 2, (c[2] + c[3]) / 2
            heading = math.atan2(top[1] - bottom[1], top[0] - bottom[0])
            out.append(TagSighting(d.tag_id, tuple(d.center), c, heading))
        return out


class Localizer:
    """Turns tag sightings into forklift poses in world metres."""

    def __init__(self):
        self.H: np.ndarray | None = None       # pixels -> metres
        self.filtered: dict[str, ForkliftPose] = {}

    def update_homography(self, sightings: list[TagSighting]) -> bool:
        seen = {s.tag_id: s for s in sightings if s.tag_id in config.CORNER_TAGS}
        if len(seen) < 4:
            return self.H is not None  # keep the last good one
        px = np.array([seen[i].center_px for i in sorted(seen)], dtype=np.float32)
        world = np.array([config.CORNER_TAGS[i] for i in sorted(seen)], dtype=np.float32)
        self.H, _ = cv2.findHomography(px, world)
        return True

    def to_world(self, px: tuple[float, float]) -> tuple[float, float]:
        p = self.H @ np.array([px[0], px[1], 1.0])
        return float(p[0] / p[2]), float(p[1] / p[2])

    def process(self, sightings: list[TagSighting]) -> dict[str, ForkliftPose]:
        """Returns the latest filtered pose for every forklift seen this frame."""
        if not self.update_homography(sightings):
            return {}
        now = time.monotonic()
        result = {}
        for s in sightings:
            fid = config.FORKLIFT_TAGS.get(s.tag_id)
            if not fid:
                continue
            x, y = self.to_world(s.center_px)
            # Heading: map a point a little "up" the tag through the homography too, so
            # camera tilt doesn't skew the angle.
            ux = s.center_px[0] + 20 * math.cos(s.heading_px)
            uy = s.center_px[1] + 20 * math.sin(s.heading_px)
            hx, hy = self.to_world((ux, uy))
            theta = math.atan2(hy - y, hx - x) + config.TAG_HEADING_OFFSET
            result[fid] = self._filter(ForkliftPose(fid, x, y, theta, 1.0, now))
        return result

    def _filter(self, p: ForkliftPose) -> ForkliftPose:
        """Exponential smoothing (alpha filter). Angles are blended on the unit circle."""
        prev = self.filtered.get(p.forklift_id)
        a = config.POSE_ALPHA
        if prev and p.t - prev.t < 0.5:
            x = a * p.x + (1 - a) * prev.x
            y = a * p.y + (1 - a) * prev.y
            theta = math.atan2(a * math.sin(p.theta) + (1 - a) * math.sin(prev.theta),
                               a * math.cos(p.theta) + (1 - a) * math.cos(prev.theta))
            p = ForkliftPose(p.forklift_id, x, y, theta, p.conf, p.t)
        self.filtered[p.forklift_id] = p
        return p
