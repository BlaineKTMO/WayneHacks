"""Missions: empty aisles into truck docks with the whole fleet.

    start({"A": ["dock_1"], "B": ["dock_2", "dock_3"], "C": ["dock_4"]})

Every pallet sitting in an assigned aisle becomes one move (GoTo slot, Pick, GoTo dock, Drop).
An aisle's pallets go to its docks in turn. Moves are handed to free forklifts, nearest
forklift to each pallet first; whenever a forklift finishes (or any forklift reports done),
the next pending moves go out. Moves run as ordinary behavior-tree tasks, so traffic control,
make-way, safety stops and episode grading all apply. Missions are built from the assignment
(no LLM): fast and predictable. Progress: missions/{id}/status.

Mission item (FleetMissions, key mission_id; plus a pointer item mission_id="current"):
  status: running | done | cancelled, assignments, moves: [{pallet, slot, dock, state, task_id, forklift}],
  rev (optimistic concurrency, like traffic.py), created_at, finished_at
"""

from __future__ import annotations

import logging
import time
import uuid

from botocore.exceptions import ClientError

from common import from_ddb, publish, table, to_ddb
from fleet_shared import topics
from fleet_shared.behavior_tree import with_status
from fleet_shared.graph import Graph
from fleet_shared.messages import now_ms

log = logging.getLogger()
GRAPH = Graph.load()
MAX_RETRIES = 8


def _t():
    return table("MISSIONS_TABLE")


def current_id() -> str | None:
    item = _t().get_item(Key={"mission_id": "current"}, ConsistentRead=True).get("Item")
    return item.get("active") if item else None


def get(mission_id: str) -> dict:
    return from_ddb(_t().get_item(Key={"mission_id": mission_id}, ConsistentRead=True).get("Item") or {})


def _mutate(mission_id: str, fn) -> dict | None:
    """fn(mission) -> mission or None (no change). Conditional on rev; retries on races."""
    for _ in range(MAX_RETRIES):
        m = get(mission_id)
        if not m:
            return None
        rev = m.get("rev", 0)
        new = fn(dict(m))
        if new is None:
            return m
        new["rev"] = rev + 1
        try:
            _t().put_item(Item=to_ddb(new), ConditionExpression="rev = :r", ExpressionAttributeValues={":r": rev})
            return new
        except ClientError as e:
            if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
    log.warning("mission %s: too many concurrent updates", mission_id)
    return None


def summary(m: dict) -> dict:
    moves = m.get("moves", [])
    per_dock: dict[str, dict] = {}
    for mv in moves:
        d = per_dock.setdefault(mv["dock"], {"planned": 0, "shipped": 0})
        d["planned"] += 1
        d["shipped"] += mv["state"] == "done"
    return {
        "mission_id": m["mission_id"], "state": m.get("status"), "assignments": m.get("assignments"),
        "total": len(moves), "done": sum(mv["state"] == "done" for mv in moves),
        "running": sum(mv["state"] == "running" for mv in moves), "failed": sum(mv["state"] == "failed" for mv in moves),
        "per_dock": per_dock,
        "active": {mv["forklift"]: f'{mv["pallet"]} {mv["slot"]} -> {mv["dock"]}' for mv in moves if mv["state"] == "running"},
        "created_at": m.get("created_at"), "finished_at": m.get("finished_at"),
    }


def _report(m: dict) -> None:
    publish(topics.mission_status(m["mission_id"]), summary(m))


# ---------------------------------------------------------------- planning
def build_moves(assignments: dict[str, list[str]], pallets: dict[str, str]) -> list[dict]:
    """Pallets in each assigned aisle -> moves, spread over that aisle's docks round-robin."""
    where = {node: pid for pid, node in pallets.items()}
    moves = []
    for aisle, docks in assignments.items():
        if aisle not in GRAPH.aisles:
            raise ValueError(f"unknown aisle {aisle!r}; this map has {sorted(GRAPH.aisles)}")
        bad = [d for d in docks if d not in GRAPH.nodes or GRAPH.nodes[d].type != "dock"]
        if not docks or bad:
            raise ValueError(f"aisle {aisle}: pick at least one valid dock (bad: {bad})")
        k = 0
        for slot in GRAPH.aisles[aisle]:
            if slot in where:
                moves.append({"pallet": where[slot], "slot": slot, "dock": docks[k % len(docks)],
                              "state": "pending", "task_id": None, "forklift": None})
                k += 1
    if not moves:
        raise ValueError("those aisles are empty: restock first")
    return moves


def _free_forklifts() -> list[dict]:
    fl = from_ddb(table("FORKLIFTS_TABLE").scan()["Items"])
    now = now_ms()
    return [f for f in fl if not f.get("current_task") and not f.get("route") and f.get("state") != "FAULT"
            and f.get("node") in GRAPH.nodes and now - int(f.get("last_seen", 0)) < 30_000]


