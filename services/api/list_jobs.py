"""GET /jobs: danh sách của người dùng, phân trang qua GSI theo contract v4."""

import base64
import json
import logging
import os
import re

import boto3
from boto3.dynamodb.conditions import Attr, Key

logger = logging.getLogger(__name__)
dynamodb = boto3.resource("dynamodb")
JOBS_TABLE = os.environ["JOBS_TABLE"]
STATUSES = frozenset({"UPLOADING", "QUEUED", "RUNNING", "PAUSED", "SUCCEEDED", "FAILED", "CANCELLED"})
FIELDS = (
    "jobId", "userId", "status", "tier", "kind", "files", "taskCount",
    "tasksSucceeded", "tasksFailed", "costCap", "chargedCredit", "createdAt",
)


def response(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json; charset=utf-8",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Authorization,Content-Type",
            "Cache-Control": "no-store",
        },
        "body": json.dumps(body, ensure_ascii=False),
    }


def decode_cursor(value, user_id):
    if value is None:
        return None
    try:
        if not isinstance(value, str) or len(value) > 2048 or not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", value):
            raise ValueError
        key = json.loads(base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True))
        if (not isinstance(key, dict) or set(key) != {"userId", "createdAt", "jobId"}
                or key["userId"] != user_id
                or not isinstance(key["jobId"], str) or not 1 <= len(key["jobId"]) <= 128
                or type(key["createdAt"]) is not int or key["createdAt"] < 0):
            raise ValueError
        return key
    except (ValueError, TypeError, UnicodeError):
        raise ValueError("Invalid pagination cursor") from None


def encode_cursor(key):
    if not key:
        return None
    native = {"userId": key["userId"], "createdAt": int(key["createdAt"]), "jobId": key["jobId"]}
    return base64.urlsafe_b64encode(json.dumps(native, separators=(",", ":")).encode()).decode().rstrip("=")


def serialize_job(item):
    return {
        "jobId": item["jobId"], "status": item["status"], "tier": item["tier"], "kind": item["kind"],
        "name": item["files"][0],
        "progress": {"total": int(item["taskCount"]), "succeeded": int(item.get("tasksSucceeded", 0)),
                     "failed": int(item.get("tasksFailed", 0))},
        "costCap": int(item["costCap"]),
        "chargedCredit": int(item["chargedCredit"]) if item.get("chargedCredit") is not None else None,
        "createdAt": int(item["createdAt"]),
    }


def lambda_handler(event, context):
    authorizer = (event.get("requestContext") or {}).get("authorizer") or {}
    user_id = authorizer.get("userId")
    if not isinstance(user_id, str) or not user_id:
        return response(401, {"error": "Unauthorized"})

    try:
        params = event.get("queryStringParameters") or {}
        raw_limit = params.get("limit", "20")
        if not isinstance(raw_limit, str) or len(raw_limit) > 10 or not raw_limit.isascii() or not raw_limit.isdecimal() or int(raw_limit) < 1:
            raise ValueError("limit must be a positive integer")
        limit = min(int(raw_limit), 50)
        status = params.get("status")
        if status is not None and (not isinstance(status, str) or status not in STATUSES):
            raise ValueError("Invalid job status")
        cursor = decode_cursor(params.get("next"), user_id)
    except (ValueError, AttributeError):
        return response(400, {"error": "Invalid limit, status or pagination cursor"})

    query = {
        "IndexName": "userId-createdAt-index",
        "KeyConditionExpression": Key("userId").eq(user_id),
        "ExpressionAttributeNames": {f"#{field}": field for field in FIELDS},
        "ProjectionExpression": ", ".join(f"#{field}" for field in FIELDS),
        "ScanIndexForward": False,
        "Limit": limit,
    }
    if status is not None:
        query["FilterExpression"] = Attr("status").eq(status)
    if cursor is not None:
        query["ExclusiveStartKey"] = cursor

    try:
        # Một request trả một trang. Không scan, không đọc Tasks, không đếm toàn bảng.
        page = dynamodb.Table(JOBS_TABLE).query(**query)
        return response(200, {"jobs": [serialize_job(item) for item in page.get("Items", [])],
                              "next": encode_cursor(page.get("LastEvaluatedKey"))})
    except Exception:
        logger.exception("Unable to list current user's jobs")
        return response(500, {"error": "Unable to load jobs"})
