"""Behavior trees: the only thing the LLM is allowed to produce.

A tree is JSON built from fixed primitives (docs/API.md section 5). `validate()` replays the
tree against the warehouse state and returns a list of human-readable errors (empty = OK).
These errors are sent back to the LLM verbatim, so keep them short and specific.
"""

from __future__ import annotations

import copy
from typing import Any

from .graph import Graph

MAX_DEPTH = 5
MAX_NODES = 20

# type -> (required fields with their python type, has children?)
PRIMITIVES: dict[str, dict[str, type]] = {
    "Sequence": {"children": list},
    "Fallback": {"children": list},
    "GoTo": {"node": str},
    "Pick": {"pallet": str},
    "Drop": {"slot": str},
    "WaitUntil": {"clear": str, "timeout_s": (int, float)},
    "Retry": {"n": int, "child": dict},
}
LEAVES = {"GoTo", "Pick", "Drop", "WaitUntil"}
RUNTIME_FIELDS = {"status", "attempts"}  # added by the runner, ignored by the validator

PRIMITIVES_DOC = """\
Sequence{children:[...]}  run children in order; fails if one fails
Fallback{children:[...]}  try children in order until one succeeds
GoTo{node}                drive to a graph node
Pick{pallet}              pick up a pallet; the forklift must already be at the pallet's node
Drop{slot}                put the carried pallet down at the current node; slot must equal that node
WaitUntil{clear,timeout_s} wait until an edge ("a|b") or node is clear
Retry{n,child}            retry child up to n times (1..5)"""

# JSON schema for the LLM tool. Kept loose on purpose: validate() is the real check.
TREE_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string", "description": "One sentence: what the plan does."},
        "tree": {
            "type": "object",
            "description": "Behavior tree root. Every node is an object with a 'type' field "
                           "and that primitive's fields. Example: "
                           '{"type":"Sequence","children":[{"type":"GoTo","node":"slot_A3"},'
                           '{"type":"Pick","pallet":"pallet_3"},{"type":"GoTo","node":"dock_B"},'
                           '{"type":"Drop","slot":"dock_B"}]}',
        },
    },
    "required": ["reasoning", "tree"],
}


def _shape_errors(node: Any, path: str, depth: int, counter: list[int], errors: list[str]) -> None:
    counter[0] += 1
    if depth > MAX_DEPTH:
        errors.append(f"{path}: tree deeper than {MAX_DEPTH}")
        return
    if not isinstance(node, dict) or "type" not in node:
        errors.append(f"{path}: every node must be an object with a 'type'")
        return
    t = node["type"]
    spec = PRIMITIVES.get(t)
    if spec is None:
        errors.append(f"{path}: unknown type '{t}'. Allowed: {', '.join(PRIMITIVES)}")
        return
    extra = set(node) - set(spec) - {"type"} - RUNTIME_FIELDS
    if extra:
        errors.append(f"{path} {t}: unexpected fields {sorted(extra)}")
    for field, typ in spec.items():
        if field not in node:
            errors.append(f"{path} {t}: missing '{field}'")
        elif not isinstance(node[field], typ) or isinstance(node[field], bool):
            errors.append(f"{path} {t}: '{field}' has the wrong type")
    if t in ("Sequence", "Fallback") and isinstance(node.get("children"), list):
        if not node["children"]:
            errors.append(f"{path} {t}: children must not be empty")
        for i, c in enumerate(node["children"]):
            _shape_errors(c, f"{path}.{i}", depth + 1, counter, errors)
    if t == "Retry":
        if isinstance(node.get("n"), int) and not 1 <= node["n"] <= 5:
            errors.append(f"{path} Retry: n must be 1..5")
        if isinstance(node.get("child"), dict):
            _shape_errors(node["child"], f"{path}.child", depth + 1, counter, errors)


class _State:
    def __init__(self, at: str | None, pallets: dict[str, str], fid: str):
        self.at, self.pallets, self.fid = at, dict(pallets), fid

    @property
    def carrying(self) -> str | None:
        return next((p for p, n in self.pallets.items() if n == f"on:{self.fid}"), None)

    def copy(self) -> "_State":
        return _State(self.at, self.pallets, self.fid)