def _dist(a: str, b: str) -> float:
    r = GRAPH.astar(a, b)
    return GRAPH.path_length(r) if r else float("inf")


def dispatch_next(mission_id: str) -> int:
    """Hand pending moves to free forklifts. Returns how many started."""
    import tree_runner  # local import: tree_runner calls back into this module

    started = []

    def fn(m):
        started.clear()
        if m.get("status") != "running":
            return None
        free = _free_forklifts()
        pending = [mv for mv in m["moves"] if mv["state"] == "pending"]
        changed = False
        while free and pending:
            # globally nearest (forklift, pallet) pair first
            fl, mv = min(((f, p) for f in free for p in pending), key=lambda fp: _dist(fp[0]["node"], fp[1]["slot"]))
            task_id = f"{mission_id}-{uuid.uuid4().hex[:4]}"
            mv.update(state="running", task_id=task_id, forklift=fl["forklift_id"])
            started.append((dict(mv), fl["forklift_id"]))
            free.remove(fl)
            pending.remove(mv)
            changed = True
        return m if changed else None

    m = _mutate(mission_id, fn)
    for mv, fid in started:
        tree = with_status({"type": "Sequence", "children": [
            {"type": "GoTo", "node": mv["slot"]}, {"type": "Pick", "pallet": mv["pallet"]},
            {"type": "GoTo", "node": mv["dock"]}, {"type": "Drop", "slot": mv["dock"]}]})
        task = {"task_id": mv["task_id"], "text": f"{mv['pallet']}: {mv['slot']} -> {mv['dock']}", "status": "running",
                "assigned_to": fid, "tree": tree, "planner_version": "mission", "mission_id": mission_id,
                "created_at": now_ms(), "started_at": now_ms()}
        table("TASKS_TABLE").put_item(Item=to_ddb(task))
        table("FORKLIFTS_TABLE").update_item(Key={"forklift_id": fid}, UpdateExpression="SET current_task = :t",
                                             ExpressionAttributeValues={":t": mv["task_id"]})
        tree_runner.start(task, fid, tree)
    if started and m:
        _report(m)
    return len(started)


# ---------------------------------------------------------------- lifecycle
def start(assignments: dict[str, list[str]]) -> dict:
    active = current_id()
    if active and get(active).get("status") == "running":
        raise ValueError(f"mission {active} is still running: cancel it first")
    pallets = {p["pallet_id"]: p["node"] for p in from_ddb(table("PALLETS_TABLE").scan()["Items"])}
    moves = build_moves(assignments, pallets)
    mission_id = f"m-{uuid.uuid4().hex[:6]}"
    m = {"mission_id": mission_id, "status": "running", "assignments": assignments, "moves": moves,
         "rev": 0, "created_at": now_ms()}
    _t().put_item(Item=to_ddb(m))
    _t().put_item(Item={"mission_id": "current", "active": mission_id})
    _report(m)
    dispatch_next(mission_id)
    return summary(get(mission_id))


def on_task_finished(task: dict, success: bool) -> None:
    """tree_runner calls this when a mission move ends; frees the forklift for the next move."""
    mission_id = task.get("mission_id")
    if not mission_id:
        return

    def fn(m):
        for mv in m["moves"]:
            if mv["task_id"] == task["task_id"] and mv["state"] == "running":
                mv["state"] = "done" if success else "failed"
                if all(x["state"] in ("done", "failed") for x in m["moves"]) and m["status"] == "running":
                    m["status"], m["finished_at"] = "done", now_ms()
                return m
        return None

    m = _mutate(mission_id, fn)
    if m:
        _report(m)
        dispatch_next(mission_id)


def poke() -> None:
    """Any forklift became free (e.g. finished a make-way move): try to hand out more moves."""
    active = current_id()
    if active:
        dispatch_next(active)


def cancel() -> dict | None:
    active = current_id()
    if not active:
        return None

    def fn(m):
        if m.get("status") != "running":
            return None
        for mv in m["moves"]:
            if mv["state"] == "pending":
                mv["state"] = "cancelled"
        m["status"], m["finished_at"] = "cancelled", now_ms()
        return m

    m = _mutate(active, fn) or get(active)
    _report(m)
    return summary(m)


def restock() -> int:
    """Put every pallet back on its home slot (shipped ones come back from the truck)."""
    for pid, node in GRAPH.pallets.items():
        table("PALLETS_TABLE").put_item(Item={"pallet_id": pid, "node": node, "last_seen": now_ms()})
    publish(topics.mission_status("restock"), {"state": "restocked", "pallets": len(GRAPH.pallets), "ts": int(time.time() * 1000)})
    return len(GRAPH.pallets)
