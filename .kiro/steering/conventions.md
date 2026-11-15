# Conventions (steering)

- Units: metres, radians, seconds. `ts` is epoch milliseconds.
- World frame: origin at the bottom-left corner tag (tag 11; IDs in `edge/fleet_edge/config.py` CORNER_TAGS), x right, y up, from the 4 corner AprilTags.
- Forklift IDs `fl-01`, `fl-02`. Graph node IDs come from `shared/warehouse.json`.
- AWS: everything in **us-east-2** (project Region). CDK in Python under `cloud/`. No Lambda@Edge, no cross-Region anything.
- Python 3.12 via `uv`. Run tests with `uv run pytest` before pushing.
- Real-time things (seeing, steering, stopping) stay on the edge. The cloud plans, stores and learns.
- Edge settings live only in `edge/fleet_edge/config.py`.
- Never commit `edge/certs/` (device keys).
