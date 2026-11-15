"""2D graph simulator: predict what a behavior tree will cost, from a warehouse snapshot.

Same graph and same A* as the real fleet, constant-speed kinematics, deterministic.
Used for shadow scoring of candidate planners and for the sim-to-real check.

    outcome = simulate(tree, snapshot, graph)
    snapshot = {"forklift": "fl-01", "start": "dock_A", "pallets": {...}, "occupied": ["j4", ...]}
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .behavior_tree import validate
from .graph import Graph, edge_id


@dataclass
class SimParams:
    speed_mps: float = 0.12      # average including turns; calibrate from real episodes
    pick_s: float = 1.5
    drop_s: float = 1.5
    node_overhead_s: float = 0.4  # slow-down / settle at each node

    @classmethod
    def load(cls) -> "SimParams":
        """Calibrated values from mlops/calibrate.py (sim_params.json next to this file), else defaults."""
        f = Path(__file__).with_name("sim_params.json")
        if f.exists():
            data = json.loads(f.read_text())
            return cls(**{k.name: data[k.name] for k in fields(cls) if k.name in data})
        return cls()


@dataclass
class Outcome:
    success: bool
    cost: float
    distance_m: float = 0.0
    duration_s: float = 0.0
    wait_s: float = 0.0
    replans: int = 0
    stops: int = 0
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# Lower is better. Failures and safety stops dominate, then distance and waiting.
def score(success: bool, distance_m: float, wait_s: float, replans: int = 0, stops: int = 0,
          llm_latency_s: float = 0.0) -> float:
    return round(100 * (not success) + 50 * stops + 1.0 * distance_m + 0.5 * wait_s
                 + 2 * replans + 0.2 * llm_latency_s, 3)


def simulate(tree: dict, snapshot: dict, graph: Graph, params: SimParams | None = None) -> Outcome:
    p = params or SimParams.load()
    fid, at = snapshot.get("forklift", "fl-??"), snapshot.get("start")
    pallets = dict(snapshot.get("pallets", {}))
    errors = validate(tree, graph, pallets, start_node=at, forklift_id=fid)
    if errors:
        return Outcome(False, score(False, 0, 0), error=errors[0])

    occupied = set(snapshot.get("occupied", [])) - {at}
    walls = {edge_id(n, m) for n in occupied for m in graph.adj[n]}
    state = {"at": at, "dist": 0.0, "time": 0.0, "wait": 0.0}

    def leaf(n: dict) -> bool:
        t = n["type"]
        if t == "GoTo":
            if state["at"] == n["node"]:
                return True
            route = graph.astar(state["at"], n["node"], blocked_edges=walls)
            if route is None:  # only way is through someone: assume we wait for them to move
                route = graph.astar(state["at"], n["node"])
                state["wait"] += 5.0
            if route is None:
                return False
            d = graph.path_length(route)
            state["dist"] += d
            state["time"] += d / p.speed_mps + p.node_overhead_s * (len(route) - 1)
            state["at"] = n["node"]
            return True
        if t == "Pick":
            state["time"] += p.pick_s
            return True
        if t == "Drop":
            state["time"] += p.drop_s
            return True
        if t == "WaitUntil":
            return True
        return False

    def run(n: dict) -> bool:
        t = n["type"]
        if t == "Sequence":
            return all(run(c) for c in n["children"])
        if t == "Fallback":
            return any(run(c) for c in n["children"])
        if t == "Retry":
            return any(run(n["child"]) for _ in range(n["n"]))
        return leaf(n)

    ok = run(tree)
    duration = state["time"] + state["wait"]
    return Outcome(ok, score(ok, state["dist"], state["wait"]), round(state["dist"], 3),
                   round(duration, 2), round(state["wait"], 2))
