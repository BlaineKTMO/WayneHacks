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
    puts it too far from the point under the camera (65 mm off at 27 cm height on our
    rig). With a lens calibration (K), the camera's position is solved from the four
    corner tags and each forklift tag's line of sight is intersected with the plane at
    its real height. Without one, it falls back to the flat table mapping.
    """

    def __init__(self, K: np.ndarray | None = None):
        self.K = K                              # camera matrix of the UNDISTORTED image
        self.H: np.ndarray | None = None       # pixels -> metres, table plane
        self.R: np.ndarray | None = None       # camera pose from the corner tags (world -> camera)
        self.cam_pos: np.ndarray | None = None  # camera centre in world metres
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
            obj = np.hstack([world, np.zeros((len(world), 1))])
            ok, rvec, tvec = cv2.solvePnP(obj, px, self.K, None, flags=cv2.SOLVEPNP_IPPE)
            if ok:
                self.R, _ = cv2.Rodrigues(rvec)
                self.cam_pos = (-self.R.T @ tvec).ravel()
        if time.monotonic() - self._saved_at > 10:
            self._save_map()
        return True

    def _save_map(self) -> None:
        try:
            Path(config.TABLE_MAP_FILE).parent.mkdir(parents=True, exist_ok=True)
            extra = {} if self.R is None else {"R": self.R, "cam_pos": self.cam_pos}
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
        if self.K is not None and "R" in d:
            self.R, self.cam_pos = d["R"], d["cam_pos"]
        print(f"[localizer] loaded table map from {f} (refreshed whenever all 4 corner tags are visible)")

    def to_world(self, px: tuple[float, float], height: float = 0.0) -> tuple[float, float]:
        """Pixel -> world (x, y) on the plane `height` metres above the table."""
        if height and self.cam_pos is not None:
            ray = self.R.T @ np.linalg.solve(self.K, np.array([px[0], px[1], 1.0]))
            # World z points either toward or away from the camera depending on the
            # tag layout; "above the table" is always the camera's side.
            z = height if self.cam_pos[2] > 0 else -height
            s = (z - self.cam_pos[2]) / ray[2]
            p = self.cam_pos + s * ray
            return float(p[0]), float(p[1])
        p = self.H @ np.array([px[0], px[1], 1.0])
        return float(p[0] / p[2]), float(p[1] / p[2])

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
