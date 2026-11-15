"""Dashboard HTTP API (API Gateway HTTP API, payload v2, Cognito JWT already verified).

Routes are documented in docs/API.md section 9.
"""

import json
import logging
import os
import time

import boto3

import mission
from common import from_ddb, publish, table
from fleet_shared import topics
from fleet_shared.graph import Graph
from fleet_shared.messages import Stop

log = logging.getLogger()
log.setLevel(logging.INFO)
GRAPH = Graph.load()


def ok(body, status=200):
    return {"statusCode": status, "headers": {"content-type": "application/json"},
            "body": json.dumps(body, default=str)}


def err(status, code, message):
    return ok({"error": code, "message": message}, status)


def scan(env_name: str, **kw) -> list:
    t, items, start = table(env_name), [], None
    while True:
        page = t.scan(**kw, **({"ExclusiveStartKey": start} if start else {}))
        items += page["Items"]
        start = page.get("LastEvaluatedKey")
        if not start:
            return from_ddb(items)


def attach_iot(event):
    """Attach the dashboard IoT policy to the caller's own Cognito identity."""
    auth = event["headers"].get("authorization", "")
    id_token = auth.removeprefix("Bearer ").strip()
    ci = boto3.client("cognito-identity")
    identity_id = ci.get_id(IdentityPoolId=os.environ["IDENTITY_POOL_ID"],
                            Logins={os.environ["USER_POOL_PROVIDER"]: id_token})["IdentityId"]
    boto3.client("iot").attach_policy(policyName=os.environ["DASHBOARD_IOT_POLICY"], target=identity_id)
    return {"identity_id": identity_id}


def planner_summaries() -> list:
    out = []
    for p in scan("PLANNERS_TABLE"):
        runs = int(p.get("shadow_runs", 0))
        out.append({"version": p["version"], "stage": p.get("stage"), "model_id": p.get("model_id"),
                    "description": p.get("description"), "shadow_runs": runs,
                    "shadow_wins": int(p.get("shadow_wins", 0)),
                    "avg_sim_cost": round(float(p.get("shadow_cost_sum", 0)) / runs, 3) if runs else None,
                    "promoted_at": p.get("promoted_at")})
    order = {"prod": 0, "shadow": 1, "retired": 2}
    return sorted(out, key=lambda p: (order.get(p["stage"], 3), p["version"]))


def compare(limit: int) -> list:
    tasks = sorted((t for t in scan("TASKS_TABLE") if t.get("sim")), key=lambda t: t.get("created_at", 0), reverse=True)
    rows = []
    for t in tasks[:limit]:
        rows.append({"task_id": t["task_id"], "text": t.get("text"), "status": t.get("status"),
                     "prod": {"version": t.get("planner_version"), "sim_cost": t["sim"].get("cost")},
                     "candidates": [{k: c.get(k) for k in ("planner_version", "sim_cost", "valid", "win", "error")}
                                    for c in t.get("shadow") or []]})
    return rows


def promote(version: str):
    planners = {p["version"]: p for p in scan("PLANNERS_TABLE")}
    if version not in planners:
        return err(404, "not_found", f"no planner {version}")
    if planners[version].get("stage") != "shadow":
        return err(409, "conflict", f"{version} is {planners[version].get('stage')}, only shadow planners can be promoted")
    prev = next((v for v, p in planners.items() if p.get("stage") == "prod"), None)
    now = int(time.time() * 1000)
    name = os.environ["PLANNERS_TABLE"]
    items = [{"Update": {"TableName": name, "Key": {"version": {"S": version}},
                         "UpdateExpression": "SET stage = :p, promoted_at = :t",
                         "ConditionExpression": "stage = :s",
                         "ExpressionAttributeValues": {":p": {"S": "prod"}, ":s": {"S": "shadow"}, ":t": {"N": str(now)}}}}]
    if prev:
        # The old prod becomes a shadow again (kept for rollback and further comparison).
        items.append({"Update": {"TableName": name, "Key": {"version": {"S": prev}},
                                 "UpdateExpression": "SET stage = :sh REMOVE shadow_runs, shadow_wins, shadow_cost_sum",
                                 "ConditionExpression": "stage = :p",
                                 "ExpressionAttributeValues": {":sh": {"S": "shadow"}, ":p": {"S": "prod"}}}})
    boto3.client("dynamodb").transact_write_items(TransactItems=items)
    publish(topics.PLANNERS_PROMOTED, {"version": version, "previous": prev, "ts": now})
    return ok({"version": version, "previous": prev})


def sim_status() -> dict:
    ecs = boto3.client("ecs")
    svc = ecs.describe_services(cluster=os.environ["SIM_CLUSTER"], services=[os.environ["SIM_SERVICE"]])["services"][0]
    return {"on": svc["desiredCount"] > 0, "desired": svc["desiredCount"], "running": svc["runningCount"],
            "pending": svc["pendingCount"]}


def sim_set(on: bool) -> dict:
    boto3.client("ecs").update_service(cluster=os.environ["SIM_CLUSTER"], service=os.environ["SIM_SERVICE"],
                                       desiredCount=1 if on else 0)
    return sim_status()


VIRTUAL = "virtual-demo"


def set_hazard(body: dict) -> dict:
    """Demo: put a (virtual) person on the floor, or clear it. Published retained on the same topic the
    real camera detector uses, so the edge safety guard reacts exactly as it would to a real person."""
    if body.get("clear"):
        objects = []
    else:
        x, y = float(body["x"]), float(body["y"])
        objects = [{"cls": "person", "x": round(x, 3), "y": round(y, 3), "conf": 1.0}]
    msg = {"model_version": VIRTUAL, "objects": objects, "ts": int(time.time() * 1000)}
    publish(topics.DETECTIONS, msg, retain=True)
    return msg


