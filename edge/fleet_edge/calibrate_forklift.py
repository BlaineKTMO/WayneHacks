"""Calibrate a NEW forklift's tags from scratch (no known reference tag needed).

  uv run python -m fleet_edge.calibrate_forklift fl-02 20 21      # forklift, then its tag IDs

Put the forklift in the middle of the table, all 4 corner tags and its tags in view, with
~15 cm clear all round. It MOVES: one short drive forward (~8 cm), then two ~45 deg left
spins. It stops if a tag is lost, and on exit. Then it prints the TAG_MOUNTS and
FORKLIFT_TAGS lines for config.py.

  height   each tag's height above the table, from its apparent size (as measure_tag)
  heading  the short forward drive gives the chassis's forward direction; each tag's
           heading offset is that minus the tag's own "up"
  fwd/left each tag relative to the TURNING POINT: the spins rotate the chassis about it,
           so it's the one point that stays put (the follower spins in place about it)
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time

import numpy as np

from . import config
from .freewili_link import open_link
from .localizer import Camera, Localizer, TagDetector

SPIN_STOP_DEG = 40      # stop each spin about here (it coasts a few degrees more)
DRIVE_S = 0.6
LOST_S = 0.3


def wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def circ_mean(a) -> float:
    return math.atan2(float(np.mean(np.sin(a))), float(np.mean(np.cos(a))))


class Rig:
    def __init__(self, fid: str, tags: list[int]):
        self.cam, self.det = Camera(), TagDetector()
        self.loc = Localizer(self.cam.K)
        if self.cam.K is None:
            raise SystemExit("needs the lens calibration (edge/calibration/camera.npz): run from the repo root")
        self.f = (self.cam.K[0, 0] + self.cam.K[1, 1]) / 2
        self.link = open_link(fid)
        self.tags = tags
        self.heights: dict[int, float] = {}

    def frame(self):
        ss = self.det.detect(self.cam.read())
        self.loc.update_homography(ss)
        return {s.tag_id: s for s in ss if s.tag_id in self.tags}

    def height(self, s) -> float | None:
        if self.loc.H is None or self.loc.cam_pos is None:
            return None
        side = np.mean([np.linalg.norm(np.subtract(s.corners_px[i], s.corners_px[(i + 1) % 4])) for i in range(4)])
        dist = self.f * config.TAG_SIZE_M / side
        C = np.asarray(self.loc.cam_pos, float)
        table = np.array([*self.loc.to_world(s.center_px, 0.0), 0.0])
        ray = (table - C) / np.linalg.norm(table - C)
        return float((C + dist * ray)[2])

    def still(self, sec: float) -> dict[int, tuple[float, float, float]]:
        """Mean (x, y, raw heading) of each tag over the last half of `sec`, wheels stopped."""
        seen: dict[int, list] = {t: [] for t in self.tags}
        end = time.time() + sec
        while time.time() < end:
            self.link.wheels(0, 0)
            for t, s in self.frame().items():
                if t in self.heights:
                    seen[t].append(self.loc.tag_world(s, dict(height=self.heights[t])))
        out = {}
        for t, v in seen.items():
            if len(v) < 4:
                raise SystemExit(f"tag {t} not visible while standing still: stopping")
            a = np.array(v[len(v) // 2:])
            out[t] = (float(a[:, 0].mean()), float(a[:, 1].mean()), circ_mean(a[:, 2]))
        return out

    def move(self, left: int, right: int, sec: float, stop_turn_deg: float | None = None) -> None:
        end, last_seen, turned, last = time.time() + sec, time.time(), 0.0, None
        t0 = self.tags[0]
        while time.time() < end:
            self.link.wheels(left, right)
            fr = self.frame()
            if fr:
                last_seen = time.time()
            elif time.time() - last_seen > LOST_S:
                self.link.stop("calibrate: tags lost")
                raise SystemExit("lost the forklift's tags while moving: stopped")
            if stop_turn_deg is not None and t0 in fr:
                raw = self.loc.tag_world(fr[t0], dict(height=self.heights[t0]))[2]
                if last is not None:
                    turned += abs(wrap(raw - last))
                last = raw
                if math.degrees(turned) >= stop_turn_deg:
                    break
        self.link.stop("calibrate")

    def close(self) -> None:
        try:
            self.link.stop("calibrate done")
            self.link.close()
        finally:
            self.cam.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("forklift")
    ap.add_argument("tags", type=int, nargs="+")
    a = ap.parse_args()
    clash = [t for t in a.tags if t in config.CORNER_TAGS or config.FORKLIFT_TAGS.get(t, a.forklift) != a.forklift]
    if clash:
        raise SystemExit(f"tag(s) {clash} already belong to a corner or another forklift")

    rig = Rig(a.forklift, a.tags)
    code = 0
    try:
        # 1. Heights, standing still.
        hs: dict[int, list] = {t: [] for t in a.tags}
        end = time.time() + 2.0
        while time.time() < end:
            rig.link.wheels(0, 0)
            for t, s in rig.frame().items():
                h = rig.height(s)
                if h is not None:
                    hs[t].append(h)
        for t, v in hs.items():
            if len(v) < 5:
                raise SystemExit(f"tag {t}: only {len(v)} good frames (all 4 corner tags and tag {t} in view?)")
            rig.heights[t] = float(np.median(v))
            print(f"tag {t}: height {rig.heights[t] * 1000:.0f} mm (spread {np.std(v) * 1000:.1f})")

        # 2. Forward direction: a short straight drive.
        before = rig.still(1.0)
        s = config.MAX_SPEED
        rig.move(s, round(s * config.RIGHT_TRIM), DRIVE_S)
        after = rig.still(1.0)
        moves = np.array([[after[t][0] - before[t][0], after[t][1] - before[t][1]] for t in a.tags])
        dist = float(np.linalg.norm(moves.mean(axis=0)))
        turned = math.degrees(wrap(after[a.tags[0]][2] - before[a.tags[0]][2]))
        print(f"forward drive: {dist * 1000:.0f} mm, turned {turned:+.1f} deg")
        if dist < 0.03:
            raise SystemExit("it barely moved: battery / wheels? (needs > 30 mm)")
        if abs(turned) > 8:
            print("  warning: it curved; the heading offsets will be off by about half that")
        fwd_dir = math.atan2(*moves.mean(axis=0)[::-1])
        heading_off = {t: wrap(fwd_dir - after[t][2]) for t in a.tags}

        # 3. Turning point: two left spins; the point that stays put.
        pivots = []
        for i in range(2):
            p0 = rig.still(1.0)
            rig.move(-config.TURN_SPEED, config.TURN_SPEED, 1.5, stop_turn_deg=SPIN_STOP_DEG)
            p1 = rig.still(1.0)
            t = a.tags[0]
            d = wrap(p1[t][2] - p0[t][2])
            if abs(math.degrees(d)) < 10:
                print(f"spin {i + 1}: only {math.degrees(d):.0f} deg, skipped")
                continue
            R = np.array([[math.cos(d), -math.sin(d)], [math.sin(d), math.cos(d)]])
            pa, pb = np.array(p0[t][:2]), np.array(p1[t][:2])
            c = np.linalg.solve(np.eye(2) - R, pb - R @ pa)
            # Turning point in tag t's chassis frame (before the spin).
            th = p0[t][2] + heading_off[t]
            v = c - pa
            pivots.append((v[0] * math.cos(th) + v[1] * math.sin(th), -v[0] * math.sin(th) + v[1] * math.cos(th)))
            print(f"spin {i + 1}: {math.degrees(d):.0f} deg, turning point {pivots[-1][0] * 1000:+.0f} fwd "
                  f"{pivots[-1][1] * 1000:+.0f} left of tag {t}")
        if not pivots:
            raise SystemExit("no usable spin")
        pf, pl = np.mean(pivots, axis=0)

        # Every tag relative to the turning point, from where they sat after the drive.
        t0 = a.tags[0]
        th = after[t0][2] + heading_off[t0]
        print("\nAdd to config.py:")
        print(f"FORKLIFT_TAGS.update({{{', '.join(f'{t}: {a.forklift!r}' for t in a.tags)}}})")
        print("TAG_MOUNTS.update({")
        for t in a.tags:
            dx, dy = after[t][0] - after[t0][0], after[t][1] - after[t0][1]
            f_rel = dx * math.cos(th) + dy * math.sin(th) - pf
            l_rel = -dx * math.sin(th) + dy * math.cos(th) - pl
            print(f"    {t}: dict(height={rig.heights[t]:.3f}, fwd={f_rel:.3f}, left={l_rel:.3f}, "
                  f"heading={heading_off[t]:.4f}),")
        print("})")
        if len(pivots) == 2:
            spread = math.hypot(pivots[0][0] - pivots[1][0], pivots[0][1] - pivots[1][1])
            print(f"(turning point: the two spins agree within {spread * 1000:.0f} mm)")
    except SystemExit as e:
        print(f"stopped: {e}")
        code = 1
    finally:
        rig.close()
        sys.stdout.flush()
        os._exit(code)   # pupil_apriltags can segfault while the interpreter shuts down


if __name__ == "__main__":
    main()
