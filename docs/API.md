# Forklift fleet: interface reference

Every boundary in the system, in one place. If you build a component, build it against this page. If you change an interface, change this page, the code in `shared/`, and the tests in the same commit, and tell the other lead.

**Status labels**

| Label | Meaning |
|---|---|
| ✅ Live | Deployed or runnable today, covered by tests |
| 🧱 Scaffolded | Code exists, not yet proven on hardware or in AWS |
| 📐 Planned | Agreed design for a later ring. Build to it, but expect small changes |

**Conventions (all interfaces)**

- Units: metres, radians, seconds. Angles in −π..π, counter-clockwise from +x.
- World frame: origin at corner tag 0, x right, y up, fixed by the 4 corner AprilTags.
- `ts`: epoch **milliseconds** (integer). Exception: DynamoDB TTL attributes are epoch **seconds** (an AWS requirement).
- Forklift IDs: `fl-01`, `fl-02` (regex `^fl-\d{2}$`).
- Node IDs: whatever `shared/warehouse.json` defines (`dock_A`, `j1`, `slot_A3`, …).
- Edge IDs: the two node IDs sorted alphabetically and joined with `|`, e.g. `j1|j3`. The same edge has the same ID in both directions.
- Task IDs: `t-xxxxxxxx` for orders from `tasks/new`, `goto-xxxxxxxx` for direct goto commands.
- AWS Region: `us-east-2`.

**Contents**

