from fleet_shared.behavior_tree import first_target, validate, with_status
from fleet_shared.graph import Graph

G = Graph.load()
PALLETS = {"pallet_1": "slot_A1", "pallet_2": "slot_A2", "pallet_3": "slot_A3"}


def seq(*c):
    return {"type": "Sequence", "children": list(c)}


def goto(n): return {"type": "GoTo", "node": n}
def pick(p): return {"type": "Pick", "pallet": p}
def drop(s): return {"type": "Drop", "slot": s}


MOVE_3_TO_B = seq(goto("slot_A3"), pick("pallet_3"), goto("dock_B"), drop("dock_B"))


def test_valid_move():
    assert validate(MOVE_3_TO_B, G, PALLETS, start_node="dock_A") == []
    assert first_target(MOVE_3_TO_B) == "slot_A3"


def test_pick_without_going_there():
    errs = validate(seq(pick("pallet_3"), goto("dock_B"), drop("dock_B")), G, PALLETS, start_node="dock_A")
    assert any("GoTo(slot_A3)" in e for e in errs)


def test_drop_on_occupied_slot():
    t = seq(goto("slot_A3"), pick("pallet_3"), goto("slot_A1"), drop("slot_A1"))
    assert any("occupied by pallet_1" in e for e in validate(t, G, PALLETS, start_node="dock_A"))


def test_unknown_things_and_shape():
    assert validate({"type": "Teleport"}, G, PALLETS)
    assert validate(seq(goto("mars")), G, PALLETS)
    assert validate(seq(goto("slot_A3"), pick("pallet_9")), G, PALLETS)
    assert validate({"type": "GoTo", "node": "j1", "speed": 9}, G, PALLETS)  # extra field
    assert validate(seq(), G, PALLETS)


def test_must_not_end_carrying():
    errs = validate(seq(goto("slot_A3"), pick("pallet_3")), G, PALLETS, start_node="dock_A")
    assert any("still carrying" in e for e in errs)


def test_depth_limit():
    t = goto("j1")
    for _ in range(6):
        t = seq(t)
    assert any("deeper" in e for e in validate(t, G, PALLETS))


def test_with_status_marks_everything():
    t = with_status({"type": "Retry", "n": 2, "child": MOVE_3_TO_B})
    assert t["status"] == "pending" and t["attempts"] == 0
    assert all(c["status"] == "pending" for c in t["child"]["children"])


def test_rule_planner():
    from fleet_shared import rule_planner
    t = rule_planner.plan("Move pallet 3 to dock B", G, PALLETS)
    assert t == MOVE_3_TO_B
    assert validate(t, G, PALLETS, start_node="dock_A") == []
    swap = rule_planner.plan("Swap pallets 1 and 2", G, PALLETS)
    assert validate(swap, G, PALLETS, start_node="dock_A") == []
    assert rule_planner.plan("make me a sandwich", G, PALLETS) is None
    assert rule_planner.plan("move pallet 9 to dock B", G, PALLETS) is None
