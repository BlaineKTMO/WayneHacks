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

    def extend(self, node: str, fid: str) -> None:
        """Push back the expiry of a lock we still hold. Never recreates a released lock
        (a plain re-put here raced with node_reached releases and resurrected old locks)."""
        try:
            self._t().update_item(Key={"edge_id": node}, UpdateExpression="SET expires_at = :e",
                                  ConditionExpression="holder = :me",
                                  ExpressionAttributeValues={":me": fid, ":e": int(time.time()) + LOCK_TTL_S})
        except ClientError as e:
            if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
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
# IoT Core invokes Lambdas in parallel, so node_reached / blocked / telemetry for one forklift
# can be processed concurrently. Every route change is a read-modify-write guarded by
# `route_rev` (optimistic concurrency): if someone else changed the route first, re-read and redo.
MAX_RETRIES = 8


class _Conflict(Exception):
    pass


def _forklift(fid: str) -> dict:
    return from_ddb(table("FORKLIFTS_TABLE").get_item(Key={"forklift_id": fid}, ConsistentRead=True).get("Item") or {})


def _write(fid: str, rev: int | None, attrs: dict) -> None:
    """SET non-None attrs, REMOVE None attrs, bump route_rev; fail if route_rev moved."""
    sets = {k: v for k, v in attrs.items() if v is not None}
    rems = [k for k, v in attrs.items() if v is None]
    new_rev = (rev or 0) + 1
    sets["route_rev"] = new_rev
    names = {f"#{k}": k for k in list(sets) + rems}
    values = {f":{k}": v for k, v in sets.items()}
    expr = "SET " + ", ".join(f"#{k} = :{k}" for k in sets)
    if rems:
        expr += " REMOVE " + ", ".join(f"#{k}" for k in rems)
    if rev is None:
        cond = "attribute_not_exists(route_rev)"
    else:
        cond = "route_rev = :expected"
        values[":expected"] = rev
    try:
        table("FORKLIFTS_TABLE").update_item(Key={"forklift_id": fid}, UpdateExpression=expr,
                                             ConditionExpression=cond, ExpressionAttributeNames=names,
                                             ExpressionAttributeValues=values)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise _Conflict() from e
        raise


def _mutate(fid: str, fn) -> dict:
    """Run fn(forklift) -> attrs|None against the latest state, retrying on concurrent changes.
    Returns the state fn saw on the successful attempt (with attrs applied)."""
    for _ in range(MAX_RETRIES):
        f = _forklift(fid)
        attrs = fn(f)
        if not attrs:
            return f
        try:
            _write(fid, f.get("route_rev"), attrs)
            return {**f, **{k: v for k, v in attrs.items() if v is not None}}
        except _Conflict:
            continue
    log.warning("%s: gave up after %d concurrent route updates", fid, MAX_RETRIES)
    return _forklift(fid)


CLEARED = dict(route=None, route_task=None, route_action=None, window_end=None,
               blocked_since=None, blocked_first=None, resume_goal=None, resume_action=None)


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
    LOCKS.acquire(start, fid, task_id)
    _mutate(fid, lambda _f: dict(CLEARED, route=route, route_task=task_id, route_action=action))
    if not task_id.startswith(("yield-", "park-")):  # don't let make-way / parking moves cascade
        make_way(fid, route)
    advance(fid)
    return True


def advance(fid: str) -> None:
    """Grab as many locks ahead as allowed and publish the drivable window if it grew."""
    out: dict = {}

    def fn(f):
        out.clear()
        route = f.get("route")
        if not route:
            return None
        task_id = f.get("route_task") or "-"
        k = 1  # route[0] (where we are) is already ours
        while k < len(route) and k <= LOOKAHEAD and LOCKS.acquire(route[k], fid, task_id):
            k += 1
        window = route[:k]
        if k > 1 and window[-1] != f.get("window_end"):
            partial = k < len(route)
            out["path"] = Path(task_id=task_id, nodes=GRAPH.to_path_nodes(window),
                               action=None if partial else f.get("route_action"), partial=partial)
            return {"window_end": window[-1], "blocked_since": None, "blocked_first": None, "window_sent_at": now_ms()}
        if k == 1 and not f.get("blocked_since"):
            now = now_ms()
            return {"blocked_since": now, "blocked_first": f.get("blocked_first") or now}
        return None

    _mutate(fid, fn)
    if "path" in out:  # publish only after the state that matches it is saved
        log.info("%s window -> %s (partial=%s)", fid, [n["id"] for n in out["path"].nodes], out["path"].partial)
        publish(topics.path(fid), out["path"].to_dict())
    publish_locks()


