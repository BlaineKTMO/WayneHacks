"""STEP 3: see tags, build the pixel->metre map, check it's accurate.

  uv run python -m fleet_edge.step3_tags

Shows every tag with its ID. Once all 4 corner tags are seen, forklift tags show their
world position in metres and a heading arrow.

Accuracy check (goal: under 1 cm): put the forklift tag on a spot you've measured with a
ruler, e.g. (0.50, 0.50), and compare with what's printed on screen.
Press q to quit.
"""

import math

import cv2

from . import config
from .localizer import Camera, Localizer, TagDetector


def main():
    cam, det, loc = Camera(), TagDetector(), Localizer()
    while True:
        frame = cam.read()
        sightings = det.detect(frame)
        poses = loc.process(sightings)
        for s in sightings:
            c = tuple(int(v) for v in s.center_px)
            colour = (255, 200, 0) if s.tag_id in config.CORNER_TAGS else (0, 255, 0)
            cv2.polylines(frame, [s.corners_px.astype(int)], True, colour, 2)
            cv2.putText(frame, str(s.tag_id), c, cv2.FONT_HERSHEY_SIMPLEX, 0.8, colour, 2)
        for fid, p in poses.items():
            tag = next(s for s in sightings if config.FORKLIFT_TAGS.get(s.tag_id) == fid)
            c = tuple(int(v) for v in tag.center_px)
            cv2.putText(frame, f"{fid} x={p.x:.3f} y={p.y:.3f} th={math.degrees(p.theta):.0f}deg",
                        (c[0] + 15, c[1] + 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        corners_seen = sum(s.tag_id in config.CORNER_TAGS for s in sightings)
        msg = "map OK" if loc.H is not None else f"need 4 corner tags, see {corners_seen}"
        cv2.putText(frame, msg, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                    (0, 255, 0) if loc.H is not None else (0, 0, 255), 2)
        cv2.imshow("Step 3: tags", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
    cam.close()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
