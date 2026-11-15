"""Behavior-tree runner. Lives in the Lambdas (no long-running process):

  start(task_id, fid, tree)   called by Dispatch after a tree is validated and a forklift chosen
  on_event(fid, task, event)  called by Ingest when that forklift reports done / fault

Each call ticks the tree until a leaf is waiting on the forklift (status "running") or the
tree finishes. Per-node status lives in the task's `tree` attribute in FleetTasks, so the
dashboard can render it live from tasks/{id}/status.

Leaf -> action:
  GoTo(n)      traffic.start_route: reserved, windowed A* route   (waits for "done")
  Pick(p)      publish a 1-node path with action "pick"          (waits for "done")
  Drop(s)      publish a 1-node path with action "drop"          (waits for "done")
  WaitUntil    succeeds immediately for now (Ring 4: edge locks)
"""

from __future__ import annotations

import logging

import json
import os
import time

from common import from_ddb, publish, s3, table, to_ddb
from fleet_shared import topics
from fleet_shared.graph import Graph
from fleet_shared.messages import Path, TaskStatus, now_ms
from fleet_shared.sim import score
import traffic

log = logging.getLogger()
GRAPH = Graph.load()


class Ctx:
    def __init__(self, fid: str, task_id: str):
        self.fid, self.task_id = fid, task_id
        item = table("FORKLIFTS_TABLE").get_item(Key={"forklift_id": fid}).get("Item") or {}
        self.node = item.get("node")


# ---------------------------------------------------------------- ticking
def _start_leaf(n: dict, ctx: Ctx) -> str:
    t = n["type"]
    if t == "WaitUntil":
        return "success"
    if t == "GoTo":
        try:
            moving = traffic.start_route(ctx.fid, n["node"], ctx.task_id)
        except ValueError as e:
            n["error"] = str(e)
            return "failure"
        return "running" if moving else "success"
    if t in ("Pick", "Drop"):
        if t == "Pick":
            where = table("PALLETS_TABLE").get_item(Key={"pallet_id": n["pallet"]}).get("Item", {}).get("node")
            if where != ctx.node:
                n["error"] = f"{n['pallet']} is at {where}, forklift at {ctx.node}"
                return "failure"
        here = GRAPH.nodes[ctx.node]
        table("FORKLIFTS_TABLE").update_item(Key={"forklift_id": ctx.fid}, UpdateExpression="REMOVE step_sent_at")
        path = Path(task_id=ctx.task_id, nodes=[{"id": here.id, "x": here.x, "y": here.y}],
                    action="pick" if t == "Pick" else "drop")
        publish(topics.path(ctx.fid), path.to_dict())
        return "running"
    raise ValueError(f"not a leaf: {t}")


def _reset(n: dict) -> None:
    n["status"] = "pending"
    n.pop("error", None)
    for c in n.get("children", []):
        _reset(c)
    if "child" in n:
        _reset(n["child"])


def tick(n: dict, ctx: Ctx) -> str:
    """Advance node n. Returns success | failure | running."""
    if n["status"] in ("success", "failure"):
        return n["status"]
    t = n["type"]
    if t == "Sequence":
        n["status"] = "running"
        for c in n["children"]:
            s = tick(c, ctx)
            if s != "success":
                n["status"] = s
                return s
        n["status"] = "success"
    elif t == "Fallback":
        n["status"] = "running"
        for c in n["children"]:
            s = tick(c, ctx)
            if s != "failure":
                n["status"] = s
                return s
        n["status"] = "failure"
    elif t == "Retry":
        n["status"] = "running"
        while True:
            s = tick(n["child"], ctx)
            if s != "failure" or n["attempts"] + 1 >= n["n"]:
                n["status"] = s
                break
            n["attempts"] += 1
            _reset(n["child"])
    else:  # leaf
        if n["status"] == "running":
            return "running"  # still waiting on the forklift
        n["status"] = _start_leaf(n, ctx)
    return n["status"]


def _running_leaf(n: dict) -> dict | None:
    if n["status"] != "running":
        return None
    for c in n.get("children", []):
        hit = _running_leaf(c)
        if hit:
            return hit
    if "child" in n:
        return _running_leaf(n["child"])
    return n if n["type"] in ("GoTo", "Pick", "Drop", "WaitUntil") else None


# ---------------------------------------------------------------- persistence
def _save_and_report(task: dict, tree: dict, fid: str) -> None:
    state = {"success": "done", "failure": "failed"}.get(tree["status"], "running")
    task_id = task["task_id"]
    upd = {"tree": tree, "status": state}
    if state != "running":
        upd["finished_at"] = now_ms()
    table("TASKS_TABLE").update_item(
        Key={"task_id": task_id},
        UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in upd),
        ExpressionAttributeNames={f"#{k}": k for k in upd},
        ExpressionAttributeValues={f":{k}": v for k, v in to_ddb(upd).items()},
    )
    if state != "running":
        table("FORKLIFTS_TABLE").update_item(Key={"forklift_id": fid}, UpdateExpression="REMOVE current_task")
    err = None
    if state == "failed":
        leaf = _first_error(tree)
        err = leaf or "plan step failed"
    publish(topics.task_status(task_id), TaskStatus(
        state=state, tree=tree, assigned_to=fid, planner_version=task.get("planner_version"),
        text=task.get("text"), error=err).to_dict())
    if state != "running":
        record_episode(task, tree, fid, state == "done")
        if task.get("mission_id"):
            import mission
            mission.on_task_finished(task, state == "done")
    log.info("task %s -> %s", task_id, state)


