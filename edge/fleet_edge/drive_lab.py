"""Drive lab: run spin / straight-line tests on the real forklift from the terminal,
with the camera measuring each one. Everything is recorded for later analysis.

  uv run python -m fleet_edge.drive_lab

Keys (no Enter needed):
  j / l     spin LEFT / RIGHT test (TEST_S seconds at the current speed)
  i / k     drive FORWARD / BACKWARD test (TEST_S seconds)
  w a s d   short nudge (0.3 s) to reposition the forklift between tests
  r / f     fork up / down nudge
  1-9, 0    set speed 10..90, 0 = 100            [ / ]   test length -/+ 0.5 s
  , / .     right-side trim -/+ 0.02 (straight tests and w/s nudges only; starts at RIGHT_TRIM)
  n         type a note (e.g. "battery moved to the back"); saved with the next tests
  SPACE     stop now                             q       quit

Each test stops early if the tag is lost (> LOST_TAG_STOP_S), the centre slides more
than --max-slide, or the centre comes within --pole-clear of the pole.

Records, appended across sessions (edge/logs/drive_lab/):
  tests.csv   one row per test: rotation, slide, pivot point, speed, stop reason, note
  frames.csv  every camera frame during tests and nudges: pose + wheel command
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import select
import sys
import termios
import time
import tty
from datetime import datetime
from pathlib import Path

import numpy as np

from . import config
from .freewili_link import SerialLink
from .localizer import Camera, Localizer, TagDetector

LOG_DIR = Path("edge/logs/drive_lab")
POLE = (0.220, 0.379)   # measured from the camera; see the WAYPOINTS comment in config.py
NUDGE_S = 0.3


def wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def pivot(a, b):
    """Body point (forward, left of the computed centre, m) that stayed still from pose a
    to pose b. None if it barely turned (the answer is meaningless then)."""
    dth = wrap(b.theta - a.theta)
    if abs(dth) < math.radians(10):
        return None
    def rot(t):
        c, s = math.cos(t), math.sin(t)
        return np.array([[c, -s], [s, c]])
    q = np.linalg.solve(rot(a.theta) - rot(b.theta), np.array([b.x - a.x, b.y - a.y]))
    return float(q[0]), float(q[1])


class Lab:
    def __init__(self, args):
        self.args = args
        self.cam = Camera()
        self.det, self.loc = TagDetector(), Localizer(self.cam.K)
        self.link = SerialLink()
        self.speed, self.test_s, self.note = 50, 2.0, ""
        self.trim = config.RIGHT_TRIM
        self.pose, self.seen_at = None, 0.0
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.session = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        tests_path, frames_path = LOG_DIR / "tests.csv", LOG_DIR / "frames.csv"
        _add_trim_column(tests_path)
        new_t, new_f = not tests_path.exists(), not frames_path.exists()
        self.tests_f = open(tests_path, "a", newline="")
        self.frames_f = open(frames_path, "a", newline="")
        self.tests, self.frames = csv.writer(self.tests_f), csv.writer(self.frames_f)
        if new_t:
            self.tests.writerow(["test_id", "session", "time", "kind", "speed", "planned_s", "ran_s",
                                 "stop_reason", "start_x_mm", "start_y_mm", "start_hdg_deg",
                                 "end_x_mm", "end_y_mm", "end_hdg_deg", "rotated_deg", "slide_mm",
                                 "slide_fwd_mm", "slide_left_mm", "pivot_fwd_mm", "pivot_left_mm",
                                 "tag_seen_pct", "min_pole_mm", "note", "right_trim"])
        if new_f:
            self.frames.writerow(["test_id", "t", "x", "y", "theta", "left", "right", "tag_seen"])
        self.test_id = self._next_id(tests_path)

    @staticmethod
    def _next_id(path: Path) -> int:
        try:
            with open(path) as f:
                ids = [int(r["test_id"]) for r in csv.DictReader(f) if r["test_id"].isdigit()]
            return max(ids, default=0) + 1
        except OSError:
            return 1

    # ------------------------------------------------------------- camera
    def look(self):
        p = self.loc.process(self.det.detect(self.cam.read())).get(config.HERO_FORKLIFT)
        if p is not None:
            self.pose, self.seen_at = p, time.monotonic()
        return p

    def settle(self, sec):
        """Wheels braked, let the pose settle; return the latest fresh pose (or None)."""
        end, last = time.monotonic() + sec, None
        while time.monotonic() < end:
            self.link.wheels(0, 0)
            last = self.look() or last
        return last

    # ------------------------------------------------------------- moves
    def run(self, kind, left, right, seconds, record_summary=True):
        tid = self.test_id if record_summary else 0
        a = self.settle(0.6)
        if a is None:
            say("  forklift tag not visible: not moving")
            return
        t0 = time.monotonic()
        last_theta, rotated, n, nseen, reason = a.theta, 0.0, 0, 0, "done"
        min_pole = math.hypot(a.x - POLE[0], a.y - POLE[1])
        while time.monotonic() - t0 < seconds:
            self.link.wheels(left, right)
            p = self.look()
            n += 1
            now = time.monotonic()
            if p is not None:
                nseen += 1
                rotated += wrap(p.theta - last_theta)
                last_theta = p.theta
                self.frames.writerow([tid, round(now, 3), round(p.x, 4), round(p.y, 4),
                                      round(p.theta, 4), left, right, 1])
                d_pole = math.hypot(p.x - POLE[0], p.y - POLE[1])
                min_pole = min(min_pole, d_pole)
                if d_pole < self.args.pole_clear:
                    reason = "near_pole"
                    break
                if math.hypot(p.x - a.x, p.y - a.y) > self.args.max_slide:
                    reason = "max_slide"
                    break
            else:
                self.frames.writerow([tid, round(now, 3), "", "", "", left, right, 0])
                if now - self.seen_at > config.LOST_TAG_STOP_S:
                    reason = "tag_lost"
                    break
        ran = time.monotonic() - t0
        self.link.stop(f"lab_{kind}")
        b = self.settle(0.8)
        self.frames_f.flush()
        if not record_summary:
            return
        if b is None:
            say(f"  #{tid} {kind}: stopped ({reason}); tag not visible afterwards, no result")
            self._write(tid, kind, left, seconds, ran, reason, a, None, rotated, n, nseen, min_pole)
            self.test_id += 1
            return
        rotated += wrap(b.theta - last_theta)
        self._write(tid, kind, left, seconds, ran, reason, a, b, rotated, n, nseen, min_pole)
        self.test_id += 1

    def _write(self, tid, kind, left, planned, ran, reason, a, b, rotated, n, nseen, min_pole):
        row = [tid, self.session, datetime.now().strftime("%H:%M:%S"), kind, abs(left),
               planned, round(ran, 2), reason,
               round(a.x * 1000), round(a.y * 1000), round(math.degrees(a.theta))]
        if b is None:
            row += [""] * 9
        else:
            dx, dy = b.x - a.x, b.y - a.y
            fwd = dx * math.cos(a.theta) + dy * math.sin(a.theta)
            lft = -dx * math.sin(a.theta) + dy * math.cos(a.theta)
            pv = pivot(a, b)
            row += [round(b.x * 1000), round(b.y * 1000), round(math.degrees(b.theta)),
                    round(math.degrees(rotated)), round(math.hypot(dx, dy) * 1000),
                    round(fwd * 1000), round(lft * 1000),
                    round(pv[0] * 1000) if pv else "", round(pv[1] * 1000) if pv else ""]
            msg = (f"  #{tid} {kind} @{abs(left)} {ran:.1f}s ({reason}): rotated "
                   f"{math.degrees(rotated):+.0f} deg, slid {math.hypot(dx, dy)*1000:.0f} mm "
                   f"({fwd*1000:+.0f} fwd, {lft*1000:+.0f} left)")
            if pv:
                msg += f", pivot {pv[0]*1000:+.0f} mm fwd {pv[1]*1000:+.0f} mm left of centre"
            say(msg)
        straight = kind in ("forward", "backward")
        row += [round(100 * nseen / max(n, 1)), round(min_pole * 1000), self.note,
                round(self.trim, 2) if straight else ""]
        self.tests.writerow(row)
        self.tests_f.flush()

    # ------------------------------------------------------------- loop
    def status(self):
        p = self.pose
        age = time.monotonic() - self.seen_at
        where = (f"({p.x*1000:4.0f},{p.y*1000:4.0f}) mm hdg {math.degrees(p.theta):4.0f}"
                 if p and age < 1.0 else "TAG NOT SEEN")
        tele = self.link.poll()
        st = f" [{tele.state}]" if tele else ""
        sys.stdout.write(f"\r  speed {self.speed:3d}  trim {self.trim:.2f}  test {self.test_s:.1f}s  {where}{st}   ")
        sys.stdout.flush()

    def ask_note(self, fd, old):
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        try:
            self.note = input("\n  note (Enter to clear): ").strip().replace(",", ";")
        finally:
            tty.setcbreak(fd)
        say(f"  note set: {self.note!r}" if self.note else "  note cleared")

    def loop(self):
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        last_status = 0.0
        try:
            say(__doc__.split("Keys")[1].split("Each test")[0].rstrip())
            say(f"  logging to {LOG_DIR}/ (next test #{self.test_id})\n")
            while True:
                self.look()
                if time.monotonic() - last_status > 0.3:
                    self.status()
                    last_status = time.monotonic()
                if not select.select([sys.stdin], [], [], 0)[0]:
                    continue
                k = sys.stdin.read(1)
                s, T = self.speed, self.test_s
                if k == "q":
                    break
                elif k == " ":
                    self.link.stop("lab_space")
                    say("  STOP")
                elif k in "0123456789":
                    self.speed = 100 if k == "0" else int(k) * 10
                elif k == "[":
                    self.test_s = max(0.5, T - 0.5)
                elif k == "]":
                    self.test_s = min(5.0, T + 0.5)
                elif k in ",.":
                    self.trim = round(min(1.0, max(0.5, self.trim + (0.02 if k == "." else -0.02))), 2)
                elif k == "n":
                    self.ask_note(fd, old)
                elif k in "jlik":
                    sr = round(s * self.trim)
                    kind, lr = {"j": ("spin_left", (-s, s)), "l": ("spin_right", (s, -s)),
                                "i": ("forward", (s, sr)), "k": ("backward", (-s, -sr))}[k]
                    say(f"\n  #{self.test_id} {kind} @{s} (L,R={lr[0]},{lr[1]}) for {T:.1f}s ...")
                    self.run(kind, *lr, T)
                elif k in "wasd":
                    sr = round(s * self.trim)
                    lr = {"w": (s, sr), "s": (-s, -sr), "a": (-s, s), "d": (s, -s)}[k]
                    self.run("nudge", *lr, NUDGE_S, record_summary=False)
                elif k in "rf":
                    self.link.fork(config.FORK_SPEED if k == "r" else -config.FORK_SPEED,
                                   config.FORK_STEP_MS)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    def close(self):
        try:
            self.link.stop("lab_quit")
            self.link.close()
        finally:
            self.cam.close()
            self.tests_f.close()
            self.frames_f.close()


def _add_trim_column(path: Path) -> None:
    """tests.csv from before trim existed: add an empty right_trim column so rows line up."""
    if not path.exists():
        return
    with open(path) as f:
        rows = list(csv.reader(f))
    if not rows or "right_trim" in rows[0]:
        return
    rows[0].append("right_trim")
    for r in rows[1:]:
        r.append("")
    with open(path, "w", newline="") as f:
        csv.writer(f).writerows(rows)


def say(msg: str) -> None:
    sys.stdout.write("\r\033[K" + msg + "\n")
    sys.stdout.flush()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-slide", type=float, default=0.15, help="stop a test if the centre moves this far (m)")
    ap.add_argument("--pole-clear", type=float, default=0.17, help="stop if the centre gets this close to the pole (m)")
    args = ap.parse_args()
    if not sys.stdin.isatty():
        raise SystemExit("run this in a terminal: it reads single key presses")
    lab = Lab(args)
    try:
        lab.loop()
    finally:
        lab.close()
        say(f"saved {LOG_DIR}/tests.csv and frames.csv")
        # pupil_apriltags can segfault while the interpreter shuts down; the work is done.
        os._exit(0)


if __name__ == "__main__":
    main()
