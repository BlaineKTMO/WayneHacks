"""Measure where a NEW forklift tag sits on the chassis, using a tag that is already known.

  uv run python -m fleet_edge.measure_tag 16            # reference: tag 13 (default)
  uv run python -m fleet_edge.measure_tag 16 --ref 13 --seconds 3

Hold the forklift still with BOTH tags fully in view. For ~2 s it measures:
  height   from the tag's apparent size: distance from the camera along its line of sight
  fwd/left the tag centre relative to the turning point, from the reference tag's pose
  heading  the angle to add to the tag's own "up" to get the forklift's heading
and prints the TAG_MOUNTS entry to paste into config.py.
"""

from __future__ import annotations

import argparse
import math
import os
import time

import numpy as np

from . import config
from .localizer import Camera, Localizer, TagDetector


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tag", type=int)
    ap.add_argument("--ref", type=int, default=13)
    ap.add_argument("--seconds", type=float, default=2.0)
    a = ap.parse_args()
    if a.ref not in config.TAG_MOUNTS:
        raise SystemExit(f"reference tag {a.ref} has no TAG_MOUNTS entry")

    cam = Camera()
    det, loc = TagDetector(), Localizer(cam.K)
    f = (cam.K[0, 0] + cam.K[1, 1]) / 2
    ref_mount = config.TAG_MOUNTS[a.ref]
    heights, fwds, lefts, heads = [], [], [], []
    end = time.time() + a.seconds
    while time.time() < end:
        sightings = det.detect(cam.read())
        loc.update_homography(sightings)
        by_id = {s.tag_id: s for s in sightings}
        if a.ref not in by_id or a.tag not in by_id or loc.H is None or loc.cam_pos is None:
            continue
        # Forklift pose from the reference tag alone.
        rx, ry, rraw = loc.tag_world(by_id[a.ref], ref_mount)
        theta = rraw + ref_mount["heading"]
        c, sn = math.cos(theta), math.sin(theta)
        px = rx - (ref_mount["fwd"] * c - ref_mount["left"] * sn)
        py = ry - (ref_mount["fwd"] * sn + ref_mount["left"] * c)
        # New tag height: distance from the camera (apparent size) along its line of sight.
        s = by_id[a.tag]
        side = np.mean([np.linalg.norm(np.subtract(s.corners_px[i], s.corners_px[(i + 1) % 4])) for i in range(4)])
        dist = f * config.TAG_SIZE_M / side
        C = np.asarray(loc.cam_pos, float)
        table = np.array([*loc.to_world(s.center_px, 0.0), 0.0])
        ray = (table - C) / np.linalg.norm(table - C)
        h = float((C + dist * ray)[2])
        tx, ty, traw = loc.tag_world(s, dict(height=h))
        dx, dy = tx - px, ty - py
        heights.append(h)
        fwds.append(dx * c + dy * sn)
        lefts.append(-dx * sn + dy * c)
        heads.append(theta - traw)
    cam.close()
    if len(heights) < 5:
        print(f"only {len(heights)} frames with tags {a.ref} and {a.tag} both visible: hold still, both fully in view")
        os._exit(1)
    head = math.atan2(np.mean(np.sin(heads)), np.mean(np.cos(heads)))
    print(f"{len(heights)} frames. tag {a.tag}: height {np.median(heights)*1000:.0f} mm "
          f"(spread {np.std(heights)*1000:.1f}), {np.median(fwds)*1000:+.0f} mm forward, "
          f"{np.median(lefts)*1000:+.0f} mm left of the turning point, heading offset {math.degrees(head):+.0f} deg")
    print("\nTAG_MOUNTS entry for config.py:")
    print(f"    {a.tag}: dict(height={np.median(heights):.3f}, fwd={np.median(fwds):.3f}, "
          f"left={np.median(lefts):.3f}, heading={head:.4f}),")
    os._exit(0)   # pupil_apriltags can segfault while the interpreter shuts down


if __name__ == "__main__":
    main()
