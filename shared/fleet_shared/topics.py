"""MQTT topic names. Always build topics with these helpers, never by hand."""

import re

FORKLIFT_ID = re.compile(r"^fl-\d{2}$")


def _check(forklift_id: str) -> str:
    if not FORKLIFT_ID.match(forklift_id):
        raise ValueError(f"bad forklift id {forklift_id!r}, expected like 'fl-01'")
    return forklift_id


def pose(fid: str) -> str:       return f"fleet/{_check(fid)}/pose"        # edge -> cloud/forklift, 10 Hz
def telemetry(fid: str) -> str:  return f"fleet/{_check(fid)}/telemetry"   # forklift -> cloud, 1 Hz
def path(fid: str) -> str:       return f"fleet/{_check(fid)}/path"        # cloud -> forklift
def event(fid: str) -> str:      return f"fleet/{_check(fid)}/event"       # forklift -> cloud
def stop(fid: str) -> str:       return f"fleet/{_check(fid)}/stop"        # edge/cloud -> forklift
def goto(fid: str) -> str:       return f"fleet/{_check(fid)}/goto"        # dashboard -> cloud (Ring 2 "click a node")

DETECTIONS = "warehouse/detections"
TASKS_NEW = "tasks/new"


def task_status(task_id: str) -> str:   return f"tasks/{task_id}/status"
def task_shadow(task_id: str) -> str:   return f"tasks/{task_id}/shadow"
def task_outcome(task_id: str) -> str:  return f"tasks/{task_id}/outcome"


def forklift_from_topic(topic: str) -> str:
    """'fleet/fl-01/pose' -> 'fl-01'"""
    parts = topic.split("/")
    if len(parts) != 3 or parts[0] != "fleet":
        raise ValueError(f"not a fleet topic: {topic}")
    return _check(parts[1])
