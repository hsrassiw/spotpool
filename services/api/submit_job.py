
import json
import os
import re
import time
import uuid

import boto3
from botocore.exceptions import ClientError

dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")

JOBS_TABLE = os.environ["JOBS_TABLE"]
TASKS_TABLE = os.environ["TASKS_TABLE"]
INPUT_BUCKET = os.environ["INPUT_BUCKET"]

jobs_table = dynamodb.Table(JOBS_TABLE)
tasks_table = dynamodb.Table(TASKS_TABLE)

ALLOWED_TIERS = {"ondemand", "spot"}
ALLOWED_KINDS = {"video", "script"}
MAX_FILES = 100
UPLOAD_URL_TTL = 900


def response(status_code, payload):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json"
        },
        "body": json.dumps(payload),
    }


def authenticated_user_id(event):
    # Lay userId tu JWT claims cua API Gateway
    claims = (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
    )
    return claims.get("sub")


def clean_filename(value):
    filename = os.path.basename(str(value or ""))
    filename = re.sub(r"[^A-Za-z0-9._-]", "_", filename)

    if not filename or filename in {".", ".."}:
        raise ValueError("Invalid fileName")

    return filename


def lambda_handler(event, context):
    try:
        # 1. Xac thuc nguoi dung
        user_id = authenticated_user_id(event)

        if not user_id:
            return response(401, {
                "message": "Unauthorized"
            })

        # 2. Doc request body
        try:
            body = json.loads(event.get("body") or "{}")
        except (json.JSONDecodeError, TypeError):
            return response(400, {
                "message": "Invalid JSON body"
            })

        tier = body.get("tier")
        kind = body.get("kind")
        vcpu = body.get("vcpu")
        files = body.get("files")

        # 3. Kiem tra tham so
        if (
            tier not in ALLOWED_TIERS
            or kind not in ALLOWED_KINDS
            or type(vcpu) is not int
            or vcpu != 1
        ):
            return response(400, {
                "message": "Invalid tier, kind, or vcpu parameters"
            })

        if (
            not isinstance(files, list)
            or not files
            or len(files) > MAX_FILES
        ):
            return response(400, {
                "message": "files must contain 1 to 100 items"
            })

        # 4. Tao job
        job_id = str(uuid.uuid4())
        created_at = int(time.time())

        prepared = []
        task_ids = set()

        # 5. Tao presigned URL cho tung file
        for item in files:
            if not isinstance(item, dict):
                return response(400, {
                    "message": "Each file must be an object"
                })

            task_id = str(item.get("taskId") or uuid.uuid4())

            if task_id in task_ids:
                return response(400, {
                    "message": "Duplicate taskId"
                })

            task_ids.add(task_id)

            try:
                filename = clean_filename(item.get("fileName"))
            except ValueError:
                return response(400, {
                    "message": "Invalid fileName"
                })

            content_type = str(
                item.get("contentType")
                or "application/octet-stream"
            )

            if (
                len(content_type) > 200
                or "\n" in content_type
                or "\r" in content_type
            ):
                return response(400, {
                    "message": "Invalid contentType"
                })

            file_key = (
                f"submissions/{user_id}/{job_id}/"
                f"{task_id}/{filename}"
            )

            upload_url = s3.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": INPUT_BUCKET,
                    "Key": file_key,
                    "ContentType": content_type,
                },
                ExpiresIn=UPLOAD_URL_TTL,
            )

            prepared.append({
                "taskId": task_id,
                "fileKey": file_key,
                "fileName": filename,
                "contentType": content_type,
                "uploadUrl": upload_url,
            })

        # 6. Luu job vao DynamoDB
        jobs_table.put_item(
            Item={
                "jobId": job_id,
                "userId": user_id,
                "tier": tier,
                "kind": kind,
                "vcpu": vcpu,
                "status": "UPLOADING",
                "createdAt": created_at,
            },
            ConditionExpression="attribute_not_exists(jobId)",
        )

        # 7. Luu task voi khoa ghep jobId + taskId
        try:
            with tasks_table.batch_writer() as batch:
                for task in prepared:
                    batch.put_item(
                        Item={
                            "jobId": job_id,
                            "taskId": task["taskId"],
                            "fileKey": task["fileKey"],
                            "status": "UPLOADING",
                        }
                    )

        except Exception:
            # Xoa job neu khong luu duoc danh sach task
            jobs_table.delete_item(
                Key={
                    "jobId": job_id
                },
                ConditionExpression="userId = :uid",
                ExpressionAttributeValues={
                    ":uid": user_id
                },
            )
            raise

        # 8. Tra jobId va URL upload cho frontend
        return response(201, {
            "message": "Job created successfully",
            "jobId": job_id,
            "status": "UPLOADING",
            "uploadExpiresIn": UPLOAD_URL_TTL,
            "files": [
                {
                    "taskId": task["taskId"],
                    "fileKey": task["fileKey"],
                    "uploadUrl": task["uploadUrl"],
                    "method": "PUT",
                    "contentType": task["contentType"],
                }
                for task in prepared
            ],
        })

    except ClientError as e:
        print("AWS service error:", str(e))
        return response(500, {
            "message": "AWS service error"
        })

    except Exception as e:
        print("Internal error:", str(e))
        return response(500, {
            "message": "Internal server error"
        })