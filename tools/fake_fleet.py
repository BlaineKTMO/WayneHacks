"""Poke the cloud without hardware. Publishes contract messages to IoT Core using your AWS login.

  uv run python tools/fake_fleet.py telemetry fl-01          # one telemetry message
  uv run python tools/fake_fleet.py pose fl-01 0.4 0.2       # one pose (x y in metres)
  uv run python tools/fake_fleet.py goto fl-01 slot_A3       # ask the planner for a route
  uv run python tools/fake_fleet.py task "move pallet 3 to dock B"
  uv run python tools/fake_fleet.py status fl-01             # read the Forklifts row

Watch live messages in the AWS console: IoT Core > MQTT test client > subscribe to "#".
"""

import argparse
import json

import boto3

from fleet_shared import topics
from fleet_shared.messages import Goto, Pose, TaskNew, Telemetry

REGION = "us-east-2"


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("telemetry"); p.add_argument("fid"); p.add_argument("--state", default="IDLE"); p.add_argument("--node")
    p = sub.add_parser("pose"); p.add_argument("fid"); p.add_argument("x", type=float); p.add_argument("y", type=float); p.add_argument("--theta", type=float, default=0.0)
    p = sub.add_parser("goto"); p.add_argument("fid"); p.add_argument("node")
    p = sub.add_parser("task"); p.add_argument("text")
    p = sub.add_parser("status"); p.add_argument("fid")
    a = ap.parse_args()

    endpoint = boto3.client("iot", region_name=REGION).describe_endpoint(endpointType="iot:Data-ATS")["endpointAddress"]
    iot = boto3.client("iot-data", region_name=REGION, endpoint_url=f"https://{endpoint}")

    def pub(topic, msg):
        iot.publish(topic=topic, qos=1, payload=msg.to_json().encode())
        print(f"published {topic}: {msg.to_json()}")

    if a.cmd == "telemetry":
        pub(topics.telemetry(a.fid), Telemetry(battery_v=7.4, motor_a=0.0, state=a.state, node=a.node))
    elif a.cmd == "pose":
        # ts ending in 000-099 so the sampled pose rule always keeps it
        msg = Pose(x=a.x, y=a.y, theta=a.theta)
        msg.ts -= msg.ts % 1000
        pub(topics.pose(a.fid), msg)
    elif a.cmd == "goto":
        pub(topics.goto(a.fid), Goto(node=a.node))
    elif a.cmd == "task":
        pub(topics.TASKS_NEW, TaskNew(text=a.text))
    elif a.cmd == "status":
        item = boto3.resource("dynamodb", region_name=REGION).Table("FleetForklifts").get_item(
            Key={"forklift_id": a.fid}).get("Item")
        print(json.dumps(item, indent=2, default=str))


if __name__ == "__main__":
    main()
