"""IoT rule target for tasks/new: plain-English order -> behavior tree -> forklift.

1. Load the prod planner (model + prompt) from FleetPlanners (falls back to DEFAULT_PLANNER).
2. Ask Claude on Bedrock (Converse + forced tool use) for a tree. It only sees node IDs,
   pallet locations and forklift locations: never coordinates, never motors.
3. validate(); on errors send them back once. Still invalid -> task failed.
4. Assign the nearest free forklift and hand the tree to tree_runner.
"""

from __future__ import annotations

import json
import logging
import time
import uuid

import boto3
from botocore.config import Config

import tree_runner
from common import from_ddb, publish, table, to_ddb
from fleet_shared import topics
from fleet_shared.behavior_tree import PRIMITIVES_DOC, TREE_TOOL_SCHEMA, first_target, validate, with_status
from fleet_shared.graph import Graph
from fleet_shared import rule_planner
from fleet_shared.messages import TaskNew, TaskStatus, now_ms
from fleet_shared.sim import score, simulate

log = logging.getLogger()
log.setLevel(logging.INFO)
GRAPH = Graph.load()
bedrock = boto3.client("bedrock-runtime", config=Config(retries={"max_attempts": 4, "mode": "adaptive"},
                                                          read_timeout=40))
ONLINE_MS = 30_000

DEFAULT_PLANNER = {
    "version": "v1-sonnet",
    "model_id": "us.anthropic.claude-sonnet-4-6",
    "prompt": """You are the task planner for a warehouse forklift fleet.
Turn the operator's order into a behavior tree using ONLY these primitives:
{primitives}

Rules:
- To move a pallet: GoTo(pallet's node), Pick(pallet), GoTo(destination), Drop(destination).
- "Go to X" / "send a forklift to X" is valid: a Sequence with a single GoTo(X).
- Drop only at slot or dock nodes, and only on an empty one.
- A forklift carries one pallet at a time and must end empty-handed.
- "pallet 3" means pallet_3, "dock B" means dock_B, "slot A2" means slot_A2.
- Keep plans minimal. Don't add Retry/Fallback/WaitUntil unless the order asks for robustness.
- If the order is impossible, ambiguous, or unrelated to moving pallets or forklifts, call reject.

Warehouse nodes (id: type): {nodes}
""",
}

TOOLS = {"tools": [
    {"toolSpec": {"name": "submit_tree", "description": "Submit the behavior tree for this order.",
                  "inputSchema": {"json": TREE_TOOL_SCHEMA}}},
    {"toolSpec": {"name": "reject", "description": "Refuse an order that can't be done safely or is unclear.",
                  "inputSchema": {"json": {"type": "object", "properties": {"reason": {"type": "string"}},
                                           "required": ["reason"]}}}},
], "toolChoice": {"any": {}}}


def load_planners() -> tuple[dict, list[dict]]:
    """(prod planner, shadow planners) from FleetPlanners; DEFAULT_PLANNER if none is prod."""
    items = from_ddb(table("PLANNERS_TABLE").scan().get("Items", []))
    prod = [p for p in items if p.get("stage") == "prod"]
    shadows = [{**DEFAULT_PLANNER, **p} for p in items if p.get("stage") == "shadow"]
    return ({**DEFAULT_PLANNER, **prod[0]} if prod else DEFAULT_PLANNER), shadows


def snapshot() -> tuple[dict[str, str], list[dict]]:
    pallets = {p["pallet_id"]: p["node"] for p in from_ddb(table("PALLETS_TABLE").scan()["Items"])}
    forklifts = from_ddb(table("FORKLIFTS_TABLE").scan()["Items"])
    return pallets, forklifts


