"""STEP 4: drive the motors by hand through the Free-WiLi, and find the deadband.

  uv run python -m fleet_edge.step4_motors          (real Free-WiLi)
  uv run python -m fleet_edge.step4_motors --sim    (try the controls without hardware)

Keys (click the window first):
  w/s = forward/back   a/d = spin left/right   SPACE = stop
  r/f = fork up/down (one short burst per press, FORK_STEP_MS)
  +/- = change speed   q = quit

Deadband: set speed low and press + until the wheels JUST start turning. Put that number
in MIN_SPEED in config.py. Do it with the forklift on the floor, not in the air.

Watchdog test: drive forward then unplug the USB. The Free-WiLi must stop within 0.5 s.
"""

import argparse
import time

import cv2
import numpy as np

from . import config
from .freewili_link import SerialLink, SimLink


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", action="store_true")
    args = ap.parse_args()
    link = SimLink() if args.sim else SerialLink()
    speed, left, right = 30, 0, 0
    try:
        while True:
            t = link.poll()
            img = np.zeros((160, 520, 3), np.uint8)
            lines = [f"speed {speed}   L={left} R={right}",
                     f"Free-WiLi: {t.state} {t.battery_v:.2f}V" if t else "Free-WiLi: no telemetry yet",
                     "w/s/a/d drive  r/f fork  SPACE stop  +/- speed  q quit"]
            for i, s in enumerate(lines):
                cv2.putText(img, s, (10, 40 + 40 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 1)
            cv2.imshow("Step 4: motors", img)
            k = cv2.waitKey(50) & 0xFF   # 20 Hz: also keeps the watchdog fed
            if k == ord("q"):
                break
            elif k == ord("w"): left, right = speed, speed
            elif k == ord("s"): left, right = -speed, -speed
            elif k == ord("a"): left, right = -speed, speed
            elif k == ord("d"): left, right = speed, -speed
            elif k == ord(" "): left, right = 0, 0
            elif k == ord("r"): link.fork(config.FORK_SPEED, config.FORK_STEP_MS)
            elif k == ord("f"): link.fork(-config.FORK_SPEED, config.FORK_STEP_MS)
            elif k in (ord("+"), ord("=")): speed = min(100, speed + 5)
            elif k == ord("-"): speed = max(0, speed - 5)
            link.wheels(left, right)
    finally:
        link.close()
        cv2.destroyAllWindows()
        time.sleep(0.1)


if __name__ == "__main__":
    main()
