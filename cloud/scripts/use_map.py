"""Switch the whole system between the demo map and the real table (registration).

  Each site already has its own map (demo: mission, wh1: table); use this only to change it.
  uv run python cloud/scripts/use_map.py table --site wh1    # real forklift on the tabletop
  uv run python cloud/scripts/use_map.py demo     # 1.5 x 1 m map for the simulated fleet
  uv run python cloud/scripts/use_map.py mission  # 3 aisles -> 4 docks, 4 simulated forklifts (missions)

Updates WAREHOUSE_MAP on every fleet Lambda (no redeploy), then resets pallets, reservations,
routes and running tasks for that map. Turn the simulated fleet OFF before using `table`.
On the edge laptop, run the bridge with the same map:  WAREHOUSE_MAP=table uv run python -m fleet_edge.bridge
The dashboard picks the new map up on reload.
"""

import os
import sys
import time

import boto3

REGION = "us-east-2"


def main():
    from fleet_shared.sites import SITES, stack_name, table_name
    args = [a for a in sys.argv[1:] if not a.startswith("--site")]
    site = sys.argv[sys.argv.index("--site") + 1] if "--site" in sys.argv else "demo"
    args = [a for a in args if a != site]
    if len(args) != 1 or args[0] not in ("demo", "table", "mission") or site not in SITES:
        raise SystemExit(__doc__)
    name = args[0]
    T = lambda n: table_name(site, n)  # noqa: E731
    lam = boto3.client("lambda", region_name=REGION)
    fns = [f["FunctionName"] for p in lam.get_paginator("list_functions").paginate() for f in p["Functions"]
           if f["FunctionName"].startswith(stack_name(site) + "-") and f["FunctionName"].count("-") >= (2 if site != "demo" else 1)
           and (site != "demo" or not f["FunctionName"].startswith("ForkliftFleet-wh1")) and "WAREHOUSE_MAP" in (f.get("Environment") or {}).get("Variables", {})]
    for fn in fns:
        env = lam.get_function_configuration(FunctionName=fn)["Environment"]["Variables"]
        env["WAREHOUSE_MAP"] = name
        lam.update_function_configuration(FunctionName=fn, Environment={"Variables": env})
    for fn in fns:
        lam.get_waiter("function_updated_v2").wait(FunctionName=fn)
    print(f"{len(fns)} Lambdas now use the {name!r} map")

    os.environ["WAREHOUSE_MAP"] = name
    from fleet_shared.graph import Graph
    g = Graph.load()
    ddb = boto3.resource("dynamodb", region_name=REGION)
    for item in ddb.Table(T("EdgeLocks")).scan()["Items"]:
        ddb.Table(T("EdgeLocks")).delete_item(Key={"edge_id": item["edge_id"]})
    pallets = ddb.Table(T("Pallets"))
    for item in pallets.scan()["Items"]:
        pallets.delete_item(Key={"pallet_id": item["pallet_id"]})
    for pid, node in g.pallets.items():
        pallets.put_item(Item={"pallet_id": pid, "node": node})
    now = int(time.time() * 1000)
    tasks = ddb.Table(T("Tasks"))
    for t in tasks.scan()["Items"]:
        if t.get("status") in ("planning", "running", "received"):
            tasks.update_item(Key={"task_id": t["task_id"]}, UpdateExpression="SET #s = :c, finished_at = :t",
                              ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues={":c": "cancelled", ":t": now})
    docks = [n.id for n in g.nodes.values() if n.type == "dock"]
    roster = g.forklift_homes or {"fl-01": docks[0], "fl-02": docks[1 % len(docks)]}
    if name == "table":
        roster = {"fl-01": docks[0]}            # one real forklift on the tabletop
    fl = ddb.Table(T("Forklifts"))
    for f in fl.scan()["Items"]:
        if f["forklift_id"] not in roster:
            fl.delete_item(Key={"forklift_id": f["forklift_id"]})
    for fid, home in roster.items():
        fl.put_item(Item={"forklift_id": fid, "node": home, "state": "IDLE"})
    boto3.resource("dynamodb", region_name=REGION).Table(T("Missions")).put_item(Item={"mission_id": "current", "active": ""})
    ecs = boto3.client("ecs", region_name=REGION)
    clusters = [c for c in ecs.list_clusters()["clusterArns"] if "SimFleet" in c] if site == "demo" else []
    if clusters:
        svc = ecs.list_services(cluster=clusters[0])["serviceArns"][0]
        d = ecs.describe_services(cluster=clusters[0], services=[svc])["services"][0]
        if d["desiredCount"] > 0:   # running: restart so it simulates the new roster on the new map
            ecs.update_service(cluster=clusters[0], service=svc, forceNewDeployment=True)
            print("hosted simulated fleet restarting with the new map (~1 min)")
    print(f"reset: {len(g.pallets)} pallet(s), {len(g.nodes)} nodes, forklifts {sorted(roster)}. Reload the dashboard.")


if __name__ == "__main__":
    main()
