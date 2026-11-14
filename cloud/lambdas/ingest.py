"""IoT rule target: keep the Forklifts table current from telemetry, events and sampled pose.

The rule SQL adds `forklift_id` (from the topic) and `kind` ("telemetry" | "event" | "pose").
"""

import logging

from common import table, to_ddb
from fleet_shared.messages import Event, Pose, Telemetry, now_ms

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
        if parsed.type == "done":
            updates["current_task"] = None
            # TODO(Ring 3): tick the behavior-tree runner for this forklift's task.

    names = {f"#{k}": k for k in updates}
    values = {f":{k}": v for k, v in to_ddb(updates).items()}
    table("FORKLIFTS_TABLE").update_item(
        Key={"forklift_id": fid},
        UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in updates),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )
