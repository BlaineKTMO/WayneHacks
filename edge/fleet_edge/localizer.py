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


def open_camera(index: int = config.CAMERA_INDEX) -> cv2.VideoCapture:
    """Open the webcam at CAMERA_WIDTH x CAMERA_HEIGHT.

    Asks for MJPG first: most USB webcams (the eMeet C950 included) only offer HD
    sizes as MJPG, and without this OpenCV silently falls back to 640x480."""
    cap = cv2.VideoCapture(index, cv2.CAP_V4L2) if hasattr(cv2, "CAP_V4L2") else cv2.VideoCapture(index)
    if not cap.isOpened():
        cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open camera {index}: wrong CAMERA_INDEX in config.py, "
                           "or another program (a preview window, ffplay, a video call) is using it")
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAMERA_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAMERA_HEIGHT)
    if config.CAMERA_EXPOSURE is not None:
        # V4L2 backend: AUTO_EXPOSURE 1 = manual, 3 = auto (aperture priority).
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)
        cap.set(cv2.CAP_PROP_EXPOSURE, config.CAMERA_EXPOSURE)
        cap.set(cv2.CAP_PROP_GAIN, config.CAMERA_GAIN)
    else:
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 3)
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if (w, h) != (config.CAMERA_WIDTH, config.CAMERA_HEIGHT):
        print(f"[camera] asked for {config.CAMERA_WIDTH}x{config.CAMERA_HEIGHT}, got {w}x{h}")
    return cap