def _replay(node: dict, s: _State, g: Graph, path: str, errors: list[str]) -> _State:
    """Walk the tree in execution order (assuming success) and check each step makes sense."""
    t = node["type"]
    if t == "Sequence":
        for i, c in enumerate(node["children"]):
            s = _replay(c, s, g, f"{path}.{i}", errors)
        return s
    if t == "Fallback":
        # Each alternative starts from the same state; continue from the first one.
        outs = [_replay(c, s.copy(), g, f"{path}.{i}", errors) for i, c in enumerate(node["children"])]
        return outs[0]
    if t == "Retry":
        return _replay(node["child"], s, g, f"{path}.child", errors)
    if t == "GoTo":
        if node["node"] not in g.nodes:
            errors.append(f"{path} GoTo: unknown node '{node['node']}'")
        else:
            s.at = node["node"]
        return s
    if t == "WaitUntil":
        c = node["clear"]
        if c not in g.nodes and "|" not in c:
            errors.append(f"{path} WaitUntil: '{c}' is not a node or edge id")
        return s
    if t == "Pick":
        p = node["pallet"]
        if p not in s.pallets:
            errors.append(f"{path} Pick: unknown pallet '{p}'. Known: {sorted(s.pallets)}")
        elif s.carrying:
            errors.append(f"{path} Pick: already carrying {s.carrying}; Drop it first")
        elif s.pallets[p] != s.at:
            errors.append(f"{path} Pick: {p} is at {s.pallets[p]} but the forklift will be at {s.at}; add GoTo({s.pallets[p]}) first")
        else:
            s.pallets[p] = f"on:{s.fid}"
        return s
    if t == "Drop":
        slot = node["slot"]
        if slot not in g.nodes:
            errors.append(f"{path} Drop: unknown node '{slot}'")
        elif g.nodes[slot].type not in ("slot", "dock"):
            errors.append(f"{path} Drop: '{slot}' is a {g.nodes[slot].type}; drop only at slots or docks")
        elif s.at != slot:
            errors.append(f"{path} Drop: forklift will be at {s.at}, not {slot}; add GoTo({slot}) first")
        elif not s.carrying:
            errors.append(f"{path} Drop: not carrying a pallet")
        elif not (g.docks_ship and g.nodes[slot].type == "dock") and any(n == slot for n in s.pallets.values()):
            other = next(p for p, n in s.pallets.items() if n == slot)
            errors.append(f"{path} Drop: {slot} is occupied by {other}")
        else:
            # On a shipping map the truck takes it: the pallet leaves the floor and the dock is free.
            ship = g.docks_ship and g.nodes[slot].type == "dock"
            s.pallets[s.carrying] = f"shipped:{slot}" if ship else slot
        return s
    return s


def validate(tree: Any, graph: Graph, pallets: dict[str, str], start_node: str | None = None,
             forklift_id: str = "fl-??") -> list[str]:
    """Return a list of errors; empty means the tree is safe to run.

    pallets: pallet_id -> node id (or "on:<forklift>").
    start_node: where the forklift starts, if known (None = check positions only after a GoTo).
    """
    errors: list[str] = []
    counter = [0]
    _shape_errors(tree, "root", 1, counter, errors)
    if counter[0] > MAX_NODES:
        errors.append(f"tree has {counter[0]} nodes; max {MAX_NODES}")
    if errors:
        return errors
    end = _replay(tree, _State(start_node, pallets, forklift_id), graph, "root", errors)
    if not errors and end.carrying:
        errors.append(f"plan ends still carrying {end.carrying}; add a Drop")
    return errors


def first_target(tree: dict) -> str | None:
    """Node the forklift drives to first (for choosing the nearest forklift)."""
    t = tree["type"]
    if t == "GoTo":
        return tree["node"]
    if t in ("Sequence", "Fallback"):
        for c in tree["children"]:
            n = first_target(c)
            if n:
                return n
    if t == "Retry":
        return first_target(tree["child"])
    return None


def with_status(tree: dict) -> dict:
    """Deep copy with every node marked pending (what the runner and dashboard use)."""
    t = copy.deepcopy(tree)

    def mark(n: dict):
        n["status"] = "pending"
        for c in n.get("children", []):
            mark(c)
        if "child" in n:
            mark(n["child"])
        if n["type"] == "Retry":
            n["attempts"] = 0
    mark(t)
    return t
