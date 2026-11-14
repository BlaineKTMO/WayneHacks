"""Build the dashboard and publish it to Amplify Hosting (or just write config for local dev).

  uv run python dashboard/deploy.py --local   # writes dashboard/public/config.json, then: cd dashboard && npm run dev
  uv run python dashboard/deploy.py           # build + upload to Amplify, prints the URL
"""

import argparse
import io
import json
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

import boto3

REGION = "us-east-2"
STACK = "ForkliftFleet"
HERE = Path(__file__).resolve().parent


def outputs() -> dict:
    cf = boto3.client("cloudformation", region_name=REGION)
    outs = cf.describe_stacks(StackName=STACK)["Stacks"][0]["Outputs"]
    return {o["OutputKey"]: o["OutputValue"] for o in outs}


def config(o: dict) -> dict:
    return {
        "region": REGION,
        "userPoolId": o["UserPoolId"],
        "userPoolClientId": o["UserPoolClientId"],
        "identityPoolId": o["IdentityPoolId"],
        "apiUrl": o["DashboardApiUrl"],
        "iotEndpoint": o["IotEndpointOut"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", action="store_true")
    a = ap.parse_args()
    o = outputs()
    cfg = json.dumps(config(o), indent=2)

    if a.local:
        (HERE / "public").mkdir(exist_ok=True)
        (HERE / "public" / "config.json").write_text(cfg)
        print("wrote dashboard/public/config.json. Now: cd dashboard && npm run dev")
        return

    subprocess.run(["npm", "run", "build"], cwd=HERE, check=True)
    dist = HERE / "dist"
    (dist / "config.json").write_text(cfg)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in dist.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(dist))

    amp = boto3.client("amplify", region_name=REGION)
    app_id, branch = o["AmplifyAppId"], "main"
    dep = amp.create_deployment(appId=app_id, branchName=branch)
    req = urllib.request.Request(dep["zipUploadUrl"], data=buf.getvalue(), method="PUT",
                                 headers={"Content-Type": "application/zip"})
    urllib.request.urlopen(req).read()
    job = dep["jobId"]
    amp.start_deployment(appId=app_id, branchName=branch, jobId=job)
    print(f"deploying job {job}…", end="", flush=True)
    while True:
        status = amp.get_job(appId=app_id, branchName=branch, jobId=job)["job"]["summary"]["status"]
        if status in ("SUCCEED", "FAILED", "CANCELLED"):
            break
        print(".", end="", flush=True)
        time.sleep(3)
    print(f" {status}")
    if status != "SUCCEED":
        sys.exit(1)
    print(f"live at {o['DashboardUrl']}")


if __name__ == "__main__":
    main()
