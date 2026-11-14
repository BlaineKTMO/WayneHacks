# Messaging contract (steering, applies to every spec)

The source of truth is code: `shared/fleet_shared/topics.py` and `shared/fleet_shared/messages.py`. Always import those; never hand-build topic strings or payloads. The full table is in `forklift-fleet-architecture.md` > Messaging contract.

Additions beyond the architecture doc:
- `fleet/{id}/goto` `{node, task_id?}`: dashboard → cloud, Ring 2 "click a node". The route planner Lambda answers with `fleet/{id}/path`.

Serial protocol laptop ↔ Free-WiLi: `W,<l>,<r>` / `S,<reason>` / `T,<state>,<battery_v>` (see `edge/fleet_edge/freewili_link.py`).

Changing the contract: update `shared/`, its tests, and this file in the same commit, and tell the other lead.
