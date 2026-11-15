"""Multi-forklift traffic control: node reservations, windowed routes, blocking and replanning.

Rules
- A forklift may only drive onto nodes it holds a lock on (FleetEdgeLocks, keyed by node id).
- It holds its current node plus up to LOOKAHEAD nodes ahead. Driving an edge needs both ends,
  so head-on collisions and junction conflicts are both prevented.
- The cloud publishes only the locked part of a route (`partial: true` if more will follow).
  At the end of a partial path the forklift stops and sends `blocked` every ~2 s.
- Every `node_reached` frees the nodes behind and tries to extend the window.
- Telemetry renews the forklift's locks, so parked forklifts keep their spot. Locks expire after
  LOCK_TTL_S, so a crashed forklift can't hold a lane forever.
- Blocked > REPLAN_AFTER_MS: replan around nodes other forklifts hold.
- Blocked > GIVE_UP_AFTER_MS: give up; the behavior tree sees the GoTo fail.

Per-forklift route state lives on the FleetForklifts item:
  route: [node ids, current first]   route_task, route_action, window_end, blocked_since, blocked_first
"""

from __future__ import annotations

import logging
import time

from botocore.exceptions import ClientError

from common import from_ddb, publish, table
from fleet_shared import topics
from fleet_shared.graph import Graph, edge_id
from fleet_shared.messages import Locks as LocksMsg
from fleet_shared.messages import Path, now_ms

log = logging.getLogger()
GRAPH = Graph.load()

LOOKAHEAD = 2
LOCK_TTL_S = 30
REPLAN_AFTER_MS = 5_000
GIVE_UP_AFTER_MS = 45_000


