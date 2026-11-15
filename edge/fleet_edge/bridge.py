"""RING 2+: connect the real forklift(s) to the cloud. Runs on the edge laptop.

  uv run python -m fleet_edge.bridge                 # camera + Free-WiLi + device certificate
  uv run python -m fleet_edge.bridge --sim --iam     # no hardware, your `aws login` (for testing)

What it does, every 50 ms:
  1. Where is each forklift?  Camera + AprilTags (or --sim: simulated motion).
  2. Publish fleet/{id}/pose at 10 Hz and fleet/{id}/telemetry at 1 Hz.
  3. Drive the route the cloud sent (fleet/{id}/path) with pure pursuit -> Free-WiLi W lines.
  4. Report node_reached / done, or "blocked" at the end of a partial (reserved) route.
Safety: tag not seen for LOST_TAG_STOP_S -> S (stop) right away, resume when it's back.
        fleet/{id}/stop -> S immediately, path dropped. The Free-WiLi watchdog covers the rest.

Before the first real run: `uv run python cloud/scripts/create_edge_cert.py` (writes edge/certs/).
Settings: FORKLIFT_LINKS in config.py says which forklift is on which serial port.
Never run this and tools/sim_fleet.py (or the hosted simulated fleet) for the same forklifts.
"""

from __future__ import annotations

import argparse
import json
import math
import queue
import threading
import time
import uuid
from pathlib import Path

from awscrt import auth, mqtt
from awsiot import mqtt_connection_builder

from fleet_shared import topics
from fleet_shared.graph import Graph
from fleet_shared.messages import Event, Path as PathMsg, Pose, Stop, Telemetry
from fleet_shared.safety import Guard, Hazards

from . import config
from .freewili_link import SerialLink, SimLink
from .pure_pursuit import WaypointFollower

CERTS = Path(__file__).resolve().parents[1] / "certs"
TICK_S = 1 / config.COMMAND_RATE_HZ
BLOCKED_EVERY_S = 2.0


