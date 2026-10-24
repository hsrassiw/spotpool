"""Đánh dấu task lỗi khi SQS hết lượt nhận."""

import json
import os
import time

from spotpool_core.contracts import (
    JOB_FAILED, JOB_QUEUED, JOB_RUNNING, JOB_SUCCEEDED, TASK_FAILED,
    TASK_QUEUED, TASK_RUNNING, fingerprint, validate_message,
)


def fail_exhausted(message, dynamodb, tasks_table, jobs_table, now=None):
    validate_message(message)
    now = int(time.time()) if now is None else now
    task = dynamodb.Table(tasks_table).get_item(
        Key={"jobId": message["jobId"], "taskId": message["taskId"]},
        ConsistentRead=True,
    ).get("Item")
    if not task or task.get("fingerprint") != fingerprint(message):
        return False
    if not (task.get("status") == TASK_QUEUED or
            task.get("status") == TASK_RUNNING and int(task.get("leaseUntil", now + 1)) < now):
        return False
    try:
        dynamodb.meta.client.transact_write_items(TransactItems=[
            {"Update": {
                "TableName": tasks_table,
                "Key": {"jobId": message["jobId"], "taskId": message["taskId"]},
                "UpdateExpression": "SET #s=:failed, finishedAt=:now, #error=:error",
                "ConditionExpression": "fingerprint=:fp AND (#s=:queued OR (#s=:running AND leaseUntil<:now))",
                "ExpressionAttributeNames": {"#s": "status", "#error": "error"},
                "ExpressionAttributeValues": {":failed": TASK_FAILED, ":now": now,
                                              ":error": "SQS retries exhausted",
                                              ":fp": fingerprint(message),
                                              ":queued": TASK_QUEUED,
                                              ":running": TASK_RUNNING},
            }},
            {"Update": {
                "TableName": jobs_table,
                "Key": {"jobId": message["jobId"]},
                "UpdateExpression": "ADD tasksRemaining :minusOne, tasksFailed :one",
                "ConditionExpression": "#s IN (:queued,:running) AND tasksRemaining>:zero",
                "ExpressionAttributeNames": {"#s": "status"},
                "ExpressionAttributeValues": {":minusOne": -1, ":one": 1,
                                              ":queued": JOB_QUEUED,
                                              ":running": JOB_RUNNING,
                                              ":zero": 0},
            }},
        ])
    except Exception as error:
        if "TransactionCanceledException" in str(error):
            return False
        raise
    jobs = dynamodb.Table(jobs_table)
    for state, failed_condition in ((JOB_SUCCEEDED, "tasksFailed=:zero"),
                                    (JOB_FAILED, "tasksFailed>:zero")):
        try:
            jobs.update_item(
                Key={"jobId": message["jobId"]},
                UpdateExpression="SET #s=:state, finishedAt=:now",
                ConditionExpression=f"#s IN (:queued,:running) AND tasksRemaining=:zero AND {failed_condition}",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":state": state, ":now": now, ":zero": 0,
                                           ":queued": JOB_QUEUED, ":running": JOB_RUNNING},
            )
            break
        except Exception as error:
            if "ConditionalCheckFailedException" not in str(error):
                raise
    return True


def lambda_handler(event, context):
    import boto3

    dynamodb = boto3.resource("dynamodb")
    failures = []
    for record in event["Records"]:
        try:
            fail_exhausted(json.loads(record["body"]), dynamodb,
                           os.environ["TASKS_TABLE"], os.environ["JOBS_TABLE"])
        except (ValueError, KeyError, TypeError) as error:
            print(f"Discarding invalid DLQ message: {error}")
        except Exception:
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
