# Autonomous forklift fleet: system architecture

Nov 14, 2026 · @Blaine

## Overview

The pitch: **the warehouse is the robot.** Fixed ceiling cameras localize every forklift, one shared model learns from the whole fleet, and the forklifts just follow graph routes.

The demo: type "move pallet 3 to dock B" on the dashboard, watch the LLM produce a behavior tree, then watch two toy forklifts drive it without colliding. A live map shows positions and telemetry, and a planner panel shows candidate plans being scored in shadow and promoted.

The tracks:

- **AWS track:** IoT Core, Greengrass, Lambda, Bedrock, DynamoDB, S3, SageMaker and Amplify, built in Kiro.
- **Free-WiLi track:** the Free-WiLi is the forklift controller. It drives the motors, follows waypoints and streams telemetry.

Design rule: anything real-time (seeing, steering, stopping) runs at the edge. The cloud plans, stores and learns, so a slow network never moves a forklift.

```
Warehouse edge (keeps running if Wi-Fi drops)
  Ceiling cameras ──► Edge laptop (Greengrass) ◄──► Free-WiLi controller
  (webcams + tags)    (localizer, RF-DETR,          (pure pursuit, telemetry,
                       local safety stop)            watchdog)
                              ▲
                              │ MQTT
                              ▼
AWS cloud
  IoT Core ◄──► Lambda + Step Functions ◄──► Bedrock (Claude)
     │          (validate tree, A* plan,      (order → behavior tree,
     │           edge locks, tree runner)      fixed primitives only)
     ▼                    │
     S3 ──► SageMaker     ▼
  (frames,  (simulate,   DynamoDB ──► Amplify dashboard
  episodes, score,       (Forklifts, Tasks, EdgeLocks,   (live map, commands,
  eval sets, fine-tune)   Pallets, Episodes, Planners)    planners)
  planners)
```

Everything meets at IoT Core. Commands flow down through Lambda and Bedrock, data flows into S3 and DynamoDB, and SageMaker scores candidate planners against real outcomes and promotes the winner.

## Build rings

The project is built from the inside out: each ring wraps the one before it and is a complete demo by itself.

```
┌─ Ring 5 · Self-improving planner ─────────────────────────────────────────┐
│  Episodes, simulator, eval set, shadow candidates, promote                │
│ ┌─ Ring 4 · Fleet ──────────────────────────────────────────────────────┐ │
│ │  Second forklift, edge locks, RF-DETR people and obstacle stops       │ │
│ │ ┌─ Ring 3 · LLM tasks ──────────────────────────────────────────────┐ │ │
│ │ │  Bedrock writes behavior trees from plain English; validator;     │ │ │
│ │ │  Pick and Drop                                                    │ │ │
│ │ │ ┌─ Ring 2 · Graph navigation + cloud ───────────────────────────┐ │ │ │
│ │ │ │  Warehouse graph, A* routes, IoT Core, live map; click a node │ │ │ │
│ │ │ │ ┌─ Ring 1 · One forklift drives itself ─────────────────────┐ │ │ │ │
│ │ │ │ │  Ceiling camera + AprilTags give the pose                 │ │ │ │ │
│ │ │ │ │  Free-WiLi follows a waypoint loop, stops on a lost tag   │ │ │ │ │
│ │ │ │ │  Runs fully offline on the edge laptop                    │ │ │ │ │
│ │ │ │ └───────────────────────────────────────────────────────────┘ │ │ │ │
│ │ │ └───────────────────────────────────────────────────────────────┘ │ │ │
│ │ └───────────────────────────────────────────────────────────────────┘ │ │
│ └───────────────────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────────────────┘
```

Only start a ring once the one inside it passes its done test. The sections after Ring 1 describe the outer rings in full, for later.

## Ring 1: one forklift drives itself

Done when one forklift drives a 4-waypoint loop three times in a row on its own, guided only by the ceiling camera and AprilTags, and stops within 0.5 s when its tag is covered.

**In scope:** one webcam, 4 corner tags + 1 forklift tag, the edge laptop, the Free-WiLi, a motor driver and one chassis.

**Out of scope (later rings):** AWS, the warehouse graph, the LLM, RF-DETR and the second forklift. Ring 1 runs fully offline.

**Data path:**

