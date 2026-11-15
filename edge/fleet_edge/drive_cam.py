"""Drive the forklift by hand while watching what the webcam sees.

  uv run python -m fleet_edge.drive_cam             # forklift on USB if plugged in, else the radio base
  uv run python -m fleet_edge.drive_cam --radio     # force the radio base station
  uv run python -m fleet_edge.drive_cam --video edge/logs/manual.mp4   # also record

Keys (click the window first):
  w / s     forward / back        a / d   spin left / right
            HOLD to drive: each press drives for HOLD_S, key repeat keeps it going,
            let go and it stops by itself
  SPACE     stop now              r / f   fork up / down (one short burst)
  + / -     speed                 x       toggle "stop if the tag is lost" (on)
  q         quit

On screen: corner tags (cyan), tag 13 (green), the forklift's turning point, heading and
chassis outline, the Ring 1 route, the pole, and the border tag 13 must stay inside for
the camera to keep seeing it (orange). The panel shows the link (USB or radio), the
forklift's own state from its telemetry, and the camera's position estimate.
"""

from __future__ import annotations

import argparse
import math
import os
import time
from pathlib import Path

import cv2
import numpy as np

from . import config
from .freewili_link import RADIO_BASE_PRODUCT, SerialLink, find_port
from .localizer import Camera, Localizer, TagDetector
from .turn_drive import body_to_world, border_from_camera, inside, tag_at

HOLD_S = 0.6              # one key press drives this long; key repeat extends it
LOST_STOP_S = 0.3         # tag unseen this long while driving -> stop
POLE = (0.220, 0.379)     # measured from the camera (see config.py WAYPOINTS comment)
# Chassis outline around the turning point (m): fork tines ahead, deck behind.
FWD, BACK, LEFT, RIGHT = 0.11, 0.17, 0.06, 0.11

CYAN, GREEN, YELLOW, ORANGE, RED, WHITE = (255, 200, 0), (0, 255, 0), (0, 255, 255), (0, 140, 255), (0, 0, 255), (255, 255, 255)


def pick_port(force_radio: bool) -> str:
    if config.SERIAL_PORT != "auto":
        return config.SERIAL_PORT
    if force_radio:
        return find_port((RADIO_BASE_PRODUCT,))
    return find_port()


class Projector:
    """World metres (at a height) -> image pixels, the inverse of Localizer.to_world."""

    def __init__(self, loc: Localizer):
        self.loc = loc

    def __call__(self, x: float, y: float, h: float = 0.0):
        loc = self.loc
        if loc.H is None:
            return None
        if h and loc.cam_pos is not None:
            k = h / loc.cam_pos[2]                 # undo the similar-triangle pull
            x = (x - loc.cam_pos[0] * k) / (1 - k)
            y = (y - loc.cam_pos[1] * k) / (1 - k)
        p = np.linalg.inv(loc.H) @ np.array([x, y, 1.0])
        return int(p[0] / p[2]), int(p[1] / p[2])


