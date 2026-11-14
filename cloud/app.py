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
    shutil.copy(ROOT / "shared" / "warehouse.json", BUILD / "fleet_shared" / "warehouse.json")
    return BUILD


app = cdk.App()
FleetStack(
    app,
    "ForkliftFleet",
    lambda_code_dir=str(stage_lambda_code()),
    # Project Region is fixed (us-east-2). Account comes from your `aws login` session.
    env=cdk.Environment(region="us-east-2"),
)
cdk.Tags.of(app).add("project", "forklift-fleet")
app.synth()
