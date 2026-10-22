
import json
import os

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

dynamodb = boto3.resource("dynamodb")
dynamodb_client = boto3.client("dynamodb")
s3 = boto3.client("s3")

JOBS_TABLE = os.environ["JOBS_TABLE"]
TASKS_TABLE = os.environ["TASKS_TABLE"]
USERS_TABLE = os.environ["USERS_TABLE"]
INPUT_BUCKET = os.environ["INPUT_BUCKET"]

jobs_table = dynamodb.Table(JOBS_TABLE)
tasks_table = dynamodb.Table(TASKS_TABLE)

CREDIT_COST = 10


def response(status_code, payload):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
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


def lambda_handler(event, context):
    user_id = authenticated_user_id(event)

    if not user_id:
        return response(401, {"message": "Unauthorized"})

    job_id = (event.get("pathParameters") or {}).get("id")

    if not job_id:
        return response(400, {"message": "Missing job ID"})

    try:
        # 1. Lay thong tin job
        result = jobs_table.get_item(
            Key={"jobId": job_id},
            ConsistentRead=True,
        )
        job = result.get("Item")

        if not job:
            return response(404, {"message": "Job not found"})

        if job.get("userId") != user_id:
            return response(403, {"message": "Forbidden"})

        if job.get("status") != "UPLOADING":
            return response(409, {
                "message": "Job is not in UPLOADING state"
            })

        if type(job.get("vcpu")) is not int or job["vcpu"] != 1:
            return response(400, {
                "message": "Invalid job configuration"
            })

        # 2. Lay danh sach task theo khoa ghep jobId + taskId
        tasks_result = tasks_table.query(
            KeyConditionExpression=Key("jobId").eq(job_id),
            ConsistentRead=True,
        )
        tasks = tasks_result.get("Items", [])

        if not tasks:
            return response(400, {"message": "Job has no tasks"})

        # 3. Kiem tra file da duoc upload len S3
        manifest_tasks = []

        for task in tasks:
            file_key = task.get("fileKey")
            prefix = f"submissions/{user_id}/{job_id}/"

            if not file_key or not file_key.startswith(prefix):
                return response(400, {
                    "message": "Invalid task file key"
                })

            try:
                s3.head_object(
                    Bucket=INPUT_BUCKET,
                    Key=file_key,
                )
            except ClientError as error:
                code = error.response.get("Error", {}).get("Code")

                if code in {"404", "NoSuchKey", "NotFound"}:
                    return response(400, {
                        "message": "One or more files have not been uploaded",
                        "taskId": task.get("taskId"),
                    })

                raise

            manifest_tasks.append({
                "taskId": task["taskId"],
                "fileKey": file_key,
            })

        # 4. Tao manifest dung duong dan submissions/userId/jobId
        manifest_key = (
            f"submissions/{user_id}/{job_id}/manifest.json"
        )

        manifest_data = {
            "jobId": job_id,
            "userId": user_id,
            "tier": job["tier"],
            "kind": job["kind"],
            "vcpu": job["vcpu"],
            "tasks": manifest_tasks,
            "status": "QUEUED",
        }

        # 5. Ghi manifest len S3
        s3.put_object(
            Bucket=INPUT_BUCKET,
            Key=manifest_key,
            Body=json.dumps(manifest_data).encode("utf-8"),
            ContentType="application/json",
        )

        # 6. Tru credit va chuyen job sang QUEUED
        try:
            dynamodb_client.transact_write_items(
                TransactItems=[
                    {
                        "Update": {
                            "TableName": USERS_TABLE,
                            "Key": {
                                "userId": {"S": user_id}
                            },
                            "UpdateExpression": (
                                "SET credit = credit - :cost"
                            ),
                            "ConditionExpression": (
                                "attribute_exists(userId) "
                                "AND credit >= :cost"
                            ),
                            "ExpressionAttributeValues": {
                                ":cost": {"N": str(CREDIT_COST)}
                            },
                        }
                    },
                    {
                        "Update": {
                            "TableName": JOBS_TABLE,
                            "Key": {
                                "jobId": {"S": job_id}
                            },
                            "UpdateExpression": (
                                "SET #status = :queued, "
                                "manifestKey = :manifest"
                            ),
                            "ConditionExpression": (
                                "#userId = :uid "
                                "AND #status = :uploading"
                            ),
                            "ExpressionAttributeNames": {
                                "#status": "status",
                                "#userId": "userId",
                            },
                            "ExpressionAttributeValues": {
                                ":queued": {"S": "QUEUED"},
                                ":uploading": {"S": "UPLOADING"},
                                ":uid": {"S": user_id},
                                ":manifest": {"S": manifest_key},
                            },
                        }
                    },
                ]
            )

        except dynamodb_client.exceptions.TransactionCanceledException:
            return response(409, {
                "message": (
                    "Unable to start job; check job status "
                    "and available credit"
                )
            })

        return response(200, {
            "message": "Job queued successfully",
            "jobId": job_id,
            "status": "QUEUED",
            "manifestKey": manifest_key,
        })

    except ClientError as error:
        print("AWS service error:", str(error))
        return response(500, {
            "message": "AWS service error"
        })

    except Exception as error:
        print("Internal error:", str(error))
        return response(500, {
            "message": "Internal server error"
        })