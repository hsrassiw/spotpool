"""Worker EC2 nhận task, chạy Docker và ghi kết quả."""

import json
import logging
import math
import os
import subprocess
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path

from spotpool_core.contracts import (
    JOB_CANCELLED, JOB_FAILED, JOB_PAUSED, JOB_QUEUED, JOB_RUNNING,
    JOB_SUCCEEDED, TASK_CANCELLED, TASK_FAILED, TASK_FINAL, TASK_PAUSED,
    TASK_QUEUED, TASK_RUNNING, TASK_SUCCEEDED, IMAGES, fingerprint,
    validate_message,
)

LOG_LIMIT = 10 * 1024 * 1024
VISIBILITY_SECONDS = 120
HEARTBEAT_SECONDS = 30
logger = logging.getLogger(__name__)


class LeaseLost(Exception):
    pass


def remaining_seconds(job, vcpu):
    price = int(job["unitPrice"])
    if price <= 0:
        raise ValueError("unitPrice must be positive")
    budget = int(job["costCap"]) * 3600 // price - int(job["usedVcpuSeconds"])
    return max(0, budget // vcpu)


def docker_command(image_uri, vcpu, source, output, name, video_height=None):
    memory = f"{vcpu * 1536}m"
    command = [
        "docker", "run", "--rm", "--name", name, "--network", "none",
        "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--pids-limit", "64", "--user", "65534:65534", "--cpus", str(vcpu),
        "--memory", memory, "--memory-swap", memory,
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        "--mount", f"type=bind,source={source},target=/input/payload,readonly",
        "--mount", f"type=bind,source={output},target=/output",
    ]
    if video_height is not None:
        command.extend(["--env", f"SPOTPOOL_VIDEO_HEIGHT={video_height}"])
    command.extend([image_uri, "/input/payload", "/output/result"])
    return command


class Worker:
    def __init__(self, *, dynamodb, s3, sqs, config, queue):
        self.db, self.s3, self.sqs = dynamodb, s3, sqs
        self.config, self.queue = config, queue
        self.queue_url = config["SQS_SPOT_URL"] if queue == "spot" else config["SQS_ONDEMAND_URL"]
        self.owner_prefix = f'{os.environ.get("HOSTNAME", "worker")}-{uuid.uuid4()}'
        self._capacity = threading.Condition()
        self._used_slots = 0

    def _reserve(self, vcpu):
        with self._capacity:
            if self._used_slots + vcpu > 2:
                return False
            self._used_slots += vcpu
            return True

    def _release(self, vcpu):
        with self._capacity:
            self._used_slots -= vcpu
            self._capacity.notify_all()

    def run(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            while True:
                with self._capacity:
                    while self._used_slots >= 2:
                        self._capacity.wait()
                response = self.sqs.receive_message(
                    QueueUrl=self.queue_url, MaxNumberOfMessages=1,
                    WaitTimeSeconds=20, VisibilityTimeout=VISIBILITY_SECONDS,
                )
                for received in response.get("Messages", []):
                    try:
                        message = json.loads(received["Body"])
                        validate_message(message)
                    except (ValueError, KeyError, TypeError):
                        # Để message lỗi tự chuyển vào DLQ.
                        continue
                    vcpu = message["vcpu"]
                    if not self._reserve(vcpu):
                        self.sqs.change_message_visibility(
                            QueueUrl=self.queue_url, ReceiptHandle=received["ReceiptHandle"],
                            VisibilityTimeout=0,
                        )
                        time.sleep(1)
                        continue
                    pool.submit(self._process_reserved, received, message, vcpu)

    def _process_reserved(self, received, message, vcpu):
        try:
            self.process(received, message)
        except Exception:
            logger.exception(
                "Task processing failed: jobId=%s taskId=%s",
                message.get("jobId"), message.get("taskId"),
            )
            raise
        finally:
            self._release(vcpu)

    def _delete(self, received):
        self.sqs.delete_message(QueueUrl=self.queue_url, ReceiptHandle=received["ReceiptHandle"])

    def _task(self, message):
        return self.db.Table(self.config["TASKS_TABLE"]).get_item(
            Key={"jobId": message["jobId"], "taskId": message["taskId"]},
            ConsistentRead=True,
        ).get("Item")

    def _job(self, message):
        return self.db.Table(self.config["JOBS_TABLE"]).get_item(
            Key={"jobId": message["jobId"]}, ConsistentRead=True,
        ).get("Item")

    def process(self, received, message):
        task, job = self._task(message), self._job(message)
        if not task or not job:
            return  # API hoặc dispatcher có thể chưa ghi xong; SQS sẽ thử lại.
        if task.get("fingerprint") != fingerprint(message) or job.get("userId") != message["userId"]:
            raise ValueError("Task fingerprint or ownership mismatch")
        if task.get("status") in TASK_FINAL | {TASK_PAUSED}:
            self._delete(received)
            return
        if task.get("queue") != self.queue:
            target = self.config["SQS_ONDEMAND_URL"] if task["queue"] == "ondemand" else self.config["SQS_SPOT_URL"]
            self.sqs.send_message(QueueUrl=target, MessageBody=received["Body"])
            self._delete(received)
            return
        if job.get("status") == JOB_CANCELLED:
            self._set_task_state(message, TASK_CANCELLED)
            self._delete(received)
            return
        if job.get("status") in (JOB_PAUSED, JOB_SUCCEEDED, JOB_FAILED):
            self._delete(received)
            return
        allowance = remaining_seconds(job, message["vcpu"])
        if allowance == 0:
            self._pause(message)
            self._delete(received)
            return
        owner = f"{self.owner_prefix}-{uuid.uuid4()}"
        now = int(time.time())
        tasks = self.db.Table(self.config["TASKS_TABLE"])
        try:
            claimed = tasks.update_item(
                Key={"jobId": message["jobId"], "taskId": message["taskId"]},
                UpdateExpression="SET #s=:running, #owner=:owner, leaseUntil=:lease, heartbeatAt=:now, startedAt=if_not_exists(startedAt,:now) ADD attempt :one",
                ConditionExpression="fingerprint=:fp AND (#s=:queued OR (#s=:running AND leaseUntil<:now))",
                ExpressionAttributeNames={"#s": "status", "#owner": "owner"},
                ExpressionAttributeValues={":running": TASK_RUNNING, ":queued": TASK_QUEUED,
                                           ":owner": owner, ":lease": now + VISIBILITY_SECONDS,
                                           ":now": now, ":one": 1, ":fp": fingerprint(message)},
                ReturnValues="ALL_NEW",
            )["Attributes"]
        except Exception as error:
            if "ConditionalCheckFailedException" not in str(error):
                raise
            latest = self._task(message)
            if latest and latest.get("status") in TASK_FINAL | {TASK_PAUSED}:
                self._delete(received)
            return
        try:
            self.db.Table(self.config["JOBS_TABLE"]).update_item(
                Key={"jobId": message["jobId"]},
                UpdateExpression="SET #s=:running, startedAt=if_not_exists(startedAt,:now)",
                ConditionExpression="#s=:queued",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":running": JOB_RUNNING, ":queued": JOB_QUEUED, ":now": now},
            )
        except Exception as error:
            if "ConditionalCheckFailedException" not in str(error):
                raise
            current = self._job(message)
            if current and current.get("status") in (JOB_CANCELLED, JOB_PAUSED, JOB_SUCCEEDED, JOB_FAILED):
                target = TASK_CANCELLED if current["status"] == JOB_CANCELLED else TASK_PAUSED
                try:
                    tasks.update_item(
                        Key={"jobId": message["jobId"], "taskId": message["taskId"]},
                        UpdateExpression="SET #s=:target",
                        ConditionExpression="#s=:running AND #owner=:owner",
                        ExpressionAttributeNames={"#s": "status", "#owner": "owner"},
                        ExpressionAttributeValues={":target": target, ":running": TASK_RUNNING,
                                                   ":owner": owner},
                    )
                except Exception as update_error:
                    if "ConditionalCheckFailedException" not in str(update_error):
                        raise
                self._delete(received)
                return
        self._execute(received, message, job, owner, int(claimed["attempt"]), allowance)

    def _set_task_state(self, message, state):
        try:
            self.db.Table(self.config["TASKS_TABLE"]).update_item(
                Key={"jobId": message["jobId"], "taskId": message["taskId"]},
                UpdateExpression="SET #s=:state",
                ConditionExpression="#s=:queued",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":state": state, ":queued": TASK_QUEUED},
            )
        except Exception as error:
            if "ConditionalCheckFailedException" not in str(error):
                raise

    def _pause(self, message):
        jobs = self.db.Table(self.config["JOBS_TABLE"])
        try:
            jobs.update_item(
                Key={"jobId": message["jobId"]},
                UpdateExpression="SET #s=:paused",
                ConditionExpression="#s IN (:queued,:running)",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":paused": JOB_PAUSED, ":queued": JOB_QUEUED,
                                           ":running": JOB_RUNNING},
            )
        except Exception as error:
            if "ConditionalCheckFailedException" not in str(error):
                raise
        self._set_task_state(message, TASK_PAUSED)

    def _heartbeat(self, message, received, owner):
        now = int(time.time())
        job = self._job(message)
        if not job or job.get("status") in (JOB_CANCELLED, JOB_PAUSED):
            return False
        try:
            self.db.Table(self.config["TASKS_TABLE"]).update_item(
                Key={"jobId": message["jobId"], "taskId": message["taskId"]},
                UpdateExpression="SET leaseUntil=:lease, heartbeatAt=:now",
                ConditionExpression="#s=:running AND #owner=:owner",
                ExpressionAttributeNames={"#s": "status", "#owner": "owner"},
                ExpressionAttributeValues={":running": TASK_RUNNING, ":owner": owner,
                                           ":lease": now + VISIBILITY_SECONDS, ":now": now},
            )
        except Exception as error:
            if "ConditionalCheckFailedException" in str(error):
                return False
            raise
        self.sqs.change_message_visibility(
            QueueUrl=self.queue_url, ReceiptHandle=received["ReceiptHandle"],
            VisibilityTimeout=VISIBILITY_SECONDS,
        )
        return True

    def _execute(self, received, message, job, owner, attempt, allowance):
        start = int(time.time())
        result_key = f'{message["output"]["key"]}/{attempt}/result'
        log_key = f'{message["output"]["key"]}/{attempt}/log.txt'
        output_bucket = message["output"]["bucket"]
        status, error, elapsed = TASK_FAILED, "", 0
        with tempfile.TemporaryDirectory(prefix="spotpool-") as folder:
            root = Path(folder)
            root.chmod(0o755)
            source, outdir, logfile = root / "payload", root / "output", root / "log.txt"
            outdir.mkdir(mode=0o777)
            outdir.chmod(0o777)
            try:
                with ThreadPoolExecutor(max_workers=1) as downloader:
                    download = downloader.submit(
                        self.s3.download_file, message["input"]["bucket"],
                        message["input"]["key"], str(source),
                    )
                    while True:
                        try:
                            download.result(timeout=HEARTBEAT_SECONDS)
                            break
                        except FutureTimeout:
                            if not self._heartbeat(message, received, owner):
                                raise LeaseLost
                source.chmod(0o644)
                image_uri = self.config["VIDEO_JOB_IMAGE"] if message["image"] == IMAGES["video"] else self.config["SCRIPT_JOB_IMAGE"]
                name = f"spotpool-{uuid.uuid4().hex}"
                video_height = None
                if message["image"] == IMAGES["video"]:
                    video_height = message["params"].get("height", 720)
                    if type(video_height) is not int or not 2 <= video_height <= 4320 or video_height % 2:
                        raise ValueError("Video height must be an even integer between 2 and 4320")
                command = docker_command(
                    image_uri, message["vcpu"], source, outdir, name, video_height,
                )
                start = int(time.time())
                start_monotonic = time.monotonic()
                proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                def copy_log():
                    with logfile.open("wb") as log:
                        while chunk := proc.stdout.read(65536):
                            remaining = LOG_LIMIT - log.tell()
                            if remaining > 0:
                                log.write(chunk[:remaining])
                reader = threading.Thread(target=copy_log, daemon=True)
                reader.start()
                task_limit = int(self.config["TASK_MAX_SECONDS"])
                if task_limit <= 0:
                    raise ValueError("TASK_MAX_SECONDS must be positive")
                deadline = start_monotonic + min(task_limit, allowance)
                lost_lease = False
                try:
                    while proc.poll() is None:
                        if time.monotonic() >= deadline:
                            if allowance <= task_limit:
                                status = TASK_PAUSED
                                error = "Cost cap reached"
                            else:
                                error = "Task time limit reached"
                            break
                        try:
                            proc.wait(timeout=min(HEARTBEAT_SECONDS, max(0.1, deadline - time.monotonic())))
                        except subprocess.TimeoutExpired:
                            if not self._heartbeat(message, received, owner):
                                lost_lease = True
                                break
                    if proc.poll() is None:
                        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15, check=False)
                        proc.wait(timeout=15)
                finally:
                    reader.join(timeout=15)
                if lost_lease:
                    return
                elapsed = max(0, math.ceil(time.monotonic() - start_monotonic))
                if status == TASK_PAUSED:
                    pass
                elif proc.returncode == 0 and (outdir / "result").is_file():
                    self.s3.upload_file(str(outdir / "result"), output_bucket, result_key)
                    status = TASK_SUCCEEDED
                else:
                    error = error or f"Container exit code {proc.returncode}; result file missing or invalid"
            except LeaseLost:
                return
            except Exception as exc:
                error = str(exc)[:1000]
                if not logfile.exists():
                    logfile.write_text(error, encoding="utf-8")
            self.s3.upload_file(str(logfile), output_bucket, log_key)
        self._finish(message, received, owner, attempt, start, elapsed, status,
                     result_key if status == TASK_SUCCEEDED else None, log_key, error[:1000])

    def _finish(self, message, received, owner, attempt, started_at, elapsed, status,
                result_key, log_key, error):
        finished_at = int(time.time())
        vcpu_seconds = message["vcpu"] * elapsed
        usage = {
            "userId": message["userId"],
            "usageId": f'{message["jobId"]}#{message["taskId"]}#{attempt}',
            "jobId": message["jobId"], "taskId": message["taskId"],
            "attempt": attempt, "tier": message["tier"], "vcpu": message["vcpu"],
            "outcome": status, "vcpuSeconds": vcpu_seconds,
            "startedAt": started_at, "finishedAt": finished_at,
            "day": time.strftime("%Y-%m-%d", time.gmtime(finished_at + 7 * 3600)),
        }
        # Ghi thông tin EC2 nếu môi trường cung cấp.
        for field, env in (("lifecycle", "SPOTPOOL_LIFECYCLE"), ("instanceType", "SPOTPOOL_INSTANCE_TYPE"),
                           ("az", "SPOTPOOL_AZ"), ("instanceId", "SPOTPOOL_INSTANCE_ID")):
            if os.environ.get(env):
                usage[field] = os.environ[env]
        task_values = {":status": status, ":running": TASK_RUNNING,
                       ":owner": owner, ":now": finished_at,
                       ":log": log_key, ":error": error}
        task_update = "SET #s=:status, finishedAt=:now, logKey=:log, #error=:error"
        if result_key:
            task_values[":result"] = result_key
            task_update += ", resultKey=:result"
        job_values = {":used": vcpu_seconds,
                      ":running": JOB_RUNNING, ":queued": JOB_QUEUED}
        job_update = "ADD usedVcpuSeconds :used"
        if status == TASK_PAUSED:
            job_update = "SET #s=:paused ADD usedVcpuSeconds :used"
            job_values[":paused"] = JOB_PAUSED
        else:
            job_values[":one"] = 1
            job_update += ", tasksRemaining :minusOne, "
            job_update += "tasksSucceeded :one" if status == TASK_SUCCEEDED else "tasksFailed :one"
            job_values[":minusOne"] = -1
        token = str(uuid.uuid5(uuid.NAMESPACE_URL, usage["usageId"] + owner))
        try:
            self.db.meta.client.transact_write_items(
                ClientRequestToken=token,
                TransactItems=[
                    {"Update": {"TableName": self.config["TASKS_TABLE"],
                                "Key": {"jobId": message["jobId"], "taskId": message["taskId"]},
                                "UpdateExpression": task_update,
                                "ConditionExpression": "#owner=:owner AND #s=:running",
                                "ExpressionAttributeNames": {"#s": "status", "#owner": "owner", "#error": "error"},
                                "ExpressionAttributeValues": task_values}},
                    {"Update": {"TableName": self.config["JOBS_TABLE"],
                                "Key": {"jobId": message["jobId"]},
                                "UpdateExpression": job_update,
                                "ConditionExpression": "#s IN (:queued,:running) AND tasksRemaining > :zero",
                                "ExpressionAttributeNames": {"#s": "status"},
                                "ExpressionAttributeValues": {**job_values, ":zero": 0}}},
                    {"Put": {"TableName": self.config["USAGE_TABLE"], "Item": usage,
                             "ConditionExpression": "attribute_not_exists(userId) AND attribute_not_exists(usageId)"}},
                ],
            )
        except Exception:
            latest = self._task(message)
            if not latest or latest.get("owner") != owner or latest.get("status") != status:
                raise
        if status != TASK_PAUSED:
            self._finish_job(message)
        self._delete(received)

    def _finish_job(self, message):
        jobs = self.db.Table(self.config["JOBS_TABLE"])
        for target, failed_condition in ((JOB_SUCCEEDED, "tasksFailed=:zero"),
                                         (JOB_FAILED, "tasksFailed>:zero")):
            try:
                jobs.update_item(
                    Key={"jobId": message["jobId"]},
                    UpdateExpression="SET #s=:target, finishedAt=:now",
                    ConditionExpression=f"#s IN (:queued,:running) AND tasksRemaining=:zero AND {failed_condition}",
                    ExpressionAttributeNames={"#s": "status"},
                    ExpressionAttributeValues={":target": target, ":queued": JOB_QUEUED,
                                               ":running": JOB_RUNNING, ":zero": 0,
                                               ":now": int(time.time())},
                )
                return
            except Exception as error:
                if "ConditionalCheckFailedException" not in str(error):
                    raise


def main():
    import boto3

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    names = ("JOBS_TABLE", "TASKS_TABLE", "USAGE_TABLE", "SQS_ONDEMAND_URL", "SQS_SPOT_URL",
             "VIDEO_JOB_IMAGE", "SCRIPT_JOB_IMAGE", "TASK_MAX_SECONDS", "AWS_DEFAULT_REGION")
    config = {key: os.environ[key] for key in names}
    queue = os.environ["SPOTPOOL_QUEUE"]
    if queue not in ("ondemand", "spot"):
        raise ValueError("SPOTPOOL_QUEUE must be ondemand or spot")
    region = config["AWS_DEFAULT_REGION"]
    Worker(dynamodb=boto3.resource("dynamodb", region_name=region),
           s3=boto3.client("s3", region_name=region),
           sqs=boto3.client("sqs", region_name=region), config=config, queue=queue).run()


if __name__ == "__main__":
    main()
