"""RING 2+: connect the real forklift(s) to the cloud. Runs on the edge laptop.

  uv run python -m fleet_edge.bridge                 # camera + Free-WiLi + device certificate
  uv run python -m fleet_edge.bridge --sim --iam     # no hardware, your `aws login` (for testing)
  uv run python -m fleet_edge.bridge --view          # + a window: camera, graph, route, events
  uv run python -m fleet_edge.bridge --local-goto dock_A --view
        # no cloud at all: plan the route here with the same A* and graph the cloud planner
        # uses, and drive it with the same Unit. For trying the hardware before the cloud.

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
import os
import json
import math
import queue
import threading
import time
import uuid
from pathlib import Path

os.environ.setdefault("SITE", "wh1")   # the edge laptop serves Warehouse 1 unless told otherwise
from fleet_shared.sites import SITES as _SITES  # noqa: E402

os.environ.setdefault("WAREHOUSE_MAP", _SITES.get(os.environ["SITE"], {}).get("map", "demo"))

from awscrt import auth, mqtt  # noqa: E402
from awsiot import mqtt_connection_builder

from fleet_shared import topics
from fleet_shared.graph import Graph
from fleet_shared.messages import Event, Path as PathMsg, Pose, Stop, Telemetry
from fleet_shared.safety import Guard, Hazards

from . import config
from .freewili_link import SimLink, open_link
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
        self.fork_until = 0.0     # monotonic time a fork move ends (0 = none)
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
                self.fork_until = 0.0
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
                elif self.path.action in ("pick", "drop") and config.FORKLIFT_HAS_FORK.get(self.fid, True):
                    # Pick = lift, Drop = lower. The firmware runs the fork for FORK_LIFT_MS and stops
                    # it itself; we report "done" when that time is up, without blocking the loop.
                    up = self.path.action == "pick"
                    self.link.fork(config.FORK_SPEED if up else -config.FORK_SPEED, config.FORK_LIFT_MS)
                    self.fork_until = now + config.FORK_LIFT_MS / 1000
                    self.follower, self.state = None, "DRIVING"
                    print(f"[{self.fid}] fork {'up' if up else 'down'} ({config.FORK_LIFT_MS} ms)")
                else:
                    self._finish()
        elif self.fork_until:
            self.link.wheels(0, 0)
            if now >= self.fork_until:
                self.fork_until = 0.0
                self._finish()
        else:
            self.link.wheels(0, 0)  # keeps the Free-WiLi watchdog fed while idle
            if self.waiting and now - self.last_blocked > BLOCKED_EVERY_S:
                self.last_blocked = now
                self._event("blocked")

    def _finish(self):
        self._event("done")
        print(f"[{self.fid}] done at {self.node}")
        self.follower, self.path, self.state = None, None, "IDLE"

    def health(self, now: float) -> dict:
        """Free-WiLi link + tag visibility for the Devices panel."""
        tele_age = now - getattr(self.link, "last_telemetry_at", 0.0) if getattr(self.link, "last_telemetry_at", 0.0) else None
        t = self.link.last_telemetry
        ok = tele_age is not None and tele_age < 3
        return {"link": "ok" if ok else ("silent" if tele_age is not None else "no telemetry yet"),
                "port": str(getattr(self.link, "port", "?")), "telemetry_age_s": round(tele_age, 1) if tele_age is not None else None,
                "fw_state": t.state if t else None, "battery_v": t.battery_v if t else None,
                "tag_age_s": round(now - self.last_seen, 1) if self.last_seen else None, "state": self.state}

    def telemetry(self):
        t = self.link.poll()
        motor = 0.02 * (abs(getattr(self.link, "left", 0)) + abs(getattr(self.link, "right", 0)))
        self.publish(topics.telemetry(self.fid), Telemetry(
            battery_v=t.battery_v if t else 0.0, motor_a=round(motor, 2), state=self.state, node=self.node))


def encode_frame(frame) -> bytes | None:
    """Downscale + JPEG for the website. Lowers quality until it fits an MQTT message."""
    import cv2
    h, w = frame.shape[:2]
    scale = config.CAMERA_STREAM_WIDTH / w
    small = cv2.resize(frame, (config.CAMERA_STREAM_WIDTH, int(h * scale))) if scale < 1 else frame
    for q in (70, 55, 40, 25):
        ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, q])
        if ok and len(buf) < 110_000:
            return buf.tobytes()
    return None


EDGE_ID = os.environ.get("EDGE_ID", "edge-01")


def offline_will(source: str) -> mqtt.Will:
    """IoT Core publishes this (retained) if we drop off without saying goodbye."""
    return mqtt.Will(topic=topics.health(source), qos=mqtt.QoS.AT_LEAST_ONCE, retain=True,
                     payload=json.dumps({"source": source, "online": False, "reason": "connection lost"}).encode())


class BridgeView:
    """--view: the camera with the warehouse graph, the active route, the forklift and the
    events it has sent. q in the window quits."""

    def __init__(self, localizer, graph: Graph, video_path: str | None):
        from .drive_cam import Projector
        from .turn_drive import border_from_camera
        self.w2p, self.graph, self.video_path, self.video = Projector(localizer), graph, video_path, None
        self.border = border_from_camera(localizer, config.CAMERA_WIDTH, config.CAMERA_HEIGHT)

    def show(self, frame, sightings, pose, unit: "Unit", log: list[str], target=None, local=False) -> bool:
        import cv2
        import numpy as np
        from .drive_cam import WHITE, GREEN, YELLOW, ORANGE, RED, draw_panel, draw_scene

        img = frame.copy()
        w2p = self.w2p
        if w2p.loc.H is not None:
            g = self.graph
            for a_id, nbrs in g.adj.items():                       # graph edges
                for b_id in nbrs:
                    a, b = g.nodes[a_id], g.nodes[b_id if isinstance(b_id, str) else b_id[0]]
                    cv2.line(img, w2p(a.x, a.y), w2p(b.x, b.y), (110, 110, 110), 1, cv2.LINE_AA)
            if unit.path:                                           # active route
                pts = [w2p(n["x"], n["y"]) for n in unit.path.nodes]
                if unit.pose:
                    pts = [w2p(unit.pose[0], unit.pose[1])] + pts
                cv2.polylines(img, [np.array(pts)], False, YELLOW, 3, cv2.LINE_AA)
            for nid, nd in g.nodes.items():                        # nodes
                p = w2p(nd.x, nd.y)
                hot = nid == target
                cv2.circle(img, p, 9 if hot else 5, RED if hot else WHITE, 2 if hot else -1)
                cv2.putText(img, nid, (p[0] + 8, p[1] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                            RED if hot else WHITE, 1, cv2.LINE_AA)
        tag_ok = unit.state != "STOPPED" or unit.follower is None
        draw_scene(img, sightings or [], pose, w2p, self.border, tag_ok, ring1_route=False)
        rows = [(f"{'LOCAL (no cloud)' if local else 'cloud'}   {unit.fid}: {unit.state}"
                 f"   node {unit.node or '?'}" + (f"   goto {target}" if target else ""),
                 GREEN if unit.state == "DRIVING" else WHITE)]
        if unit.path:
            rows.append(("route: " + " > ".join(n["id"] for n in unit.path.nodes), YELLOW))
        rows += [(f"event: {e}", ORANGE) for e in log[-4:]]
        rows.append(("q quits (the forklift stops)", (180, 180, 180)))
        draw_panel(img, rows, "")
        cv2.imshow("Forklift bridge", img)
        if self.video_path:
            if self.video is None:
                Path(self.video_path).parent.mkdir(parents=True, exist_ok=True)
                self.video = cv2.VideoWriter(self.video_path, cv2.VideoWriter_fourcc(*"mp4v"), 20,
                                             (img.shape[1], img.shape[0]))
            self.video.write(img)
        return (cv2.waitKey(1) & 0xFF) != ord("q")

    def close(self):
        import cv2
        if self.video is not None:
            self.video.release()
            print(f"saved video {self.video_path}")
        cv2.destroyAllWindows()


def connect(use_iam: bool, region: str = "us-east-2") -> mqtt.Connection:
    def interrupted(connection, error, **_):
        print(f"!! connection interrupted: {error}")

    def resumed(connection, return_code, session_present, **_):
        print(f"!! reconnected (session_present={session_present})")
        if not session_present:
            connection.resubscribe_existing_topics()

    common = dict(clean_session=False, keep_alive_secs=15, on_connection_interrupted=interrupted,
                  on_connection_resumed=resumed, will=offline_will(EDGE_ID))
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
    ap.add_argument("--view", action="store_true", help="show the camera with the graph, route and events")
    ap.add_argument("--video", help="with --view: also record the window to this .mp4")
    ap.add_argument("--local-goto", metavar="NODE",
                    help="no cloud: plan a route to NODE here (same A* + graph) and drive it, then exit")
    ap.add_argument("--forklift", default=config.HERO_FORKLIFT,
                    help="with --local-goto: which forklift drives (default %(default)s)")
    ap.add_argument("--max-seconds", type=float, default=60.0,
                    help="with --local-goto: stop the forklift and exit after this long (default 60)")
    ap.add_argument("--log", help="with --local-goto: per-frame CSV (t, pose, tags seen, mode, wheels)")
    a = ap.parse_args()

    graph = Graph.load()
    lock = threading.Lock()
    hazards = Hazards()
    log: list[str] = []          # recent published events, for the view
    conn = None
    if a.local_goto:
        if a.local_goto not in graph.nodes:
            raise SystemExit(f"unknown node {a.local_goto}; nodes: {', '.join(graph.nodes)}")

        def publish(topic, msg):
            if topic.endswith("/event"):
                log.append(f"{msg.type} {msg.node or ''}".strip())
                print(f"  event: {msg.to_json()}")
    else:
        if not a.iam and not (CERTS / "device.pem.crt").exists():
            raise SystemExit("No device certificate. Run: uv run python cloud/scripts/create_edge_cert.py"
                             "  (or use --iam, or --local-goto NODE to try without the cloud)")
        conn = connect(a.iam)

        def publish(topic, msg):
            if topic.endswith("/event"):
                log.append(f"{msg.type} {msg.node or ''}".strip())
            with lock:
                conn.publish(topic=topic, payload=msg.to_json(), qos=mqtt.QoS.AT_MOST_ONCE)

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
        factory = lambda fid: (lambda wps: TurnDriveFollower(wps, loop=False, border=border, fid=fid))  # noqa: E731

    units: dict[str, Unit] = {}
    fids = config.SIM_START if a.sim else config.FORKLIFT_LINKS
    for fid in fids:
        if a.sim:
            start = graph.nodes[config.SIM_START.get(fid, "dock_A")]
            link = SimLink(x=start.x, y=start.y, theta=0.0)
        else:
            try:
                link = open_link(fid)
            except Exception as e:   # not plugged in / no radio base: run without it
                print(f"[{fid}] no link ({e}); skipping it")
                continue
        units[fid] = Unit(fid, link, graph, publish, hazards,
                          follower_factory=factory(fid) if factory else None)
    if not units:
        raise SystemExit("no forklift reachable")
        for kind, topic in (("path", topics.path(fid)), ("stop", topics.stop(fid))):
            if conn is None:
                break
            fut, _ = conn.subscribe(topic=topic, qos=mqtt.QoS.AT_LEAST_ONCE,
                                    callback=lambda topic, payload, u=units[fid], k=kind, **_: u.on_message(k, payload))
            fut.result(timeout=10)
    print(f"bridge up: {', '.join(units)} ({'sim' if a.sim else 'camera + Free-WiLi'})"
          f"{'  [LOCAL: no cloud]' if a.local_goto else ''}")

    view = BridgeView(localizer, graph, a.video) if a.view and not a.sim else None
    if a.local_goto and a.forklift not in units:
        raise SystemExit(f"--forklift {a.forklift}: not connected (have {', '.join(units)})")
    goto_unit = units[a.forklift] if a.local_goto else None
    goto_sent, goto_until = False, None
    t_start = time.monotonic()
    csv = None
    if a.local_goto and a.log:
        Path(a.log).parent.mkdir(parents=True, exist_ok=True)
        csv = open(a.log, "w")
        csv.write("t,x_mm,y_mm,heading_deg,tags,state,mode,target,left,right\n")

    n = 0
    last_frame = 0.0
    started = time.monotonic()
    cam_frames, cam_last, corners_seen, fps_mark = 0, 0.0, 0, (time.monotonic(), 0)

    def publish_health(now: float):
        nonlocal fps_mark
        fps = (cam_frames - fps_mark[1]) / max(1e-6, now - fps_mark[0])
        fps_mark = (now, cam_frames)
        cam = ({"mode": "simulated"} if a.sim else {
            "mode": "real", "fps": round(fps, 1), "frame_age_s": round(now - cam_last, 1) if cam_last else None,
            "corner_tags_seen": corners_seen, "corner_tags_total": len(config.CORNER_TAGS),
            "map_locked": localizer.H is not None if localizer else False,
            "streaming": bool(config.CAMERA_STREAM_FPS)})
        msg = {"source": EDGE_ID, "kind": "edge", "online": True, "uptime_s": int(now - started),
               "map": os.environ.get("WAREHOUSE_MAP", "demo"), "sim": a.sim, "camera": cam,
               "forklifts": {fid: u.health(now) for fid, u in units.items()}, "ts": int(time.time() * 1000)}
        with lock:
            conn.publish(topic=topics.health(EDGE_ID), payload=json.dumps(msg), qos=mqtt.QoS.AT_MOST_ONCE, retain=True)

    try:
        while True:
            t0 = time.monotonic()
            frame = sightings = None
            poses = {}
            if a.sim:
                seen = {fid: (u.link.x, u.link.y, u.link.theta) for fid, u in units.items()}
            else:
                frame = camera.read()
                sightings = detector.detect(frame)
                poses = localizer.process(sightings)
                cam_frames += 1
                cam_last = t0
                corners_seen = sum(1 for sg in sightings if sg.tag_id in config.CORNER_TAGS)
                if conn is not None and config.CAMERA_STREAM_FPS and t0 - last_frame >= 1 / config.CAMERA_STREAM_FPS:
                    last_frame = t0
                    jpg = encode_frame(frame)
                    if jpg:
                        with lock:
                            conn.publish(topic=topics.CAMERA, payload=jpg, qos=mqtt.QoS.AT_MOST_ONCE)
                seen = {fid: (p.x, p.y, p.theta) for fid, p in poses.items()}

            # --local-goto: once the camera has placed the forklift on the graph, plan the
            # route here exactly as the RoutePlanner Lambda does (A* from its node).
            if goto_unit and not goto_sent and goto_unit.node is not None:
                route = graph.astar(goto_unit.node, a.local_goto) or [a.local_goto]
                if route[0] != goto_unit.node:
                    route = [goto_unit.node] + route
                msg = PathMsg(task_id=f"local-{uuid.uuid4().hex[:6]}", nodes=graph.to_path_nodes(route))
                print(f"[local] goto {a.local_goto}: A* route {route}")
                log.append(f"route {' > '.join(route)}")
                goto_unit.on_message("path", msg.to_json().encode())
                goto_sent = True
            for fid, u in units.items():
                u.tick(t0, seen.get(fid))
                if n % 2 == 0 and fid in seen:
                    x, y, th = seen[fid]
                    publish(topics.pose(fid), Pose(x=round(x, 4), y=round(y, 4), theta=round(math.remainder(th, math.tau), 4)))
                if n % config.COMMAND_RATE_HZ == 0:
                    u.telemetry()
            if conn is not None and n % config.COMMAND_RATE_HZ == 0:
                publish_health(t0)
            if csv is not None and goto_unit is not None:
                p, f = seen.get(goto_unit.fid), goto_unit.follower
                tags = "+".join(str(t.tag_id) for t in (sightings or []) if t.tag_id in config.FORKLIFT_TAGS)
                w = getattr(goto_unit.link, "last_wheels", ("", ""))
                csv.write(f"{t0 - t_start:.2f},{p[0]*1000:.1f},{p[1]*1000:.1f},{math.degrees(p[2]):.1f}," if p else f"{t0 - t_start:.2f},,,,")
                csv.write(f"{tags},{goto_unit.state},{getattr(f, 'mode', '')},"
                          f"{'%.0f/%.0f' % tuple(v * 1000 for v in f.target) if f else ''},{w[0]},{w[1]}\n")
            if goto_unit and t0 - t_start > a.max_seconds:
                for u in units.values():
                    u.link.stop("max_seconds")
                print(f"[local] gave up after {a.max_seconds:.0f} s: state {goto_unit.state}, at {goto_unit.node}")
                break
            if view is not None and frame is not None:
                fid0 = goto_unit.fid if goto_unit else next(iter(units))
                if not view.show(frame, sightings, poses.get(fid0), units[fid0], log,
                                 target=a.local_goto, local=bool(a.local_goto)):
                    break
            if goto_unit and goto_sent and goto_unit.follower is None and goto_unit.inbox.empty():
                goto_until = goto_until or (t0 + 2.0)        # finished (done or fault): linger 2 s
                if t0 > goto_until:
                    print(f"[local] finished: state {goto_unit.state}, at {goto_unit.node}")
                    break
            n += 1
            time.sleep(max(0.0, TICK_S - (time.monotonic() - t0)))
    except KeyboardInterrupt:
        pass
    finally:
        if conn is not None:
            try:  # say goodbye so the dashboard shows offline right away
                with lock:
                    conn.publish(topic=topics.health(EDGE_ID), qos=mqtt.QoS.AT_LEAST_ONCE, retain=True,
                                 payload=json.dumps({"source": EDGE_ID, "online": False, "reason": "bridge stopped"}))
            except Exception:  # noqa: BLE001
                pass
        if csv is not None:
            csv.close()
        for u in units.values():
            u.link.stop("exit")
            u.link.close()
        if camera:
            camera.close()
        if view is not None:
            view.close()
        if conn is not None:
            conn.disconnect().result(timeout=5)
        if not a.sim:
            import os
            os._exit(0)   # pupil_apriltags can segfault while the interpreter shuts down


if __name__ == "__main__":
    main()
