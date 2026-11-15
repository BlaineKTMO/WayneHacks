"""Save one annotated camera frame: AprilTags (with IDs and, once all 4 corner tags are
seen, forklift positions in metres) and the chessboard if visible. No window.

  uv run python -m fleet_edge.snapshot edge/logs/snapshot.jpg
  uv run python -m fleet_edge.snapshot out.jpg --raw     # also save the unannotated frame
"""

from __future__ import annotations

import argparse
import functools
import math
import time
from pathlib import Path

import cv2

from . import config

print = functools.partial(print, flush=True)  # noqa: A001 - survive the exit-time segfault
from .localizer import Camera, Localizer, TagDetector


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default="edge/logs/snapshot.jpg")
    ap.add_argument("--raw", action="store_true")
    ap.add_argument("--settle", type=float, default=1.5, help="seconds to let exposure settle")
    args = ap.parse_args()

    cam = Camera()
    det, loc = TagDetector(), Localizer(cam.K)
    t = time.time()
    frame = cam.read()
    while time.time() - t < args.settle:
        frame = cam.read()
    cam.close()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if args.raw:
        cv2.imwrite(str(out.with_name(out.stem + "_raw" + out.suffix)), frame)

    view = frame.copy()
    sightings = det.detect(frame)
    poses = loc.process(sightings)
    for s in sightings:
        c = tuple(int(v) for v in s.center_px)
        colour = (255, 200, 0) if s.tag_id in config.CORNER_TAGS else (0, 255, 0)
        cv2.polylines(view, [s.corners_px.astype(int)], True, colour, 2)
        cv2.putText(view, str(s.tag_id), c, cv2.FONT_HERSHEY_SIMPLEX, 0.9, colour, 2)
    print(f"tags: {sorted(s.tag_id for s in sightings)}")
    for fid, p in poses.items():
        print(f"{fid}: x={p.x:.3f} m  y={p.y:.3f} m  heading={math.degrees(p.theta):.0f} deg")

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    found, corners = cv2.findChessboardCorners(gray, config.CHESSBOARD_INNER)
    if found:
        cv2.drawChessboardCorners(view, config.CHESSBOARD_INNER, corners, found)
    corners_seen = sum(s.tag_id in config.CORNER_TAGS for s in sightings)
    print(f"chessboard {config.CHESSBOARD_INNER}: {'found' if found else 'not found'}; "
          f"corner tags seen: {corners_seen}/4; map {'OK' if loc.H is not None else 'not built'}")
    h, w = frame.shape[:2]
    print(f"frame {w}x{h}; calibration {'applied' if cam.K is not None else 'NOT applied'}")
    cv2.imwrite(str(out), view)
    print(f"saved {out}", flush=True)
    # pupil_apriltags can segfault while the interpreter shuts down; the work is done.
    import os
    os._exit(0)


if __name__ == "__main__":
    main()