class Camera:
    """Webcam with optional undistortion from step2_calibrate."""

    def __init__(self, index: int = config.CAMERA_INDEX):
        self.cap = open_camera(index)
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
            frame = cv2.undistort(frame, self.K, self.dist)   # same K describes the result
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
    """Turns tag sightings into forklift poses in world metres.

    Corner tags lie on the table, but the forklift's tag rides FORKLIFT_TAG_HEIGHT_M above
    it. A camera sees a raised tag from an angle, so mapping it as if it were on the table
    puts it too far from the point under the camera. With a lens calibration (K), the
    camera's position is found from how big each corner tag looks (distance = focal
    length x tag size / apparent size; four distances fix the position), and the raised
    tag is pulled toward the camera by similar triangles.

    Not solvePnP on the four corner-tag centres: with four coplanar points it has two
    near-equal answers and flipped between them frame to frame here ((414, 23, 645) vs
    (120, 289, 676) mm), which moved a 271 mm-high tag by up to ~17 cm. The size method
    gave (258, 135, 616) mm with 2.6 mm residuals, matching four equal-looking corner tags.
    """

    def __init__(self, K: np.ndarray | None = None):
        self.K = K                              # camera matrix of the UNDISTORTED image
        self.H: np.ndarray | None = None       # pixels -> metres, table plane
        self.cam_pos: np.ndarray | None = None  # camera centre in world metres (z = height)
        self.filtered: dict[str, ForkliftPose] = {}
        self._saved_at = 0.0
        self._load_map()
        if K is None and config.FORKLIFT_TAG_HEIGHT_M > 0:
            print(f"[localizer] no lens calibration: forklift tags are mapped as if on the table, "
                  f"ignoring their {config.FORKLIFT_TAG_HEIGHT_M * 1000:.0f} mm height (positions will be off)")

    def update_homography(self, sightings: list[TagSighting]) -> bool:
        seen = {s.tag_id: s for s in sightings if s.tag_id in config.CORNER_TAGS}
        if len(seen) < 4:
            return self.H is not None  # keep the last good one (or the one loaded from disk)
        px = np.array([seen[i].center_px for i in sorted(seen)], dtype=np.float64)
        world = np.array([config.CORNER_TAGS[i] for i in sorted(seen)], dtype=np.float64)
        self.H, _ = cv2.findHomography(px, world)
        if self.K is not None:
            c = self._camera_from_tag_sizes([seen[i] for i in sorted(seen)], world)
            if c is not None:   # smooth: one frame's tag sizes are a little noisy
                self.cam_pos = c if self.cam_pos is None else 0.9 * self.cam_pos + 0.1 * c
        if time.monotonic() - self._saved_at > 10:
            self._save_map()
        return True

    def _camera_from_tag_sizes(self, tags, world) -> np.ndarray | None:
        """Camera centre (world metres) from the corner tags' apparent sizes."""
        f = (self.K[0, 0] + self.K[1, 1]) / 2
        sides = np.array([np.mean([np.linalg.norm(np.subtract(t.corners_px[i], t.corners_px[(i + 1) % 4]))
                                   for i in range(4)]) for t in tags])
        d = f * config.TAG_SIZE_M / sides
        P = np.hstack([world, np.zeros((len(world), 1))])
        C = np.array([P[:, 0].mean(), P[:, 1].mean(), d.mean()])   # start above the middle
        for _ in range(30):     # Gauss-Newton on |P - C| = d
            v = C - P
            r = np.linalg.norm(v, axis=1)
            C = C - np.linalg.lstsq(v / r[:, None], r - d, rcond=None)[0]
        return C if np.isfinite(C).all() and C[2] > 0.1 else None

    def _save_map(self) -> None:
        try:
            Path(config.TABLE_MAP_FILE).parent.mkdir(parents=True, exist_ok=True)
            extra = {} if self.cam_pos is None else {"cam_from_sizes": self.cam_pos}
            np.savez(config.TABLE_MAP_FILE, H=self.H,
                     corners=np.array(sorted(config.CORNER_TAGS.items()), dtype=object), **extra)
            self._saved_at = time.monotonic()
        except OSError as e:
            print(f"[localizer] could not save table map: {e}")

    def _load_map(self) -> None:
        f = Path(config.TABLE_MAP_FILE)
        if not f.exists():
            return
        d = np.load(f, allow_pickle=True)
        if [tuple(c) for c in d["corners"]] != sorted(config.CORNER_TAGS.items()):
            print("[localizer] saved table map is for different CORNER_TAGS; ignoring it")
            return
        self.H = d["H"]
        if self.K is not None and "cam_from_sizes" in d:   # older maps' PnP camera was unreliable
            self.cam_pos = d["cam_from_sizes"]
        print(f"[localizer] loaded table map from {f} (refreshed whenever all 4 corner tags are visible)")

    def to_world(self, px: tuple[float, float], height: float = 0.0) -> tuple[float, float]:
        """Pixel -> world (x, y) on the plane `height` metres above the table."""
        p = self.H @ np.array([px[0], px[1], 1.0])
        x, y = p[0] / p[2], p[1] / p[2]          # where the line of sight meets the table
        if height and self.cam_pos is not None:
            # A point `height` up on that line is `height / camera height` of the way from
            # the table hit back toward the camera (similar triangles).
            k = height / self.cam_pos[2]
            x, y = x + (self.cam_pos[0] - x) * k, y + (self.cam_pos[1] - y) * k
        return float(x), float(y)

    def process(self, sightings: list[TagSighting]) -> dict[str, ForkliftPose]:
        """Returns the latest filtered pose for every forklift seen this frame."""
        if not self.update_homography(sightings):
            return {}
        now = time.monotonic()
        h = config.FORKLIFT_TAG_HEIGHT_M
        result = {}
        for s in sightings:
            fid = config.FORKLIFT_TAGS.get(s.tag_id)
            if not fid:
                continue
            x, y = self.to_world(s.center_px, h)
            # Heading: map a point a little "up" the tag the same way, so camera tilt
            # doesn't skew the angle.
            ux = s.center_px[0] + 20 * math.cos(s.heading_px)
            uy = s.center_px[1] + 20 * math.sin(s.heading_px)
            hx, hy = self.to_world((ux, uy), h)
            theta = math.atan2(hy - y, hx - x) + config.TAG_HEADING_OFFSET
            theta = (theta + math.pi) % (2 * math.pi) - math.pi   # keep in [-pi, pi)
            # Tag position -> chassis centre.
            fwd, left = config.TAG_OFFSET_FORWARD_M, config.TAG_OFFSET_LEFT_M
            x -= fwd * math.cos(theta) - left * math.sin(theta)
            y -= fwd * math.sin(theta) + left * math.cos(theta)
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
