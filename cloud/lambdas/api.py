"""Dashboard HTTP API (API Gateway HTTP API, payload v2, Cognito JWT already verified).

Routes are documented in docs/API.md section 9.
"""

import json
import logging
import os

import boto3

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
        if route == "POST /iot/attach":
            return ok(attach_iot(event))
        return err(404, "not_found", route)
    except ValueError as e:
        return err(400, "bad_request", str(e))
    except Exception:
        log.exception("api error on %s", route)
        return err(500, "internal", "see Lambda logs")
