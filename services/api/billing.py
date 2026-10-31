"""Settle held credits after a job reaches a terminal state."""

import logging
import os
import time
from datetime import datetime, timedelta, timezone

TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})
BILLABLE_OUTCOMES = frozenset({"SUCCEEDED", "PAUSED", "FAILED"})
logger = logging.getLogger(__name__)


def _deserialize_image(image):
    from boto3.dynamodb.types import TypeDeserializer

    deserializer = TypeDeserializer()
    return {name: deserializer.deserialize(value) for name, value in image.items()}


def _usage_for_job(table, user_id, job_id):
    from boto3.dynamodb.conditions import Key

    usage = []
    query = table.query
    # usageId bắt đầu bằng jobId nên chỉ truy vấn các lần chạy của job này, không quét cả bảng.
    response = query(
        KeyConditionExpression=Key("userId").eq(user_id)
        & Key("usageId").begins_with(f"{job_id}#"),
        ConsistentRead=True,
    )
    usage.extend(response.get("Items", []))
    while response.get("LastEvaluatedKey"):
        response = query(
            KeyConditionExpression=Key("userId").eq(user_id)
            & Key("usageId").begins_with(f"{job_id}#"),
            ConsistentRead=True,
            ExclusiveStartKey=response["LastEvaluatedKey"],
        )
        usage.extend(response.get("Items", []))
    return usage


def _charge_for_job(job, usage, free_failed):
    unit_price = int(job["unitPrice"])
    cost_cap = int(job["costCap"])
    if unit_price <= 0 or cost_cap < 0:
        raise ValueError("Job unitPrice must be positive and costCap cannot be negative")

    billable_seconds = sum(
        int(item.get("vcpuSeconds", 0))
        for item in usage
        if item.get("outcome") in BILLABLE_OUTCOMES
        and not (free_failed and item.get("outcome") == "FAILED")
    )
    measured_charge = (billable_seconds * unit_price + 3599) // 3600
    # Credit là số nguyên; phí không vượt trần, phần vượt do các task chạy song song sẽ do nền tảng chịu.
    return min(cost_cap, measured_charge)


def _transaction(job, user, users_table, jobs_table, charge, today, settled_at, free_failed):
    cost_cap = int(job["costCap"])
    values = {
        ":cap": {"N": str(cost_cap)},
        ":refund": {"N": str(cost_cap - charge)},
        ":one": {"N": "1"},
        ":zero": {"N": "0"},
    }
    user_update = (
        "SET creditHeld = creditHeld - :cap, "
        "creditAvailable = creditAvailable + :refund, "
        "runningJobs = runningJobs - :one"
    )
    user_condition = "creditHeld >= :cap AND runningJobs > :zero"

    if free_failed:
        errors_day = user.get("errorsDay")
        errors_today = int(user.get("errorsToday", 0))
        if errors_day == today:
            if "errorsToday" in user:
                # So khớp số lượt đã đọc để hai job quyết toán cùng lúc không dùng trùng lượt miễn phí.
                values.update({
                    ":today": {"S": today},
                    ":previous": {"N": str(errors_today)},
                    ":next": {"N": str(errors_today + 1)},
                })
                user_update += ", errorsToday = :next"
                quota_condition = "errorsDay = :today AND errorsToday = :previous AND errorsToday < freeErrorQuotaPerDay"
            else:
                values.update({":today": {"S": today}, ":one_error": {"N": "1"}})
                user_update += ", errorsToday = :one_error"
                quota_condition = "errorsDay = :today AND attribute_not_exists(errorsToday) AND freeErrorQuotaPerDay > :zero"
        else:
            values.update({":today": {"S": today}, ":one_error": {"N": "1"}})
            user_update += ", errorsDay = :today, errorsToday = :one_error"
            quota_condition = "(attribute_not_exists(errorsDay) OR errorsDay <> :today) AND freeErrorQuotaPerDay > :zero"
        user_condition += f" AND ({quota_condition})"

    return [
        {
            "Update": {
                "TableName": users_table,
                "Key": {"userId": {"S": job["userId"]}},
                "UpdateExpression": user_update,
                "ConditionExpression": user_condition,
                "ExpressionAttributeValues": values,
            }
        },
        {
            "Update": {
                "TableName": jobs_table,
                "Key": {"jobId": {"S": job["jobId"]}},
                "UpdateExpression": "SET chargedCredit = :charge, settledAt = :settled",
                "ConditionExpression": (
                    "attribute_not_exists(settledAt) AND attribute_exists(queuedAt) "
                    "AND #status IN (:succeeded, :failed, :cancelled)"
                ),
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": {
                    ":charge": {"N": str(charge)},
                    ":settled": {"N": str(settled_at)},
                    ":succeeded": {"S": "SUCCEEDED"},
                    ":failed": {"S": "FAILED"},
                    ":cancelled": {"S": "CANCELLED"},
                },
            }
        },
    ]