def on_node_reached(fid: str, node: str) -> None:
    def fn(f):
        route = f.get("route")
        if not route or node not in route or route[0] == node:
            return None
        i = route.index(node)
        for behind in route[:i]:
            LOCKS.release(behind, fid)
        return {"route": route[i:]}

    _mutate(fid, fn)
    advance(fid)


def on_done(fid: str, node: str | None) -> None:
    """Route finished. Keep only the lock on where we're parked."""
    for n, h in LOCKS.held().items():
        if h == fid and n != node:
            LOCKS.release(n, fid)
    if node:
        LOCKS.acquire(node, fid)
    _mutate(fid, lambda f: dict(CLEARED) if f.get("route") else None)
    publish_locks()


def make_way(fid: str, route: list[str]) -> list[str]:
    """Move idle forklifts parked anywhere on `route` (ahead of us) to the nearest spot off it.
    Called when a route starts, so they move before we arrive (a dead-end dock can't be
    cleared once we're standing at its only exit). Returns the ids asked to move."""
    moved = []
    held = LOCKS.held()
    for node in route[1:]:
        holder = held.get(node)
        if not holder or holder == fid or holder in moved:
            continue
        other = _forklift(holder)
        if other.get("route") or other.get("current_task"):
            continue  # busy driving: traffic rules (wait / replan) handle it
        taken = set(LOCKS.held()) | set(route)
        spots = [n.id for n in GRAPH.nodes.values() if n.type in ("junction", "dock") and n.id not in taken]
        best, best_d = None, float("inf")
        for sp in spots:
            r = _route_avoiding_others(node, sp, holder)
            if r and GRAPH.path_length(r) < best_d:
                best, best_d = sp, GRAPH.path_length(r)
        if best:
            log.info("%s is parked on %s's route at %s; moving it to %s", holder, fid, node, best)
            try:
                start_route(holder, best, f"yield-{fid}")
                moved.append(holder)
            except ValueError as e:  # unknown position / no route: leave it, normal waiting applies
                log.warning("could not move %s: %s", holder, e)
    return moved


DEADLOCK_AFTER_MS = 2_000


def break_deadlock(fid: str) -> bool:
    """Two busy forklifts each waiting on a node the other holds: rerouting can't help (each sits on
    the other's way) and make-way only moves idle forklifts. The higher-numbered one yields: it
    detours to the nearest free spot off the other's route, then carries on with its own job
    (finish_detour). Returns True if this forklift started a detour."""
    me = _forklift(fid)
    route = me.get("route")
    if not route or len(route) < 2 or not me.get("blocked_since") or me.get("resume_goal"):
        return False
    if now_ms() - int(me["blocked_since"]) < DEADLOCK_AFTER_MS:
        return False
    held = LOCKS.held()
    other_id = held.get(route[1])
    if not other_id or other_id == fid or fid < other_id:   # only the higher id yields
        return False
    other = _forklift(other_id)
    o_route = other.get("route") or []
    if not (other.get("blocked_since") and len(o_route) > 1 and held.get(o_route[1]) == fid):
        return False                                       # not mutual: normal waiting / replanning
    keep_clear = set(o_route) | set(route[1:2])
    best, best_d = None, float("inf")
    for n in GRAPH.nodes.values():
        if n.type != "junction" or n.id in keep_clear or n.id in held:
            continue
        r = _route_avoiding_others(route[0], n.id, fid)
        if r and not (set(r[1:]) & set(o_route)) and GRAPH.path_length(r) < best_d:
            best, best_d = n.id, GRAPH.path_length(r)
    if not best:
        return False
    log.info("deadlock %s <-> %s: %s detours to %s, then resumes to %s", fid, other_id, fid, best, route[-1])
    detour = _route_avoiding_others(route[0], best, fid)
    for n, h in held.items():
        if h == fid and n != route[0]:
            LOCKS.release(n, fid)
    _mutate(fid, lambda f: {"route": detour, "window_end": None, "blocked_since": None, "blocked_first": None,
                            "resume_goal": route[-1], "resume_action": f.get("route_action"), "route_action": None})
    advance(fid)
    return True


def finish_detour(fid: str) -> bool:
    """Arrived at a deadlock detour spot: resume the original route. True = this 'done' was the detour's,
    so the task must not count it as a finished step."""
    f = _forklift(fid)
    goal = f.get("resume_goal")
    if not goal:
        return False
    task, action = f.get("route_task") or "-", f.get("resume_action")
    _mutate(fid, lambda _f: {"resume_goal": None, "resume_action": None})
    log.info("%s detour done: resuming to %s", fid, goal)
    if not start_route(fid, goal, task, action):
        return False   # already there (unlikely): let the task see the 'done'
    return True


