"""Message payloads for every MQTT topic.

Each class maps 1:1 to a row of the messaging contract. Use `.to_json()` to publish and
`Cls.from_dict(d)` to parse (it raises ValueError on missing/extra fields, so bad
messages fail loudly instead of silently).
"""

from __future__ import annotations

import json
import time
from dataclasses import MISSING, asdict, dataclass, field, fields
from typing import Literal, Optional

ForkliftState = Literal["IDLE", "DRIVING", "BLOCKED", "STOPPED", "FAULT"]
EventType = Literal["node_reached", "done", "blocked", "fault"]
Action = Optional[Literal["pick", "drop"]]

FORKLIFT_STATES = ("IDLE", "DRIVING", "BLOCKED", "STOPPED", "FAULT")
EVENT_TYPES = ("node_reached", "done", "blocked", "fault", "safety_stop", "safety_clear")


def now_ms() -> int:
    return int(time.time() * 1000)


class _Msg:
    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, d: dict):
        names = {f.name for f in fields(cls)}
        required = {f.name for f in fields(cls)
                    if f.default is MISSING and f.default_factory is MISSING}
        extra, missing = set(d) - names, required - set(d)
        if extra or missing:
            raise ValueError(f"{cls.__name__}: missing={sorted(missing)} extra={sorted(extra)}")
        obj = cls(**{k: d[k] for k in names if k in d})
        obj.validate()
        return obj

    @classmethod
    def from_json(cls, s: str | bytes):
        return cls.from_dict(json.loads(s))

    def validate(self) -> None:
        pass


@dataclass
class Pose(_Msg):
    x: float
    y: float
    theta: float
    conf: float = 1.0
    ts: int = field(default_factory=now_ms)


@dataclass
class Telemetry(_Msg):
    battery_v: float
    motor_a: float
    state: str
    node: Optional[str] = None
    ts: int = field(default_factory=now_ms)

    def validate(self):
        if self.state not in FORKLIFT_STATES:
            raise ValueError(f"bad state {self.state}")


@dataclass
class PathNode(_Msg):
    id: str
    x: float
    y: float


@dataclass
class Path(_Msg):
    task_id: str
    nodes: list  # list of {"id","x","y"} dicts
    action: Optional[str] = None  # "pick" | "drop" | None
    partial: bool = False  # True: more of the route will follow; stop at the end and report "blocked"

    def validate(self):
        for n in self.nodes:
            PathNode.from_dict(n)
        if self.action not in (None, "pick", "drop"):
            raise ValueError(f"bad action {self.action}")


@dataclass
class Event(_Msg):
    type: str
    node: Optional[str] = None
    ts: int = field(default_factory=now_ms)

    def validate(self):
        if self.type not in EVENT_TYPES:
            raise ValueError(f"bad event type {self.type}")


@dataclass
class Stop(_Msg):
    reason: str


@dataclass
class Goto(_Msg):
    node: str
    task_id: Optional[str] = None


@dataclass
class Detections(_Msg):
    model_version: str
    objects: list  # [{"cls","x","y","conf"}]
    ts: int = field(default_factory=now_ms)


@dataclass
class Locks(_Msg):
    locks: dict  # node id -> forklift id holding it
    ts: int = field(default_factory=now_ms)


@dataclass
class TaskNew(_Msg):
    text: str


@dataclass
class TaskStatus(_Msg):
    state: str
    tree: Optional[dict] = None
    assigned_to: Optional[str] = None
    planner_version: Optional[str] = None
    error: Optional[str] = None
    text: Optional[str] = None  # the original order, echoed so the dashboard can label it
