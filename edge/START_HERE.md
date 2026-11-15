# Edge + Forklift: start here

You're building **Ring 1**: one toy forklift drives a 4-point loop by itself. A ceiling webcam sees AprilTags, this laptop works out where the forklift is, and the Free-WiLi drives the motors. No internet needed.

**Done when:** the forklift drives the loop 3 times in a row on its own, and stops within half a second when you cover its tag with your hand.

You don't need to write much code. Most of the work is the physical setup and tuning numbers in **one file: [`fleet_edge/config.py`](fleet_edge/config.py)**.

---

## 0. One-time setup (10 min)

Open **Terminal** and paste these one at a time:

```bash
# Install uv (the Python tool we use). Skip if `uv --version` already works.
curl -LsSf https://astral.sh/uv/install.sh | sh

# Get the code (ask the cloud lead to add your GitHub account first)
git clone <repo-url> WayneHacks
cd WayneHacks

# Install everything the edge needs
uv sync --group edge --group dev

# Check it all works: should end with "passed"
uv run pytest edge shared
```

**Every command below is run from the `WayneHacks` folder.** If you see `No module named fleet_edge`, you're in the wrong folder; `cd` back to `WayneHacks`.

## 1. Watch it work in simulation first (5 min)

```bash
uv run python -m fleet_edge.step6_ring1 --sim
```

A window opens with a green arrow (the forklift) and 4 blue dots (the waypoints). Click the window, press **SPACE**. The arrow drives the loop. This is what the real one should do. Press **q** to quit.

## 2. The real thing, step by step

Do these in order. Don't move on until the step's check passes.

| Step | Command | Check |
|---|---|---|
| **1. Camera** | `uv run python -m fleet_edge.step1_camera` | Whole table visible, camera straight down, sharp. Tape the mount so it **never moves**. |
| **2. Lens calibration** | `uv run python -m fleet_edge.step2_calibrate` | Prints `reprojection error` under 1.0. Creates `edge/calibration/camera.npz`. |
| **3. Tags** | `uv run python -m fleet_edge.step3_tags` | Says `map OK`. Put the forklift tag on a ruler-measured spot: screen matches within 1 cm. Arrow points the way the forklift faces (if not, fix `TAG_HEADING_OFFSET`). |
| **4. Motors** | `uv run python -m fleet_edge.step4_motors` | w/a/s/d drives the forklift. Find the lowest speed that moves it, put it in `MIN_SPEED`. Unplug USB while driving → stops within 0.5 s. |
| **4b. Drive with the camera** | `uv run python -m fleet_edge.drive_cam` | Live webcam view with tags, the forklift's outline and heading, the route, the pole and the camera border. Hold w/a/s/d to drive (stops when you let go); stops by itself if tag 13 is lost. `--radio` uses the radio base, `--video out.mp4` records. |
| **5. Serial debug** | `uv run python -m fleet_edge.step5_serial_monitor` | Only when step 4 doesn't work: shows exactly what the Free-WiLi sends and receives. |
| **6. Ring 1** | `uv run python -m fleet_edge.step6_ring1` | SPACE to go. 3 clean laps. Cover the tag → stops. **Record a backup video.** |

**Before step 4:** the Free-WiLi OG needs the forklift firmware and the L298N wired. Follow [freewili/README.md](../freewili/README.md) sections 1–5 first.

### Printing tags

- Family **tag36h11**. Download PNGs from https://github.com/AprilRobotics/apriltag-imgs/tree/master/tag36h11
- Corner tags: IDs **11** (bottom-left, the origin), **9** (bottom-right), **5** (top-right), **4** (top-left), matching `CORNER_TAGS` in `config.py`. Measure centre-to-centre with a ruler and update the numbers (currently 527 × 406 mm).
- Forklift tag: ID **13** (= `fl-01`). Pick any unused ID for a second forklift and add it to `FORKLIFT_TAGS`.
- Print at 100% scale, about 6 cm black square. Measure and set `TAG_SIZE_M`. Matte paper; glossy reflects the lights.

### Finding the Free-WiLi port

Nothing to do: `SERIAL_PORT = "auto"` finds the OG main CPU by name. If that fails, the error lists the ports it saw. Put the right one in `SERIAL_PORT` in `config.py`.

## 2b. Connect to the cloud (after Ring 1 passes)

| Step | Command | Check |
|---|---|---|
| **7. Bridge, no hardware** | `uv run python -m fleet_edge.bridge --sim --iam` | Prints `bridge up`. On the dashboard, forklifts move when you click the map. |
| **8. Device certificate** (once) | `uv run python cloud/scripts/create_edge_cert.py` | Creates `edge/certs/`. **Never** commit or share these files. |
| **9. Bridge, real forklift** | `uv run python -m fleet_edge.bridge` | Click a spot on the dashboard: the real forklift drives there. Cover its tag: it stops, uncover: it carries on. |

**Before step 9 (registration):** the cloud has two maps. The demo map is 1.5 × 1 m for the simulated fleet; the real table uses `shared/warehouse_table.json`, drawn inside the safe rectangle you measured for Ring 1. **Check those 6 nodes on the table first** (move them only inside x 0.12–0.42, y 0.14–0.20 unless you re-run the clearance search). Then:

```bash
uv run python cloud/scripts/use_map.py table          # cloud uses the table map
WAREHOUSE_MAP=table uv run python -m fleet_edge.bridge # bridge uses it too
```

Switch back with `use_map.py demo` when the simulated fleet drives. Pick/Drop now run the fork for `FORK_LIFT_MS` (config.py): measure a full lift and set it. The camera view streams to the website automatically (`CAMERA_STREAM_FPS`).

**Also before step 9: turn the dashboard's "Simulated fleet" switch OFF**, or the cloud gets two `fl-01`s. Which Free-WiLi drives which forklift is `FORKLIFT_LINKS` in `config.py`.

## 3. When something goes wrong

| Symptom | Try |
|---|---|
| Camera window is your face | `CAMERA_INDEX = 1` in config |
| `need 4 corner tags` | More light (desk lamp), bigger tags, check the corner IDs match `CORNER_TAGS` |
| Position jumps around | Lower `POSE_ALPHA` (e.g. 0.4) |
| Forklift wobbles / zig-zags | Raise `LOOKAHEAD_M` (0.10–0.15), lower `MAX_SPEED` |
| Spins forever at a waypoint | Raise `ARRIVE_RADIUS_M` to 0.05; check heading arrow direction in step 3 |
| Drives the wrong way | Swap the motor wires on that side, or swap left/right in the Free-WiLi firmware |
| Doesn't move at low speed | `MIN_SPEED` too low: redo the deadband test |
| `Permission denied` / port busy | Close any other program using the Free-WiLi (including step 5) |

Each run of step 6 writes `edge/logs/ring1.csv` (time, position, wheel commands). Send it to the cloud lead if you're stuck.

## 4. Using an AI assistant (Kiro / Claude) on this

It helps to say: *"Read edge/START_HERE.md and edge/fleet_edge/config.py. I'm on step N and here's what happens: …"* and paste the terminal output. Ask it to **only change `config.py`** unless you really need code changes; the protocol and file layout are shared with the cloud side.

**Don't change** `shared/` or the serial protocol (`W`, `S`, `T` lines) without telling the cloud lead.