def on_blocked(fid: str) -> str | None:
    """Forklift is waiting at the end of its window. Returns an error string if we gave up."""
    advance(fid)
    if break_deadlock(fid):
        return None
    f = _forklift(fid)
    if f.get("route") and f.get("blocked_since"):
        make_way(fid, f["route"])  # backup: someone parked on our route after we started
    result: dict = {}

    def fn(f):
        result.clear()
        route, since, first = f.get("route"), f.get("blocked_since"), f.get("blocked_first")
        if not route or not since:
            return None
        now = now_ms()
        if first and now - first > GIVE_UP_AFTER_MS:
            held = LOCKS.held()
            blocker = next((held[n] for n in route[1:] if held.get(n, fid) != fid), "another forklift")
            result["gave_up"] = (route[0], f"blocked for {GIVE_UP_AFTER_MS // 1000}s by {blocker}")
            return None
        if now - since <= REPLAN_AFTER_MS:
            return None
        new = _route_avoiding_others(route[0], route[-1], fid)
        if new and new != route:
            log.info("%s replanning around traffic: %s -> %s", fid, route, new)
            for n, h in LOCKS.held().items():
                if h == fid and n not in new:
                    LOCKS.release(n, fid)
            result["replanned"] = True
            return {"route": new, "window_end": None, "blocked_since": now}
        return {"blocked_since": now}  # nothing better yet; check again in REPLAN_AFTER_MS

    _mutate(fid, fn)
    if "gave_up" in result:
        node, err = result["gave_up"]
        on_done(fid, node)
        return err
    if result.get("replanned"):
        advance(fid)
    return None


RESEND_AFTER_MS = 4_000


def park_if_dead_end(fid: str) -> str | None:
    """Docks and slots are dead ends: a forklift idling on one traps anyone who needs it (it can only
    leave through the junction the newcomer is waiting on). After a job, drive to the nearest free
    junction that isn't beside a dock. Only with more than one forklift on the site.
    Returns the parking spot, or None."""
    f = _forklift(fid)
    node = f.get("node")
    if f.get("route") or f.get("current_task") or node not in GRAPH.nodes or GRAPH.nodes[node].type == "junction":
        return None
    fleet = from_ddb(table("FORKLIFTS_TABLE").scan()["Items"])
    if len(fleet) < 2:
        return None
    held = LOCKS.held()
    busy_routes = {n for x in fleet if x["forklift_id"] != fid for n in (x.get("route") or [])}
    docks_nearby = {m for n in GRAPH.nodes.values() if n.type == "dock" for m in GRAPH.adj[n.id]}
    spots = [n.id for n in GRAPH.nodes.values() if n.type == "junction" and n.id not in held
             and n.id not in busy_routes and n.id not in docks_nearby]
    best, best_d = None, float("inf")
    for sp in spots:
        r = _route_avoiding_others(node, sp, fid)
        if r and GRAPH.path_length(r) < best_d:
            best, best_d = sp, GRAPH.path_length(r)
    if best:
        log.info("%s parked on dead end %s: moving to %s", fid, node, best)
        start_route(fid, best, f"park-{fid}")
    return best


def retry_if_stuck(fid: str, reported_state: str | None = None) -> str | None:
    """Heartbeat (1 Hz telemetry). Returns an error if the forklift gave up.
    - Waiting for a reservation: run the blocked logic (retry, replan after 5 s, give up after 45 s).
      A forklift blocked before its first move never got a path, so never says "blocked" itself.
    - Has a reserved stretch but reports IDLE for a while: its path message was lost (Wi-Fi blip,
      simulator restart). Send the same stretch again; a forklift that has it just replaces it."""
    f = _forklift(fid)
    route = f.get("route")
    if route and f.get("blocked_since"):
        if break_deadlock(fid):
            return None
        return on_blocked(fid)
    end = f.get("window_end")
    if route and end in route and reported_state == "IDLE" \
            and now_ms() - int(f.get("window_sent_at") or 0) > RESEND_AFTER_MS:
        window = route[:route.index(end) + 1]
        if len(window) > 1:
            partial = len(window) < len(route)
            msg = Path(task_id=f.get("route_task") or "-", nodes=GRAPH.to_path_nodes(window),
                       action=None if partial else f.get("route_action"), partial=partial)
            log.warning("%s idle with a reserved stretch %s: re-sending its path", fid, window)
            _mutate(fid, lambda _f: {"window_sent_at": now_ms()})
            publish(topics.path(fid), msg.to_dict())
    return None


def renew(fid: str, node: str | None) -> None:
    """Telemetry heartbeat: keep this forklift's locks (and its parking spot) alive."""
    held = LOCKS.held()
    for n, h in held.items():
        if h == fid:
            LOCKS.extend(n, fid)
    # Parked and not holding its own spot (e.g. after a restart): claim it.
    if node in GRAPH.nodes and node not in held and not _forklift(fid).get("route"):
        if LOCKS.acquire(node, fid):
            publish_locks()
