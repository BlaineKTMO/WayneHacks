"""Helpers shared by all Lambdas: AWS clients, MQTT publish, DynamoDB float handling."""

import json
import os
from decimal import Decimal

import boto3

_ddb = boto3.resource("dynamodb")
_iot = None


def table(env_name: str):
    return _ddb.Table(os.environ[env_name])


def to_ddb(obj):
    """DynamoDB wants Decimal, not float."""
    return json.loads(json.dumps(obj), parse_float=Decimal)


def from_ddb(obj):
    """Decimal -> int/float so it can be JSON-serialised."""
    if isinstance(obj, list):
        return [from_ddb(v) for v in obj]
    if isinstance(obj, dict):
        return {k: from_ddb(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    return obj


def publish(topic: str, payload: dict | str) -> None:
    global _iot
    if _iot is None:
        _iot = boto3.client("iot-data", endpoint_url=f"https://{os.environ['IOT_ENDPOINT']}")
    body = payload if isinstance(payload, str) else json.dumps(payload)
    _iot.publish(topic=topic, qos=1, payload=body.encode())