def record_episode(task: dict, tree: dict, fid: str, success: bool) -> dict:
    """Grade the finished task by its real outcome and store it as an episode (S3 + FleetEpisodes).

    Real duration comes from timestamps. Distance and wait are estimates for now: distance from
    the planned route, wait = real time beyond what the simulator predicted. The edge
    episode-logger will replace these with measured values.
    """
    now = now_ms()
    started = int(task.get("started_at") or task.get("created_at") or now)
    duration_s = max(0.0, (now - started) / 1000)
    pred = task.get("sim") or {}
    distance = float(pred.get("distance_m", 0.0))
    wait_s = max(0.0, duration_s - float(pred.get("duration_s", duration_s)))
    latency_s = int(task.get("llm_latency_ms", 0)) / 1000
    outcome = {"success": success, "distance_m": round(distance, 3), "wait_s": round(wait_s, 2),
               "duration_s": round(duration_s, 2), "replans": 0, "stops": 0,
               "cost": score(success, distance, wait_s, llm_latency_s=latency_s)}
    episode = {"task_id": task["task_id"], "text": task.get("text"), "planner_version": task.get("planner_version"),
               "forklift": fid, "snapshot": task.get("snapshot"), "tree": tree, "outcome": outcome,
               "sim": pred, "sim_gap_s": round(duration_s - float(pred.get("duration_s", 0)), 2),
               "shadow": task.get("shadow"), "created_at": now}
    day = time.strftime("%Y-%m-%d", time.gmtime(now / 1000))
    key = f"episodes/{day}/{task['task_id']}.json"
    try:
        s3().put_object(Bucket=os.environ["DATA_BUCKET"], Key=key, Body=json.dumps(episode, default=str).encode(),
                        ContentType="application/json")
        table("EPISODES_TABLE").put_item(Item=to_ddb({
            "task_id": task["task_id"], "planner_version": task.get("planner_version") or "-",
            "snapshot_uri": f"s3://{os.environ['DATA_BUCKET']}/{key}", "cost": outcome["cost"],
            "sim_cost": pred.get("cost"), "success": success, "created_at": now}))
    except Exception:
        log.exception("episode write failed for %s", task["task_id"])
    publish(topics.task_outcome(task["task_id"]), outcome)
    return outcome


def _first_error(n: dict) -> str | None:
    if n.get("error"):
        return n["error"]
    for c in n.get("children", []) + ([n["child"]] if "child" in n else []):
        e = _first_error(c)
        if e:
            return e
    return None


def start(task: dict, fid: str, tree: dict) -> None:
    ctx = Ctx(fid, task["task_id"])
    tick(tree, ctx)
    _save_and_report(task, tree, fid)


def resend_step(fid: str, task_id: str) -> bool:
    """The forklift is idle mid-task with no route: the Pick/Drop (single-node) path was lost.
    Re-send that step. Returns True if something was re-sent."""
    task = from_ddb(table("TASKS_TABLE").get_item(Key={"task_id": task_id}).get("Item") or {})
    tree = task.get("tree")
    if not tree or task.get("status") != "running":
        return False
    leaf = _running_leaf(tree)
    if not leaf or leaf["type"] not in ("Pick", "Drop"):
        return False
    here = GRAPH.nodes.get(Ctx(fid, task_id).node)
    if not here:
        return False
    log.warning("%s idle during %s: re-sending the step", fid, leaf["type"])
    publish(topics.path(fid), Path(task_id=task_id, nodes=[{"id": here.id, "x": here.x, "y": here.y}],
                                   action="pick" if leaf["type"] == "Pick" else "drop").to_dict())
    return True


def on_event(fid: str, task_id: str, event_type: str, error: str | None = None) -> None:
    """Forklift `fid` reported done/fault while running `task_id`."""
    task = from_ddb(table("TASKS_TABLE").get_item(Key={"task_id": task_id}).get("Item") or {})
    tree = task.get("tree")
    if not tree or task.get("status") != "running":
        return
    leaf = _running_leaf(tree)
    if not leaf:
        log.warning("task %s has no running leaf; ignoring %s", task_id, event_type)
        return
    if event_type == "done":
        leaf["status"] = "success"
        if leaf["type"] == "Pick":
            table("PALLETS_TABLE").put_item(Item={"pallet_id": leaf["pallet"], "node": f"on:{fid}", "last_seen": now_ms()})
        elif leaf["type"] == "Drop":
            carried = [p for p in from_ddb(table("PALLETS_TABLE").scan()["Items"]) if p["node"] == f"on:{fid}"]
            ship = GRAPH.docks_ship and GRAPH.nodes[leaf["slot"]].type == "dock"
            for p in carried:   # shipping map: the truck takes it (off the floor, dock free again)
                where = f"shipped:{leaf['slot']}" if ship else leaf["slot"]
                table("PALLETS_TABLE").put_item(Item={"pallet_id": p["pallet_id"], "node": where, "last_seen": now_ms()})
    else:
        leaf["status"] = "failure"
        leaf["error"] = error or f"forklift reported {event_type}"
    tick(tree, Ctx(fid, task_id))
    _save_and_report(task, tree, fid)