class Unit:
    """One forklift: its link to the motors, its route, and what it reports."""

    def __init__(self, fid: str, link, graph: Graph, publish, hazards: Hazards | None = None,
                 follower_factory=None):
        self.fid, self.link, self.graph, self.publish = fid, link, graph, publish
        # How a route is driven. Default (and --sim): pure pursuit, which the simulator was
        # calibrated against. Real forklift: turn-then-drive inside the camera border
        # (turn_drive.py), because this skid-steer chassis curves and slides under pure pursuit.
        self.make_follower = follower_factory or (lambda wps: WaypointFollower(wps, loop=False))
        self.hazards = hazards or Hazards()
        self.guard = Guard()
        self.node: str | None = None
        self.path: PathMsg | None = None
        self.follower: WaypointFollower | None = None
        self.state = "IDLE"
        self.waiting = False
        self.pose: tuple[float, float, float] | None = None
        self.last_seen = 0.0
        self.last_blocked = 0.0
        self.inbox: queue.Queue = queue.Queue()

    def on_message(self, kind: str, payload: bytes):  # MQTT thread: just queue it
        self.inbox.put((kind, payload))

    def _event(self, type_: str):
        self.publish(topics.event(self.fid), Event(type=type_, node=self.node))

    def _handle(self, kind: str, payload: bytes):
        try:
            if kind == "stop":
                reason = Stop.from_json(payload).reason
                self.link.stop(reason)
                self.follower, self.path, self.waiting, self.state = None, None, False, "STOPPED"
                print(f"[{self.fid}] STOP ({reason})")
            elif kind == "path":
                p = PathMsg.from_json(payload)
                nodes = p.nodes[1:] if p.nodes and p.nodes[0]["id"] == self.node and len(p.nodes) > 1 else p.nodes
                if not nodes:
                    self.follower, self.path, self.state = None, None, "IDLE"
                    return
                self.path = PathMsg(task_id=p.task_id, nodes=nodes, action=p.action, partial=p.partial)
                self.follower = self.make_follower([(n["x"], n["y"]) for n in nodes])
                self.state, self.waiting = "DRIVING", False
                print(f"[{self.fid}] route {[n['id'] for n in nodes]}{' (partial)' if p.partial else ''}")
        except ValueError as e:
            print(f"[{self.fid}] ignored bad {kind}: {e}")

    def tick(self, now: float, fresh_pose: tuple[float, float, float] | None):
        while not self.inbox.empty():
            self._handle(*self.inbox.get())
        if fresh_pose:
            self.pose, self.last_seen = fresh_pose, now
            if self.node is None:
                self.node = self.graph.nearest_node(fresh_pose[0], fresh_pose[1])
        tag_ok = now - self.last_seen < config.LOST_TAG_STOP_S

        if not tag_ok:
            if self.state != "STOPPED":
                self.link.stop("lost_tag")
                print(f"[{self.fid}] tag lost: stopped")
            self.state = "STOPPED" if self.follower or self.state != "IDLE" else self.state
            return
        if self.state == "STOPPED" and self.follower and not self.guard.holding:  # tag is back: carry on
            self.state = "DRIVING"

        # Safety guard: a person in or near the path stops us here, at the edge, with no cloud round trip.
        upcoming = self.path.nodes[self.follower.index:] if self.follower and self.path else []
        action = self.guard.update(self.pose, upcoming, self.hazards)
        if action == "stop":
            self.link.stop("person")
            self.state = "STOPPED"
            self._event("safety_stop")
            print(f"[{self.fid}] SAFETY STOP: person in path")
        elif action == "clear":
            self.state = "DRIVING" if self.follower else "IDLE"
            self._event("safety_clear")
            print(f"[{self.fid}] path clear, resuming")
        if self.guard.holding:
            self.link.stop("person")
            return

        if self.follower and self.pose:
            before = self.follower.index
            left, right = self.follower.update(*self.pose)
            # turn_drive refusals: every move would lose the tag (blocked), the tag drifted out
            # of the camera view (border), or spins keep sliding (drift_stop). Brake and give the
            # route back as a fault rather than ever reporting it done.
            mode = getattr(self.follower, "mode", "")
            if mode in ("blocked", "border", "drift_stop"):
                self.link.stop(mode)
                print(f"[{self.fid}] route abandoned: {mode} near {self.node}")
                self._event("fault")
                self.follower, self.path, self.state, self.waiting = None, None, "STOPPED", False
                return
            self.link.wheels(left, right)
            done_upto = len(self.path.nodes) if self.follower.finished else self.follower.index
            for i in range(before, done_upto):
                self.node = self.path.nodes[i]["id"]
                self._event("node_reached")
            if self.follower.finished:
                self.link.wheels(0, 0)
                if self.path.partial:
                    self.follower, self.state, self.waiting = None, "BLOCKED", True
                    self.last_blocked = now
                    self._event("blocked")
                else:
                    if self.path.action:
                        time.sleep(0.5)  # TODO(forks): send the fork servo command here
                    self._event("done")
                    print(f"[{self.fid}] done at {self.node}")
                    self.follower, self.path, self.state = None, None, "IDLE"
        else:
            self.link.wheels(0, 0)  # keeps the Free-WiLi watchdog fed while idle
            if self.waiting and now - self.last_blocked > BLOCKED_EVERY_S:
                self.last_blocked = now
                self._event("blocked")

    def telemetry(self):
        t = self.link.poll()
        motor = 0.02 * (abs(getattr(self.link, "left", 0)) + abs(getattr(self.link, "right", 0)))
        self.publish(topics.telemetry(self.fid), Telemetry(
            battery_v=t.battery_v if t else 0.0, motor_a=round(motor, 2), state=self.state, node=self.node))


