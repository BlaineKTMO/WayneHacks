#!/usr/bin/env python3
"""CDK entry point. Run from repo root:  cd cloud && npx aws-cdk@2 deploy"""

import shutil
from pathlib import Path

import aws_cdk as cdk

from fleet_cloud.stack import FleetStack
from fleet_shared.sites import SITES

ROOT = Path(__file__).resolve().parent.parent
BUILD = Path(__file__).resolve().parent / "build" / "lambda"


def stage_lambda_code() -> Path:
    """Bundle cloud/lambdas + shared/fleet_shared + warehouse.json into one Lambda asset."""
    if BUILD.exists():
        shutil.rmtree(BUILD)
    shutil.copytree(ROOT / "cloud" / "lambdas", BUILD,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(ROOT / "shared" / "fleet_shared", BUILD / "fleet_shared",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("warehouse.json", "warehouse_table.json", "warehouse_mission.json"):
        shutil.copy(ROOT / "shared" / name, BUILD / "fleet_shared" / name)
    return BUILD


def stage_sim_code() -> Path:
    """tools/sim_fleet.py + the modules it imports, for the Fargate simulated fleet."""
    out = Path(__file__).resolve().parent / "build" / "simfleet"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    shutil.copy(ROOT / "tools" / "sim_fleet.py", out / "sim_fleet.py")
    ign = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(ROOT / "shared" / "fleet_shared", out / "fleet_shared", ignore=ign)
    for name in ("warehouse.json", "warehouse_table.json", "warehouse_mission.json"):
        shutil.copy(ROOT / "shared" / name, out / "fleet_shared" / name)
    (out / "fleet_edge").mkdir()
    for f in ("__init__.py", "config.py", "freewili_link.py", "pure_pursuit.py"):
        shutil.copy(ROOT / "edge" / "fleet_edge" / f, out / "fleet_edge" / f)
    return out


def demo_outputs() -> dict | None:
    """Shared sign-in values for the other sites, read from the deployed demo stack (plain strings:
    no cross-stack exports). None before the demo stack exists."""
    import boto3
    try:
        outs = boto3.client("cloudformation", region_name="us-east-2").describe_stacks(
            StackName=SITES["demo"]["stack"])["Stacks"][0]["Outputs"]
    except Exception:  # noqa: BLE001
        return None
    o = {x["OutputKey"]: x["OutputValue"] for x in outs}
    return {k: o[k] for k in ("UserPoolId", "UserPoolClientId", "IdentityPoolId") if k in o} or None


app = cdk.App()
env = cdk.Environment(region="us-east-2")   # project Region; account from your `aws login` session
lambda_dir = str(stage_lambda_code())
demo = SITES["demo"]
FleetStack(app, demo["stack"], site="demo", warehouse_map=demo["map"], table_prefix=demo["prefix"],
           lambda_code_dir=lambda_dir, sim_code_dir=str(stage_sim_code()), env=env)
shared = demo_outputs()
if shared:   # other sites reuse the demo stack's sign-in; deploy the demo stack first
    for site, cfg in SITES.items():
        if site != "demo":
            FleetStack(app, cfg["stack"], site=site, warehouse_map=cfg["map"], table_prefix=cfg["prefix"],
                       lambda_code_dir=lambda_dir, shared=shared, env=env)
cdk.Tags.of(app).add("project", "forklift-fleet")
app.synth()