def reset_demo() -> dict:
    """Back to the opening scene between judges: pallets home, nothing running, no reservations,
    no person on the floor, naive planner in prod with fresh head-to-head counters."""
    now = int(time.time() * 1000)
    for item in scan("EDGE_LOCKS_TABLE"):
        table("EDGE_LOCKS_TABLE").delete_item(Key={"edge_id": item["edge_id"]})
    cancelled = 0
    for t in scan("TASKS_TABLE"):
        if t.get("status") in ("planning", "running", "received"):
            table("TASKS_TABLE").update_item(Key={"task_id": t["task_id"]}, UpdateExpression="SET #s = :c, finished_at = :t",
                                             ExpressionAttributeNames={"#s": "status"},
                                             ExpressionAttributeValues={":c": "cancelled", ":t": now})
            publish(topics.task_status(t["task_id"]), {"state": "cancelled", "text": t.get("text")})
            cancelled += 1
    for f in scan("FORKLIFTS_TABLE"):
        table("FORKLIFTS_TABLE").update_item(
            Key={"forklift_id": f["forklift_id"]},
            UpdateExpression="REMOVE current_task, route, route_task, route_action, window_end, blocked_since, blocked_first")
        publish(topics.path(f["forklift_id"]), {"task_id": "reset", "nodes": [], "action": None})  # stop driving
    for pid, node in GRAPH.pallets.items():
        table("PALLETS_TABLE").put_item(Item={"pallet_id": pid, "node": node})
    for p in scan("PLANNERS_TABLE"):
        stage = {"v1-naive": "prod", "v2-sonnet": "shadow"}.get(p["version"], p.get("stage"))
        table("PLANNERS_TABLE").update_item(
            Key={"version": p["version"]}, UpdateExpression="SET stage = :s REMOVE shadow_runs, shadow_wins, shadow_cost_sum",
            ExpressionAttributeValues={":s": stage})
    set_hazard({"clear": True})
    publish(topics.LOCKS, {"locks": {}, "ts": now})
    publish(topics.PLANNERS_PROMOTED, {"version": "v1-naive", "previous": None, "ts": now, "reset": True})
    return {"reset": True, "cancelled_tasks": cancelled}


def handler(event, _ctx):
    route = event["routeKey"]
    params = event.get("pathParameters") or {}
    qs = event.get("queryStringParameters") or {}
    try:
        if route == "GET /graph":
            return ok(GRAPH.data)
        if route == "GET /forklifts":
            return ok(sorted(scan("FORKLIFTS_TABLE"), key=lambda f: f["forklift_id"]))
        if route == "GET /pallets":
            return ok(scan("PALLETS_TABLE"))
        if route == "GET /tasks":
            limit = min(int(qs.get("limit", 20)), 100)
            tasks = sorted(scan("TASKS_TABLE"), key=lambda t: t.get("created_at", 0), reverse=True)
            return ok([{k: t.get(k) for k in ("task_id", "text", "status", "assigned_to", "created_at")}
                       for t in tasks[:limit]])
        if route == "GET /tasks/{task_id}":
            item = table("TASKS_TABLE").get_item(Key={"task_id": params["task_id"]}).get("Item")
            return ok(from_ddb(item)) if item else err(404, "not_found", "no such task")
        if route == "POST /forklifts/{forklift_id}/stop":
            fid = params["forklift_id"]
            publish(topics.stop(fid), Stop(reason="operator").to_dict())
            return ok({"forklift_id": fid, "stopped": True}, 202)
        if route == "GET /planners":
            return ok(planner_summaries())
        if route == "GET /planners/compare":
            return ok(compare(min(int(qs.get("limit", 20)), 50)))
        if route == "POST /planners/{version}/promote":
            return promote(params["version"])
        if route == "GET /sim":
            return ok(sim_status())
        if route == "POST /sim":
            body = json.loads(event.get("body") or "{}")
            if not isinstance(body.get("on"), bool):
                return err(400, "bad_request", 'body must be {"on": true|false}')
            return ok(sim_set(body["on"]))
        if route == "POST /hazard":
            return ok(set_hazard(json.loads(event.get("body") or "{}")))
        if route == "POST /demo/reset":
            return ok(reset_demo())
        if route == "GET /missions/current":
            mid = mission.current_id()
            m = mission.get(mid) if mid else None
            return ok({"map_has_aisles": bool(GRAPH.aisles), "aisles": GRAPH.aisles,
                       "docks": [n.id for n in GRAPH.nodes.values() if n.type == "dock"],
                       "mission": mission.summary(m) if m else None})
        if route == "POST /missions":
            body = json.loads(event.get("body") or "{}")
            return ok(mission.start(body.get("assignments") or {}))
        if route == "POST /missions/current/cancel":
            return ok(mission.cancel() or {"state": "none"})
        if route == "POST /missions/restock":
            mid = mission.current_id()
            if mid and mission.get(mid).get("status") == "running":
                return err(409, "conflict", "a mission is running: cancel it first")
            return ok({"restocked": mission.restock()})
        if route == "POST /iot/attach":
            return ok(attach_iot(event))
        return err(404, "not_found", route)
    except ValueError as e:
        return err(400, "bad_request", str(e))
    except Exception:
        log.exception("api error on %s", route)
        return err(500, "internal", "see Lambda logs")
