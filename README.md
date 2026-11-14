# Forklift fleet: the warehouse is the robot

Ceiling cameras localize every forklift, the cloud plans routes and turns plain English into behavior trees, and toy forklifts just follow paths. Full design: [forklift-fleet-architecture.md](forklift-fleet-architecture.md).

## Who does what

| Folder | Owner | What | Status |
|---|---|---|---|
| [`edge/`](edge/START_HERE.md) | Hardware lead | Camera → AprilTags → pose → pure pursuit → Free-WiLi | Ring 1 scaffold, runs in `--sim` |
| [`freewili/`](freewili/README.md) | Hardware lead | Motor firmware + watchdog | Spec only |
| [`shared/`](shared/) | Both | MQTT topics, message types, `warehouse.json`, A* | Done, tested |
| [`cloud/`](cloud/) | Cloud lead | CDK: DynamoDB, S3, IoT rules, Lambdas | Ring 2 deployed |
| [`tools/`](tools/) | Both | `fake_fleet.py`: poke the cloud without hardware | Done |
| `dashboard/` | Cloud lead | Amplify React live map + command box | Next |
| `mlops/` | Cloud lead | Simulator, eval set, planner pipeline | Ring 5 |

**Hardware lead: start at [edge/START_HERE.md](edge/START_HERE.md).**

## Setup

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh     # if you don't have uv
uv sync --group dev --group edge                     # hardware lead
uv sync --group dev --group cloud                    # cloud lead (needs Node.js for CDK)
uv run pytest                                        # everything should pass
```

## Cloud (us-east-2)

```bash
aws login                                            # credentials
cd cloud && npx aws-cdk@2 diff && npx aws-cdk@2 deploy
cd .. && uv run python cloud/scripts/seed.py         # pallets + forklifts into DynamoDB
uv run python cloud/scripts/create_edge_cert.py      # once: device cert for the edge laptop
```

Try it with no hardware:

```bash
uv run python tools/fake_fleet.py telemetry fl-01 --node dock_A
uv run python tools/fake_fleet.py goto fl-01 slot_A3     # planner publishes fleet/fl-01/path
uv run python tools/fake_fleet.py status fl-01
```

Watch messages live: AWS console → IoT Core → MQTT test client → subscribe to `#`.

### What's deployed

- **DynamoDB:** `FleetForklifts`, `FleetTasks`, `FleetEdgeLocks` (TTL), `FleetPallets`, `FleetEpisodes`, `FleetPlanners`
- **S3:** data lake; IoT rules archive telemetry and events to `raw/`
- **Lambdas:** `Ingest` (telemetry/event/sampled pose → Forklifts), `RoutePlanner` (`fleet/+/goto` → A* → `fleet/+/path`), `Dispatch` (`tasks/new`; Ring 3 adds Bedrock)
- **IoT:** thing `forklift-edge-01` + `ForkliftEdgePolicy` (edge may only use `fleet/*`, `warehouse/*`)

Tear down: `cd cloud && npx aws-cdk@2 destroy` (deletes tables and bucket contents).
