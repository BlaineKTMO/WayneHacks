# Forklift fleet: the warehouse is the robot

Ceiling cameras localize every forklift, the cloud plans routes and turns plain English into behavior trees, and toy forklifts just follow paths. Full design: [forklift-fleet-architecture.md](forklift-fleet-architecture.md).

## Who does what

| Folder | Owner | What | Status |
|---|---|---|---|
| [`edge/`](edge/START_HERE.md) | Hardware lead | Camera → AprilTags → pose → pure pursuit → Free-WiLi | Ring 1 scaffold, runs in `--sim` |
| [`freewili/`](freewili/README.md) | Hardware lead | Free-WiLi OG firmware: L298N motors + watchdog | Written, not yet on hardware |
| [`shared/`](shared/) | Both | MQTT topics, message types, `warehouse.json`, A* | Done, tested |
| [`cloud/`](cloud/) | Cloud lead | CDK: DynamoDB, S3, IoT rules, Lambdas | Ring 2 deployed |
| [`tools/`](tools/) | Both | `fake_fleet.py`: poke the cloud without hardware | Done |
| [`dashboard/`](dashboard/) | Cloud lead | React live map, click-to-goto, command box (Amplify Hosting) | Live |
| `mlops/` | Cloud lead | Simulator, eval set, planner pipeline | Ring 5 |

**Hardware lead: start at [edge/START_HERE.md](edge/START_HERE.md).** All interfaces: [docs/API.md](docs/API.md).

## Demo day

1. iPad: open **https://main.d2ixq5egzkgfpj.amplifyapp.com/demo** and sign in once (session persists).
2. No hardware yet? Flip **Simulated fleet: On** on the demo page (AWS-hosted forklifts, ~1 min to boot).
   Turn it **Off** when real forklifts drive. Never run it and `tools/sim_fleet.py` at the same time.
3. Pre-flight: `uv run python tools/e2e.py --cloud-sim` → must say `36/36 checks passed`.
4. Real forklift instead? `uv run python cloud/scripts/use_map.py table`, sim **Off**, then on the edge laptop
   `WAREHOUSE_MAP=table uv run python -m fleet_edge.bridge`. Back to the sim: `use_map.py demo`.
5. Between judges: **Reset demo** (bottom of /demo).

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

Fake forklifts that drive routes on the real cloud (no hardware):

```bash
uv run python tools/sim_fleet.py                         # leave running
```

### Dashboard

```bash
uv run python cloud/scripts/add_user.py you@example.com  # invite (self sign-up is off)
uv run python dashboard/deploy.py                        # build + publish to Amplify
uv run python dashboard/deploy.py --local && cd dashboard && npm run dev   # local dev
```

Watch messages live: AWS console → IoT Core → MQTT test client → subscribe to `#`.

### What's deployed

- **DynamoDB:** `FleetForklifts`, `FleetTasks`, `FleetEdgeLocks` (TTL), `FleetPallets`, `FleetEpisodes`, `FleetPlanners`
- **S3:** data lake; IoT rules archive telemetry and events to `raw/`
- **Lambdas:** `Ingest` (telemetry/event/sampled pose → Forklifts), `RoutePlanner` (`fleet/+/goto` → A* → `fleet/+/path`), `Dispatch` (`tasks/new`; Ring 3 adds Bedrock)
- **IoT:** thing `forklift-edge-01` + `ForkliftEdgePolicy` (edge may only use `fleet/*`, `warehouse/*`)

Tear down: `cd cloud && npx aws-cdk@2 destroy` (deletes tables and bucket contents).
