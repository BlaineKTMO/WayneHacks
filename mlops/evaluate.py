"""Offline gate: run planners over every recorded episode and compare them in the simulator.

  uv run python -m mlops.evaluate                      # all non-retired planners vs prod
  uv run python -m mlops.evaluate v2-sonnet            # one candidate vs prod
  uv run python -m mlops.evaluate fallback-rules       # the rule planner (works without Bedrock)
  uv run python -m mlops.evaluate --dry-run            # don't write results to S3 / DynamoDB

Each episode becomes an eval case: (order text, warehouse snapshot). Every planner plans each
case from that exact snapshot; the simulator scores the result. A candidate PASSES the gate if:
  success rate >= prod, no more invalid plans than prod, and mean cost < prod.
Results go to s3://bucket/evalsets/<date>/cases.jsonl and s3://bucket/planners/<version>/scores.json,
and onto the planner's `eval` attribute in FleetPlanners (the dashboard planner lab reads it).
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from decimal import Decimal

import boto3

from mlops.common import REGION, bucket, load_episodes, put_json
from fleet_shared import rule_planner
from fleet_shared.graph import Graph
from fleet_shared.sim import simulate

GRAPH = Graph.load()


def cases_from(episodes: list[dict]) -> list[dict]:
    out = []
    for e in episodes:
        snap = e.get("snapshot")
        if not snap or not e.get("text"):
            continue
        out.append({"case_id": f"real-{e['task_id']}", "text": e["text"], "snapshot": snap, "source": "real",
                    "real_cost": (e.get("outcome") or {}).get("cost")})
    return out


def plan_case(planner: dict, case: dict) -> tuple[dict | None, str | None, float]:
    """(tree, error, latency_s) for one planner on one case."""
    snap = case["snapshot"]
    pallets = snap["pallets"]
    t0 = time.time()
    if planner["version"] == rule_planner.VERSION:
        tree = rule_planner.plan(case["text"], GRAPH, pallets)
        return tree, None if tree else "rule planner didn't understand", time.time() - t0
    import dispatch  # same code path as the live Lambda
    now = int(time.time() * 1000)
    forklifts = [{"forklift_id": snap["forklift"], "node": snap["start"], "state": "IDLE", "last_seen": now}]
    forklifts += [{"forklift_id": f"fl-x{i}", "node": n, "state": "IDLE", "current_task": "busy"}
                  for i, n in enumerate(snap.get("occupied", []))]
    try:
        tree, _ = dispatch.make_plan(planner, case["text"], pallets, forklifts)
        return tree, None, time.time() - t0
    except dispatch.PlanError as e:
        return None, str(e), time.time() - t0


def evaluate(planner: dict, cases: list[dict]) -> dict:
    rows = []
    for c in cases:
        try:
            tree, err, lat = plan_case(planner, c)
        except Exception as e:  # LLM unavailable etc.
            return {"version": planner["version"], "error": f"{type(e).__name__}: {e}"[:300]}
        out = simulate(tree, c["snapshot"], GRAPH) if tree else None
        rows.append({"case_id": c["case_id"], "valid": tree is not None, "error": err,
                     "success": bool(out and out.success), "cost": out.cost if out else 100.0,
                     "latency_s": round(lat, 2)})
        print(f"  {planner['version']:<16} {c['case_id']:<20} cost={rows[-1]['cost']:<8} {err or ''}")
    n = len(rows) or 1
    return {"version": planner["version"], "cases": len(rows),
            "success_rate": round(sum(r["success"] for r in rows) / n, 3),
            "invalid": sum(not r["valid"] for r in rows),
            "mean_cost": round(statistics.fmean(r["cost"] for r in rows), 3) if rows else None,
            "mean_latency_s": round(statistics.fmean(r["latency_s"] for r in rows), 2) if rows else None,
            "rows": rows}


def gate(cand: dict, prod: dict) -> tuple[bool, list[str]]:
    reasons = []
    if cand["success_rate"] < prod["success_rate"]:
        reasons.append(f"success {cand['success_rate']} < prod {prod['success_rate']}")
    if cand["invalid"] > prod["invalid"]:
        reasons.append(f"invalid plans {cand['invalid']} > prod {prod['invalid']}")
    if cand["mean_cost"] is not None and prod["mean_cost"] is not None and cand["mean_cost"] >= prod["mean_cost"]:
        reasons.append(f"mean cost {cand['mean_cost']} >= prod {prod['mean_cost']}")
    return not reasons, reasons


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("versions", nargs="*", help="planner versions to evaluate (default: all non-retired)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    bkt = bucket()
    episodes = load_episodes(bkt)
    cases = cases_from(episodes)
    print(f"{len(episodes)} episodes -> {len(cases)} eval cases")
    if not cases:
        raise SystemExit("No episodes yet: run some orders first (tools/sim_fleet.py + dashboard).")

    import dispatch
    table = boto3.resource("dynamodb", region_name=REGION).Table("FleetPlanners")
    registry = {p["version"]: {**dispatch.DEFAULT_PLANNER, **p} for p in table.scan()["Items"]}
    registry[rule_planner.VERSION] = {"version": rule_planner.VERSION, "stage": "baseline"}
    prod_v = next((v for v, p in registry.items() if p.get("stage") == "prod"), None)
    wanted = a.versions or [v for v, p in registry.items() if p.get("stage") in ("prod", "shadow")]
    if prod_v and prod_v not in wanted:
        wanted.insert(0, prod_v)

    results = {}
    for v in wanted:
        if v not in registry:
            raise SystemExit(f"unknown planner {v}; known: {sorted(registry)}")
        print(f"\n== {v}")
        results[v] = evaluate(registry[v], cases)

    day = time.strftime("%Y-%m-%d")
    print("\n" + "planner".ljust(18) + "cases  success  invalid  mean_cost  verdict")
    prod = results.get(prod_v)
    for v, r in results.items():
        if "error" in r:
            print(f"{v:<18}unavailable: {r['error']}")
            continue
        verdict = "PROD" if v == prod_v else ""
        if v != prod_v and prod and "error" not in prod:
            ok, why = gate(r, prod)
            r["gate"] = {"passed": ok, "reasons": why, "vs": prod_v}
            verdict = "PASS" if ok else "fail: " + "; ".join(why)
        print(f"{v:<18}{r['cases']:<7}{r['success_rate']:<9}{r['invalid']:<9}{r['mean_cost']:<11}{verdict}")
        if not a.dry_run:
            put_json(bkt, f"planners/{v}/scores.json", r)
            if v in registry and v != rule_planner.VERSION:
                summary = {k: r.get(k) for k in ("cases", "success_rate", "invalid", "mean_cost", "mean_latency_s")}
                summary["gate"] = r.get("gate")
                summary["evaluated_at"] = int(time.time() * 1000)
                table.update_item(Key={"version": v}, UpdateExpression="SET #e = :e",
                                  ExpressionAttributeNames={"#e": "eval"},
                                  ExpressionAttributeValues={":e": json.loads(json.dumps(summary), parse_float=Decimal)})
    if not a.dry_run:
        uri = put_json(bkt, f"evalsets/{day}/cases.json", cases)
        print(f"\neval set: {uri}")


if __name__ == "__main__":
    main()