1. [System map](#1-system-map)
2. [MQTT topics (IoT Core)](#2-mqtt-topics-iot-core)
3. [Serial protocol: laptop ↔ Free-WiLi](#3-serial-protocol-laptop--free-wili)
4. [Warehouse graph file](#4-warehouse-graph-file)
5. [Behavior trees](#5-behavior-trees)
6. [DynamoDB tables](#6-dynamodb-tables)
7. [S3 layout](#7-s3-layout)
8. [Lambda functions](#8-lambda-functions)
9. [Dashboard API](#9-dashboard-api)
10. [Edge components (Greengrass)](#10-edge-components-greengrass)
11. [Python libraries](#11-python-libraries)
12. [MLOps: simulator and planner pipeline](#12-mlops-simulator-and-planner-pipeline)
13. [Security and identities](#13-security-and-identities)
14. [Changing an interface](#14-changing-an-interface)

---

## 1. System map

```
 Free-WiLi ──serial §3──► Edge laptop ──MQTT §2──► IoT Core ──rules──► Lambdas §8 ──► DynamoDB §6
 (motors)                 (camera, tags,                │                    │          S3 §7
                           pure pursuit,                │                    └──► Bedrock §5
                           safety guard §10)            │
                                                        └──WebSocket──► Dashboard §9 ◄──HTTP── API §9
```

Everything meets at IoT Core. The dashboard reads live data from MQTT and stored state from the HTTP API.

---

## 2. MQTT topics (IoT Core)

Endpoint: `a2xi7375e47ilj-ats.iot.us-east-2.amazonaws.com`, port 8883 (devices, mTLS) or 443 (dashboard, WebSocket).
Payloads are UTF-8 JSON objects. Publish with QoS 1 unless noted. Build topics and payloads with `fleet_shared.topics` and `fleet_shared.messages`; never by hand.

**Strict parsing:** `Message.from_dict()` rejects missing required fields **and unknown extra fields**. Don't add fields to a payload without adding them to `messages.py` first.

### Topic summary

| Topic | Direction | Rate | Status |
|---|---|---|---|
| [`fleet/{id}/pose`](#fleetidpose) | edge → cloud, edge → forklift | 10 Hz | ✅ |
| [`fleet/{id}/telemetry`](#fleetidtelemetry) | edge (for forklift) → cloud | 1 Hz | ✅ |
| [`fleet/{id}/event`](#fleetidevent) | edge (for forklift) → cloud | on change | ✅ |
| [`fleet/{id}/goto`](#fleetidgoto) | dashboard → cloud | on demand | ✅ |
| [`fleet/{id}/path`](#fleetidpath) | cloud → edge/forklift | on change | ✅ |
| [`fleet/{id}/stop`](#fleetidstop) | edge or cloud → forklift | on demand | 📐 |
| [`warehouse/detections`](#warehousedetections) | edge → cloud | 2 Hz | 📐 |
| [`tasks/new`](#tasksnew) | dashboard → cloud | on demand | ✅ (records only) |
| [`tasks/{task_id}/status`](#taskstask_idstatus) | cloud → dashboard | on change | ✅ |
| [`tasks/{task_id}/shadow`](#taskstask_idshadow) | cloud → dashboard | on change | 📐 |
| [`tasks/{task_id}/outcome`](#taskstask_idoutcome) | cloud → dashboard | on finish | 📐 |

### `fleet/{id}/pose`

Where a forklift is, from the ceiling camera. The edge is the only publisher.

| Field | Type | Req | Notes |
|---|---|---|---|
| `x` | number | ✓ | metres |
| `y` | number | ✓ | metres |
| `theta` | number | ✓ | radians, forklift's forward direction |
| `conf` | number | | 0..1, default `1.0`. Lower it when the tag is partly hidden or the filter is coasting |
| `ts` | integer | | epoch ms, defaults to now |

```json
{"x": 0.412, "y": 0.205, "theta": 1.571, "conf": 0.98, "ts": 1794685929894}
```

Cloud handling: rule `IngestPoseRule` keeps only messages with `ts % 1000 < 100` (about 1 Hz at a 10 Hz publish rate) and writes them to `FleetForklifts.pose`. The dashboard reads full-rate pose straight from MQTT.

### `fleet/{id}/telemetry`

Forklift health, relayed by the edge from the Free-WiLi `T` lines.

| Field | Type | Req | Notes |
|---|---|---|---|
| `battery_v` | number | ✓ | volts |
| `motor_a` | number | ✓ | motor current, amps (send `0` if not measured) |
| `state` | string | ✓ | `IDLE` \| `DRIVING` \| `BLOCKED` \| `STOPPED` \| `FAULT` |
| `node` | string \| null | | last graph node reached |
| `ts` | integer | | epoch ms |

```json
{"battery_v": 7.4, "motor_a": 0.3, "state": "DRIVING", "node": "j1", "ts": 1794685929894}
```

Cloud handling: updates `FleetForklifts` and is archived to S3 `raw/telemetry/`.

**States**

| State | Meaning |
|---|---|
| `IDLE` | No path, motors off |
| `DRIVING` | Following a path |
| `BLOCKED` | Waiting for an edge lock (Ring 4) |
| `STOPPED` | Stopped by safety: lost tag, watchdog, person or obstacle, or a `stop` message |
| `FAULT` | Hardware problem; needs a human |

### `fleet/{id}/event`

Discrete things that happened to a forklift.

| Field | Type | Req | Notes |
|---|---|---|---|
| `type` | string | ✓ | `node_reached` \| `done` \| `blocked` \| `fault` |
| `node` | string \| null | | the node concerned |
| `ts` | integer | | epoch ms |

```json
{"type": "node_reached", "node": "j3", "ts": 1794685931000}
```

| Type | Sent when | Cloud reaction |
|---|---|---|
| `node_reached` | Within `ARRIVE_RADIUS_M` (3 cm) of a path node | Updates `node`. Ring 4: releases the lock behind and takes the next one |
| `done` | Final node reached and the path's `action` finished | Clears `current_task`. Ring 3: ticks the behavior tree |
| `blocked` | Lock denied, waiting | Ring 4: replans after 5 s |
| `fault` | Hardware fault | Task fails; dashboard alert |

Cloud handling: updates `FleetForklifts` and is archived to S3 `raw/events/`.

### `fleet/{id}/goto`

"Drive this forklift to this node." This is the Ring 2 click-a-node command, and what a behavior tree `GoTo` turns into.

| Field | Type | Req | Notes |
|---|---|---|---|
| `node` | string | ✓ | target node ID |
| `task_id` | string \| null | | set by the tree runner; if omitted the planner makes `goto-xxxxxxxx` |

```json
{"node": "slot_A3"}
```

Cloud handling (`RoutePlanner` Lambda):

1. Start node = `FleetForklifts.node`, or the node nearest to the last pose if that's missing.
2. A* to `node`.
3. Writes a `FleetTasks` row (`status: running`), sets `current_task` on the forklift.
4. Publishes `fleet/{id}/path` and `tasks/{task_id}/status` with `running`.

Failures (unknown node, no position yet, no route) publish `tasks/{task_id}/status` with `state: failed` and an `error`.

### `fleet/{id}/path`

The route a forklift should drive. A new path **replaces** the current one.

| Field | Type | Req | Notes |
|---|---|---|---|
| `task_id` | string | ✓ | |
| `nodes` | array | ✓ | ordered `{id, x, y}`, starting with the forklift's current node |
| `action` | string \| null | | `pick` \| `drop` \| null, done at the last node |

```json
{"task_id": "goto-916e206c",
 "nodes": [{"id": "j1", "x": 0.4, "y": 0.2}, {"id": "j3", "x": 0.9, "y": 0.2}, {"id": "slot_A3", "x": 1.35, "y": 0.2}],
 "action": null}
```

An empty `nodes` list means "cancel and stop where you are" (📐).

### `fleet/{id}/stop` 📐

Stop immediately. This overrides any path. To resume, send a new `path`.

| Field | Type | Req | Notes |
|---|---|---|---|
| `reason` | string | ✓ | `person` \| `obstacle` \| `operator` \| `lost_tag` \| free text |

```json
{"reason": "person"}
```

The edge `safety-guard` sends this **locally first** and then publishes it so the cloud and dashboard know. Stopping never waits on the cloud.

### `warehouse/detections` 📐

Things without tags that RF-DETR sees.

| Field | Type | Req | Notes |
|---|---|---|---|
| `model_version` | string | ✓ | e.g. `rfdetr-nano-coco` |
| `objects` | array | ✓ | `{cls, x, y, conf}`: class name, world metres, 0..1 |
| `ts` | integer | | epoch ms |

```json
{"model_version": "rfdetr-nano-coco", "objects": [{"cls": "person", "x": 0.62, "y": 0.41, "conf": 0.91}], "ts": 1794685931200}
```

Cloud handling (Ring 4): edges within 5 cm of an object are blocked for planning until the object clears.

### `tasks/new`

A plain-English order from the dashboard.

| Field | Type | Req |
|---|---|---|
| `text` | string | ✓ |

```json
{"text": "move pallet 3 to dock B"}
```

Today, `Dispatch` stores the task and replies `received`. Ring 3 adds the Bedrock → validate → assign flow (§8).

**Correlation gap:** the dashboard doesn't know the `task_id` until the first status message arrives. Two ways to close it, pick one in Ring 3:

- (a) the dashboard subscribes to `tasks/+/status` and matches on `text`; or
- (b) add an optional `client_ref` field to `tasks/new` that is echoed back in the status. This is a contract change.

### `tasks/{task_id}/status`

The lifecycle of a task.

| Field | Type | Req | Notes |
|---|---|---|---|
| `state` | string | ✓ | see table |
| `tree` | object \| null | | behavior tree (§5), once planned |
| `assigned_to` | string \| null | | forklift ID |
| `planner_version` | string \| null | | which planner wrote the tree |
| `error` | string \| null | | set when `state` is `failed` |

| State | Status | Meaning |
|---|---|---|
| `received` | ✅ | Stored, not yet planned |
| `planning` | 📐 | Bedrock is writing the tree |
| `running` | ✅ | Executing |
| `done` | 📐 | Finished successfully |
| `failed` | ✅ | See `error` |

📐 Ring 3 adds per-node tree status: `tree` nodes gain a `status` field (`pending` \| `running` \| `success` \| `failure`) so the dashboard can colour the tree live.

### `tasks/{task_id}/shadow` 📐

A candidate planner's tree for the same command, validated and simulated but never run.

```json
{"planner_version": "v2-fewshot", "tree": {"type": "Sequence", "children": []}, "sim_cost": 3.42, "valid": true}
```

### `tasks/{task_id}/outcome` 📐

The real result of a finished task. It is also written to S3 as part of the episode.

| Field | Type | Notes |
|---|---|---|
| `success` | boolean | |
| `cost` | number | weighted outcome score (§12), lower is better |
| `distance_m` | number | total distance driven |
| `wait_s` | number | time spent `BLOCKED` |
| `replans` | integer | |
| `stops` | integer | safety stops |
| `duration_s` | number | |

---

## 3. Serial protocol: laptop ↔ Free-WiLi

USB serial at 115200 baud, 8N1, ASCII. One message per line, ending in `\n` (`\r\n` is tolerated). Fields are comma-separated with no spaces. Code: `edge/fleet_edge/freewili_link.py`.

### Ring 1: laptop drives the wheels 🧱

| Direction | Line | Meaning | Rate |
|---|---|---|---|
| laptop → FW | `W,<left>,<right>` | wheel speed, integers −100..100. Sign = direction | 20 Hz |
| laptop → FW | `S,<reason>` | stop now. Reason has no commas | on demand |
| FW → laptop | `T,<state>,<battery_v>` | state as in §2, volts with 2 decimals | 1 Hz |

```
> W,45,52
> W,44,53
< T,DRIVING,7.41
> S,lost_tag
< T,STOPPED,7.40
```

**What the Free-WiLi must do:**

- **Watchdog:** if no `W` arrives for **500 ms**, turn the motors off and set state to `STOPPED`.
- `S` overrides any `W` until the next `W` arrives.
- **Unknown lines:** ignore them and don't crash. Optionally reply `E,bad_line`.
- Clamp values outside −100..100.

### Ring 1b: controller on the Free-WiLi 📐

After the loop works, pure pursuit moves onto the Free-WiLi. The laptop then sends where the forklift is and where to go, and the Free-WiLi does the steering.

| Direction | Line | Meaning |
|---|---|---|
| laptop → FW | `P,<x>,<y>,<theta>` | pose in metres/radians, 3 decimals, at 10–20 Hz. Also feeds the watchdog |
| laptop → FW | `R,<task_id>,<action>,<n>,<id1>,<x1>,<y1>,…` | replace the route: `n` nodes. `action` is `pick`, `drop` or `-` |
| laptop → FW | `S,<reason>` | stop now |
| FW → laptop | `T,<state>,<battery_v>,<motor_a>` | telemetry, 1 Hz |
| FW → laptop | `E,<type>,<node>` | event: `node_reached`, `done`, `blocked`, `fault` |

The edge `fleet-bridge` turns `fleet/{id}/path` into `R` and `E` back into `fleet/{id}/event`. Keep lines under 256 bytes. A route longer than about 8 nodes needs splitting into several `R` messages, so the planner should avoid producing routes that long.

---

## 4. Warehouse graph file ✅

`shared/warehouse.json` is the single source for the map. The cloud planner, edge, dashboard and simulator all load it. It **must match the printed floor map**.

```json
{
  "size":   {"width": 1.5, "height": 1.0},
  "nodes":  [{"id": "dock_A", "x": 0.10, "y": 0.20, "type": "dock"}],
  "edges":  [{"from": "dock_A", "to": "j1", "bidirectional": true}],
  "pallets":[{"id": "pallet_1", "node": "slot_A1"}]
}
```

| Field | Rules |
|---|---|
| `nodes[].id` | unique. Prefix by type: `dock_`, `j`, `slot_` |
| `nodes[].type` | `dock` \| `junction` \| `slot` |
| `nodes[].x`, `y` | metres, inside `size` |
| `edges[]` | must reference existing nodes. `bidirectional` defaults to true |
| `pallets[]` | **initial** pallet positions only; live positions are in `FleetPallets` |

After editing, run `uv run pytest shared` (it checks the references) and `uv run python cloud/scripts/seed.py`, then redeploy the cloud so the Lambdas get the new copy.

---

## 5. Behavior trees 📐

What Bedrock produces and the tree runner executes. JSON, with every node an object that has a `type`.

| `type` | Fields | Meaning | Succeeds when |
|---|---|---|---|
| `Sequence` | `children: [node]` | run in order | all children succeed |
| `Fallback` | `children: [node]` | try in order | any child succeeds |
| `GoTo` | `node: string` | plan and drive to a node | forklift reports `done` |
| `Pick` | `pallet: string` | lower forks, approach, lift. Forklift must be at the pallet's node | lift complete |
| `Drop` | `slot: string` | lower forks, back out. Forklift must be at `slot` | backed out |
| `WaitUntil` | `clear: string` (edge ID or node ID), `timeout_s: number` | wait for it to be free | clear before the timeout |
| `Retry` | `n: integer (1..5)`, `child: node` | retry a failing child | the child succeeds within `n` tries |

Example: "move pallet 3 to dock B".

```json
{"type": "Sequence", "children": [
  {"type": "GoTo", "node": "slot_A3"},
  {"type": "Pick", "pallet": "pallet_3"},
  {"type": "Retry", "n": 2, "child": {"type": "GoTo", "node": "dock_B"}},
  {"type": "Drop", "slot": "dock_B"}
]}
```

**Validator rules.** A tree is rejected unless all of these hold:

1. It matches the schema: known `type`s only, required fields present, no extra fields.
2. Every `node`, `slot` and `clear` exists in the graph, and every `pallet` exists in `FleetPallets`.
3. A `Pick(p)` comes after a `GoTo` to the node where `p` currently is. A `Drop(s)` comes after a `GoTo(s)`.
4. The forklift holds at most one pallet: `Pick` and `Drop` alternate.
5. Depth ≤ 5 and ≤ 20 nodes in total.

**Bedrock contract.** Use the Converse API with one tool, `submit_tree(tree)`, whose input schema is the table above, and force the tool choice. The prompt gets:

- the primitive list;
- node IDs only (no coordinates);
- current pallet → node mapping;
- free forklifts.

If validation fails, send the errors back once. If it fails again, the task fails.

**Execution mapping.** The runner turns each leaf into one cloud action:

| Leaf | Becomes |
|---|---|
| `GoTo(n)` | `fleet/{id}/goto {node: n, task_id}` |
| `Pick` / `Drop` | `path` with a single node and `action: pick` or `drop` |

---

## 6. DynamoDB tables

All tables are on-demand capacity with a string partition key and no sort key. Floats are stored as `Number`; Python code converts them with `common.to_ddb` / `common.from_ddb`.

### `FleetForklifts` ✅ (key `forklift_id`)

| Attribute | Type | Written by | Notes |
|---|---|---|---|
| `forklift_id` | S | seed | `fl-01` |
| `state` | S | Ingest (telemetry) | §2 states |
| `node` | S | Ingest, seed | last node reached |
| `pose` | M `{x,y,theta,conf}` | Ingest (sampled pose) | |
| `battery_v`, `motor_a` | N | Ingest | |
| `last_event` | S | Ingest | |
| `current_task` | S \| NULL | RoutePlanner, Ingest | cleared on `done` |
| `last_seen` | N | Ingest | epoch ms. The dashboard shows "offline" after 5 s |

### `FleetTasks` ✅ (key `task_id`)

| Attribute | Type | Notes |
|---|---|---|
| `task_id` | S | |
| `text` | S | the order, or `goto <node>` |
| `status` | S | §2 task states |
| `assigned_to` | S | |
| `path` | M | last published path |
| `tree` | M | 📐 behavior tree with per-node status |
| `planner_version` | S | 📐 |
| `created_at`, `finished_at` | N | epoch ms |
| `error` | S | |

### `FleetEdgeLocks` 🧱 table exists, 📐 logic (key `edge_id`, TTL `expires_at`)

| Attribute | Type | Notes |
|---|---|---|
| `edge_id` | S | `j1\|j3` |
| `holder` | S | forklift ID |
| `task_id` | S | |
| `expires_at` | N | **epoch seconds**, now + 30 s, renewed on each `node_reached` |

To take a lock, do a conditional `PutItem`:

```
ConditionExpression: attribute_not_exists(edge_id) OR holder = :me OR expires_at < :now
```

A `ConditionalCheckFailedException` means the lock is held by someone else: set the forklift to `BLOCKED`. Release a lock by deleting it with the condition `holder = :me`.

DynamoDB's TTL can take up to about 48 hours to remove expired items, so **always check `expires_at` yourself** rather than relying on deletion.

### `FleetPallets` ✅ (key `pallet_id`)

| Attribute | Type | Notes |
|---|---|---|
| `pallet_id` | S | `pallet_3` |
| `node` | S | where it is now. `on:fl-01` while carried (📐) |
| `last_seen` | N | 📐 |

### `FleetEpisodes` 📐 (key `task_id`)

| Attribute | Type | Notes |
|---|---|---|
| `task_id` | S | |
| `planner_version` | S | |
| `snapshot_uri` | S | `s3://…/episodes/{date}/{task_id}.json` |
| `cost` | N | outcome score |
| `sim_cost` | N | simulator prediction, for the sim-to-real check |
| `success` | BOOL | |
| `created_at` | N | |

### `FleetPlanners` 📐 (key `version`)

| Attribute | Type | Notes |
|---|---|---|
| `version` | S | `v1-naive`, `v2-fewshot`, … |
| `stage` | S | `prod` \| `shadow` \| `retired`. Exactly one `prod` |
| `model_id` | S | Bedrock model ID, or `sagemaker:<endpoint>` |
| `prompt_uri` | S | `s3://…/planners/{version}/prompt.md` |
| `fewshot_uri` | S | optional |
| `eval` | M | `{success_rate, mean_cost, reject_rate, safety_violations}` |
| `shadow_wins`, `shadow_runs` | N | live head-to-head counts |
| `promoted_at` | N | |

---

## 7. S3 layout

Bucket: `forkliftfleet-datalakee54831b2-2c5xhrj854ls`. This is a CloudFormation output, so read it from there rather than hard-coding it. Public access is blocked and requests must use TLS.

| Prefix | Status | Written by | Key format |
|---|---|---|---|
| `raw/telemetry/` | ✅ | IoT rule | `raw/telemetry/{yyyy-MM-dd}/{forklift_id}/{ts}-{uuid}.json` |
| `raw/events/` | ✅ | IoT rule | `raw/events/{yyyy-MM-dd}/{forklift_id}/{ts}-{uuid}.json` |
| `raw/frames/` | 📐 | edge `uploader` | `raw/frames/{yyyy-MM-dd}/{camera}/{ts}.jpg` |
| `episodes/` | 📐 | episode logger + Dispatch | `episodes/{yyyy-MM-dd}/{task_id}.json` |
| `evalsets/` | 📐 | MLOps | `evalsets/{version}/cases.jsonl` |
| `planners/` | 📐 | MLOps | `planners/{version}/prompt.md`, `fewshot.jsonl`, `scores.json`, `weights/` |
| `shadow/` | 📐 | Dispatch | `shadow/{task_id}/{planner_version}.json` |

Archived raw objects are the original payload plus `forklift_id`.

**Episode file (📐):**

```json
{"task_id": "t-0ee52341", "text": "move pallet 3 to dock B", "planner_version": "v1-naive",
 "snapshot": {"forklifts": {"fl-01": {"node": "j1", "state": "IDLE"}}, "pallets": {"pallet_3": "slot_A3"}, "locks": {}},
 "tree": {"type": "Sequence", "children": []},
 "outcome": {"success": true, "cost": 2.8, "distance_m": 1.9, "wait_s": 0, "replans": 0, "stops": 0, "duration_s": 14.2}}
```

---

## 8. Lambda functions

All three Lambdas share one code bundle (`cloud/lambdas` + `fleet_shared` + `warehouse.json`). They run Python 3.12 on arm64 with 256 MB, and their logs are kept for 1 week.

**Environment** (set on all three): `FORKLIFTS_TABLE`, `TASKS_TABLE`, `EDGE_LOCKS_TABLE`, `PALLETS_TABLE`, `PLANNERS_TABLE`, `DATA_BUCKET`, `IOT_ENDPOINT`.

IoT rules call the Lambdas asynchronously. The event is the MQTT payload plus the fields the rule SQL adds.

### `Ingest` ✅ (handler `ingest.handler`)

| Rule | SQL | Event |
|---|---|---|
| `IngestTelemetryRule` | `SELECT *, topic(2) AS forklift_id, 'telemetry' AS kind FROM 'fleet/+/telemetry'` | Telemetry + `forklift_id`, `kind` |
| `IngestEventRule` | `… 'event' AS kind FROM 'fleet/+/event'` | Event + extras |
| `IngestPoseRule` | `… 'pose' AS kind FROM 'fleet/+/pose' WHERE ts % 1000 < 100` | Pose + extras |

It merges the message into `FleetForklifts` with `UpdateItem`, so it never overwrites the whole item. Invalid messages are logged and dropped.

### `RoutePlanner` ✅ (handler `route_planner.handler`, timeout 10 s)

| Rule | SQL |
|---|---|
| `GotoRule` | `SELECT *, topic(2) AS forklift_id FROM 'fleet/+/goto'` |

Behaviour is described under [`fleet/{id}/goto`](#fleetidgoto). It can publish to `fleet/*` and `tasks/*`.

📐 Ring 4 adds:

- an `EdgeLocks` penalty of +1.0 m for each edge another forklift holds;
- obstacle-blocked edges removed from the graph;
- lock acquisition before the path is published.

### `Dispatch` ✅ stub → 📐 Ring 3 (handler `dispatch.handler`, timeout 60 s)

| Rule | SQL |
|---|---|
| `TaskNewRule` | `SELECT * FROM 'tasks/new'` |

- **Today:** writes `FleetTasks {status: received}` and publishes status.
- **Ring 3:**
  1. Load the `prod` planner (and any `shadow` planners).
  2. Snapshot the warehouse.
  3. Call Bedrock with the `submit_tree` tool.
  4. Validate the tree (§5), retrying once if it fails.
  5. Assign the nearest free forklift.
  6. Store the tree and start the runner.
  7. Run the shadow planners in the background and publish `tasks/{id}/shadow`.

The function is already allowed `bedrock:InvokeModel` and `bedrock:Converse`.

### `TreeRunner` 📐

This function ticks behavior trees. It is triggered by `fleet/+/event` (`done`, `fault`) through its own rule, or replaced by Step Functions if trees get long. It must be idempotent, because IoT delivers to Lambda at least once. Ignore a `done` for a leaf that is already marked `success`.

### `Promote` 📐

Called by `POST /planners/{version}/promote`. In one `TransactWriteItems`, it sets the current `prod` planner to `retired` and the new version to `prod`.

---

## 9. Dashboard API

The dashboard uses two channels: **MQTT over WebSocket** for anything live, and an **HTTP API** for stored state and actions that need checks.

### Auth 📐

- **Sign-in:** a Cognito user pool with team members only; self sign-up is disabled.
- **AWS credentials:** a Cognito identity pool gives signed-in users temporary credentials. The authenticated role allows:
  - `iot:Connect` with client ID `dash-*`;
  - `iot:Subscribe`/`Receive` on `fleet/*`, `warehouse/*`, `tasks/*`;
  - `iot:Publish` only on `tasks/new` and `fleet/*/goto`.
- **HTTP API:** API Gateway HTTP API with a Cognito JWT authorizer.

### MQTT (browser) 📐

| Subscribe | For |
|---|---|
| `fleet/+/pose` | moving icons on the map |
| `fleet/+/telemetry`, `fleet/+/event` | forklift cards |
| `warehouse/detections` | red obstacle dots |
| `tasks/+/status`, `tasks/+/shadow`, `tasks/+/outcome` | command panel and planner panel |

| Publish | For |
|---|---|
| `tasks/new` | command box |
| `fleet/{id}/goto` | clicking a node with a forklift selected |

### HTTP API 📐

Base URL: a CloudFormation output (`DashboardApiUrl`). Every request needs `Authorization: Bearer <id token>`. Bodies are JSON.

| Method & path | Returns / does |
|---|---|
| `GET /graph` | contents of `warehouse.json` |
| `GET /forklifts` | `[{forklift_id, state, node, pose, battery_v, current_task, last_seen}]` |
| `GET /pallets` | `[{pallet_id, node}]` |
| `GET /locks` | `[{edge_id, holder, expires_at}]`, unexpired only |
| `GET /tasks?limit=20` | newest first: `[{task_id, text, status, assigned_to, created_at}]` |
| `GET /tasks/{task_id}` | full task including `tree` and `path` |
| `GET /planners` | `[{version, stage, model_id, eval, shadow_wins, shadow_runs}]` |
| `GET /planners/compare?limit=20` | per recent task: `{task_id, text, prod: {version, cost}, candidate: {version, sim_cost}}` |
| `POST /planners/{version}/promote` | `200 {version, previous}`. `409` if the version isn't `shadow` |
| `POST /forklifts/{id}/stop` | publishes `fleet/{id}/stop {reason: "operator"}`. `202` |

**Errors** look like `{"error": "<code>", "message": "<human text>"}`:

| Status | Code |
|---|---|
| `400` | `bad_request` |
| `401` | `unauthorized` (handled by API Gateway) |
| `404` | `not_found` |
| `409` | `conflict` |
| `500` | `internal` |

---

## 10. Edge components (Greengrass)

Ring 1 runs the edge as plain scripts: 🧱 `python -m fleet_edge.step6_ring1`. From Ring 2 it becomes Greengrass v2 components on the laptop. They talk to each other over **Greengrass local pub/sub (IPC)** using the same topic names and payloads as §2, so moving a message from local to cloud is only a routing change.

| Component | Subscribes (local) | Publishes | Notes |
|---|---|---|---|
| `com.fleet.localizer` 📐 | — | `fleet/{id}/pose` (local + cloud) | camera → tags → filter, 15–30 Hz |
| `com.fleet.detector` 📐 | — | `warehouse/detections` (local + cloud) | RF-DETR on the GPU |
| `com.fleet.safety-guard` 📐 | pose, detections, `fleet/{id}/path` | `fleet/{id}/stop` (local first, then cloud) | Must work offline. Stops when a person or obstacle is within 10 cm of the forklift's next 2 path edges, or when the tag is lost for more than 300 ms |
| `com.fleet.fleet-bridge` 📐 | `fleet/{id}/path`, `fleet/{id}/stop`, `fleet/{id}/pose` | `fleet/{id}/telemetry`, `fleet/{id}/event` | Owns the serial port (§3). One per Free-WiLi |
| `com.fleet.episode-logger` 📐 | everything for a task | S3 `episodes/` (through the uploader) | |
| `com.fleet.uploader` 📐 | — | S3 `raw/frames/` | samples 1 frame every 5 s |

Shared config for every component comes from the Greengrass deployment as JSON: `{forklift_tags, corner_tags, serial_ports, camera_index}`. This replaces `config.py` on the laptop once Greengrass is in use.

Cloud connection: thing `forklift-edge-01`, certificate from `cloud/scripts/create_edge_cert.py`, policy `ForkliftEdgePolicy` (§13). Use client ID `edge-01`.

---

## 11. Python libraries

### `fleet_shared` ✅ (standard library only; used by edge, cloud and tests)

**`fleet_shared.topics`**

| Function | Returns |
|---|---|
| `pose(fid)`, `telemetry(fid)`, `path(fid)`, `event(fid)`, `stop(fid)`, `goto(fid)` | `fleet/{fid}/…`. Raises `ValueError` on a bad ID |
| `task_status(tid)`, `task_shadow(tid)`, `task_outcome(tid)` | `tasks/{tid}/…` |
| `DETECTIONS`, `TASKS_NEW` | constants |
| `forklift_from_topic(topic)` | `"fleet/fl-01/pose"` → `"fl-01"` |

**`fleet_shared.messages`**

Message classes: `Pose`, `Telemetry`, `Event`, `Path`, `PathNode`, `Goto`, `Stop`, `Detections`, `TaskNew`, `TaskStatus`. Each has:

| Method | Does |
|---|---|
| `.to_dict()` / `.to_json()` | serialize. JSON is compact |
| `Cls.from_dict(d)` / `Cls.from_json(s)` | parse and validate. Raises `ValueError` on missing or extra fields or bad enum values |

Also: `now_ms()`, `FORKLIFT_STATES`, `EVENT_TYPES`.

**`fleet_shared.graph`**

| Member | Does |
|---|---|
| `Graph.load(path=None)` | loads `$WAREHOUSE_JSON`, the bundled copy, or `shared/warehouse.json` |
| `.nodes: dict[str, Node]`, `.adj`, `.pallets` | parsed graph |
| `.astar(start, goal, blocked_edges=(), edge_penalty=None) -> list[str] \| None` | shortest route. `edge_penalty` maps edge ID → extra metres |
| `.nearest_node(x, y)`, `.dist(a, b)`, `.path_length(route)` | geometry |
| `.to_path_nodes(route)` | `[{id, x, y}]`, ready for `Path.nodes` |
| `edge_id(a, b)` | `"j1\|j3"` |

### `fleet_edge` 🧱 (edge laptop)

| Module | Public API |
|---|---|
| `config` | all tunables; see the comments in the file |
| `freewili_link` | `SerialLink()` / `SimLink()` with `.wheels(l, r)`, `.stop(reason)`, `.poll() -> FreeWiliTelemetry \| None`, `.close()`. Helpers: `format_wheels`, `format_stop`, `parse_line` |
| `localizer` | `Camera().read()`; `TagDetector().detect(frame) -> [TagSighting]`; `Localizer().process(sightings) -> {fid: ForkliftPose}` (needs all 4 corner tags once, then keeps the last homography) |
| `pure_pursuit` | `WaypointFollower(waypoints, loop=True)` with `.update(x, y, theta) -> (left, right)`, `.index`, `.laps`, `.finished`, `.target` |
| `overlay` | `WorldCanvas`, `draw(img, canvas, pose, follower, status, ok)` |
| `step1_…step6_` | runnable scripts (`python -m fleet_edge.stepN_…`) |

### `tools/fake_fleet.py` ✅

A command-line tool that publishes contract messages using your AWS login:

```
telemetry <fid> [--state S] [--node N]
pose <fid> <x> <y> [--theta T]
goto <fid> <node>
task "<text>"
status <fid>
```

📐 Planned: `replay <file>` (replay a recorded MQTT session) and `sim` (fake forklifts that follow `path` messages, so the dashboard can be built with no hardware).

---

## 12. MLOps: simulator and planner pipeline 📐

**Simulator** (`mlops/sim.py`):

```python
simulate(tree: dict, snapshot: dict, graph: Graph, params: SimParams) -> Outcome
```

- Same A* and edge locks as the cloud, with constant-speed kinematics.
- `SimParams`: `{speed_mps, pick_s, drop_s, lock_wait_s}`, calibrated from real episodes.
- `Outcome` has the same fields as `tasks/{id}/outcome`.
- Deterministic for a given input.

**Cost function** (lower is better; the weights are a starting point):

```
cost = 100*(not success) + 50*stops + 30*deadlock + 1.0*distance_m + 0.5*wait_s + 2*replans + 0.2*llm_latency_s
```

**Eval case** (one line of `evalsets/{v}/cases.jsonl`):

```json
{"case_id": "real-t-0ee52341", "text": "move pallet 3 to dock B", "snapshot": {}, "source": "real"}
```

`source` is `real` or `synthetic`.

**Planner gate.** A candidate passes if all of these hold:

- success rate ≥ prod's;
- no new safety violations;
- mean cost < prod's;
- validator reject rate ≤ prod's.

**Promotion** happens after N = 5 live shadow tasks with candidate cost ≤ prod cost, via `POST /planners/{v}/promote` or automatically.

---

## 13. Security and identities

| Identity | Can do | Status |
|---|---|---|
| Edge laptop: cert on thing `forklift-edge-01`, policy `ForkliftEdgePolicy` | Connect as `edge-*`. Publish `fleet/*`, `warehouse/*`. Subscribe and receive `fleet/*` | ✅ |
| Lambdas (one role each) | Least-privilege table access through CDK `grant_*`. Publish `fleet/*`, `tasks/*`. Dispatch: Bedrock invoke | ✅ |
| IoT rule role | Write `raw/*` in the bucket, write the error log group | ✅ |
| Dashboard user (Cognito) | See §9 Auth | 📐 |
| Team members (AWS console) | Managed by AWS Settings; no IAM changes needed | ✅ |

Rules:

- **Device keys** live only in `edge/certs/`, which is git-ignored. Move them by USB or AirDrop. If a key leaks, deactivate the certificate in IoT Core and run `create_edge_cert.py` again.
- **The LLM never sees coordinates and never publishes to `fleet/*`.** Only validated trees reach the runner, and only the runner publishes `goto` and `path`.
- **Stopping always works offline.** Safety-guard and the Free-WiLi watchdog don't depend on the cloud.

---

## 14. Changing an interface

1. Edit `shared/fleet_shared/` (topics or messages) **and** this file.
2. Add or adjust a test in `shared/tests/`, then run `uv run pytest`.
3. If the cloud is affected, run `cd cloud && npx aws-cdk@2 diff`, then `deploy`.
4. Tell the other lead. The serial protocol (§3) needs a matching Free-WiLi firmware change.
5. Never rename a topic. Add a new one and retire the old one after both sides have moved.
