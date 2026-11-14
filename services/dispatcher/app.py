"""Tạo một task và message SQS cho mỗi file trong manifest."""

import json
import logging
import os
import re
import time
from decimal import Decimal
from urllib.parse import unquote_plus

from spotpool_core.contracts import (
    IMAGES, JOB_QUEUED, JOB_RUNNING, SCHEMA_VERSION, TASK_QUEUED,
    TIERS, UUID_RE, canonical_json, fingerprint, validate_message,
)

MANIFEST_KEY_RE = re.compile(
    r"submissions/([0-9a-f-]+)/([0-9a-f-]+)/manifest\.json\Z", re.I
)
logger = logging.getLogger(__name__)


def start_first_instance(autoscaling, asg_name):
    """Nhóm đang 0 máy thì bật 1 máy ngay (lấy từ warm pool), không chờ alarm CloudWatch 2–3 phút.

    Lỗi chỉ ghi log: task đã vào hàng đợi, alarm bật máy từ 0 vẫn là đường dự phòng.
    """
    try:
        groups = autoscaling.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])["AutoScalingGroups"]
        if not groups or groups[0]["DesiredCapacity"] > 0 or groups[0]["MaxSize"] < 1:
            return False
        autoscaling.set_desired_capacity(AutoScalingGroupName=asg_name, DesiredCapacity=1, HonorCooldown=False)
        logger.info("Started first instance of %s", asg_name)
        return True
    except Exception:
        logger.exception("Cannot start first instance of %s; CloudWatch alarm will start it", asg_name)
        return False


def _manifest_messages(manifest, key, input_bucket, output_bucket):
    match = MANIFEST_KEY_RE.fullmatch(key)
    if not match or not all(UUID_RE.fullmatch(v) for v in match.groups()):
        raise ValueError("Invalid manifest key")
    expected = {"schemaVersion", "jobId", "userId", "tier", "kind", "vcpu", "image", "params", "outputBucket", "inputs"}
    if not isinstance(manifest, dict) or set(manifest) != expected:
        raise ValueError("Invalid manifest fields")
    user_id, job_id = match.groups()
    if (manifest["schemaVersion"] != SCHEMA_VERSION or manifest["userId"] != user_id
            or manifest["jobId"] != job_id or manifest["tier"] not in TIERS
            or manifest["image"] != IMAGES.get(manifest["kind"])
            or manifest["outputBucket"] != output_bucket
            or not isinstance(manifest["params"], dict)):
        raise ValueError("Manifest does not match its key or deployment")
    inputs = manifest["inputs"]
    if not isinstance(inputs, list) or not 1 <= len(inputs) <= 100:
        raise ValueError("Manifest must contain 1 to 100 inputs")
    messages = []
    for index, item in enumerate(inputs, 1):
        task_id = f"task-{index:04d}"
        if (not isinstance(item, dict) or set(item) != {"taskId", "fileName", "bucket", "key"}
                or item["taskId"] != task_id or not isinstance(item["fileName"], str)
                or not item["fileName"] or item["bucket"] != input_bucket):
            raise ValueError(f"Invalid input {task_id}")
        message = {
            "schemaVersion": SCHEMA_VERSION, "jobId": job_id, "taskId": task_id,
            "userId": user_id, "tier": manifest["tier"], "kind": manifest["kind"],
            "vcpu": manifest["vcpu"], "image": manifest["image"],
            "params": manifest["params"],
            "input": {"bucket": item["bucket"], "key": item["key"]},
            "output": {"bucket": output_bucket, "key": f"results/{user_id}/{job_id}/{task_id}"},
        }
        validate_message(message)
        messages.append((message, item["fileName"]))
    return messages


def dispatch_record(record, *, s3, dynamodb, sqs, config, now=None, autoscaling=None):
    bucket = record["s3"]["bucket"]["name"]
    key = unquote_plus(record["s3"]["object"]["key"])
    if bucket != config["INPUT_BUCKET"]:
        raise ValueError("Unexpected S3 bucket")
    if not key.startswith("submissions/"):
        return 0
    response = s3.get_object(Bucket=bucket, Key=key)
    manifest = json.loads(response["Body"].read())
    entries = _manifest_messages(manifest, key, bucket, config["OUTPUT_BUCKET"])
    job = dynamodb.Table(config["JOBS_TABLE"]).get_item(
        Key={"jobId": manifest["jobId"]}, ConsistentRead=True
    ).get("Item")
    if not job or job.get("userId") != manifest["userId"]:
        raise ValueError("Manifest has no matching job")
    if job.get("status") not in (JOB_QUEUED, JOB_RUNNING):
        return 0
    if (job.get("tier") != manifest["tier"] or job.get("kind") != manifest["kind"]
            or job.get("vcpu") != manifest["vcpu"] or job.get("image") != manifest["image"]
            or job.get("params") != json.loads(json.dumps(manifest["params"]), parse_float=Decimal)
            or job.get("files") != [file_name for _, file_name in entries]
            or job.get("taskCount") != len(entries)):
        raise ValueError("Manifest differs from job")
    tasks = dynamodb.Table(config["TASKS_TABLE"])
    queue_url = config["SQS_ONDEMAND_URL"] if manifest["tier"] == "ondemand" else config["SQS_SPOT_URL"]
    timestamp = int(time.time()) if now is None else now
    for message, file_name in entries:
        digest = fingerprint(message)
        item = {**message, "fileName": file_name, "fingerprint": digest,
                "status": TASK_QUEUED, "queue": manifest["tier"],
                "interruptCount": 0, "attempt": 0, "queuedAt": timestamp}
        item = json.loads(json.dumps(item), parse_float=Decimal)
        try:
            tasks.put_item(Item=item, ConditionExpression="attribute_not_exists(jobId) AND attribute_not_exists(taskId)")
        except Exception as error:
            if "ConditionalCheckFailedException" not in str(error):
                raise
            existing = tasks.get_item(
                Key={"jobId": message["jobId"], "taskId": message["taskId"]},
                ConsistentRead=True,
            ).get("Item")
            if not existing or existing.get("fingerprint") != digest:
                raise ValueError("Existing task conflicts with manifest") from error
        # Gửi lại để khôi phục nếu lần trước dừng sau PutItem.
        sqs.send_message(QueueUrl=queue_url, MessageBody=canonical_json(message))
    # Gói Ổn định dùng warm pool: bật máy sau khi task đã vào hàng đợi
    if manifest["tier"] == "ondemand" and autoscaling is not None and config.get("ONDEMAND_ASG"):
        start_first_instance(autoscaling, config["ONDEMAND_ASG"])
    return len(entries)


def lambda_handler(event, context):
    import boto3

    logging.getLogger().setLevel(logging.INFO)
    config = {key: os.environ[key] for key in (
        "INPUT_BUCKET", "OUTPUT_BUCKET", "JOBS_TABLE", "TASKS_TABLE",
        "SQS_ONDEMAND_URL", "SQS_SPOT_URL",
    )}
    config["ONDEMAND_ASG"] = os.environ.get("ONDEMAND_ASG", "")
    s3, dynamodb, sqs = boto3.client("s3"), boto3.resource("dynamodb"), boto3.client("sqs")
    autoscaling = boto3.client("autoscaling") if config["ONDEMAND_ASG"] else None
    return {"dispatched": sum(dispatch_record(record, s3=s3, dynamodb=dynamodb,
                                               sqs=sqs, config=config, autoscaling=autoscaling)
                              for record in event["Records"])}
