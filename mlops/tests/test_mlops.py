from mlops.evaluate import cases_from, gate


def test_cases_from_episodes_skip_incomplete():
    eps = [{"task_id": "t1", "text": "move pallet 3 to dock B", "snapshot": {"forklift": "fl-01", "start": "dock_A", "pallets": {}},
            "outcome": {"cost": 5}}, {"task_id": "t2", "text": "x"}]
    cases = cases_from(eps)
    assert [c["case_id"] for c in cases] == ["real-t1"] and cases[0]["real_cost"] == 5


def test_gate():
    prod = {"success_rate": 1.0, "invalid": 0, "mean_cost": 9.0}
    assert gate({"success_rate": 1.0, "invalid": 0, "mean_cost": 6.0}, prod)[0]
    ok, why = gate({"success_rate": 0.9, "invalid": 1, "mean_cost": 6.0}, prod)
    assert not ok and len(why) == 2