def draw_scene(img, sightings, pose, w2p, border, tag_ok):
    for s in sightings:
        colour = CYAN if s.tag_id in config.CORNER_TAGS else GREEN
        cv2.polylines(img, [s.corners_px.astype(int)], True, colour, 2)
        c = tuple(int(v) for v in s.center_px)
        cv2.putText(img, str(s.tag_id), (c[0] - 8, c[1] + 8), cv2.FONT_HERSHEY_SIMPLEX, 0.8, colour, 2)
    if w2p.loc.H is None:
        return
    # Route and pole on the table.
    pts = [w2p(x, y) for x, y in config.WAYPOINTS]
    cv2.polylines(img, [np.array(pts)], True, YELLOW, 1)
    for i, p in enumerate(pts):
        cv2.circle(img, p, 5, YELLOW, -1)
        cv2.putText(img, f"wp{i}", (p[0] + 6, p[1] - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.45, YELLOW, 1)
    pp = w2p(*POLE)
    cv2.circle(img, pp, 12, RED, 2)
    cv2.putText(img, "pole", (pp[0] + 14, pp[1] + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, RED, 1)
    # Border for tag 13 (drawn where it sits at the tag's height).
    if border:
        bp = [w2p(x, y, config.FORKLIFT_TAG_HEIGHT_M) for x, y in border]
        cv2.polylines(img, [np.array(bp)], True, ORANGE, 1)
    if pose is None:
        return
    colour = GREEN if tag_ok else RED
    outline = [w2p(*body_to_world(pose.x, pose.y, pose.theta, f, l))
               for f, l in [(FWD, LEFT), (FWD, -RIGHT), (-BACK, -RIGHT), (-BACK, LEFT)]]
    cv2.polylines(img, [np.array(outline)], True, colour, 2)
    c = w2p(pose.x, pose.y)
    tip = w2p(*body_to_world(pose.x, pose.y, pose.theta, 0.12, 0.0))
    cv2.circle(img, c, 6, colour, -1)
    cv2.arrowedLine(img, c, tip, colour, 3, tipLength=0.3)


def draw_panel(img, rows, warn):
    h = 26 * len(rows) + 16
    overlay = img.copy()
    cv2.rectangle(overlay, (0, 0), (560, h), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.6, img, 0.4, 0, img)
    for i, (text, colour) in enumerate(rows):
        cv2.putText(img, text, (10, 26 + 26 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.6, colour, 1, cv2.LINE_AA)
    if warn:
        cv2.putText(img, warn, (10, img.shape[0] - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.9, RED, 2, cv2.LINE_AA)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--radio", action="store_true", help="use the radio base station even if the forklift is on USB")
    ap.add_argument("--video", help="also record the window to this .mp4")
    args = ap.parse_args()

    port = pick_port(args.radio)
    via = "radio base" if "flbase" in _product(port) else ("WiFi" if port.startswith("socket://") else "USB")
    link = SerialLink(port=port)
    cam = Camera()
    det, loc = TagDetector(), Localizer(cam.K)
    w2p = Projector(loc)
    border = border_from_camera(loc, config.CAMERA_WIDTH, config.CAMERA_HEIGHT)

    speed, cmd, drive_until = 45, (0, 0), 0.0
    stop_on_lost = True
    pose, last_seen = None, 0.0
    status_msg, video, frames, t_fps, fps = "", None, 0, time.time(), 0.0
    print(f"[drive_cam] link: {via} ({port}). Click the window, then w/a/s/d.")
    try:
        while True:
            now = time.monotonic()
            frame = cam.read()
            sightings = det.detect(frame)
            poses = loc.process(sightings)
            if border is None and loc.H is not None:
                border = border_from_camera(loc, config.CAMERA_WIDTH, config.CAMERA_HEIGHT)
            if config.HERO_FORKLIFT in poses:
                pose, last_seen = poses[config.HERO_FORKLIFT], now
            tag_ok = now - last_seen < LOST_STOP_S

            # Wheels: hold-to-drive, with the safety stops.
            driving = now < drive_until and cmd != (0, 0)
            warn = ""
            if driving and stop_on_lost and not tag_ok:
                driving, drive_until = False, 0.0
                link.stop("tag_lost")
                status_msg = "stopped: tag 13 lost"
            if pose is not None and border and not inside(border, tag_at(pose.x, pose.y, pose.theta)):
                warn = "tag 13 at the edge of the camera view"
            if driving:
                link.wheels(*cmd)
            else:
                link.wheels(0, 0)       # also keeps the forklift's watchdog fed
            tele = link.poll()

            frames += 1
            if time.time() - t_fps >= 1.0:
                fps, frames, t_fps = frames / (time.time() - t_fps), 0, time.time()
            view = frame.copy()
            draw_scene(view, sightings, pose, w2p, border, tag_ok)
            where = (f"x {pose.x * 1000:4.0f}  y {pose.y * 1000:4.0f} mm  heading {math.degrees(pose.theta):4.0f} deg"
                     if pose else "forklift not seen yet")
            age = now - last_seen if last_seen else float("inf")
            rows = [
                (f"link: {via}   forklift says: {tele.state if tele else '(no telemetry yet)'}", WHITE),
                (f"speed {speed}   L={cmd[0] if driving else 0}  R={cmd[1] if driving else 0}"
                 f"   {'DRIVING' if driving else 'stopped'}", GREEN if driving else WHITE),
                (f"{where}", WHITE if tag_ok else RED),
                (f"tag 13 {'seen' if tag_ok else f'LOST {age:.1f}s'}   camera {fps:.0f} fps   "
                 f"stop-if-lost {'ON' if stop_on_lost else 'OFF'}", WHITE if tag_ok else RED),
                ("w/s/a/d hold to drive  SPACE stop  r/f fork  +/- speed  x lost-stop  q quit", (180, 180, 180)),
            ]
            if status_msg:
                rows.append((status_msg, ORANGE))
            draw_panel(view, rows, warn)
            cv2.imshow("Forklift: manual drive", view)
            if args.video:
                if video is None:
                    Path(args.video).parent.mkdir(parents=True, exist_ok=True)
                    video = cv2.VideoWriter(args.video, cv2.VideoWriter_fourcc(*"mp4v"), 20,
                                            (view.shape[1], view.shape[0]))
                video.write(view)

            k = cv2.waitKey(1) & 0xFF
            s = speed
            sr = round(s * config.RIGHT_TRIM)   # same straight-line trim as the follower
            moves = {ord("w"): (s, sr), ord("s"): (-s, -sr), ord("a"): (-s, s), ord("d"): (s, -s)}
            if k == ord("q"):
                break
            if k in moves:
                cmd, drive_until, status_msg = moves[k], now + HOLD_S, ""
            elif k == ord(" "):
                cmd, drive_until = (0, 0), 0.0
                link.stop("space")
                status_msg = "stopped (space)"
            elif k == ord("r"):
                link.fork(config.FORK_SPEED, config.FORK_STEP_MS)
            elif k == ord("f"):
                link.fork(-config.FORK_SPEED, config.FORK_STEP_MS)
            elif k in (ord("+"), ord("=")):
                speed = min(100, speed + 5)
            elif k == ord("-"):
                speed = max(config.MIN_SPEED, speed - 5)
            elif k == ord("x"):
                stop_on_lost = not stop_on_lost
    finally:
        link.close()
        cam.close()
        if video is not None:
            video.release()
            print(f"[drive_cam] saved {args.video}")
        cv2.destroyAllWindows()
        os._exit(0)   # pupil_apriltags can segfault while the interpreter shuts down


def _product(port: str) -> str:
    from serial.tools import list_ports
    for p in list_ports.comports():
        if p.device == port:
            return p.product or ""
    return ""


if __name__ == "__main__":
    main()
