"""Switch the whole system between the demo map and the real table (registration).

  uv run python cloud/scripts/use_map.py table    # real forklift on the tabletop
  uv run python cloud/scripts/use_map.py demo     # 1.5 x 1 m map for the simulated fleet

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
    if len(sys.argv) != 2 or sys.argv[1] not in ("demo", "table"):
        raise SystemExit(__doc__)
    name = sys.argv[1]
    lam = boto3.client("lambda", region_name=REGION)
    fns = [f["FunctionName"] for p in lam.get_paginator("list_functions").paginate() for f in p["Functions"]
           if f["FunctionName"].startswith("ForkliftFleet-") and "WAREHOUSE_MAP" in (f.get("Environment") or {}).get("Variables", {})]
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
    for item in ddb.Table("FleetEdgeLocks").scan()["Items"]:
        ddb.Table("FleetEdgeLocks").delete_item(Key={"edge_id": item["edge_id"]})
    pallets = ddb.Table("FleetPallets")
    for item in pallets.scan()["Items"]:
        pallets.delete_item(Key={"pallet_id": item["pallet_id"]})
    for pid, node in g.pallets.items():
        pallets.put_item(Item={"pallet_id": pid, "node": node})
    now = int(time.time() * 1000)
    tasks = ddb.Table("FleetTasks")
    for t in tasks.scan()["Items"]:
        if t.get("status") in ("planning", "running", "received"):
            tasks.update_item(Key={"task_id": t["task_id"]}, UpdateExpression="SET #s = :c, finished_at = :t",
                              ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues={":c": "cancelled", ":t": now})
    first_dock = next(n.id for n in g.nodes.values() if n.type == "dock")
    for f in ddb.Table("FleetForklifts").scan()["Items"]:
        node = f.get("node") if f.get("node") in g.nodes else first_dock  # forget nodes from the other map
        ddb.Table("FleetForklifts").update_item(
            Key={"forklift_id": f["forklift_id"]}, ExpressionAttributeValues={":n": node},
            UpdateExpression="SET node = :n REMOVE current_task, route, route_task, route_action, window_end, blocked_since, blocked_first")
    print(f"reset: {len(g.pallets)} pallet(s), {len(g.nodes)} nodes. Reload the dashboard.")


if __name__ == "__main__":
    main()
