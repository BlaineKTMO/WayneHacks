"""Create an X.509 certificate for the edge laptop and attach it to the IoT thing + policy.

Run once (after `cdk deploy`):  uv run python cloud/scripts/create_edge_cert.py
Writes edge/certs/ (git-ignored). Hand that folder to the edge laptop over a USB stick or
AirDrop, never through git or chat.
"""

import json
import urllib.request
from pathlib import Path

import boto3

REGION = "us-east-2"
THING = "forklift-edge-01"
POLICY = "ForkliftEdgePolicy"
OUT = Path(__file__).resolve().parents[2] / "edge" / "certs"
ROOT_CA = "https://www.amazontrust.com/repository/AmazonRootCA1.pem"


def main():
    if (OUT / "device.pem.crt").exists():
        raise SystemExit(f"{OUT} already has a certificate; delete it first to make a new one")
    iot = boto3.client("iot", region_name=REGION)
    cert = iot.create_keys_and_certificate(setAsActive=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "device.pem.crt").write_text(cert["certificatePem"])
    key = OUT / "private.pem.key"
    key.write_text(cert["keyPair"]["PrivateKey"])
    key.chmod(0o600)
    urllib.request.urlretrieve(ROOT_CA, OUT / "AmazonRootCA1.pem")
    iot.attach_policy(policyName=POLICY, target=cert["certificateArn"])
    iot.attach_thing_principal(thingName=THING, principal=cert["certificateArn"])
    endpoint = iot.describe_endpoint(endpointType="iot:Data-ATS")["endpointAddress"]
    (OUT / "endpoint.json").write_text(json.dumps({"endpoint": endpoint, "thing": THING}, indent=2))
    print(f"wrote certificate to {OUT}\nendpoint: {endpoint}")


if __name__ == "__main__":
    main()
