"""Fit the simulator's speed and per-node overhead to real episodes (the sim-to-real check).

  uv run python -m mlops.calibrate            # fit, print the before/after error, write sim_params.json
  uv run python -m mlops.calibrate --dry-run

Model, per successful episode:  real_duration ≈ distance / speed + nodes * overhead + actions * action_s
Fit by least squares over (1/speed, overhead) with action time held fixed. Writes
shared/fleet_shared/sim_params.json, which the Lambdas bundle on the next `cdk deploy`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mlops.common import ROOT, bucket, load_episodes
from fleet_shared.graph import Graph
from fleet_shared.sim import SimParams, simulate

GRAPH = Graph.load()
OUT = ROOT / "shared" / "fleet_shared" / "sim_params.json"


def features(ep: dict) -> tuple[float, int, int] | None:
    """(distance_m, nodes_traversed, pick+drop count) of the plan from its snapshot."""
    snap, tree = ep.get("snapshot"), ep.get("tree")
    if not snap or not tree:
        return None
    at, dist, nodes, acts = snap["start"], 0.0, 0, 0

    def walk(n):
        nonlocal at, dist, nodes, acts
        t = n["type"]
        if t in ("Sequence", "Fallback"):
            for c in n["children"]:
                walk(c)
        elif t == "Retry":
            walk(n["child"])
        elif t == "GoTo" and n["node"] != at:
            r = GRAPH.astar(at, n["node"]) or [at]
            dist += GRAPH.path_length(r)
            nodes += len(r) - 1
            at = n["node"]
        elif t in ("Pick", "Drop"):
            acts += 1
    walk(tree)
    return dist, nodes, acts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    eps = [e for e in load_episodes(bucket()) if (e.get("outcome") or {}).get("success")]
    data = [(features(e), e["outcome"]["duration_s"], e) for e in eps]
    data = [(f, d, e) for f, d, e in data if f and f[0] > 0]
    print(f"{len(data)} successful episodes with movement")
    if len(data) < 2:
        raise SystemExit("Need at least 2 successful episodes to calibrate. Run more orders first.")

    base = SimParams()
    # Least squares for y = a*dist + b*nodes, where y = duration - actions*action_s.
    sxx = sxy = syy = sx = sy = 0.0
    for (dist, nodes, acts), dur, _ in data:
        y = dur - acts * base.pick_s
        sxx += dist * dist; sxy += dist * nodes; syy += nodes * nodes
        sx += dist * y; sy += nodes * y
    det = sxx * syy - sxy * sxy
    if abs(det) < 1e-9:
        raise SystemExit("Episodes are too similar to separate speed from per-node time; vary the orders.")
    inv_speed = (sx * syy - sy * sxy) / det
    overhead = (sxx * sy - sxy * sx) / det
    if inv_speed <= 0:  # degenerate fit: put everything into speed
        inv_speed = sum(d - acts * base.pick_s for (_, _, acts), d, _ in data) / sum(f[0] for f, _, _ in data)
        overhead = 0.0
    fitted = SimParams(speed_mps=round(1 / inv_speed, 4), pick_s=base.pick_s, drop_s=base.drop_s,
                       node_overhead_s=round(max(0.0, overhead), 3))

    def err(p):
        return sum(abs(simulate(e["tree"], e["snapshot"], GRAPH, p).duration_s - d) for _, d, e in data) / len(data)
    current = SimParams.load()
    print(f"current {current}: mean |sim - real| = {err(current):.2f}s")
    print(f"fitted  {fitted}: mean |sim - real| = {err(fitted):.2f}s")
    if not a.dry_run:
        OUT.write_text(json.dumps(fitted.__dict__, indent=2) + "\n")
        print(f"wrote {OUT.relative_to(ROOT)}; redeploy the cloud so the Lambdas use it")


if __name__ == "__main__":
    main()
