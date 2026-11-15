"""Shared helpers for the offline MLOps scripts: AWS clients and episode loading."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import boto3

REGION = "us-east-2"
STACK = "ForkliftFleet"
ROOT = Path(__file__).resolve().parents[1]

# Let the scripts reuse the exact planning code the Dispatch Lambda runs.
os.environ.setdefault("AWS_DEFAULT_REGION", REGION)
for env, name in [("FORKLIFTS_TABLE", "FleetForklifts"), ("TASKS_TABLE", "FleetTasks"),
                  ("EDGE_LOCKS_TABLE", "FleetEdgeLocks"), ("PALLETS_TABLE", "FleetPallets"),
                  ("PLANNERS_TABLE", "FleetPlanners"), ("EPISODES_TABLE", "FleetEpisodes")]:
    os.environ.setdefault(env, name)
sys.path.insert(0, str(ROOT / "cloud" / "lambdas"))


def bucket() -> str:
    outs = boto3.client("cloudformation", region_name=REGION).describe_stacks(StackName=STACK)["Stacks"][0]["Outputs"]
    return next(o["OutputValue"] for o in outs if o["OutputKey"] == "DataBucketName")


def load_episodes(bkt: str) -> list[dict]:
    """Every episode JSON under s3://bucket/episodes/, oldest first."""
    s3 = boto3.client("s3", region_name=REGION)
    eps = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bkt, Prefix="episodes/"):
        for obj in page.get("Contents", []):
            eps.append(json.loads(s3.get_object(Bucket=bkt, Key=obj["Key"])["Body"].read()))
    return sorted(eps, key=lambda e: e.get("created_at", 0))


def put_json(bkt: str, key: str, data) -> str:
    boto3.client("s3", region_name=REGION).put_object(
        Bucket=bkt, Key=key, Body=json.dumps(data, indent=2, default=str).encode(), ContentType="application/json")
    return f"s3://{bkt}/{key}"
