"""Hands-free lens calibration: no window, no key presses.

  uv run python -m fleet_edge.calib_auto              # up to 3 minutes, 25 views
  uv run python -m fleet_edge.calib_auto --views 30 --seconds 240

Hold the chessboard (CHESSBOARD_INNER in config.py) under the camera and keep moving it
slowly: all over the image (corners and edges too), near and far, and tilted 20-40
degrees in different directions. A view is kept automatically whenever the whole board
is visible somewhere new. Hold still for a moment at each spot (motion blur is rejected).

Writes edge/calibration/camera.npz (same file step2_calibrate writes) only if the
reprojection error is under --max-error (1.0 px), plus edge/calibration/coverage.jpg
showing where the kept views were and views.npz (the raw detections).
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2
import numpy as np

from . import config
from .localizer import open_camera

PATTERN = config.CHESSBOARD_INNER
SUBPIX = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)


def describe(corners: np.ndarray, w: int, h: int) -> np.ndarray:
    """Where the board is, how big, and how tilted: used to keep only new-looking views."""
    pts = corners.reshape(-1, 2)
    cx, cy = pts.mean(axis=0)
    x0, y0 = pts.min(axis=0)
    x1, y1 = pts.max(axis=0)
    size = ((x1 - x0) * (y1 - y0)) ** 0.5 / ((w * h) ** 0.5)
    # Tilt proxy: how unequal opposite edges of the board look (perspective).
    c = pts.reshape(PATTERN[1], PATTERN[0], 2)
    top, bot = np.linalg.norm(c[0, -1] - c[0, 0]), np.linalg.norm(c[-1, -1] - c[-1, 0])
    left, right = np.linalg.norm(c[-1, 0] - c[0, 0]), np.linalg.norm(c[-1, -1] - c[0, -1])
    return np.array([cx / w, cy / h, size, (top - bot) / (top + bot), (left - right) / (left + right)])


def sharpness(gray: np.ndarray, corners: np.ndarray) -> float:
    x, y, bw, bh = cv2.boundingRect(corners.astype(np.float32))
    roi = gray[max(0, y):y + bh, max(0, x):x + bw]
    return float(cv2.Laplacian(roi, cv2.CV_64F).var()) if roi.size else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--views", type=int, default=25)
    ap.add_argument("--seconds", type=float, default=180)
    ap.add_argument("--min-sharpness", type=float, default=60.0)
    ap.add_argument("--max-error", type=float, default=1.0, help="don't save above this (px)")
    args = ap.parse_args()

    cap = open_camera()
    objp = np.zeros((PATTERN[0] * PATTERN[1], 3), np.float32)
    objp[:, :2] = np.mgrid[0:PATTERN[0], 0:PATTERN[1]].T.reshape(-1, 2)

    kept_desc, img_pts, kept_corners = [], [], []
    size = None
    t_end = time.time() + args.seconds
    last_keep = 0.0
    last_report = 0.0
    seen = blurry = 0
    print(f"[calib] looking for a {PATTERN[0]}x{PATTERN[1]} inner-corner board; "
          f"target {args.views} views, {args.seconds:.0f} s max", flush=True)

    while time.time() < t_end and len(img_pts) < args.views:
        ok, frame = cap.read()
        if not ok:
            raise SystemExit("camera read failed")
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape
        size = (w, h)

        # Detect on a half-size image (fast), refine on the full one (accurate).
        small = cv2.resize(gray, (w // 2, h // 2))
        found, corners = cv2.findChessboardCorners(
            small, PATTERN, cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE | cv2.CALIB_CB_FAST_CHECK)
        now = time.time()
        if now - last_report > 5:
            last_report = now
            print(f"[calib] {len(img_pts)}/{args.views} kept, board seen in {seen} frames, "
                  f"{blurry} too blurry, {t_end - now:.0f} s left", flush=True)
        if not found:
            continue
        seen += 1
        corners = cv2.cornerSubPix(gray, corners * 2.0, (11, 11), (-1, -1), SUBPIX)
        if sharpness(gray, corners) < args.min_sharpness:
            blurry += 1
            continue
        d = describe(corners, w, h)
        novel = all(np.abs(d - k)[:3].max() > 0.08 or np.abs(d - k)[3:].max() > 0.06 for k in kept_desc)
        if not novel or now - last_keep < 0.7:
            continue
        kept_desc.append(d)
        img_pts.append(corners)
        kept_corners.append(corners)
        last_keep = now
        print(f"[calib] kept view {len(img_pts)}: centre=({d[0]:.2f},{d[1]:.2f}) "
              f"size={d[2]:.2f} tilt=({d[3]:+.2f},{d[4]:+.2f})", flush=True)

    cap.release()
    if len(img_pts) < 10:
        raise SystemExit(f"[calib] only {len(img_pts)} views (need 10+). Move the board around more and rerun.")

    out = Path(config.CALIBRATION_FILE)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez(out.parent / "views.npz", corners=np.array(img_pts), desc=np.array(kept_desc),
             size=np.array(size), pattern=np.array(PATTERN))
    solve(img_pts, kept_desc, size, out, args.max_error)


def per_view_errors(objp, img_pts, K, dist, rvecs, tvecs):
    errs = []
    for ip, r, t in zip(img_pts, rvecs, tvecs):
        proj, _ = cv2.projectPoints(objp, r, t, K, dist)
        errs.append(float(np.sqrt(np.mean(np.sum((proj - ip) ** 2, axis=2)))))
    return np.array(errs)


def solve(img_pts, descs, size, out: Path, max_error: float) -> bool:
    """Calibrate, drop views that disagree with the rest, recalibrate, save if good."""
    objp = np.zeros((PATTERN[0] * PATTERN[1], 3), np.float32)
    objp[:, :2] = np.mgrid[0:PATTERN[0], 0:PATTERN[1]].T.reshape(-1, 2)
    # k3 fixed at 0: webcam lenses don't need it, and with few tilted views it overfits wildly.
    flags = cv2.CALIB_FIX_K3
    pts, descs = list(img_pts), list(descs)
    for _ in range(3):
        err, K, dist, rv, tv = cv2.calibrateCamera([objp] * len(pts), pts, tuple(int(v) for v in size),
                                                   None, None, flags=flags)
        e = per_view_errors(objp, pts, K, dist, rv, tv)
        keep = e <= max(2.5 * np.median(e), 0.5)
        if keep.all() or keep.sum() < 10:
            break
        print(f"[calib] dropping {int((~keep).sum())} view(s) that disagree with the rest "
              f"(errors {np.round(e[~keep], 2).tolist()} px)")
        pts = [p for p, k in zip(pts, keep) if k]
        descs = [d for d, k in zip(descs, keep) if k]

    d = np.array(descs)
    tilt = np.abs(d[:, 3:]).max(axis=1)
    verdict = "good" if err < 0.5 else "fine" if err < max_error else "POOR"
    print(f"[calib] {len(pts)} views, reprojection error {err:.3f} px ({verdict})")
    print(f"[calib] fx={K[0,0]:.1f} fy={K[1,1]:.1f} cx={K[0,2]:.1f} cy={K[1,2]:.1f} "
          f"dist(k1,k2,p1,p2)={np.round(dist.ravel()[:4], 4).tolist()}")
    print(f"[calib] coverage x {d[:,0].min():.2f}-{d[:,0].max():.2f}, y {d[:,1].min():.2f}-{d[:,1].max():.2f}, "
          f"size {d[:,2].min():.2f}-{d[:,2].max():.2f}, tilted views (>0.10): {int((tilt > 0.10).sum())}")
    if (tilt > 0.10).sum() < 5:
        print("[calib] WARNING: fewer than 5 tilted views. Tilt the board 20-40 degrees toward/away "
              "from the camera, not just rotated flat on the table.")

    cov = np.zeros((int(size[1]), int(size[0]), 3), np.uint8)
    for c in pts:
        cv2.polylines(cov, [cv2.convexHull(c.astype(np.int32))], True, (0, 255, 0), 2)
    cv2.imwrite(str(out.parent / "coverage.jpg"), cov)

    if err >= max_error:
        print(f"[calib] NOT saved: error {err:.2f} px >= {max_error} px. {out} left unchanged.")
        return False
    np.savez(out, K=K, dist=dist)
    print(f"[calib] saved {out}")
    return True


if __name__ == "__main__":
    main()
