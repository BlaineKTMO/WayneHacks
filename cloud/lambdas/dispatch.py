"""IoT rule target for tasks/new: plain-English order -> behavior tree -> forklift.

Ring 2 (now): records the task and reports it as received.
Ring 3 TODO, in order:
  1. Load the prod planner (prompt + model id) from the Planners table.
  2. Call Bedrock Converse with tool use so the reply is a tree from the fixed primitives only.
  3. validate_tree(); on errors retry once with the errors, else report failed.
  4. Assign the nearest free forklift, store the tree, start the tree runner.
"""

import logging
import time
import uuid

from common import publish, table, to_ddb
from fleet_shared import topics
from fleet_shared.messages import TaskNew, TaskStatus

log = logging.getLogger()
log.setLevel(logging.INFO)


def handler(msg, _ctx):
    task = TaskNew.from_dict(msg)
    task_id = f"t-{uuid.uuid4().hex[:8]}"
    table("TASKS_TABLE").put_item(Item=to_ddb({
        "task_id": task_id, "text": task.text, "status": "received",
        "created_at": int(time.time() * 1000),
    }))
    publish(topics.task_status(task_id), TaskStatus(state="received").to_dict())
    log.info("task %s received: %r (Ring 3 will plan it)", task_id, task.text)
    return {"task_id": task_id}
