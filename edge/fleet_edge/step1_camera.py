"""STEP 1: is the camera working and looking at the whole table?

  uv run python -m fleet_edge.step1_camera

You should see a live window. Check: all 4 corner tags visible, camera looking straight down,
image sharp. Press q to quit, s to save a snapshot to edge/logs/.
"""

from pathlib import Path

import cv2

from . import config


def main():
    cap = cv2.VideoCapture(config.CAMERA_INDEX)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAMERA_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAMERA_HEIGHT)
    if not cap.isOpened():
        raise SystemExit(f"Camera {config.CAMERA_INDEX} didn't open. Change CAMERA_INDEX in config.py.")
    n = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            raise SystemExit("Camera stopped sending frames.")
        h, w = frame.shape[:2]
        cv2.putText(frame, f"{w}x{h}  q=quit s=snapshot", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        cv2.imshow("Step 1: camera", frame)
        k = cv2.waitKey(1) & 0xFF
        if k == ord("q"):
            break
        if k == ord("s"):
            Path("edge/logs").mkdir(parents=True, exist_ok=True)
            cv2.imwrite(f"edge/logs/snapshot_{n}.jpg", frame)
            print(f"saved edge/logs/snapshot_{n}.jpg")
            n += 1
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
