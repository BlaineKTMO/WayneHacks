"""End-to-end test of the whole live system, the way a judge's iPad uses it.

  uv run python tools/e2e.py              # starts its own simulated forklifts locally
  uv run python tools/e2e.py --no-sim     # use forklifts that are already running (cloud sim / hardware)

Signs in through Cognito as a test user (e2e-bot@example.com, created if missing), gets
identity-pool credentials, connects to IoT Core exactly like the dashboard, then checks:
API routes, auth, map goto, a Claude order with shadow scoring, episode recording, lane
reservations, stop, promote (and restore), and that browsers cannot fake telemetry.
Resets fleet state first. Exits non-zero if anything fails.
"""

from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import boto3
from awscrt import auth, mqtt
from awsiot import mqtt_connection_builder

REGION = "us-east-2"
ROOT = Path(__file__).resolve().parents[1]
TEST_USER = "e2e-bot@example.com"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  · ' + detail) if detail else ''}", flush=True)
    return bool(ok)


SITE = "demo"


def P(rest: str) -> str:
    """Site-scoped topic: P("tasks/new") -> "demo/tasks/new"."""
    return f"{SITE}/{rest}"


def outputs(stack: str = "ForkliftFleet") -> dict:
    o = boto3.client("cloudformation", region_name=REGION).describe_stacks(StackName=stack)["Stacks"][0]["Outputs"]
    return {x["OutputKey"]: x["OutputValue"] for x in o}


# ---------------------------------------------------------------- setup
def reset_fleet(keep_forklifts: bool) -> None:
    from fleet_shared.sites import table_name
    ddb = boto3.client("dynamodb", region_name=REGION)
    for item in ddb.scan(TableName=table_name(SITE, "EdgeLocks"))["Items"]:
        ddb.delete_item(TableName=table_name(SITE, "EdgeLocks"), Key={"edge_id": item["edge_id"]})
    for item in ddb.scan(TableName=table_name(SITE, "Forklifts"))["Items"]:
        fid = item["forklift_id"]["S"]
        ddb.update_item(TableName=table_name(SITE, "Forklifts"), Key={"forklift_id": {"S": fid}},
                        UpdateExpression="REMOVE route, route_task, route_action, window_end, blocked_since, "
                                         "blocked_first, current_task, route_rev")
    cmd = [sys.executable, str(ROOT / "cloud/scripts/seed.py"), "--site", SITE] + (["--keep-forklifts"] if keep_forklifts else [])
    subprocess.run(cmd, check=True, capture_output=True)


def sign_in(o: dict) -> str:
    from pycognito import Cognito
    idp = boto3.client("cognito-idp", region_name=REGION)
    try:
        idp.admin_create_user(UserPoolId=o["UserPoolId"], Username=TEST_USER, MessageAction="SUPPRESS",
                              UserAttributes=[{"Name": "email", "Value": TEST_USER}, {"Name": "email_verified", "Value": "true"}])
    except idp.exceptions.UsernameExistsException:
        pass
    pw = "E2e!" + secrets.token_urlsafe(18)
    idp.admin_set_user_password(UserPoolId=o["UserPoolId"], Username=TEST_USER, Password=pw, Permanent=True)
    u = Cognito(o["UserPoolId"], o["UserPoolClientId"], username=TEST_USER, user_pool_region=REGION)
    u.authenticate(password=pw)
    return u.id_token