def ask_llm(planner: dict, text: str, pallets: dict, forklifts: list[dict], messages: list) -> dict:
    nodes = ", ".join(f"{n.id}: {n.type}" for n in GRAPH.nodes.values())
    system = planner["prompt"].format(primitives=PRIMITIVES_DOC, nodes=nodes)
    if not messages:
        fl = ", ".join(f"{f['forklift_id']} at {f.get('node')} ({'busy' if f.get('current_task') else 'free'})"
                       for f in forklifts)
        messages.append({"role": "user", "content": [{"text":
            f"Pallets: {json.dumps(pallets)}\nForklifts: {fl}\n\nOrder: {text}"}]})
    resp = bedrock.converse(modelId=planner["model_id"], system=[{"text": system}], messages=messages,
                            toolConfig=TOOLS, inferenceConfig={"maxTokens": 1500, "temperature": 0})
    msg = resp["output"]["message"]
    messages.append(msg)
    use = next(b["toolUse"] for b in msg["content"] if "toolUse" in b)
    log.info("LLM %s -> %s %s", planner["model_id"], use["name"], json.dumps(use["input"])[:500])
    return use


def pick_forklift(forklifts: list[dict], target: str | None) -> dict | None:
    now = now_ms()
    # Available = the cloud has nothing running on it. Don't trust the 1 Hz motion state (it lags
    # by up to a second after "done"); a STOPPED forklift with no task is fine: its next path restarts it.
    free = [f for f in forklifts if not f.get("current_task") and not f.get("route")
            and f.get("state") != "FAULT" and f.get("node") in GRAPH.nodes]
    online = [f for f in free if now - int(f.get("last_seen", 0)) < ONLINE_MS]
    pool = online or free
    if not pool:
        return None
    if not target:
        return pool[0]

    def cost(f):
        route = GRAPH.astar(f["node"], target)
        return GRAPH.path_length(route) if route else float("inf")
    return min(pool, key=cost)


class PlanError(Exception):
    pass


def make_plan(planner: dict, text: str, pallets: dict, forklifts: list[dict]) -> tuple[dict, dict | None]:
    """Ask one planner for a valid tree (one repair round). Returns (tree, chosen forklift).
    Raises PlanError for a rejected/invalid plan; other exceptions mean the LLM is unavailable."""
    messages: list = []
    errors = ["no attempt"]
    tree, fl = None, None
    for _ in range(2):
        use = ask_llm(planner, text, pallets, forklifts, messages)
        if use["name"] == "reject":
            raise PlanError(f"Planner declined: {use['input'].get('reason', 'no reason')}")
        tree = use["input"].get("tree")
        target = first_target(tree) if isinstance(tree, dict) and "type" in tree else None
        fl = pick_forklift(forklifts, target)
        errors = validate(tree, GRAPH, pallets, start_node=fl["node"] if fl else None,
                          forklift_id=fl["forklift_id"] if fl else "fl-??")
        if not errors:
            return tree, fl
        messages.append({"role": "user", "content": [{"toolResult": {
            "toolUseId": use["toolUseId"], "status": "error",
            "content": [{"text": "Invalid tree:\n- " + "\n- ".join(errors) + "\nFix it and call submit_tree again."}]}}]})
    raise PlanError("Invalid plan: " + "; ".join(errors[:3]))


def sim_snapshot(fl: dict, pallets: dict, forklifts: list[dict]) -> dict:
    return {"forklift": fl["forklift_id"], "start": fl["node"], "pallets": pallets,
            "occupied": [f["node"] for f in forklifts if f["forklift_id"] != fl["forklift_id"] and f.get("node")]}


def run_shadows(task: dict, shadows: list[dict], text: str, pallets: dict, forklifts: list[dict],
                prod_cost: float, snap: dict) -> list[dict]:
    """Every shadow planner plans the same order from the same snapshot. Simulated, never executed."""
    results = []
    for sp in shadows:
        t0 = time.time()
        try:
            tree, _ = make_plan(sp, text, pallets, forklifts)
            out = simulate(tree, snap, GRAPH)
            r = {"planner_version": sp["version"], "tree": tree, "sim_cost": out.cost, "valid": True}
        except PlanError as e:
            r = {"planner_version": sp["version"], "tree": None, "sim_cost": score(False, 0, 0), "valid": False,
                 "error": str(e)}
        except Exception as e:  # LLM unavailable: skip this candidate, don't count it
            log.warning("shadow %s unavailable: %s", sp["version"], e)
            continue
        r["latency_ms"] = int((time.time() - t0) * 1000)
        r["prod_sim_cost"] = prod_cost
        r["win"] = r["valid"] and r["sim_cost"] <= prod_cost
        results.append(r)
        publish(topics.task_shadow(task["task_id"]), r)
        table("PLANNERS_TABLE").update_item(
            Key={"version": sp["version"]},
            UpdateExpression="ADD shadow_runs :one, shadow_wins :w, shadow_cost_sum :c",
            ExpressionAttributeValues=to_ddb({":one": 1, ":w": 1 if r["win"] else 0, ":c": r["sim_cost"]}))
    return results