# ---------------------------------------------------------------- lock store
class Locks:
    """FleetEdgeLocks wrapper. Swapped for an in-memory fake in tests."""

    def _t(self):
        return table("EDGE_LOCKS_TABLE")

    def acquire(self, node: str, fid: str, task_id: str | None = None) -> bool:
        now = int(time.time())
        try:
            self._t().put_item(
                Item={"edge_id": node, "holder": fid, "task_id": task_id or "-", "expires_at": now + LOCK_TTL_S},
                ConditionExpression="attribute_not_exists(edge_id) OR holder = :me OR expires_at < :now",
                ExpressionAttributeValues={":me": fid, ":now": now},
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def release(self, node: str, fid: str) -> None:
        try:
            self._t().delete_item(Key={"edge_id": node}, ConditionExpression="holder = :me",
                                  ExpressionAttributeValues={":me": fid})
        except ClientError as e:
            if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise

    def held(self) -> dict[str, str]:
        """node -> holder, unexpired only."""
        now = int(time.time())
        items = from_ddb(self._t().scan()["Items"])
        return {i["edge_id"]: i["holder"] for i in items if int(i.get("expires_at", 0)) >= now}


LOCKS = Locks()


def publish_locks() -> None:
    publish(topics.LOCKS, LocksMsg(locks=LOCKS.held()).to_dict())


# ---------------------------------------------------------------- forklift route state
def _forklift(fid: str) -> dict:
    return from_ddb(table("FORKLIFTS_TABLE").get_item(Key={"forklift_id": fid}).get("Item") or {})


def _set(fid: str, **attrs) -> None:
    """SET non-None attrs, REMOVE None attrs."""
    sets = {k: v for k, v in attrs.items() if v is not None}
    rems = [k for k, v in attrs.items() if v is None]
    expr = []
    if sets:
        expr.append("SET " + ", ".join(f"#{k} = :{k}" for k in sets))
    if rems:
        expr.append("REMOVE " + ", ".join(f"#{k}" for k in rems))
    kw = {"ExpressionAttributeNames": {f"#{k}": k for k in attrs}}
    if sets:
        kw["ExpressionAttributeValues"] = {f":{k}": v for k, v in sets.items()}
    table("FORKLIFTS_TABLE").update_item(Key={"forklift_id": fid}, UpdateExpression=" ".join(expr), **kw)


def _clear_route(fid: str) -> None:
    _set(fid, route=None, route_task=None, route_action=None, window_end=None,
         blocked_since=None, blocked_first=None)


def _route_avoiding_others(start: str, goal: str, fid: str) -> list[str] | None:
    """A* that treats nodes held by other forklifts as walls (except the goal: we'll wait for it)."""
    others = {n for n, h in LOCKS.held().items() if h != fid and n not in (start, goal)}
    blocked = {edge_id(n, m) for n in others for m in GRAPH.adj[n]}
    return GRAPH.astar(start, goal, blocked_edges=blocked)


# ---------------------------------------------------------------- public API
def start_route(fid: str, goal: str, task_id: str, action: str | None = None) -> bool:
    """Begin driving fid to goal. Returns False if already there. Raises ValueError if unroutable."""
    f = _forklift(fid)
    start = f.get("node")
    if start not in GRAPH.nodes:
        if f.get("pose"):
            start = GRAPH.nearest_node(f["pose"]["x"], f["pose"]["y"])
        else:
            raise ValueError(f"{fid} has no known position yet")
    if start == goal:
        return False
    route = _route_avoiding_others(start, goal, fid) or GRAPH.astar(start, goal)
    if route is None:
        raise ValueError(f"no route {start} -> {goal}")
    # Drop any stale reservations from a previous route.
    for n, h in LOCKS.held().items():
        if h == fid and n != start:
            LOCKS.release(n, fid)
    _set(fid, route=route, route_task=task_id, route_action=action, window_end=None,
         blocked_since=None, blocked_first=None)
    advance(fid)
    return True


def advance(fid: str) -> None:
    """Grab as many locks ahead as allowed and publish the drivable window if it grew."""
    f = _forklift(fid)
    route = f.get("route")
    if not route:
        return
    task_id = f.get("route_task") or "-"
    LOCKS.acquire(route[0], fid, task_id)
    k = 1
    while k < len(route) and k <= LOOKAHEAD and LOCKS.acquire(route[k], fid, task_id):
        k += 1
    window = route[:k]
    if k > 1 and window[-1] != f.get("window_end"):
        partial = k < len(route)
        msg = Path(task_id=task_id, nodes=GRAPH.to_path_nodes(window),
                   action=None if partial else f.get("route_action"), partial=partial)
        publish(topics.path(fid), msg.to_dict())
        _set(fid, window_end=window[-1], blocked_since=None)
    elif k == 1 and not f.get("blocked_since"):
        now = now_ms()
        _set(fid, blocked_since=now, blocked_first=f.get("blocked_first") or now)
    publish_locks()


def on_node_reached(fid: str, node: str) -> None:
    f = _forklift(fid)
    route = f.get("route")
    if not route or node not in route:
        return
    i = route.index(node)
    for behind in route[:i]:
        LOCKS.release(behind, fid)
    _set(fid, route=route[i:])
    advance(fid)


def on_done(fid: str, node: str | None) -> None:
    """Route finished. Keep only the lock on where we're parked."""
    for n, h in LOCKS.held().items():
        if h == fid and n != node:
            LOCKS.release(n, fid)
    if node:
        LOCKS.acquire(node, fid)
    _clear_route(fid)
    publish_locks()


def on_blocked(fid: str) -> str | None:
    """Forklift is waiting at the end of its window. Returns an error string if we gave up."""
    advance(fid)
    f = _forklift(fid)
    route, since, first = f.get("route"), f.get("blocked_since"), f.get("blocked_first")
    if not route or not since:
        return None
    now = now_ms()
    if first and now - first > GIVE_UP_AFTER_MS:
        held = LOCKS.held()
        blocker = next((held[n] for n in route[1:] if held.get(n, fid) != fid), "another forklift")
        on_done(fid, route[0])
        return f"blocked for {GIVE_UP_AFTER_MS // 1000}s by {blocker}"
    if now - since > REPLAN_AFTER_MS:
        new = _route_avoiding_others(route[0], route[-1], fid)
        if new and new != route:
            log.info("%s replanning around traffic: %s -> %s", fid, route, new)
            for n, h in LOCKS.held().items():
                if h == fid and n not in new:
                    LOCKS.release(n, fid)
            _set(fid, route=new, window_end=None, blocked_since=now)
            advance(fid)
        else:
            _set(fid, blocked_since=now)  # check again in another REPLAN_AFTER_MS
    return None


def renew(fid: str, node: str | None) -> None:
    """Telemetry heartbeat: keep this forklift's locks (and its parking spot) alive."""
    changed = False
    held = LOCKS.held()
    for n, h in held.items():
        if h == fid:
            LOCKS.acquire(n, fid)
    if node in GRAPH.nodes and held.get(node) != fid:
        changed = LOCKS.acquire(node, fid)
    if changed:
        publish_locks()
