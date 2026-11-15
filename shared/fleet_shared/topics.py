"""MQTT topic names. Always build topics with these helpers, never by hand.

Every topic is scoped to a SITE (a warehouse): "<site>/fleet/fl-01/pose". Two sites share one
AWS IoT endpoint without ever seeing each other's messages:
  demo  the simulated site (hosted simulated fleet, mission map)
  wh1   Warehouse 1, the real tabletop (edge laptop + Free-WiLi)
The site comes from the SITE environment variable (Lambdas get it from their stack; the edge
bridge defaults to wh1, the simulated fleet to demo).
"""

import os
import re

SITE = os.environ.get("SITE", "demo")
SITES = {"demo": "Demo site (simulated)", "wh1": "Warehouse 1"}
FORKLIFT_ID = re.compile(r"^fl-\d{2}$")


def _check(forklift_id: str) -> str:
    if not FORKLIFT_ID.match(forklift_id):
        raise ValueError(f"bad forklift id {forklift_id!r}, expected like 'fl-01'")
    return forklift_id


def _s(rest: str) -> str:
    return f"{SITE}/{rest}"


def pose(fid: str) -> str:       return _s(f"fleet/{_check(fid)}/pose")        # edge -> cloud/forklift, 10 Hz
def telemetry(fid: str) -> str:  return _s(f"fleet/{_check(fid)}/telemetry")   # forklift -> cloud, 1 Hz
def path(fid: str) -> str:       return _s(f"fleet/{_check(fid)}/path")        # cloud -> forklift
def event(fid: str) -> str:      return _s(f"fleet/{_check(fid)}/event")       # forklift -> cloud
def stop(fid: str) -> str:       return _s(f"fleet/{_check(fid)}/stop")        # edge/cloud -> forklift
def goto(fid: str) -> str:       return _s(f"fleet/{_check(fid)}/goto")        # dashboard -> cloud


DETECTIONS = _s("warehouse/detections")   # edge (camera detector / demo person) -> edge safety guard, dashboard
LOCKS = _s("warehouse/locks")             # cloud -> dashboard, on change
CAMERA = _s("warehouse/camera")           # edge -> dashboard, ~4 fps, raw JPEG bytes (not JSON)
TASKS_NEW = _s("tasks/new")               # dashboard -> cloud
PLANNERS_PROMOTED = _s("planners/promoted")


def health(source: str) -> str:          return _s(f"warehouse/health/{source}")   # 1 Hz, retained
def task_status(task_id: str) -> str:    return _s(f"tasks/{task_id}/status")
def task_shadow(task_id: str) -> str:    return _s(f"tasks/{task_id}/shadow")
def task_outcome(task_id: str) -> str:   return _s(f"tasks/{task_id}/outcome")
def mission_status(mid: str) -> str:     return _s(f"missions/{mid}/status")


def forklift_from_topic(topic: str) -> str:
    """'demo/fleet/fl-01/pose' -> 'fl-01'"""
    parts = topic.split("/")
    if len(parts) != 4 or parts[1] != "fleet":
        raise ValueError(f"not a fleet topic: {topic}")
    return _check(parts[2])