def status(task_id: str, **kw) -> None:
    publish(topics.task_status(task_id), TaskStatus(**kw).to_dict())


def handler(msg, _ctx):
    order = TaskNew.from_dict(msg)
    task_id = f"t-{uuid.uuid4().hex[:8]}"
    planner, shadows = load_planners()
    task = {"task_id": task_id, "text": order.text, "status": "planning",
            "planner_version": planner["version"], "created_at": now_ms()}
    table("TASKS_TABLE").put_item(Item=to_ddb(task))
    status(task_id, state="planning", text=order.text, planner_version=planner["version"])

    def fail(error: str):
        table("TASKS_TABLE").update_item(
            Key={"task_id": task_id}, UpdateExpression="SET #s = :s, #e = :e, finished_at = :t",
            ExpressionAttributeNames={"#s": "status", "#e": "error"},
            ExpressionAttributeValues={":s": "failed", ":e": error, ":t": now_ms()})
        status(task_id, state="failed", text=order.text, error=error, planner_version=planner["version"])
        log.warning("task %s failed: %s", task_id, error)

    pallets, forklifts = snapshot()
    t0 = time.time()
    llm_ok = True
    try:
        tree, fl = make_plan(planner, order.text, pallets, forklifts)
    except PlanError as e:
        return fail(str(e))
    except Exception as e:  # Bedrock outage, throttling, not enabled: fall back to rules
        log.exception("LLM planner error; trying rule-based fallback")
        llm_ok = False
        tree = rule_planner.plan(order.text, GRAPH, pallets)
        if tree is None:
            return fail(f"AI planner unavailable ({type(e).__name__}) and the backup planner didn't understand the order")
        planner = {**planner, "version": rule_planner.VERSION}
        task["planner_version"] = planner["version"]
        fl = pick_forklift(forklifts, first_target(tree))
        errors = validate(tree, GRAPH, pallets, start_node=fl["node"] if fl else None,
                          forklift_id=fl["forklift_id"] if fl else "fl-??")
        if errors:
            return fail("Invalid plan: " + "; ".join(errors[:3]))

    if fl is None:
        return fail("No free forklift available")

    latency_ms = int((time.time() - t0) * 1000)
    snap = sim_snapshot(fl, pallets, forklifts)
    predicted = simulate(tree, snap, GRAPH)
    run_tree = with_status(tree)
    task.update({"status": "running", "assigned_to": fl["forklift_id"], "tree": run_tree,
                 "llm_latency_ms": latency_ms, "snapshot": snap, "sim": predicted.to_dict(),
                 "started_at": now_ms()})
    table("TASKS_TABLE").put_item(Item=to_ddb(task))
    table("FORKLIFTS_TABLE").update_item(Key={"forklift_id": fl["forklift_id"]},
                                         UpdateExpression="SET current_task = :t",
                                         ExpressionAttributeValues={":t": task_id})
    tree_runner.start(task, fl["forklift_id"], run_tree)

    # Shadow candidates plan the same order while the forklift is already driving.
    if llm_ok and shadows:
        results = run_shadows(task, shadows, order.text, pallets, forklifts, predicted.cost, snap)
        if results:
            table("TASKS_TABLE").update_item(Key={"task_id": task_id}, UpdateExpression="SET shadow = :s",
                                             ExpressionAttributeValues={":s": to_ddb(results)})
    return {"task_id": task_id}