1. Camera → laptop: detect tags, map pixels to metres through the corner-tag homography, filter the pose (x, y, heading) at 15–30 Hz.
2. Laptop: pure-pursuit controller picks the next waypoint and computes left/right wheel speeds.
3. Laptop → Free-WiLi over USB serial: one short text line per command.
4. Free-WiLi: drives PWM to the motor driver, runs the watchdog, shows its state on the display, reports back.
5. Laptop: an OpenCV overlay window draws the tag, heading arrow, path and current waypoint. That window is the demo.

**Serial protocol (newline-terminated text):**

| Direction | Line | Meaning |
| --- | --- | --- |
| laptop → Free-WiLi | `W,<left>,<right>` | Wheel speeds, -100 to 100, sent at 20 Hz |
| laptop → Free-WiLi | `S,<reason>` | Stop now |
| Free-WiLi → laptop | `T,<state>,<battery_v>` | Telemetry at 1 Hz |

Start with the controller on the laptop because it's easier to debug. Once the loop works, move pure pursuit onto the Free-WiLi (the laptop then sends pose + waypoints instead of wheel speeds) to strengthen the Free-WiLi track story.

**Checklist:**

- [ ] Mount the camera rigidly, looking straight down; print the tags (tag36h11, about 6 cm)
- [ ] Calibrate the camera with a chessboard and save the intrinsics
- [ ] Corner tags → homography; measure that pixel-to-metre error is under 1 cm
- [ ] Forklift pose + filter running at 15 Hz or more, logged to a file
- [ ] Free-WiLi drives both motors from `W` lines; measure each motor's deadband (minimum PWM that moves it)
- [ ] Watchdog: no line for 500 ms → motors off
- [ ] Pure pursuit around the 4-waypoint loop, 3 cm arrival radius
- [ ] Overlay window with tag, heading, path and current waypoint
- [ ] Lost-tag test: cover the tag, forklift stops
- [ ] Three clean laps in a row; record a backup video

**Watch for:** camera-to-motor latency (aim for under 100 ms end to end), heading jitter at low speed (take heading from the tag itself, not from motion) and motors that stall below their deadband.

## Physical setup

A tabletop warehouse, about 1.5 x 1 m, with two toy forklifts and one or two webcams on a pole or the ceiling looking straight down.

| Item | Qty | Notes |
| --- | --- | --- |
| RC car or 2-motor robot chassis | 2 | One is the Free-WiLi-driven "hero"; the second can run on a cheap ESP32 to show multi-robot |
| Motor driver (e.g. TB6612 or L298N) | 2 | Driven from Free-WiLi GPIO/PWM |
| Free-WiLi | 1+ | Confirm which model the track provides |
| USB webcam, 1080p | 1–2 | Fixed mount, looking down; tape the mount so it never moves |
| AprilTags (tag36h11) | ~8 | One on each forklift's roof, 4 at the table corners as fixed anchors |
| Printed floor map | 1 | Aisles, pallet slots and docks, matching the graph JSON |
| Small "pallets" | 3–4 | Foam blocks, each with its own tag |
| Edge laptop | 1 | NVIDIA GPU; runs RF-DETR, tag localization and Greengrass |

The corner tags define the world frame, so a bumped camera can recalibrate itself in a few seconds.

## Edge node

The edge laptop turns camera frames into forklift poses at 15–30 Hz and is the only bridge between the warehouse and AWS.

**Localization pipeline (per frame):**

1. Grab the frame and undistort it using the calibration from a one-time OpenCV chessboard run.
2. Detect AprilTags. The corner tags give a homography from image pixels to table metres.
3. Map each forklift tag to (x, y, heading) in metres and smooth it with a simple Kalman or alpha-beta filter.
4. Run RF-DETR (Nano or Small, COCO-pretrained, on the GPU) for things without tags: people (a hand in the scene), unknown obstacles, dropped pallets.
5. Publish poses and detections over MQTT.

Tags give exact ground truth for forklift positions, so the detector only handles people and obstacles. The pretrained model already covers people, so no training is needed for the demo.

**Greengrass components:**

