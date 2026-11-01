"""GET /users/me: chỉ trả hồ sơ công khai của người dùng đã xác thực."""

import json
import logging
import os

import boto3

logger = logging.getLogger(__name__)
dynamodb = boto3.resource("dynamodb")
USERS_TABLE = os.environ["USERS_TABLE"]

PUBLIC_FIELDS = (
    "userId", "email", "role", "creditAvailable", "creditHeld",
    "runningJobs", "maxConcurrentJobs", "createdAt",
)
NUMBER_FIELDS = (
    "creditAvailable", "creditHeld", "runningJobs", "maxConcurrentJobs", "createdAt",
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


def lambda_handler(event, context):
    authorizer = (event.get("requestContext") or {}).get("authorizer") or {}
    user_id = authorizer.get("userId")
    if not isinstance(user_id, str) or not user_id:
        return response(401, {"error": "Unauthorized"})

    try:
        item = dynamodb.Table(USERS_TABLE).get_item(
            Key={"userId": user_id},
            ConsistentRead=True,
            ProjectionExpression=", ".join(f"#{field}" for field in PUBLIC_FIELDS),
            ExpressionAttributeNames={f"#{field}": field for field in PUBLIC_FIELDS},
        ).get("Item")
        if not item:
            return response(404, {"error": "User not found"})

        # Allowlist lần nữa: tuyệt đối không serialize nguyên Item chứa apiKeyHash.
        profile = {field: item[field] for field in PUBLIC_FIELDS}
        for field in NUMBER_FIELDS:
            profile[field] = int(profile[field])
        return response(200, profile)
    except Exception:
        logger.exception("Unable to read current user profile")
        return response(500, {"error": "Unable to load user profile"})