def settle_job(job_id, dynamodb, table_names, *, today=None, settled_at=None):
    from botocore.exceptions import ClientError

    jobs = dynamodb.Table(table_names["JOBS_TABLE"])
    users = dynamodb.Table(table_names["USERS_TABLE"])
    usage_table = dynamodb.Table(table_names["USAGE_TABLE"])
    now = int(time.time()) if settled_at is None else settled_at
    date = (
        datetime.fromtimestamp(now, timezone.utc) + timedelta(hours=7)
    ).strftime("%Y-%m-%d") if today is None else today

    for _ in range(5):
        job = jobs.get_item(Key={"jobId": job_id}, ConsistentRead=True).get("Item")
        if (
            not job or "queuedAt" not in job
            or job.get("status") not in TERMINAL_STATUSES
            or "settledAt" in job
        ):
            return False

        user = users.get_item(
            Key={"userId": job["userId"]}, ConsistentRead=True,
        ).get("Item")
        if not user:
            raise ValueError(f"Billing user not found: {job['userId']}")

        usage = _usage_for_job(usage_table, job["userId"], job_id)
        has_failed_work = job.get("status") in {"FAILED", "CANCELLED"}
        quota = int(user.get("freeErrorQuotaPerDay", 0))
        errors_today = int(user.get("errorsToday", 0)) if user.get("errorsDay") == date else 0
        # Chỉ miễn phí phần chạy lỗi; thời gian chạy thành công hoặc tạm dừng vẫn được tính.
        free_failed = has_failed_work and errors_today < quota
        charge = _charge_for_job(job, usage, free_failed)
        transaction = _transaction(
            job, user, table_names["USERS_TABLE"], table_names["JOBS_TABLE"],
            charge, date, now, free_failed,
        )

        try:
            # Cập nhật số dư, giảm số job đang chạy và đánh dấu đã quyết toán trong cùng giao dịch.
            dynamodb.meta.client.transact_write_items(TransactItems=transaction)
            return True
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") != "TransactionCanceledException":
                raise
            latest = jobs.get_item(
                Key={"jobId": job_id}, ConsistentRead=True,
            ).get("Item")
            if latest and "settledAt" in latest:
                return False

    raise RuntimeError(f"Could not settle job after concurrent updates: {job_id}")


def lambda_handler(event, context):
    import boto3

    dynamodb = boto3.resource("dynamodb")
    table_names = {
        "JOBS_TABLE": os.environ["JOBS_TABLE"],
        "USERS_TABLE": os.environ["USERS_TABLE"],
        "USAGE_TABLE": os.environ["USAGE_TABLE"],
    }
    failures = []
    for record in event.get("Records", []):
        if record.get("eventName") not in {"INSERT", "MODIFY"}:
            continue
        image = record.get("dynamodb", {}).get("NewImage")
        if not image:
            continue
        job = _deserialize_image(image)
        if (
            job.get("status") not in TERMINAL_STATUSES
            or "settledAt" in job
            or "queuedAt" not in job
        ):
            continue
        try:
            settle_job(job["jobId"], dynamodb, table_names)
        except Exception:
            logger.exception("Billing failed for jobId=%s", job.get("jobId"))
            failures.append({
                "itemIdentifier": record["dynamodb"]["SequenceNumber"],
            })
    return {"batchItemFailures": failures}