| Component | Job |
| --- | --- |
| `localizer` | Camera capture, tags, homography, pose filter |
| `detector` | RF-DETR on the GPU, publishes obstacles |
| `episode-logger` | Records each task's real outcome (time, distance, waits, stops) for the planner loop |
| `safety-guard` | Sends an immediate stop if a person or obstacle enters a forklift's path, without waiting for the cloud |
| `uploader` | Samples frames and logs to S3 |
| `fleet-bridge` | Relays waypoints between IoT Core and the Free-WiLi |

The safety guard is local on purpose: stopping must still work if the Wi-Fi drops.

## Free-WiLi controller

The Free-WiLi runs the forklift's low-level loop: it gets waypoints plus its live pose, steers toward the next waypoint and reports back.

**Control loop (~20 Hz):**

1. Receive `pose` (from the cameras, relayed by the edge) and the active `path` (list of graph nodes as x, y).
2. Pure-pursuit steering: pick a look-ahead point on the path and set the left/right PWM to turn toward it.
3. On arrival at a node (within ~3 cm), report `node_reached`. On the final node, run the action (Pick/Drop = raise or lower the fork servo).
4. Publish telemetry: battery voltage, motor current, current node, state.

**Safety:**

- Watchdog: no pose or heartbeat for 500 ms → motors off.
- A `stop` message from `safety-guard` overrides everything.
- The on-device display shows the forklift ID and its state (IDLE, DRIVING, BLOCKED, STOPPED), which reads well on camera.

**Connection:** the simplest path is USB serial to the edge laptop, using the Free-WiLi's Python API. If your unit has Wi-Fi (the FREE-WILi 2 has an ESP32), it can speak MQTT directly. Treat that as a stretch goal and keep USB as the fallback.

Open question: which Free-WiLi model the track provides, and whether its PWM/GPIO headers can drive the motor driver directly.

## Messaging contract

All components talk over AWS IoT Core MQTT using the topics and JSON below. Write this spec first: everything else codes against it.

| Topic | Direction | Rate | Payload |
| --- | --- | --- | --- |
| `fleet/{id}/pose` | edge → cloud, edge → forklift | 10 Hz | `{ts, x, y, theta, conf}` |
| `fleet/{id}/telemetry` | forklift → cloud | 1 Hz | `{ts, battery_v, motor_a, state, node}` |
| `fleet/{id}/path` | cloud → forklift | on change | `{task_id, nodes:[{id,x,y}], action}` |
| `fleet/{id}/event` | forklift → cloud | on change | `{ts, type: node_reached\|done\|blocked\|fault, node}` |
| `fleet/{id}/stop` | edge or cloud → forklift | on demand | `{reason}` |
| `warehouse/detections` | edge → cloud | 2 Hz | `{ts, model_version, objects:[{cls,x,y,conf}]}` |
| `tasks/new` | dashboard → cloud | on demand | `{text}` |
| `tasks/{task_id}/status` | cloud → dashboard | on change | `{state, tree, assigned_to, planner_version}` |
| `tasks/{task_id}/shadow` | cloud → dashboard | on change | `{planner_version, tree, sim_cost}` |
| `tasks/{task_id}/outcome` | cloud → dashboard, S3 | on finish | `{success, cost, distance_m, wait_s, replans, stops}` |

Conventions: metres and radians, world frame from the corner tags, `ts` in epoch milliseconds, forklift IDs `fl-01`, `fl-02`. Use IoT Core rules to fan telemetry and detections out to DynamoDB and S3 without extra code.

## Warehouse graph and route planning

The warehouse is a hand-written graph. A Lambda plans routes over it with A*, and edge reservations keep two forklifts from occupying the same lane.

**Graph file (`warehouse.json`):**

```json
{
  "nodes": [
    {"id": "dock_A", "x": 0.10, "y": 0.20, "type": "dock"},
    {"id": "j1", "x": 0.40, "y": 0.20, "type": "junction"},
    {"id": "slot_A3", "x": 0.40, "y": 0.60, "type": "slot"}
  ],
  "edges": [
    {"from": "dock_A", "to": "j1", "bidirectional": true},
    {"from": "j1", "to": "slot_A3", "bidirectional": true}
  ]
}
```

**Planning:**

- A* with Euclidean distance; edge cost = length, plus a penalty for edges another forklift has reserved.
- Edges where RF-DETR currently sees an obstacle are removed from the graph until the obstacle clears.

**Reservations (multi-robot):**

