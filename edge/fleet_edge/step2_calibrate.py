"""STEP 2: calibrate the camera lens with a printed chessboard (removes fisheye bend).

  uv run python -m fleet_edge.step2_calibrate

Print a chessboard with 9x6 INNER corners (10x7 squares), tape it flat to cardboard.
Hold it in front of the camera at many angles and distances. Press SPACE to capture when
the corners are drawn in colour. Capture ~15 views, then press c to compute and save.
A reprojection error under 0.5 pixels is good; under 1.0 is fine.
"""

from pathlib import Path

import cv2
import numpy as np

from . import config

PATTERN = (9, 6)


def main():
    cap = cv2.VideoCapture(config.CAMERA_INDEX)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAMERA_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAMERA_HEIGHT)
    objp = np.zeros((PATTERN[0] * PATTERN[1], 3), np.float32)
    objp[:, :2] = np.mgrid[0:PATTERN[0], 0:PATTERN[1]].T.reshape(-1, 2)
    obj_pts, img_pts, size = [], [], None
    while True:
        ok, frame = cap.read()
        if not ok:
            raise SystemExit("camera read failed")
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        size = gray.shape[::-1]
        found, corners = cv2.findChessboardCorners(gray, PATTERN)
        view = frame.copy()
        if found:
            cv2.drawChessboardCorners(view, PATTERN, corners, found)
        cv2.putText(view, f"captured {len(obj_pts)}  SPACE=capture c=compute q=quit",
                    (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        cv2.imshow("Step 2: calibrate", view)
        k = cv2.waitKey(1) & 0xFF
        if k == ord("q"):
            return
        if k == ord(" ") and found:
            corners = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1),
                                       (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001))
            obj_pts.append(objp)
            img_pts.append(corners)
        if k == ord("c"):
            if len(obj_pts) < 10:
                print("capture at least 10 views first")
                continue
            break
    err, K, dist, _, _ = cv2.calibrateCamera(obj_pts, img_pts, size, None, None)
    Path(config.CALIBRATION_FILE).parent.mkdir(parents=True, exist_ok=True)
    np.savez(config.CALIBRATION_FILE, K=K, dist=dist)
    print(f"saved {config.CALIBRATION_FILE}; reprojection error = {err:.3f} px")
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
