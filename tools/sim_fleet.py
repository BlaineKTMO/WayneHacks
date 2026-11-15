"""Simulated forklifts that live on real AWS IoT Core. Lets the cloud and dashboard be built
with no hardware.

  uv run python tools/sim_fleet.py                 # fl-01 at dock_A, fl-02 at dock_B
  uv run python tools/sim_fleet.py fl-01           # just one

Each fake forklift:
  subscribes  fleet/{id}/path, fleet/{id}/stop
  publishes   fleet/{id}/pose (10 Hz), fleet/{id}/telemetry (1 Hz), fleet/{id}/event
It drives with the same pure-pursuit code and SimLink the edge laptop uses.

Then in another terminal:  uv run python tools/fake_fleet.py goto fl-01 slot_A3
Connects over WebSocket signed with your `aws login` credentials (no device cert needed).
"""

from __future__ import annotations

import argparse
import json
import math
import queue
import threading
import time
import uuid

import boto3
from awscrt import auth, mqtt
from awsiot import mqtt_connection_builder

from fleet_edge.freewili_link import SimLink
from fleet_edge.pure_pursuit import WaypointFollower
from fleet_shared import topics
from fleet_shared.graph import Graph
from fleet_shared.messages import Event, Path, Pose, Stop, Telemetry

REGION = "us-east-2"
START = {"fl-01": "dock_A", "fl-02": "dock_B"}
TICK_S = 0.05            # 20 Hz control
POSE_EVERY = 2           # 10 Hz pose
TELEMETRY_EVERY = 20     # 1 Hz
BLOCKED_EVERY = 40       # re-send "blocked" every 2 s while waiting at the end of a partial path


class SimForklift:
    def __init__(self, fid: str, graph: Graph, publish):
        n = graph.nodes[START.get(fid, "dock_A")]
        self.fid, self.publish = fid, publish
        self.link = SimLink(x=n.x, y=n.y, theta=0.0)
        self.node = n.id
        self.path: Path | None = None
        self.follower: WaypointFollower | None = None
        self.state = "IDLE"
        self.waiting = False     # stopped at the end of a partial path
        self.inbox: queue.Queue = queue.Queue()   # messages from MQTT threads

    # -- MQTT callbacks just queue; all state changes happen in tick() on one thread
    def on_message(self, kind: str, payload: bytes):
        self.inbox.put((kind, payload))

    def _handle(self, kind: str, payload: bytes):
        try:
            if kind == "path":
                p = Path.from_json(payload)
                if not p.nodes:
                    self._stop_driving("IDLE")
                    return
                # First node is where we are; drive to the rest.
                targets = p.nodes[1:] if p.nodes[0]["id"] == self.node and len(p.nodes) > 1 else p.nodes
                self.path = Path(task_id=p.task_id, nodes=targets, action=p.action, partial=p.partial)
                self.follower = WaypointFollower([(n["x"], n["y"]) for n in targets], loop=False)
                self.state, self.waiting = "DRIVING", False
                print(f"[{self.fid}] path {p.task_id}: {[n['id'] for n in targets]}{' (partial)' if p.partial else ''}")
            elif kind == "stop":
                reason = Stop.from_json(payload).reason
                self._stop_driving("STOPPED")
                self.waiting = False
                print(f"[{self.fid}] STOP ({reason})")
        except ValueError as e:
            print(f"[{self.fid}] bad {kind} message: {e}")

    def _stop_driving(self, state: str):
        self.link.stop(state.lower())
        self.follower, self.path, self.state, self.waiting = None, None, state, False

    def _event(self, type_: str, node: str | None):
        self.publish(topics.event(self.fid), Event(type=type_, node=node))

    def tick(self, n: int):
        while not self.inbox.empty():
            self._handle(*self.inbox.get())

        if self.follower:
            before = self.follower.index
            left, right = self.follower.update(self.link.x, self.link.y, self.link.theta)
            self.link.wheels(left, right)
            reached = range(before, self.follower.index) if not self.follower.finished \
                else range(before, len(self.path.nodes))
            for i in reached:
                self.node = self.path.nodes[i]["id"]
                self._event("node_reached", self.node)
            if self.follower.finished:
                if self.path.partial:
                    # End of the reserved window: wait here until the cloud extends the route.
                    self.link.wheels(0, 0)
                    self.follower, self.state, self.waiting = None, "BLOCKED", True
                    self._event("blocked", self.node)
                    print(f"[{self.fid}] waiting at {self.node} for the lane to clear")
                else:
                    if self.path.action:
                        time.sleep(0.5)  # pretend to raise/lower the forks
                    self._event("done", self.node)
                    print(f"[{self.fid}] done at {self.node}")
                    self._stop_driving("IDLE")
        else:
            self.link.wheels(0, 0)
            if self.waiting and n % BLOCKED_EVERY == 0:
                self._event("blocked", self.node)

        if n % POSE_EVERY == 0:
            self.publish(topics.pose(self.fid), Pose(x=round(self.link.x, 4), y=round(self.link.y, 4),
                                                     theta=round(self.link.theta, 4)))
        if n % TELEMETRY_EVERY == 0:
            self.publish(topics.telemetry(self.fid), Telemetry(
                battery_v=7.4, motor_a=round(0.02 * (abs(self.link.left) + abs(self.link.right)), 2),
                state=self.state, node=self.node))


def connect(client_id: str) -> mqtt.Connection:
    session = boto3.Session(region_name=REGION)
    creds = session.get_credentials().get_frozen_credentials()
    provider = auth.AwsCredentialsProvider.new_static(creds.access_key, creds.secret_key, creds.token)
    endpoint = session.client("iot").describe_endpoint(endpointType="iot:Data-ATS")["endpointAddress"]
    conn = mqtt_connection_builder.websockets_with_default_aws_signing(
        endpoint=endpoint, region=REGION, credentials_provider=provider,
        client_id=client_id, clean_session=True, keep_alive_secs=30)
    conn.connect().result(timeout=15)
    print(f"connected to {endpoint} as {client_id}")
    return conn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("forklifts", nargs="*", default=list(START))
    a = ap.parse_args()

    graph = Graph.load()
    conn = connect(f"sim-{uuid.uuid4().hex[:6]}")
    lock = threading.Lock()

    def publish(topic: str, msg):
        with lock:
            conn.publish(topic=topic, payload=msg.to_json(), qos=mqtt.QoS.AT_MOST_ONCE)

    fleet = {fid: SimForklift(fid, graph, publish) for fid in a.forklifts}
    for fid, f in fleet.items():
        for kind, topic in (("path", topics.path(fid)), ("stop", topics.stop(fid))):
            fut, _ = conn.subscribe(topic=topic, qos=mqtt.QoS.AT_LEAST_ONCE,
                                    callback=lambda topic, payload, f=f, kind=kind, **_: f.on_message(kind, payload))
            fut.result(timeout=10)
        print(f"[{fid}] ready at {f.node}")

    print("running. Ctrl+C to quit.")
    n = 0
    try:
        while True:
            t0 = time.monotonic()
            for f in fleet.values():
                f.tick(n)
            n += 1
            time.sleep(max(0.0, TICK_S - (time.monotonic() - t0)))
    except KeyboardInterrupt:
        pass
    finally:
        conn.disconnect().result(timeout=5)


if __name__ == "__main__":
    main()
