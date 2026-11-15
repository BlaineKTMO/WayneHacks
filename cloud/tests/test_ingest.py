"""Ingest: duplicate done events must not advance a plan twice; null telemetry node must not erase position."""

import os

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-2")

import ingest  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402
from fakes import FakeTable  # noqa: E402


class CondTable(FakeTable):
    """FakeTable that understands the last_step_ts condition."""
    def update_item(self, Key, UpdateExpression, ConditionExpression=None, ExpressionAttributeValues=None, **kw):
        item = self.items.setdefault(Key[self.key], dict(Key))
        if ConditionExpression and "last_step_ts" in ConditionExpression and item.get("last_step_ts") == ExpressionAttributeValues[":ts"]:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        return super().update_item(Key, UpdateExpression, ExpressionAttributeValues=ExpressionAttributeValues, **kw)


def test_duplicate_done_ignored(monkeypatch):
    t = CondTable("forklift_id", [{"forklift_id": "fl-01", "node": "j1"}])
    monkeypatch.setattr(ingest, "table", lambda name: t)
    assert ingest.first_delivery("fl-01", 1000) is True
    assert ingest.first_delivery("fl-01", 1000) is False      # same event again
    assert ingest.first_delivery("fl-01", 2000) is True       # next event


def test_telemetry_without_node_keeps_position(monkeypatch):
    t = CondTable("forklift_id", [{"forklift_id": "fl-01", "node": "j3"}])
    monkeypatch.setattr(ingest, "table", lambda name: t)
    monkeypatch.setattr(ingest.traffic, "renew", lambda fid, node: None)
    ingest.handler({"forklift_id": "fl-01", "kind": "telemetry", "battery_v": 7.4, "motor_a": 0, "state": "IDLE", "node": None}, None)
    assert t.items["fl-01"]["node"] == "j3"