1. Before driving an edge, the forklift's route needs a lock on it in DynamoDB (conditional write: only if the lock is free or already ours).
2. A lock is held for the next 2 edges ahead and released on `node_reached`.
3. Lock denied → the forklift waits at its node (state BLOCKED). After 5 s, replan around the blocked edge.
4. Every lock has a TTL, so a crashed forklift can't hold a lane forever.

On stage, this is the moment two forklifts meet at a junction: one waits, then both continue.

## LLM task dispatch

A Claude model on Amazon Bedrock turns a plain-English order into a behavior tree built only from fixed primitives. A validator Lambda checks it before anything moves.

**Primitives:**

| Node | Meaning |
| --- | --- |
| `Sequence[...]` | Run children in order; fail if one fails |
| `Fallback[...]` | Try children until one succeeds |
| `GoTo(node)` | Plan and drive to a graph node |
| `Pick(pallet)` | Lower forks, approach, lift |
| `Drop(slot)` | Lower forks, back out |
| `WaitUntil(clear, timeout_s)` | Wait for an edge or zone to clear |
| `Retry(n, child)` | Retry a child up to n times |

**Request flow:**

1. Dashboard publishes `tasks/new` with the text.
2. The dispatch Lambda calls the prod planner (a prompt + model version from the Planners table) on Bedrock with: the primitive list, the graph's node IDs, the current pallet locations from DynamoDB and the free forklifts. Use tool use / JSON output so the reply is a tree, not prose.
3. The validator checks: valid JSON schema, every node and pallet exists, the pallet is where the tree assumes, depth ≤ 5.
4. Invalid → send the errors back to Bedrock once. Still invalid → show the error on the dashboard.
5. Valid → assign the nearest free forklift, store the tree, start executing.

**Execution:** a small tree runner (in the dispatch Lambda via Step Functions, or on the edge laptop for speed) ticks the tree. `GoTo` calls the planner and publishes `path`; it completes when the forklift reports `done`.

The LLM never sees coordinates and never touches motors. That's the safety story for judges.

## Fleet manager

DynamoDB holds what's true right now, S3 holds everything that ever happened, and an Amplify web app shows both.

**DynamoDB tables:**

| Table | Key | Holds |
| --- | --- | --- |
| `Forklifts` | `forklift_id` | Last pose, state, battery, current task, last seen |
| `Tasks` | `task_id` | Text, behavior tree, assigned forklift, status, timestamps |
| `EdgeLocks` | `edge_id` | Holder, expiry (TTL) |
| `Pallets` | `pallet_id` | Current node, last seen |
| `Episodes` | `task_id` | Planner version, snapshot S3 URI, outcome, cost |
| `Planners` | `version` | Prompt, Bedrock model ID or fine-tuned weights URI, eval scores, stage (prod / shadow / retired) |

**S3 layout:**

- `raw/frames/{date}/{camera}/{ts}.jpg`: sampled frames, for debugging and the optional pallet fine-tune
- `raw/telemetry/{date}/`: IoT rule batches via Firehose
- `episodes/{date}/{task_id}.json`: snapshot, tree, planner version, outcome
- `evalsets/{version}/`: frozen real + synthetic test cases
- `planners/{version}/`: prompt, few-shot set, fine-tuned weights, scores
- `shadow/{task_id}/`: candidate trees and their simulated outcomes

**Dashboard (Amplify, React):**

- Live map: the graph drawn to scale, forklift icons moving, locked edges highlighted, RF-DETR obstacles as red dots.
- Command box: text in, behavior tree shown as a tree view, live status per node.
- Per-forklift cards: battery, state, current task.
- Planner panel: prod vs candidate tree and score for each command, a Promote button.

The dashboard subscribes to IoT Core over WebSockets for live data and reads DynamoDB through API Gateway or AppSync for state.

## Self-improving planner (MLOps loop)

The planner is what learns. Every behavior tree that runs is graded by its real outcome, so training and eval data need no human labelling.

**Each task becomes an episode:** the command text, a snapshot of the warehouse (forklift poses, pallets, locks), the tree, the planner version and the outcome.

**Outcome score:** one weighted cost. Failures and safety stops dominate it, then deadlocks, travel distance, junction wait time, replans and LLM latency.