def api(o: dict, token: str | None, path: str, method: str = "GET", body: dict | None = None):
    headers = {"authorization": f"Bearer {token}"} if token else {}
    if body is not None:
        headers["content-type"] = "application/json"
    req = urllib.request.Request(o["DashboardApiUrl"] + path, method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def browser_mqtt(o: dict, token: str, client_id: str):
    """Identity-pool credentials + SigV4 WebSocket: the same path as the dashboard."""
    ci = boto3.client("cognito-identity", region_name=REGION)
    logins = {f"cognito-idp.{REGION}.amazonaws.com/{o['UserPoolId']}": token}
    ident = ci.get_id(IdentityPoolId=o["IdentityPoolId"], Logins=logins)["IdentityId"]
    c = ci.get_credentials_for_identity(IdentityId=ident, Logins=logins)["Credentials"]
    prov = auth.AwsCredentialsProvider.new_static(c["AccessKeyId"], c["SecretKey"], c["SessionToken"])
    closed = threading.Event()
    def resumed(connection, return_code, session_present, **_):
        print(f"    (mqtt reconnected, session_present={session_present})")
        if not session_present:  # same as the dashboard: resubscribe after losing the session
            connection.resubscribe_existing_topics()

    conn = mqtt_connection_builder.websockets_with_default_aws_signing(
        endpoint=o["IotEndpointOut"], region=REGION, credentials_provider=prov, client_id=client_id,
        clean_session=False, keep_alive_secs=30, on_connection_resumed=resumed,
        on_connection_interrupted=lambda connection, error, **_: (print(f"    (mqtt interrupted: {error})"), closed.set()),
        on_connection_closed=lambda *a, **k: closed.set())
    conn.connect().result(timeout=15)
    return conn, closed


class Inbox:
    def __init__(self):
        self.msgs: list[tuple[float, str, dict]] = []
        self.lock = threading.Lock()

    def on(self, topic, payload, **_):
        if not topic.startswith(SITE + "/"):
            return
        topic = topic[len(SITE) + 1:]   # compare site-relative topics below
        try:
            data = json.loads(payload)
        except ValueError:
            data = {}
        with self.lock:
            self.msgs.append((time.time(), topic, data))

    def wait(self, pred, timeout: float, since: float = 0):
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                for t, topic, d in self.msgs:
                    if t >= since and pred(topic, d):
                        return topic, d
            time.sleep(0.2)
        return None


# ---------------------------------------------------------------- run
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-sim", action="store_true", help="don't start local simulated forklifts")
    ap.add_argument("--cloud-sim", action="store_true", help="use the AWS-hosted simulated fleet (switched on via the API)")
    ap.add_argument("--site", default="demo", help="demo (simulated, mission map) or wh1 (Warehouse 1)")
    a = ap.parse_args()
    global SITE
    SITE = a.site
    if a.cloud_sim:
        a.no_sim = True
    from fleet_shared.sites import SITES as SITE_INFO
    o = outputs()                                   # shared sign-in + IoT endpoint (demo stack)
    if SITE != "demo":                              # this site's own API + bucket
        so = outputs(SITE_INFO[SITE]["stack"])
        o = {**o, "DashboardApiUrl": so["DashboardApiUrl"], "DataBucketName": so["DataBucketName"]}
    sim = None

    print("\n== Setup")
    reset_fleet(keep_forklifts=a.no_sim)
    check("fleet state reset and reseeded", True)
    if not a.no_sim:
        sim = subprocess.Popen([sys.executable, "-u", str(ROOT / "tools/sim_fleet.py")],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(10)
    try:
        token = sign_in(o)
        check("Cognito sign-in (SRP, like the browser)", bool(token))

        if a.cloud_sim:
            print("\n== Hosted simulated fleet")
            st, hs = api(o, token, "/sim", "POST", {"on": True})
            check("POST /sim turns the hosted fleet on", st == 200 and hs.get("on"), str(hs))
            for _ in range(40):
                st, hs = api(o, token, "/sim")
                if hs.get("running"):
                    break
                time.sleep(5)
            check("hosted fleet task running", bool(hs.get("running")), str(hs))
            time.sleep(15)  # first poses after boot

        print("\n== Demo reset")
        st, body = api(o, token, "/demo/reset", "POST")
        check("POST /demo/reset", st == 200, str(body))
        _, pl = api(o, token, "/pallets")
        _, graph = api(o, token, "/graph")
        homes = {p["id"]: p["node"] for p in graph.get("pallets", [])}
        check("pallets back at their starting slots", {p["pallet_id"]: p["node"] for p in pl} == homes)
        _, pls = api(o, token, "/planners")
        check("naive planner in production, counters zeroed",
              any(p["version"] == "v1-naive" and p["stage"] == "prod" for p in pls) and all(not p["shadow_runs"] for p in pls))
        time.sleep(3)  # let forklifts settle after the reset's empty paths

        print("\n== API")
        st, _ = api(o, None, "/forklifts")
        check("API rejects requests without a token", st == 401, f"HTTP {st}")
        for path in ("/graph", "/forklifts", "/pallets", "/tasks", "/planners", "/planners/compare", "/sim"):
            st, body = api(o, token, path)
            check(f"GET {path}", st == 200, f"HTTP {st}")
        st, body = api(o, token, "/iot/attach", "POST")
        check("POST /iot/attach (IoT policy on identity)", st == 200, body.get("identity_id", "") if st == 200 else str(body))

        print("\n== Browser MQTT")
        inbox = Inbox()
        conn, closed = browser_mqtt(o, token, f"dash-e2e-{uuid.uuid4().hex[:6]}")
        for f in [P(x) for x in ("fleet/+/+", "tasks/+/+", "warehouse/#", "planners/+", "missions/+/+")]:
            fut, _ = conn.subscribe(topic=f, qos=mqtt.QoS.AT_LEAST_ONCE, callback=inbox.on)
            fut.result(timeout=10)
        check("connect + 4 subscriptions with Cognito credentials", not closed.is_set())
        got = inbox.wait(lambda t, d: t.endswith("/pose"), 15)
        check("live forklift poses arriving", got is not None, got[0] if got else "no pose in 15s (is a fleet running?)")

        hl = inbox.wait(lambda t, d: t.startswith("warehouse/health/") and d.get("online") is True, 15)
        check("device health heartbeat online", hl is not None,
              f"{hl[1]['source']}: {len(hl[1].get('forklifts', {}))} forklift(s), camera {hl[1].get('camera', {}).get('mode')}" if hl else "none in 15s")

        print("\n== Camera feed")
        import numpy as np, cv2  # noqa: E401  (edge group)
        img = (np.random.default_rng(0).random((480, 640, 3)) * 255).astype("uint8")
        jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 40])[1].tobytes()
        iot = boto3.client("iot-data", region_name=REGION, endpoint_url=f"https://{o['IotEndpointOut']}")
        t0 = time.time()
        iot.publish(topic=P("warehouse/camera"), qos=0, payload=jpg)
        raw = []
        def grab(topic, payload, **_):
            if topic == P("warehouse/camera"):
                raw.append(bytes(payload))
        fut, _ = conn.subscribe(topic=P("warehouse/camera"), qos=mqtt.QoS.AT_MOST_ONCE, callback=grab)
        fut.result(timeout=10)
        time.sleep(0.5)
        iot.publish(topic=P("warehouse/camera"), qos=0, payload=jpg)
        end = time.time() + 10
        while not raw and time.time() < end:
            time.sleep(0.2)
        check("browser receives a camera JPEG intact", bool(raw) and raw[0] == jpg, f"{len(jpg) // 1024} KB")

        print("\n== Map goto (dashboard publish)")
        _, fls = api(o, token, "/forklifts")
        here = next((f.get("node") for f in fls if f["forklift_id"] == "fl-02"), None)
        juncs = [n["id"] for n in graph["nodes"] if n["type"] == "junction"]
        target = next(j for j in reversed(juncs) if j != here)   # somewhere it isn't, so a route is needed
        t0 = time.time()
        conn.publish(topic=P("fleet/fl-02/goto"), payload=json.dumps({"node": target}), qos=mqtt.QoS.AT_LEAST_ONCE)
        p = inbox.wait(lambda t, d: t == "fleet/fl-02/path", 15, t0)
        check("route planner answers with a path", p is not None, f"fl-02 {here} -> {target}")
        lk = inbox.wait(lambda t, d: t == "warehouse/locks" and "fl-02" in d.get("locks", {}).values(), 15, t0)
        check("lane reservations published", lk is not None)
        dn = inbox.wait(lambda t, d: t.startswith("tasks/goto-") and t.endswith("/status") and d.get("state") == "done", 60, t0)
        check("goto task completes", dn is not None)

        print("\n== Claude order")
        t0 = time.time()
        docks = [n["id"] for n in graph["nodes"] if n["type"] == "dock"]
        pallet, dock = sorted(homes)[-1], docks[-1]
        words = f"move pallet {pallet.split('_')[1]} to {dock.replace('_', ' ')}"
        expected_at = f"shipped:{dock}" if graph.get("docks_ship") else dock
        conn.publish(topic=P("tasks/new"), payload=json.dumps({"text": words}), qos=mqtt.QoS.AT_LEAST_ONCE)
        pl = inbox.wait(lambda t, d: t.endswith("/status") and d.get("state") == "planning", 15, t0)
        check("task enters planning", pl is not None)
        task_id = pl[0].split("/")[1] if pl else None
        rn = inbox.wait(lambda t, d: t == f"tasks/{task_id}/status" and d.get("state") == "running" and d.get("tree"), 30, t0)
        check("plan validated and running", rn is not None, rn[1].get("planner_version", "") if rn else "")
        if rn and rn[1].get("planner_version") == "fallback-rules":
            check("planned by Claude (not the rule fallback)", False, "Bedrock unavailable")
        # Safety: put a person on the next stop of the assigned forklift's route.
        fid = rn[1].get("assigned_to") if rn else None
        pth = inbox.wait(lambda t, d: fid and t == f"fleet/{fid}/path" and len(d.get("nodes", [])) > 1, 20, t0)
        if pth:
            nxt = pth[1]["nodes"][1]
            t1 = time.time()
            api(o, token, "/hazard", "POST", {"x": nxt["x"], "y": nxt["y"]})
            ss = inbox.wait(lambda t, d: t == f"fleet/{fid}/event" and d.get("type") == "safety_stop", 15, t1)
            check("person in path -> edge safety stop", ss is not None, f"{fid} near {nxt['id']}")
            time.sleep(3)
            t2 = time.time()
            api(o, token, "/hazard", "POST", {"clear": True})
            sc = inbox.wait(lambda t, d: t == f"fleet/{fid}/event" and d.get("type") == "safety_clear", 15, t2)
            check("person gone -> forklift resumes", sc is not None)
        else:
            check("person in path -> edge safety stop", False, "no path seen")
        check("browser MQTT still connected after safety events", not closed.is_set())
        sh = inbox.wait(lambda t, d: t == f"tasks/{task_id}/shadow", 45, t0)
        check("shadow planner scored", sh is not None,
              f"{sh[1].get('planner_version')} {sh[1].get('sim_cost')} vs prod {sh[1].get('prod_sim_cost')}" if sh else "")
        dn = inbox.wait(lambda t, d: t == f"tasks/{task_id}/status" and d.get("state") in ("done", "failed"), 150, t0)
        check("order completes", dn is not None and dn[1]["state"] == "done", dn[1].get("error") or "" if dn else "timeout")
        oc = inbox.wait(lambda t, d: t == f"tasks/{task_id}/outcome", 15, t0)
        check("episode graded (outcome message)", oc is not None, f"cost {oc[1].get('cost')}" if oc else "")
        st, pallets = api(o, token, "/pallets")
        where = {p["pallet_id"]: p["node"] for p in pallets} if st == 200 else {}
        check(f"{pallet} delivered to {dock}", where.get(pallet) == expected_at, where.get(pallet, "?"))
        s3 = boto3.client("s3", region_name=REGION)
        keys = [x["Key"] for x in s3.list_objects_v2(Bucket=o["DataBucketName"], Prefix="episodes/").get("Contents", [])]
        check("episode stored in S3", any(task_id and k.endswith(f"{task_id}.json") for k in keys))

        print("\n== Stop")
        t0 = time.time()
        st, _ = api(o, token, "/forklifts/fl-01/stop", "POST")
        sp = inbox.wait(lambda t, d: t == "fleet/fl-01/stop", 10, t0)
        check("stop button reaches the forklift", st == 202 and sp is not None, f"HTTP {st}")

        print("\n== Promote (then restore)")
        st, planners = api(o, token, "/planners")
        prod = next((p["version"] for p in planners if p["stage"] == "prod"), None)
        cand = next((p["version"] for p in planners if p["stage"] == "shadow"), None)
        if prod and cand:
            # Snapshot planner records so the demo's head-to-head stats survive this test.
            ptable = boto3.resource("dynamodb", region_name=REGION).Table("FleetPlanners")
            saved = ptable.scan()["Items"]
            t0 = time.time()
            st, body = api(o, token, f"/planners/{cand}/promote", "POST")
            pm = inbox.wait(lambda t, d: t == "planners/promoted", 10, t0)
            _, after = api(o, token, "/planners")
            ok = st == 200 and pm and next(p["stage"] for p in after if p["version"] == cand) == "prod"
            check(f"promote {cand}", ok, f"HTTP {st}")
            st, _ = api(o, token, f"/planners/{prod}/promote", "POST")
            for item in saved:
                ptable.put_item(Item=item)
            check(f"restore {prod} to production (stats kept)", st == 200, f"HTTP {st}")
        else:
            check("promote", False, "need one prod and one shadow planner")

        conn.disconnect().result(timeout=5)

        if graph.get("aisles"):
            print("\n== Mission (whole fleet)")
            api(o, token, "/missions/restock", "POST")
            aisle = sorted(graph["aisles"])[-1]
            t0 = time.time()
            st, ms = api(o, token, "/missions", "POST", {"assignments": {aisle: docks[:2]}})
            check(f"mission starts: aisle {aisle} -> {docks[:2]}", st == 200 and ms.get("running", 0) >= 1, str(ms)[:120])
            md = inbox.wait(lambda t, d: t.startswith("missions/m-") and d.get("state") == "done", 300, t0)
            check("mission completes (every pallet shipped)", md is not None and md[1]["done"] == md[1]["total"],
                  f"{md[1]['done']}/{md[1]['total']} in {time.time() - t0:.0f}s" if md else "timeout")
            api(o, token, "/missions/restock", "POST")

        print("\n== Security")
        conn2, closed2 = browser_mqtt(o, token, f"dash-e2e-{uuid.uuid4().hex[:6]}")
        conn2.publish(topic=P("fleet/fl-01/telemetry"), payload=json.dumps({"battery_v": 1, "motor_a": 0, "state": "FAULT"}),
                      qos=mqtt.QoS.AT_LEAST_ONCE)
        closed2.wait(8)
        check("browser cannot fake forklift telemetry (IoT disconnects it)", closed2.is_set())
    finally:
        if sim:
            sim.terminate()

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
