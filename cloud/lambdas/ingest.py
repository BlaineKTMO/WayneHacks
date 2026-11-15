"""IoT rule target: keep the Forklifts table current from telemetry, events and sampled pose.

The rule SQL adds `forklift_id` (from the topic) and `kind` ("telemetry" | "event" | "pose").
"""

import logging

import traffic
import tree_runner

from common import publish, table, to_ddb
from fleet_shared import topics
from fleet_shared.messages import Event, Pose, TaskStatus, Telemetry, now_ms

log = logging.getLogger()
log.setLevel(logging.INFO)

PARSERS = {"telemetry": Telemetry, "event": Event, "pose": Pose}


def handler(msg, _ctx):
    fid = msg.pop("forklift_id")
    kind = msg.pop("kind")
    try:
        parsed = PARSERS[kind].from_dict(msg)
    except (KeyError, ValueError) as e:
        log.warning("dropping bad %s from %s: %s (%s)", kind, fid, e, msg)
        return

    updates = {"last_seen": now_ms()}
    if kind == "telemetry":
        updates |= {"state": parsed.state, "battery_v": parsed.battery_v,
                    "motor_a": parsed.motor_a, "node": parsed.node}
    elif kind == "pose":
        updates |= {"pose": {"x": parsed.x, "y": parsed.y, "theta": parsed.theta, "conf": parsed.conf}}
    elif kind == "event":
        updates |= {"last_event": parsed.type}
        if parsed.node:
            updates["node"] = parsed.node

    names = {f"#{k}": k for k in updates}
    values = {f":{k}": v for k, v in to_ddb(updates).items()}
    old = table("FORKLIFTS_TABLE").update_item(
        Key={"forklift_id": fid},
        UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in updates),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )

    if kind == "telemetry":
        traffic.renew(fid, parsed.node)
    elif kind == "event":
        if parsed.type == "node_reached":
            traffic.on_node_reached(fid, parsed.node)
        elif parsed.type in ("done", "fault"):
            traffic.on_done(fid, parsed.node)
            on_task_event(fid, parsed.type)
        elif parsed.type == "blocked":
            gave_up = traffic.on_blocked(fid)
            if gave_up:
                on_task_event(fid, "fault", gave_up)


def on_task_event(fid: str, event_type: str, error: str | None = None) -> None:
    """The forklift finished (or faulted) its current step: advance whatever task it's running."""
    item = table("FORKLIFTS_TABLE").get_item(Key={"forklift_id": fid}).get("Item") or {}
    task_id = item.get("current_task")
    if not task_id:
        return
    task = table("TASKS_TABLE").get_item(Key={"task_id": task_id}).get("Item") or {}
    if task.get("tree"):
        tree_runner.on_event(fid, task_id, event_type, error)  # behavior tree: next step
        return
    # Plain goto from the map: the task is the single route.
    state = "done" if event_type == "done" else "failed"
    table("TASKS_TABLE").update_item(
        Key={"task_id": task_id},
        UpdateExpression="SET #s = :s, finished_at = :t",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": state, ":t": now_ms()},
    )
    table("FORKLIFTS_TABLE").update_item(Key={"forklift_id": fid}, UpdateExpression="REMOVE current_task")
    publish(topics.task_status(task_id), TaskStatus(state=state, assigned_to=fid, text=task.get("text"),
                                                    error=error).to_dict())