1. **Record.** The `episode-logger` and the dispatch Lambda write each episode to S3 and its score to DynamoDB.
2. **Simulate.** A 2D graph simulator (same graph, same A* and edge locks, simple kinematics) replays any tree from an episode's starting snapshot in milliseconds. Calibrate its drive speed and wait times against real episodes.
3. **Grow the eval set.** Every real episode becomes a test case. Bedrock also generates synthetic commands and starting states for the hard cases: multi-pallet moves, blocked aisles, a forklift out of service.
4. **Make candidates.** A new system prompt, a different Bedrock model, few-shot examples drawn from the best-scoring trees, or a small open model fine-tuned on SageMaker using the top-scoring (command, state, tree) examples. That last one is a distilled planner: faster and cheaper.
5. **Offline gate.** A SageMaker Pipeline runs each candidate over the eval set in the simulator. Pass if the success rate is at least prod's, there are no new safety violations, average cost is lower and the validator rejects no more trees than before.
6. **Shadow.** For every live command, the candidate also writes a tree. It is validated and simulated from the same snapshot but never executed. The dashboard shows both trees and their scores side by side.
7. **Promote.** After N live commands where the candidate wins or ties, promote it with the button or automatically. The prod version swaps in the `Planners` table, and the old one is kept for rollback.

**Sim-to-real check:** each time a tree runs, compare the simulator's predicted cost with the real cost. A growing gap means the simulator needs recalibrating.

**Perception stays simple:** pretrained RF-DETR on the GPU handles people and obstacles, and forklift positions come from tags. Fine-tuning for pallets (labels from SAM or a quick hand pass) is an optional extra, not part of the pitch.

Demo trick: start the day with a deliberately naive prompt that ignores edge locks, so the candidate's improvement (fewer junction waits) shows on stage.

## Kiro workflow and team split

One monorepo, one Kiro spec per component, and the messaging contract as shared steering, so each person's agent builds against the same schemas.

**Repo layout:**

```
/edge          localizer, detector, safety-guard, Greengrass recipes
/freewili      controller firmware or scripts
/cloud         CDK stack: IoT rules, Lambdas, DynamoDB, S3, Step Functions
/mlops         simulator, eval sets, planner pipeline
/dashboard     Amplify React app
/shared        warehouse.json, MQTT schemas (JSON Schema)
.kiro/steering contract.md, conventions.md
.kiro/specs    one folder per component
```

**How to use Kiro:**

- Put the messaging contract and units/frame conventions in steering, so every spec inherits them.
- Write one spec per component (requirements → design → tasks). Start with `shared` and `cloud` so the others have something to talk to.
- Add a hook that re-validates the JSON schemas and runs the planner unit tests on save.
- Have Kiro generate a fake publisher that replays a recorded MQTT session. Dashboard and cloud work can then proceed before the hardware drives.

**Team split (2 people):**

| Person | Owns |
| --- | --- |
| Hardware lead | Physical setup, edge node, Free-WiLi controller, safety guard |
| Cloud lead | Messaging, planner and reservations, Bedrock dispatch, dashboard, MLOps |

With a third person, give them the dashboard and MLOps.

## Demo script and risks

The demo runs about three minutes and builds to the self-improving loop.

1. **Hook (20 s):** "Retrofitting forklifts with lidar costs a fortune per truck. We put the intelligence in the building instead."
2. **Command (60 s):** Type "move pallet 3 to dock B." Show the generated tree, then the car drives it.
3. **Fleet (40 s):** Send a second command so the two cars cross. One waits at the junction. Put a hand in the path to trigger a safety stop.
4. **Learning (40 s):** Open the planner panel: for every command so far, the candidate planner wrote its own tree, the simulator scored both, and the candidate wins. Press Promote.
5. **Close (20 s):** Name the AWS services and the Free-WiLi role on one architecture slide.

**Risks:**

| Risk | Mitigation |
| --- | --- |
| Venue Wi-Fi is slow or blocked | Edge runs the control loop locally; bring a phone hotspot |
| Lighting breaks tag detection | Bring a desk lamp; use larger tags |
| Bedrock is slow or returns bad JSON | Validator + one retry; preset commands as fallback |
| Free-WiLi can't drive motors directly | Use it for control and telemetry over USB; motor driver on a small MCU |
| Fine-tuning takes too long | Lead with prompt and model candidates; run the fine-tune overnight |
| Something breaks on stage | Backup video of the full run |
