#!/usr/bin/env python3
"""CDK entry point. Run from repo root:  cd cloud && npx aws-cdk@2 deploy"""

import shutil
from pathlib import Path

import aws_cdk as cdk

from fleet_cloud.stack import FleetStack

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
    for name in ("warehouse.json", "warehouse_table.json"):
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
    shutil.copy(ROOT / "shared" / "warehouse.json", out / "fleet_shared" / "warehouse.json")
    (out / "fleet_edge").mkdir()
    for f in ("__init__.py", "config.py", "freewili_link.py", "pure_pursuit.py"):
        shutil.copy(ROOT / "edge" / "fleet_edge" / f, out / "fleet_edge" / f)
    return out


app = cdk.App()
FleetStack(
    app,
    "ForkliftFleet",
    lambda_code_dir=str(stage_lambda_code()),
    sim_code_dir=str(stage_sim_code()),
    # Project Region is fixed (us-east-2). Account comes from your `aws login` session.
    env=cdk.Environment(region="us-east-2"),
)
cdk.Tags.of(app).add("project", "forklift-fleet")
app.synth()
