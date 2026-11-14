"""RING 1: one forklift drives a waypoint loop by itself.

  Simulation (no hardware):  uv run python -m fleet_edge.step6_ring1 --sim
  Real forklift:             uv run python -m fleet_edge.step6_ring1

Keys in the window:  SPACE = start/pause   q = quit   r = reset laps
Safety: if the forklift tag isn't seen for LOST_TAG_STOP_S, we send S (stop) immediately.
"""

from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

import cv2

from . import config
from .freewili_link import SerialLink, SimLink
from .localizer import ForkliftPose
from .overlay import WorldCanvas, draw
from .pure_pursuit import WaypointFollower


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", action="store_true", help="no camera, no Free-WiLi: simulated forklift")
    ap.add_argument("--log", default="edge/logs/ring1.csv", help="pose log file")
    args = ap.parse_args()

    follower = WaypointFollower(config.WAYPOINTS, loop=True)
    canvas = WorldCanvas()
    if args.sim:
        link = SimLink(x=config.WAYPOINTS[0][0] - 0.1, y=config.WAYPOINTS[0][1] - 0.05)
        camera = detector = localizer = None
    else:
        from .localizer import Camera, Localizer, TagDetector
        link = SerialLink()
        camera, detector, localizer = Camera(), TagDetector(), Localizer()

    Path(args.log).parent.mkdir(parents=True, exist_ok=True)
    log = csv.writer(open(args.log, "w", newline=""))
    log.writerow(["t", "x", "y", "theta", "left", "right", "wp", "lap"])

    running, last_seen, pose = False, 0.0, None
    period = 1 / config.COMMAND_RATE_HZ
    print("SPACE to start. q to quit.")
    try:
        while True:
            t0 = time.monotonic()

            # 1. Where is the forklift?
            if args.sim:
                pose = ForkliftPose(config.HERO_FORKLIFT, link.x, link.y, link.theta, 1.0, t0)
                img = canvas.blank()
                fresh = True
            else:
                frame = camera.read()
                poses = localizer.process(detector.detect(frame))
                fresh = config.HERO_FORKLIFT in poses
                if fresh:
                    pose = poses[config.HERO_FORKLIFT]
                img = frame
                if localizer.H is not None:
                    import numpy as np
                    Hinv = np.linalg.inv(localizer.H)
                    def w2p(x, y, Hinv=Hinv):
                        p = Hinv @ np.array([x, y, 1.0])
                        return int(p[0] / p[2]), int(p[1] / p[2])
                    canvas = WorldCanvas(world_to_px=w2p)
            if fresh:
                last_seen = t0
            tag_ok = (t0 - last_seen) < config.LOST_TAG_STOP_S

            # 2. Decide wheel speeds (or stop).
            left = right = 0
            if not running:
                link.wheels(0, 0)
                status = "PAUSED (space to start)"
            elif not tag_ok:
                link.stop("lost_tag")
                status = "STOPPED: tag lost"
            elif follower.laps >= config.LAPS:
                link.stop("done")
                status = f"DONE: {follower.laps} laps"
            else:
                left, right = follower.update(pose.x, pose.y, pose.theta)
                link.wheels(left, right)
                status = f"DRIVING  L={left} R={right}"
            tele = link.poll()
            if tele:
                status += f"   [{tele.state} {tele.battery_v:.1f}V]"

            if pose:
                log.writerow([round(t0, 3), round(pose.x, 4), round(pose.y, 4), round(pose.theta, 4),
                              left, right, follower.index, follower.laps])

            # 3. Show it.
            cv2.imshow("Ring 1", draw(img, canvas, pose, follower, status, tag_ok))
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord(" "):
                running = not running
            if key == ord("r"):
                follower = WaypointFollower(config.WAYPOINTS, loop=True)

            # 4. Keep a steady 20 Hz.
            time.sleep(max(0.0, period - (time.monotonic() - t0)))
    finally:
        link.close()
        if camera:
            camera.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
