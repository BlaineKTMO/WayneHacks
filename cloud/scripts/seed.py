"""Seed DynamoDB from shared/warehouse.json (pallet locations) and register forklifts.

Run after every deploy that wipes tables:  uv run python cloud/scripts/seed.py
"""

import boto3

from fleet_shared.graph import Graph

REGION = "us-east-2"
FORKLIFTS = {"fl-01": "dock_A", "fl-02": "dock_B"}


def main():
    ddb = boto3.resource("dynamodb", region_name=REGION)
    g = Graph.load()
    with ddb.Table("FleetPallets").batch_writer() as w:
        for pid, node in g.pallets.items():
            w.put_item(Item={"pallet_id": pid, "node": node})
    with ddb.Table("FleetForklifts").batch_writer() as w:
        for fid, node in FORKLIFTS.items():
            w.put_item(Item={"forklift_id": fid, "node": node, "state": "IDLE"})
    print(f"seeded {len(g.pallets)} pallets, {len(FORKLIFTS)} forklifts")


if __name__ == "__main__":
    main()