def connect(use_iam: bool, region: str = "us-east-2") -> mqtt.Connection:
    def interrupted(connection, error, **_):
        print(f"!! connection interrupted: {error}")

    def resumed(connection, return_code, session_present, **_):
        print(f"!! reconnected (session_present={session_present})")
        if not session_present:
            connection.resubscribe_existing_topics()

    common = dict(clean_session=False, keep_alive_secs=30, on_connection_interrupted=interrupted,
                  on_connection_resumed=resumed)
    if use_iam:
        import boto3
        s = boto3.Session(region_name=region)
        c = s.get_credentials().get_frozen_credentials()
        endpoint = s.client("iot").describe_endpoint(endpointType="iot:Data-ATS")["endpointAddress"]
        conn = mqtt_connection_builder.websockets_with_default_aws_signing(
            endpoint=endpoint, region=region, client_id=f"sim-bridge-{uuid.uuid4().hex[:6]}",
            credentials_provider=auth.AwsCredentialsProvider.new_static(c.access_key, c.secret_key, c.token), **common)
    else:
        info = json.loads((CERTS / "endpoint.json").read_text())
        conn = mqtt_connection_builder.mtls_from_path(
            endpoint=info["endpoint"], cert_filepath=str(CERTS / "device.pem.crt"),
            pri_key_filepath=str(CERTS / "private.pem.key"), ca_filepath=str(CERTS / "AmazonRootCA1.pem"),
            client_id=f"edge-{uuid.uuid4().hex[:6]}", **common)
    conn.connect().result(timeout=15)
    return conn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", action="store_true", help="no camera / Free-WiLi: simulated motion")
    ap.add_argument("--iam", action="store_true", help="connect with your aws login instead of the device cert")
    a = ap.parse_args()

    graph = Graph.load()
    if not a.iam and not (CERTS / "device.pem.crt").exists():
        raise SystemExit("No device certificate. Run: uv run python cloud/scripts/create_edge_cert.py  (or use --iam)")
    conn = connect(a.iam)
    lock = threading.Lock()

    def publish(topic, msg):
        with lock:
            conn.publish(topic=topic, payload=msg.to_json(), qos=mqtt.QoS.AT_MOST_ONCE)

    hazards = Hazards()
    fut, _ = conn.subscribe(topic=topics.DETECTIONS, qos=mqtt.QoS.AT_LEAST_ONCE,
                            callback=lambda topic, payload, **_: hazards.on_detections(json.loads(payload)))
    fut.result(timeout=10)
    camera = detector = localizer = None
    factory = None
    if not a.sim:
        from .localizer import Camera, Localizer, TagDetector
        from .turn_drive import TurnDriveFollower, border_from_camera
        camera, detector = Camera(), TagDetector()
        localizer = Localizer(camera.K)   # K: needed for the raised tag's height correction
        border = border_from_camera(localizer, config.CAMERA_WIDTH, config.CAMERA_HEIGHT)
        factory = lambda wps: TurnDriveFollower(wps, loop=False, border=border)  # noqa: E731

    units: dict[str, Unit] = {}
    fids = config.SIM_START if a.sim else config.FORKLIFT_LINKS
    for fid in fids:
        if a.sim:
            start = graph.nodes[config.SIM_START.get(fid, "dock_A")]
            link = SimLink(x=start.x, y=start.y, theta=0.0)
        else:
            link = SerialLink(config.FORKLIFT_LINKS[fid])
        units[fid] = Unit(fid, link, graph, publish, hazards, follower_factory=factory)
        for kind, topic in (("path", topics.path(fid)), ("stop", topics.stop(fid))):
            fut, _ = conn.subscribe(topic=topic, qos=mqtt.QoS.AT_LEAST_ONCE,
                                    callback=lambda topic, payload, u=units[fid], k=kind, **_: u.on_message(k, payload))
            fut.result(timeout=10)
    print(f"bridge up: {', '.join(units)} ({'sim' if a.sim else 'camera + Free-WiLi'})")

    n = 0
    try:
        while True:
            t0 = time.monotonic()
            if a.sim:
                seen = {fid: (u.link.x, u.link.y, u.link.theta) for fid, u in units.items()}
            else:
                poses = localizer.process(detector.detect(camera.read()))
                seen = {fid: (p.x, p.y, p.theta) for fid, p in poses.items()}
            for fid, u in units.items():
                u.tick(t0, seen.get(fid))
                if n % 2 == 0 and fid in seen:
                    x, y, th = seen[fid]
                    publish(topics.pose(fid), Pose(x=round(x, 4), y=round(y, 4), theta=round(math.remainder(th, math.tau), 4)))
                if n % config.COMMAND_RATE_HZ == 0:
                    u.telemetry()
            n += 1
            time.sleep(max(0.0, TICK_S - (time.monotonic() - t0)))
    except KeyboardInterrupt:
        pass
    finally:
        for u in units.values():
            u.link.close()
        if camera:
            camera.close()
        conn.disconnect().result(timeout=5)


if __name__ == "__main__":
    main()
