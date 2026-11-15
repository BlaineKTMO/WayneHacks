"""IoT rule target for fleet/{id}/goto: plan an A* route and publish fleet/{id}/path.

Ring 2: "click a node on the map, the forklift drives there".
Ring 4: routing and reservations go through traffic.start_route.
"""

from __future__ import annotations

import logging
import time
import uuid

import traffic
from common import from_ddb, publish, table, to_ddb
from fleet_shared import topics
from fleet_shared.graph import Graph
from fleet_shared.messages import Goto, Path, TaskStatus

log = logging.getLogger()
log.setLevel(logging.INFO)
GRAPH = Graph.load()


def current_node(forklift: dict) -> str | None:
    """Where the forklift is now: last reported node, else nearest node to last pose."""
    if forklift.get("node") in GRAPH.nodes:
        return forklift["node"]
    pose = forklift.get("pose")
    if pose:
        return GRAPH.nearest_node(pose["x"], pose["y"])
    return None


def plan(fid: str, goal: str, task_id: str) -> dict:
    """Pure-ish planning step, separated for unit tests."""
    item = from_ddb(table("FORKLIFTS_TABLE").get_item(Key={"forklift_id": fid}).get("Item") or {})
    start = current_node(item)
    if start is None:
        raise ValueError(f"{fid} has no known position yet")
    route = GRAPH.astar(start, goal)
    if route is None:
        raise ValueError(f"no route {start} -> {goal}")
    return Path(task_id=task_id, nodes=GRAPH.to_path_nodes(route)).to_dict()


def handler(msg, _ctx):
    fid = msg.pop("forklift_id")
    goto = Goto.from_dict(msg)
    task_id = goto.task_id or f"goto-{uuid.uuid4().hex[:8]}"
    if goto.node not in GRAPH.nodes:
        publish(topics.task_status(task_id), TaskStatus(state="failed", error=f"unknown node {goto.node}").to_dict())
        return
    try:
        moving = traffic.start_route(fid, goto.node, task_id)
    except ValueError as e:
        log.warning("%s", e)
        publish(topics.task_status(task_id), TaskStatus(state="failed", assigned_to=fid, error=str(e)).to_dict())
        return

    table("TASKS_TABLE").put_item(Item=to_ddb({
        "task_id": task_id, "text": f"goto {goto.node}", "assigned_to": fid,
        "status": "running" if moving else "done", "created_at": int(time.time() * 1000),
    }))
    if not moving:
        publish(topics.task_status(task_id), TaskStatus(state="done", assigned_to=fid, text=f"goto {goto.node}").to_dict())
        return
    table("FORKLIFTS_TABLE").update_item(
        Key={"forklift_id": fid}, UpdateExpression="SET current_task = :t",
        ExpressionAttributeValues={":t": task_id})
    publish(topics.task_status(task_id), TaskStatus(state="running", assigned_to=fid, text=f"goto {goto.node}").to_dict())
    log.info("%s -> %s", fid, goto.node)
