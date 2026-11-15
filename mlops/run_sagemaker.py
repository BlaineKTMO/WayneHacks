"""Run the offline planner gate (mlops/evaluate.py) as an Amazon SageMaker Processing job.

  uv run python -m mlops.run_sagemaker                 # all planners vs prod, waits and prints the result
  uv run python -m mlops.run_sagemaker v2-sonnet       # one candidate
  uv run python -m mlops.run_sagemaker --no-wait

Packages shared/fleet_shared + cloud/lambdas + mlops into a zip in s3://<bucket>/mlops-code/, then
starts a Processing job on AWS's prebuilt scikit-learn image (no Docker build). Inside the job the
same evaluate.py runs over every recorded episode, calls Claude on Bedrock with the job's role, and
writes results to S3 (evalsets/, planners/<v>/scores.json) and FleetPlanners.eval.
"""

from __future__ import annotations

import argparse
import io
import time
import zipfile

import boto3

from mlops.common import REGION, ROOT, STACK, bucket

# AWS-managed SageMaker scikit-learn processing image for us-east-2 (python 3.9).
IMAGE = "257758044811.dkr.ecr.us-east-2.amazonaws.com/sagemaker-scikit-learn:1.2-1-cpu-py3"
INSTANCE = "ml.t3.medium"
CODE_DIR = "/opt/ml/processing/code"


def package() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for base, arc in ((ROOT / "shared" / "fleet_shared", "fleet_shared"), (ROOT / "cloud" / "lambdas", "cloud/lambdas"),
                          (ROOT / "mlops", "mlops")):
            for f in base.rglob("*.py"):
                if "__pycache__" not in f.parts and "tests" not in f.parts:
                    z.write(f, f"{arc}/{f.relative_to(base)}")
        for f in (ROOT / "shared" / "fleet_shared").glob("*.json"):
            z.write(f, f"fleet_shared/{f.name}")
        z.write(ROOT / "shared" / "warehouse.json", "fleet_shared/warehouse.json")
    return buf.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("versions", nargs="*")
    ap.add_argument("--no-wait", action="store_true")
    a = ap.parse_args()

    outs = {o["OutputKey"]: o["OutputValue"] for o in boto3.client("cloudformation", region_name=REGION)
            .describe_stacks(StackName=STACK)["Stacks"][0]["Outputs"]}
    bkt, role = bucket(), outs["SageMakerRoleArn"]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    key = f"mlops-code/{stamp}/code.zip"
    boto3.client("s3", region_name=REGION).put_object(Bucket=bkt, Key=key, Body=package())

    # Old boto3 in the image lacks the Bedrock Converse API: upgrade, unpack, run evaluate.py unchanged.
    script = (f"pip install -q --upgrade 'boto3>=1.35' && cd {CODE_DIR} && python3 -m zipfile -e code.zip . && "
              f"PYTHONPATH={CODE_DIR}:{CODE_DIR}/cloud/lambdas python3 -m mlops.evaluate {' '.join(a.versions)}")
    name = f"planner-eval-{stamp}"
    sm = boto3.client("sagemaker", region_name=REGION)
    sm.create_processing_job(
        ProcessingJobName=name,
        RoleArn=role,
        AppSpecification={"ImageUri": IMAGE, "ContainerEntrypoint": ["bash", "-c", script]},
        ProcessingResources={"ClusterConfig": {"InstanceCount": 1, "InstanceType": INSTANCE, "VolumeSizeInGB": 5}},
        ProcessingInputs=[{"InputName": "code", "S3Input": {
            "S3Uri": f"s3://{bkt}/{key}", "LocalPath": CODE_DIR, "S3DataType": "S3Prefix",
            "S3InputMode": "File", "S3DataDistributionType": "FullyReplicated"}}],
        Environment={"DATA_BUCKET": bkt, "AWS_DEFAULT_REGION": REGION},
        StoppingCondition={"MaxRuntimeInSeconds": 1800},
        Tags=[{"Key": "project", "Value": "forklift-fleet"}],
    )
    print(f"started SageMaker Processing job {name}")
    print(f"console: https://{REGION}.console.aws.amazon.com/sagemaker/home?region={REGION}#/processing-jobs/{name}")
    if a.no_wait:
        return
    while True:
        d = sm.describe_processing_job(ProcessingJobName=name)
        st = d["ProcessingJobStatus"]
        if st in ("Completed", "Failed", "Stopped"):
            break
        print(f"  {st}…", flush=True)
        time.sleep(20)
    print(f"{name}: {st} {d.get('FailureReason', '')}")
    logs = boto3.client("logs", region_name=REGION)
    try:
        streams = logs.describe_log_streams(logGroupName="/aws/sagemaker/ProcessingJobs", logStreamNamePrefix=name)["logStreams"]
        for s in streams:
            for e in logs.get_log_events(logGroupName="/aws/sagemaker/ProcessingJobs", logStreamName=s["logStreamName"])["events"][-14:]:
                print("  |", e["message"])
    except Exception as e:  # noqa: BLE001
        print(f"(logs unavailable: {e})")
    raise SystemExit(0 if st == "Completed" else 1)


if __name__ == "__main__":
    main()
