"""Warehouse graph + A* route planning. Used by the cloud planner, the simulator and tests."""

from __future__ import annotations

import heapq
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Node:
    id: str
    x: float
    y: float
    type: str


def edge_id(a: str, b: str) -> str:
    """Undirected edge key, same for a->b and b->a. Used as the EdgeLocks table key."""
    return "|".join(sorted((a, b)))


class Graph:
    def __init__(self, data: dict):
        self.data = data
        self.nodes: dict[str, Node] = {n["id"]: Node(n["id"], n["x"], n["y"], n["type"]) for n in data["nodes"]}
        self.adj: dict[str, set[str]] = {nid: set() for nid in self.nodes}
        for e in data["edges"]:
            a, b = e["from"], e["to"]
            if a not in self.nodes or b not in self.nodes:
                raise ValueError(f"edge {a}->{b} references unknown node")
            self.adj[a].add(b)
            if e.get("bidirectional", True):
                self.adj[b].add(a)
        self.pallets: dict[str, str] = {p["id"]: p["node"] for p in data.get("pallets", [])}

    # ---- loading ---------------------------------------------------------
    @classmethod
    def load(cls, path: str | os.PathLike | None = None) -> "Graph":
        if path is None:
            path = os.environ.get("WAREHOUSE_JSON")
        candidates = [Path(path)] if path else [
            Path(__file__).parent / "warehouse.json",          # bundled copy (Lambda)
            Path(__file__).parent.parent / "warehouse.json",   # repo: shared/warehouse.json
        ]
        for p in candidates:
            if p.exists():
                return cls(json.loads(p.read_text()))
        raise FileNotFoundError(f"warehouse.json not found in {candidates}")

    # ---- geometry --------------------------------------------------------
    def dist(self, a: str, b: str) -> float:
        na, nb = self.nodes[a], self.nodes[b]
        return math.hypot(na.x - nb.x, na.y - nb.y)

    def nearest_node(self, x: float, y: float) -> str:
        return min(self.nodes.values(), key=lambda n: math.hypot(n.x - x, n.y - y)).id

    def path_length(self, path: list[str]) -> float:
        return sum(self.dist(a, b) for a, b in zip(path, path[1:], strict=False))

    # ---- planning --------------------------------------------------------
    def astar(
        self,
        start: str,
        goal: str,
        blocked_edges: Iterable[str] = (),
        edge_penalty: dict[str, float] | None = None,
    ) -> list[str] | None:
        """Shortest node path start->goal, or None if unreachable.

        blocked_edges: edge_ids removed entirely (obstacles).
        edge_penalty:  edge_id -> extra cost in metres (e.g. reserved by another forklift).
        """
        for n in (start, goal):
            if n not in self.nodes:
                raise KeyError(f"unknown node {n}")
        blocked = set(blocked_edges)
        penalty = edge_penalty or {}
        open_heap = [(self.dist(start, goal), 0.0, start)]
        came: dict[str, str] = {}
        g = {start: 0.0}
        while open_heap:
            _, cost, cur = heapq.heappop(open_heap)
            if cur == goal:
                out = [cur]
                while cur in came:
                    cur = came[cur]
                    out.append(cur)
                return out[::-1]
            if cost > g.get(cur, math.inf):
                continue
            for nxt in self.adj[cur]:
                eid = edge_id(cur, nxt)
                if eid in blocked:
                    continue
                # Slots and docks are destinations, never shortcuts: don't drive through them.
                if nxt != goal and self.nodes[nxt].type != "junction":
                    continue
                ng = cost + self.dist(cur, nxt) + penalty.get(eid, 0.0)
                if ng < g.get(nxt, math.inf):
                    g[nxt] = ng
                    came[nxt] = cur
                    heapq.heappush(open_heap, (ng + self.dist(nxt, goal), ng, nxt))
        return None

    def to_path_nodes(self, path: list[str]) -> list[dict]:
        return [{"id": n, "x": self.nodes[n].x, "y": self.nodes[n].y} for n in path]
