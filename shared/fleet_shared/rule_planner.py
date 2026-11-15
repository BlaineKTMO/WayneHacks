"""Rule-based backup planner: used only when Bedrock is unreachable, so the demo never dies.

Understands the preset-style orders:
  "move pallet 3 to dock B" / "bring pallet 1 to dock A" / "take pallet_2 to slot A4"
  "swap pallets 1 and 2"
  "send fl-01 to j3" / "go to slot A2"
Returns a behavior tree (same format as the LLM) or None if it doesn't understand.
"""

from __future__ import annotations

import re

from .graph import Graph

VERSION = "fallback-rules"


def _node(text: str, g: Graph) -> str | None:
    """'dock B' / 'dock_b' / 'slot A3' / 'j3' -> node id."""
    key = re.sub(r"[\s-]+", "_", text.strip().lower())
    for nid in g.nodes:
        if nid.lower() == key:
            return nid
    return None


def _move(p: str, dest: str, src: str) -> list[dict]:
    return [{"type": "GoTo", "node": src}, {"type": "Pick", "pallet": p},
            {"type": "GoTo", "node": dest}, {"type": "Drop", "slot": dest}]


def plan(text: str, g: Graph, pallets: dict[str, str]) -> dict | None:
    t = text.lower().strip().rstrip(".")
    loc = r"((?:dock|slot)[\s_-]*[a-z]\d*|j\d+)"

    m = re.search(rf"(?:move|bring|take|put|carry)\s+pallet[\s_]*(\d+)\s+(?:to|into|onto|at)\s+(?:the\s+)?{loc}", t)
    if m:
        p, dest = f"pallet_{m.group(1)}", _node(m.group(2), g)
        if p in pallets and dest:
            return {"type": "Sequence", "children": _move(p, dest, pallets[p])}
        return None

    m = re.search(r"swap\s+pallets?\s*(\d+)\s+(?:and|with|&)\s+(?:pallet\s*)?(\d+)", t)
    if m:
        a, b = f"pallet_{m.group(1)}", f"pallet_{m.group(2)}"
        if a not in pallets or b not in pallets:
            return None
        na, nb = pallets[a], pallets[b]
        # Park A at a free slot/dock, move B to A's spot, then A to B's old spot.
        used = set(pallets.values())
        free = next((n.id for n in g.nodes.values() if n.type in ("slot", "dock") and n.id not in used), None)
        if not free:
            return None
        return {"type": "Sequence", "children": _move(a, free, na) + _move(b, na, nb) + _move(a, nb, free)}

    m = re.search(rf"(?:go|drive|send\s+fl-\d+)\s+(?:to\s+)?(?:the\s+)?{loc}", t)
    if m and _node(m.group(1), g):
        return {"type": "Sequence", "children": [{"type": "GoTo", "node": _node(m.group(1), g)}]}
    return None
