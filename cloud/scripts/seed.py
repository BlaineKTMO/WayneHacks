"""Seed DynamoDB from shared/warehouse.json (pallet locations), register forklifts, add planners.

Run after every deploy that wipes tables:  uv run python cloud/scripts/seed.py
"""

import boto3

from fleet_shared.graph import Graph

REGION = "us-east-2"
FORKLIFTS = {"fl-01": "dock_A", "fl-02": "dock_B"}

# Demo setup for the self-improving loop: start on a deliberately naive planner in prod, with a
# better candidate in shadow. Only written if the Planners table is empty (keeps promotions).
NAIVE_PROMPT = """You control warehouse forklifts. Write a behavior tree for the order using these primitives:
{primitives}
Safety habit: before every job, the forklift first drives back to dock_A to check in.
Nodes: {nodes}
"""
PLANNERS = [
    {"version": "v1-naive", "stage": "prod", "model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
     "prompt": NAIVE_PROMPT, "description": "Haiku 4.5, minimal prompt with a check-in detour"},
    {"version": "v2-sonnet", "stage": "shadow", "model_id": "us.anthropic.claude-sonnet-4-6",
     "description": "Sonnet 4.6, full planning rules (default prompt)"},
]


def main():
    import sys
    keep_forklifts = "--keep-forklifts" in sys.argv  # a fleet is running: don't contradict real positions
    ddb = boto3.resource("dynamodb", region_name=REGION)
    g = Graph.load()
    with ddb.Table("FleetPallets").batch_writer() as w:
        for pid, node in g.pallets.items():
            w.put_item(Item={"pallet_id": pid, "node": node})
    if not keep_forklifts:
        with ddb.Table("FleetForklifts").batch_writer() as w:
            for fid, node in FORKLIFTS.items():
                w.put_item(Item={"forklift_id": fid, "node": node, "state": "IDLE"})
    planners = ddb.Table("FleetPlanners")
    if not planners.scan(Limit=1).get("Items"):
        for p in PLANNERS:
            planners.put_item(Item=p)
        print(f"seeded {len(PLANNERS)} planners")
    print(f"seeded {len(g.pallets)} pallets, {len(FORKLIFTS)} forklifts")


if __name__ == "__main__":
    main()
