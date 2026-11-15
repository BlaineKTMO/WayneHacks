"""Drive the forklifts by hand while watching what the webcam sees.

  uv run python -m fleet_edge.drive_cam             # every forklift it can reach
  uv run python -m fleet_edge.drive_cam --forklift fl-02          # just one
  uv run python -m fleet_edge.drive_cam --radio     # force the radio base station
  uv run python -m fleet_edge.drive_cam --video edge/logs/manual.mp4   # also record

Keys (click the window first):
  1 / 2     drive fl-01 / fl-02  TAB   next forklift  (the others are held stopped)
  w / s     forward / back        a / d   spin left / right
            HOLD to drive: each press drives for HOLD_S, key repeat keeps it going,
            let go and it stops by itself
  SPACE     stop ALL now          r / f   fork up / down (one short burst; fl-01 only)
  + / -     speed                 x       toggle "stop if the tag is lost" (on)
  q         quit

On screen: corner tags (cyan), forklift tags (green), cargo (magenta) and dock (blue)
tags with their names, each forklift's turning point, heading and chassis outline (the
one you're driving thick), and the border at least one of its tags must stay inside for
the camera to keep seeing it (orange). The panel shows each forklift's link, its own state
from telemetry, and its position.
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
from .freewili_link import RADIO_BASE_PRODUCT, RadioHub, RadioLink, find_port, open_link
from .localizer import Camera, Localizer, TagDetector
from .turn_drive import body_to_world, border_from_camera, in_view

HOLD_S = 0.6              # one key press drives this long; key repeat extends it
LOST_STOP_S = 0.3         # tag unseen this long while driving -> stop
POLE = (0.220, 0.379)     # the old table's pole (only drawn with the Ring 1 route)
# Chassis outline around the turning point (m): fork tines ahead, deck behind.
FWD, BACK, LEFT, RIGHT = 0.11, 0.17, 0.04, 0.13   # turning point 21 mm left of centre line (fork motor)

CYAN, GREEN, YELLOW, ORANGE, RED, WHITE = (255, 200, 0), (0, 255, 0), (0, 255, 255), (0, 140, 255), (0, 0, 255), (255, 255, 255)
MAGENTA, BLUE, GREY = (255, 0, 255), (255, 120, 0), (150, 150, 150)


def pick_link(fid: str, force_radio: bool):
    """(link, how it's connected) for forklift fid."""
    if force_radio:
        hub = RadioHub.on(find_port((RADIO_BASE_PRODUCT,)))
        return RadioLink(hub, config.FORKLIFT_RADIO_IDS[fid]), "radio"
    link = open_link(fid)
    if isinstance(link, RadioLink):
        return link, "radio"
    port = link.ser.port or ""
    return link, ("WiFi" if str(port).startswith("socket://") else "USB")


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


def tag_colour(tag_id: int):
    if tag_id in config.CORNER_TAGS:
        return CYAN
    if tag_id in config.CARGO_TAGS:
        return MAGENTA
    if tag_id in config.DOCK_TAGS:
        return BLUE
    return GREEN


def tag_label(tag_id: int) -> str:
    return config.CARGO_TAGS.get(tag_id) or config.DOCK_TAGS.get(tag_id) or str(tag_id)


def draw_pose(img, pose, w2p, colour, thick=2, label=""):
    """One forklift: chassis outline, turning point, heading arrow, name."""
    outline = [w2p(*body_to_world(pose.x, pose.y, pose.theta, f, l))
               for f, l in [(FWD, LEFT), (FWD, -RIGHT), (-BACK, -RIGHT), (-BACK, LEFT)]]
    cv2.polylines(img, [np.array(outline)], True, colour, thick)
    c = w2p(pose.x, pose.y)
    tip = w2p(*body_to_world(pose.x, pose.y, pose.theta, 0.12, 0.0))
    cv2.circle(img, c, 3 + 2 * thick, colour, -1)
    cv2.arrowedLine(img, c, tip, colour, thick + 1, tipLength=0.3)
    if label:
        cv2.putText(img, label, (c[0] + 10, c[1] - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.6, colour, 2)


def draw_scene(img, sightings, pose, w2p, border, tag_ok, ring1_route=True):
    for s in sightings:
        colour = tag_colour(s.tag_id)
        cv2.polylines(img, [s.corners_px.astype(int)], True, colour, 2)
        c = tuple(int(v) for v in s.center_px)
        cv2.putText(img, tag_label(s.tag_id), (c[0] - 8, c[1] + 8), cv2.FONT_HERSHEY_SIMPLEX, 0.7, colour, 2)
    if w2p.loc.H is None:
        return
    # Ring 1 route and the pole: the old table only.
    if ring1_route:
        pts = [w2p(x, y) for x, y in config.WAYPOINTS]
        cv2.polylines(img, [np.array(pts)], True, YELLOW, 1)
        for i, p in enumerate(pts):
            cv2.circle(img, p, 5, YELLOW, -1)
            cv2.putText(img, f"wp{i}", (p[0] + 6, p[1] - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.45, YELLOW, 1)
        pp = w2p(*POLE)
        cv2.circle(img, pp, 12, RED, 2)
        cv2.putText(img, "pole", (pp[0] + 14, pp[1] + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, RED, 1)
    # Camera border, drawn where it sits at each tag's height (all the same image rectangle).
    for t, poly in (border or {}).items():
        h = config.TAG_MOUNTS[t]["height"]
        cv2.polylines(img, [np.array([w2p(x, y, h) for x, y in poly])], True, ORANGE, 1)
    if pose is not None:
        draw_pose(img, pose, w2p, GREEN if tag_ok else RED)


def draw_panel(img, rows, warn):
    h = 26 * len(rows) + 16
    overlay = img.copy()
    cv2.rectangle(overlay, (0, 0), (620, h), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.6, img, 0.4, 0, img)
    for i, (text, colour) in enumerate(rows):
        cv2.putText(img, text, (10, 26 + 26 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.6, colour, 1, cv2.LINE_AA)
    if warn:
        cv2.putText(img, warn, (10, img.shape[0] - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.9, RED, 2, cv2.LINE_AA)


class Truck:
    """One forklift in the manual-drive session."""

    def __init__(self, fid: str, link, via: str):
        self.fid, self.link, self.via = fid, link, via
        self.has_fork = config.FORKLIFT_HAS_FORK.get(fid, True)
        self.pose, self.last_seen, self.tele = None, 0.0, None

    def tag_ok(self, now: float) -> bool:
        return now - self.last_seen < LOST_STOP_S


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--radio", action="store_true", help="use the radio base station even if a forklift is on USB")
    ap.add_argument("--video", help="also record the window to this .mp4")
    ap.add_argument("--forklift", choices=sorted(config.FORKLIFT_RADIO_IDS),
                    help="drive only this one (default: every forklift it can reach)")
    args = ap.parse_args()

    trucks: list[Truck] = []
    for fid in ([args.forklift] if args.forklift else sorted(config.FORKLIFT_RADIO_IDS)):
        try:
            link, via = pick_link(fid, args.radio)
        except Exception as e:     # not plugged in, no base: drive the others
            print(f"[drive_cam] {fid}: no link ({e})")
            continue
        trucks.append(Truck(fid, link, via))
        print(f"[drive_cam] {fid}: {via}")
    if not trucks:
        raise SystemExit("no forklift reachable")
    active = 0

    cam = Camera()
    det, loc = TagDetector(), Localizer(cam.K)
    w2p = Projector(loc)
    border = border_from_camera(loc, config.CAMERA_WIDTH, config.CAMERA_HEIGHT)

    speed, cmd, drive_until = 45, (0, 0), 0.0
    stop_on_lost = True
    status_msg, video, frames, t_fps, fps = "", None, 0, time.time(), 0.0
    print("[drive_cam] click the window, then 1/2 to pick a forklift and w/a/s/d.")
    try:
        while True:
            now = time.monotonic()
            frame = cam.read()
            sightings = det.detect(frame)
            poses = loc.process(sightings)
            if border is None and loc.H is not None:
                border = border_from_camera(loc, config.CAMERA_WIDTH, config.CAMERA_HEIGHT)
            for t in trucks:
                if t.fid in poses:
                    t.pose, t.last_seen = poses[t.fid], now
            me = trucks[active]

            # Wheels: hold-to-drive for the active forklift, the others held stopped
            # (which also keeps their watchdogs fed).
            driving = now < drive_until and cmd != (0, 0)
            warn = ""
            if driving and stop_on_lost and not me.tag_ok(now):
                driving, drive_until = False, 0.0
                me.link.stop("tag_lost")
                status_msg = f"stopped {me.fid}: its tags are lost"
            if me.pose is not None and border and not in_view(border, me.pose.x, me.pose.y, me.pose.theta, me.fid):
                warn = f"{me.fid} at the edge of the camera view"
            for t in trucks:
                if t is me and driving:
                    t.link.wheels(*cmd)
                else:
                    t.link.wheels(0, 0)
                t.tele = t.link.poll()

            frames += 1
            if time.time() - t_fps >= 1.0:
                fps, frames, t_fps = frames / (time.time() - t_fps), 0, time.time()
            view = frame.copy()
            draw_scene(view, sightings, None, w2p, border, True, ring1_route=False)
            if loc.H is not None:
                for t in trucks:
                    if t.pose is not None:
                        ok = t.tag_ok(now)
                        draw_pose(view, t.pose, w2p, (GREEN if t is me else GREY) if ok else RED,
                                  thick=3 if t is me else 1, label=t.fid)
            rows = [(f"driving {me.fid}   speed {speed}   L={cmd[0] if driving else 0}  R={cmd[1] if driving else 0}"
                     f"   {'DRIVING' if driving else 'stopped'}", GREEN if driving else WHITE)]
            for i, t in enumerate(trucks):
                tags = ",".join(str(s.tag_id) for s in sightings if config.FORKLIFT_TAGS.get(s.tag_id) == t.fid)
                age = now - t.last_seen if t.last_seen else float("inf")
                where = (f"x {t.pose.x * 1000:5.0f} y {t.pose.y * 1000:5.0f} mm {math.degrees(t.pose.theta):4.0f} deg"
                         if t.pose else "not seen yet")
                seen = f"tags {tags}" if tags else f"LOST {age:.1f}s" if t.last_seen else "no tag"
                rows.append((f"{'>' if t is me else ' '}{i + 1} {t.fid} [{t.via}] says {t.tele.state if t.tele else '-'}"
                             f"  {seen}  {where}", (WHITE if t.tag_ok(now) else RED) if t is me else GREY))
            rows.append((f"camera {fps:.0f} fps   stop-if-lost {'ON' if stop_on_lost else 'OFF'}", WHITE))
            rows.append((f"1/2/TAB pick  w/s/a/d drive  SPACE stop all  {'r/f fork  ' if me.has_fork else ''}"
                         "+/- speed  x lost-stop  q quit", (180, 180, 180)))
            if status_msg:
                rows.append((status_msg, ORANGE))
            draw_panel(view, rows, warn)
            cv2.imshow("Forklifts: manual drive", view)
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
            pick = {ord(str(i + 1)): i for i in range(len(trucks))}
            if k == ord("q"):
                break
            if k in pick or k == 9:                    # 9 = TAB
                new = pick[k] if k in pick else (active + 1) % len(trucks)
                if new != active:
                    me.link.stop("switch")
                    active, cmd, drive_until = new, (0, 0), 0.0
                    status_msg = f"now driving {trucks[active].fid}"
            elif k in moves:
                cmd, drive_until, status_msg = moves[k], now + HOLD_S, ""
            elif k == ord(" "):
                cmd, drive_until = (0, 0), 0.0
                for t in trucks:
                    t.link.stop("space")
                status_msg = "stopped all (space)"
            elif k in (ord("r"), ord("f")) and me.has_fork:
                me.link.fork(config.FORK_SPEED if k == ord("r") else -config.FORK_SPEED, config.FORK_STEP_MS)
            elif k in (ord("+"), ord("=")):
                speed = min(100, speed + 5)
            elif k == ord("-"):
                speed = max(config.MIN_SPEED, speed - 5)
            elif k == ord("x"):
                stop_on_lost = not stop_on_lost
    finally:
        for t in trucks:
            try:
                t.link.close()
            except Exception:
                pass
        cam.close()
        if video is not None:
            video.release()
            print(f"[drive_cam] saved {args.video}")
        cv2.destroyAllWindows()
        os._exit(0)   # pupil_apriltags can segfault while the interpreter shuts down


if __name__ == "__main__":
    main()
