"""In-memory stand-ins for DynamoDB tables, the lock store and MQTT publish."""

import re


class FakeTable:
    def __init__(self, key, items=()):
        self.key, self.items = key, {i[key]: dict(i) for i in items}

    def get_item(self, Key, **_):
        k = Key[self.key]
        return {"Item": dict(self.items[k])} if k in self.items else {}

    def put_item(self, Item, **_):
        self.items[Item[self.key]] = dict(Item)

    def scan(self, **_):
        return {"Items": [dict(v) for v in self.items.values()]}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeNames=None, ExpressionAttributeValues=None,
                    ConditionExpression=None, **_):
        names, values = ExpressionAttributeNames or {}, ExpressionAttributeValues or {}
        item = self.items.setdefault(Key[self.key], dict(Key))
        if ConditionExpression == "attribute_not_exists(route_rev)" and "route_rev" in item or \
           ConditionExpression == "route_rev = :expected" and item.get("route_rev") != values[":expected"]:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        for verb, body in re.findall(r"(SET|REMOVE)\s+(.*?)(?=\s+(?:SET|REMOVE)\s|$)", UpdateExpression):
            for part in body.split(","):
                part = part.strip()
                if verb == "REMOVE":
                    item.pop(names.get(part, part), None)
                else:
                    name, val = (x.strip() for x in part.split("="))
                    item[names.get(name, name)] = values[val]
        return {}


class FakeLocks:
    def __init__(self):
        self.locks = {}

    def acquire(self, node, fid, task_id=None):
        if self.locks.get(node, fid) != fid:
            return False
        self.locks[node] = fid
        return True

    def extend(self, node, fid):
        pass  # only touches expiry; never creates a lock

    def release(self, node, fid):
        if self.locks.get(node) == fid:
            del self.locks[node]

    def held(self):
        return dict(self.locks)


def install(monkeypatch, forklifts, pallets=()):
    """Patch every Lambda module to use fakes. Returns (tables, locks, sent)."""
    import route_planner
    import traffic
    import tree_runner

    tables = {
        "FORKLIFTS_TABLE": FakeTable("forklift_id", forklifts),
        "TASKS_TABLE": FakeTable("task_id"),
        "PALLETS_TABLE": FakeTable("pallet_id", pallets),
    }
    locks, sent = FakeLocks(), []
    for mod in (tree_runner, route_planner, traffic):
        monkeypatch.setattr(mod, "table", lambda name: tables[name])
        monkeypatch.setattr(mod, "publish", lambda topic, payload: sent.append((topic, payload)))
    monkeypatch.setattr(traffic, "LOCKS", locks)
    return tables, locks, sent


def paths(sent, fid):
    return [p for t, p in sent if t == f"fleet/{fid}/path"]
