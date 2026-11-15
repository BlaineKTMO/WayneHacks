"""Behavior-tree runner. Lives in the Lambdas (no long-running process):

  start(task_id, fid, tree)   called by Dispatch after a tree is validated and a forklift chosen
  on_event(fid, task, event)  called by Ingest when that forklift reports done / fault

Each call ticks the tree until a leaf is waiting on the forklift (status "running") or the
tree finishes. Per-node status lives in the task's `tree` attribute in FleetTasks, so the
dashboard can render it live from tasks/{id}/status.

Leaf -> action:
  GoTo(n)      plan A* route, publish fleet/{id}/path            (waits for "done")
  Pick(p)      publish a 1-node path with action "pick"          (waits for "done")
  Drop(s)      publish a 1-node path with action "drop"          (waits for "done")
  WaitUntil    succeeds immediately for now (Ring 4: edge locks)
"""

from __future__ import annotations

import logging

from common import from_ddb, publish, table, to_ddb
from fleet_shared import topics
from fleet_shared.graph import Graph
from fleet_shared.messages import Path, TaskStatus, now_ms
from route_planner import plan

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
        if ctx.node == n["node"]:
            return "success"
        try:
            path = plan(ctx.fid, n["node"], ctx.task_id)
        except ValueError as e:
            n["error"] = str(e)
            return "failure"
        publish(topics.path(ctx.fid), path)
        return "running"
    if t in ("Pick", "Drop"):
        if t == "Pick":
            where = table("PALLETS_TABLE").get_item(Key={"pallet_id": n["pallet"]}).get("Item", {}).get("node")
            if where != ctx.node:
                n["error"] = f"{n['pallet']} is at {where}, forklift at {ctx.node}"
                return "failure"
        here = GRAPH.nodes[ctx.node]
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
    log.info("task %s -> %s", task_id, state)


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


def on_event(fid: str, task_id: str, event_type: str) -> None:
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
            for p in carried:
                table("PALLETS_TABLE").put_item(Item={"pallet_id": p["pallet_id"], "node": leaf["slot"], "last_seen": now_ms()})
    else:
        leaf["status"] = "failure"
        leaf["error"] = f"forklift reported {event_type}"
    tick(tree, Ctx(fid, task_id))
    _save_and_report(task, tree, fid)
