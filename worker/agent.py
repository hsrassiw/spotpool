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
    OUTCOME_INTERRUPTED, validate_message,
)
from worker.spot import SpotInterrupted, SpotMonitor

LOG_LIMIT = 10 * 1024 * 1024
VISIBILITY_SECONDS = 120
HEARTBEAT_SECONDS = 30
logger = logging.getLogger(__name__)


class LeaseLost(Exception):
    pass


class JobPaused(Exception):
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
    def __init__(self, *, dynamodb, s3, sqs, config, queue, autoscaling=None):
        self.db, self.s3, self.sqs = dynamodb, s3, sqs
        self.autoscaling = autoscaling
        self.instance_id = config.get("SPOTPOOL_INSTANCE_ID") or os.environ.get("SPOTPOOL_INSTANCE_ID")
        self.asg_name = config.get("SPOTPOOL_ASG") or os.environ.get("SPOTPOOL_ASG")
        self.config, self.queue = config, queue
        self.queue_url = config["SQS_SPOT_URL"] if queue == "spot" else config["SQS_ONDEMAND_URL"]
        self.owner_prefix = f'{os.environ.get("HOSTNAME", "worker")}-{uuid.uuid4()}'
        self._capacity = threading.Condition()
        self._protection_lock = threading.Lock()
        self._used_slots = 0
        self._draining = threading.Event()
        self.spot_monitor = None

    def request_interruption(self, notice):
        with self._capacity:
            self._draining.set()
            self._capacity.notify_all()
        logger.warning("Spot interruption: action=%s time=%s; stop accepting tasks",
                       notice["action"], notice["time"])

    def _check_interruption(self, transferred_bytes=None):
        if self._draining.is_set():
            raise SpotInterrupted("EC2 Spot interruption notice received")

    def _make_visible(self, received):
        self.sqs.change_message_visibility(
            QueueUrl=self.queue_url, ReceiptHandle=received["ReceiptHandle"], VisibilityTimeout=0,
        )

    def _reserve(self, vcpu):
        # Giữ thứ tự bật/tắt bảo vệ khi hai thread nhận và trả suất cùng lúc.
        with self._protection_lock:
            with self._capacity:
                if self._draining.is_set() or self._used_slots + vcpu > 2:
                    return False
                was_idle = self._used_slots == 0
                self._used_slots += vcpu
            if was_idle:
                self._set_instance_protection(True)
            return True

    def _release(self, vcpu):
        with self._protection_lock:
            with self._capacity:
                self._used_slots -= vcpu
                is_idle = self._used_slots == 0
                self._capacity.notify_all()
            if is_idle:
                self._set_instance_protection(False)

    def _set_instance_protection(self, protected):
        if self.autoscaling is None:
            return  # Cho phép chạy test local không có client AWS.
        if not self.instance_id or not self.asg_name:
            logger.warning("Cannot set scale-in protection: missing SPOTPOOL_INSTANCE_ID or SPOTPOOL_ASG")
            return
        try:
            self.autoscaling.set_instance_protection(
                InstanceIds=[self.instance_id], AutoScalingGroupName=self.asg_name,
                ProtectedFromScaleIn=protected,
            )
        except Exception:
            # Bảo vệ là best effort; lỗi AWS không được làm mất suất hoặc dừng worker.
            logger.exception("Cannot set scale-in protection: instanceId=%s asg=%s protected=%s",
                             self.instance_id, self.asg_name, protected)

    def run(self):
        if self.queue == "spot":
            self.spot_monitor = self.spot_monitor or SpotMonitor(self.request_interruption)
            self.spot_monitor.start()
        try:
            self._poll_tasks()
        finally:
            if self.spot_monitor is not None:
                self.spot_monitor.stop()
        if self._draining.is_set():
            self._wait_for_shutdown()

    def _wait_for_shutdown(self):
        logger.warning("Spot tasks drained; waiting for EC2 shutdown")
        # Không thoát để systemd Restart=always khởi động lại mỗi 10 giây.
        # Dùng Event mới: _draining đã set nên wait trên nó sẽ trả về ngay.
        threading.Event().wait()

    def _poll_tasks(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            while not self._draining.is_set():
                with self._capacity:
                    while self._used_slots >= 2 and not self._draining.is_set():
                        self._capacity.wait()
                    if self._draining.is_set():
                        break
                response = self.sqs.receive_message(
                    QueueUrl=self.queue_url, MaxNumberOfMessages=1,
                    WaitTimeSeconds=20, VisibilityTimeout=VISIBILITY_SECONDS,
                )
                if self._draining.is_set():
                    for received in response.get("Messages", []):
                        self._make_visible(received)
                    break
                for received in response.get("Messages", []):
                    try:
                        message = json.loads(received["Body"])
                        validate_message(message)
                    except (ValueError, KeyError, TypeError):
                        # Để message lỗi tự chuyển vào DLQ.
                        continue
                    vcpu = message["vcpu"]
                    if not self._reserve(vcpu):
                        self._make_visible(received)
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
        if self._draining.is_set():
            self._make_visible(received)
            return
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
        if job.get("status") == JOB_PAUSED:
            if self._pause(message, job):
                self._delete(received)
            return
        if job.get("status") in (JOB_SUCCEEDED, JOB_FAILED):
            self._delete(received)
            return
        allowance = remaining_seconds(job, message["vcpu"])
        if allowance == 0:
            if self._pause(message, job):
                self._delete(received)
            return
        owner = f"{self.owner_prefix}-{uuid.uuid4()}"
        now = int(time.time())
        tasks = self.db.Table(self.config["TASKS_TABLE"])
        # Notice có thể xuất hiện trong lúc đọc task/job ở phía trên.
        if self._draining.is_set():
            self._make_visible(received)
            return
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
            if current and current.get("status") == JOB_PAUSED:
                if self._pause(message, current, owner=owner):
                    self._delete(received)
                return
            if current and current.get("status") in (JOB_CANCELLED, JOB_SUCCEEDED, JOB_FAILED):
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

    def _pause(self, message, job, owner=None):
        job_values = {":paused": JOB_PAUSED}
        job_condition = "#s=:paused"
        if job["status"] != JOB_PAUSED:
            # Không tạm dừng từ ngân sách cũ nếu API vừa nạp thêm.
            job_condition = "#s IN (:queued,:running) AND costCap=:cap AND usedVcpuSeconds=:used"
            job_values.update({":queued": JOB_QUEUED, ":running": JOB_RUNNING,
                               ":cap": job["costCap"], ":used": job["usedVcpuSeconds"]})
        task_names = {"#s": "status"}
        task_values = {":paused": TASK_PAUSED, ":running": TASK_RUNNING,
                       ":fp": fingerprint(message)}
        if owner is None:
            task_condition = "fingerprint=:fp AND (#s=:queued OR (#s=:running AND leaseUntil<:now))"
            task_values.update({":queued": TASK_QUEUED, ":now": int(time.time())})
        else:
            task_condition = "fingerprint=:fp AND #s=:running AND #owner=:owner"
            task_names["#owner"] = "owner"
            task_values[":owner"] = owner
        try:
            self.db.meta.client.transact_write_items(TransactItems=[
                {"Update": {
                    "TableName": self.config["JOBS_TABLE"],
                    "Key": {"jobId": message["jobId"]},
                    "UpdateExpression": "SET #s=:paused",
                    "ConditionExpression": job_condition,
                    "ExpressionAttributeNames": {"#s": "status"},
                    "ExpressionAttributeValues": job_values,
                }},
                {"Update": {
                    "TableName": self.config["TASKS_TABLE"],
                    "Key": {"jobId": message["jobId"], "taskId": message["taskId"]},
                    "UpdateExpression": "SET #s=:paused",
                    "ConditionExpression": task_condition,
                    "ExpressionAttributeNames": task_names,
                    "ExpressionAttributeValues": task_values,
                }},
            ])
        except Exception as error:
            if "TransactionCanceledException" in str(error):
                # Job/task đã đổi hoặc giao dịch chưa ghi được: giữ message để retry.
                return False
            raise
        return True

    def _heartbeat(self, message, received, owner):
        now = int(time.time())
        job = self._job(message)
        if not job or job.get("status") in (JOB_CANCELLED, JOB_SUCCEEDED, JOB_FAILED):
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
        if job.get("status") == JOB_PAUSED:
            raise JobPaused("Job paused because its cost cap was reached")
        self._check_interruption()
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
            logfile.touch()
            proc, reader, start_monotonic = None, None, None
            try:
                self._check_interruption()
                with ThreadPoolExecutor(max_workers=1) as downloader:
                    download = downloader.submit(
                        self.s3.download_file, message["input"]["bucket"],
                        message["input"]["key"], str(source), Callback=self._check_interruption,
                    )
                    next_heartbeat = time.monotonic() + HEARTBEAT_SECONDS
                    while True:
                        try:
                            download.result(timeout=min(1, HEARTBEAT_SECONDS))
                            break
                        except FutureTimeout:
                            self._check_interruption()
                            if time.monotonic() >= next_heartbeat:
                                if not self._heartbeat(message, received, owner):
                                    raise LeaseLost
                                next_heartbeat = time.monotonic() + HEARTBEAT_SECONDS
                self._check_interruption()
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
                # Chỉ truyền chiều cao vào container video; container chạy script vẫn giữ nguyên tham số.
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
                next_heartbeat = start_monotonic + HEARTBEAT_SECONDS
                while proc.poll() is None:
                    self._check_interruption()
                    if time.monotonic() >= deadline:
                        if allowance <= task_limit:
                            status = TASK_PAUSED
                            error = "Cost cap reached"
                        else:
                            error = "Task time limit reached"
                        break
                    try:
                        proc.wait(timeout=min(1, max(0.1, deadline - time.monotonic())))
                    except subprocess.TimeoutExpired:
                        pass
                    if time.monotonic() >= next_heartbeat:
                        if not self._heartbeat(message, received, owner):
                            raise LeaseLost
                        next_heartbeat = time.monotonic() + HEARTBEAT_SECONDS
            except SpotInterrupted as exc:
                status, error = OUTCOME_INTERRUPTED, str(exc)
            except JobPaused as exc:
                status, error = TASK_PAUSED, str(exc)
            except LeaseLost:
                return
            except Exception as exc:
                error = str(exc)[:1000]
            finally:
                try:
                    if proc is not None and proc.poll() is None:
                        try:
                            subprocess.run(["docker", "rm", "-f", name], capture_output=True,
                                           timeout=15, check=True)
                        except subprocess.CalledProcessError:
                            # Container --rm có thể vừa tự kết thúc giữa poll() và rm.
                            if proc.poll() is None:
                                raise
                        proc.wait(timeout=15)
                finally:
                    if reader is not None:
                        reader.join(timeout=15)
                        if reader.is_alive():
                            raise RuntimeError("Container log reader did not stop")
                    if proc is not None and proc.stdout is not None:
                        proc.stdout.close()
                if start_monotonic is not None:
                    elapsed = max(0, math.ceil(time.monotonic() - start_monotonic))
            if status in (TASK_PAUSED, OUTCOME_INTERRUPTED):
                pass
            elif not error and proc is not None and proc.returncode == 0 and (outdir / "result").is_file():
                try:
                    self._check_interruption()
                    self.s3.upload_file(str(outdir / "result"), output_bucket, result_key,
                                        Callback=self._check_interruption)
                    status = TASK_SUCCEEDED
                except SpotInterrupted as exc:
                    status, error = OUTCOME_INTERRUPTED, str(exc)
            else:
                error = error or f"Container exit code {proc.returncode if proc else 'unknown'}; result file missing or invalid"
            if error and logfile.stat().st_size == 0:
                logfile.write_text(error, encoding="utf-8")
            self.s3.upload_file(str(logfile), output_bucket, log_key)
        self._finish(message, received, owner, attempt, start, elapsed, status,
                     result_key if status == TASK_SUCCEEDED else None, log_key, error[:1000])

    def _finish(self, message, received, owner, attempt, started_at, elapsed, status,
                result_key, log_key, error):
        interrupted = status == OUTCOME_INTERRUPTED
        target_status = TASK_QUEUED if interrupted else status
        interrupt_count, target_queue = None, None
        if interrupted:
            current = self._task(message)
            if (not current or current.get("owner") != owner
                    or int(current.get("attempt", 0)) != attempt):
                raise LeaseLost("Cannot record interruption after losing task ownership")
            if current.get("status") == TASK_QUEUED and int(current.get("interruptCount", 0)) > 0:
                # Giao dịch đã ghi, nhưng lần trước có thể chưa gửi/xóa SQS xong.
                usage = self.db.Table(self.config["USAGE_TABLE"]).get_item(
                    Key={"userId": message["userId"],
                         "usageId": f'{message["jobId"]}#{message["taskId"]}#{attempt}'},
                    ConsistentRead=True,
                ).get("Item")
                if usage and usage.get("outcome") == OUTCOME_INTERRUPTED:
                    self._return_interrupted(received, current["queue"])
                    return
            if current.get("status") != TASK_RUNNING:
                raise LeaseLost("Task is no longer running")
            interrupt_count = int(current.get("interruptCount", 0)) + 1
            target_queue = "ondemand" if interrupt_count > 2 else "spot"
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
        task_values = {":status": target_status, ":running": TASK_RUNNING,
                       ":owner": owner, ":now": finished_at,
                       ":log": log_key, ":error": error}
        task_update = "SET #s=:status, finishedAt=:now, logKey=:log, #error=:error"
        task_names = {"#s": "status", "#owner": "owner", "#error": "error"}
        if interrupted:
            task_update += ", interruptCount=:interrupts, #queue=:queue"
            task_values.update({":interrupts": interrupt_count, ":queue": target_queue})
            task_names["#queue"] = "queue"
        if result_key:
            task_values[":result"] = result_key
            task_update += ", resultKey=:result"
        job_values = {":used": 0 if interrupted else vcpu_seconds, ":paused": JOB_PAUSED,
                      ":running": JOB_RUNNING, ":queued": JOB_QUEUED}
        job_update = "ADD usedVcpuSeconds :used"
        if status == TASK_PAUSED:
            job_update = "SET #s=:paused ADD usedVcpuSeconds :used"
        elif not interrupted:
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
                                "ExpressionAttributeNames": task_names,
                                "ExpressionAttributeValues": task_values}},
                    {"Update": {"TableName": self.config["JOBS_TABLE"],
                                "Key": {"jobId": message["jobId"]},
                                "UpdateExpression": job_update,
                                "ConditionExpression": "#s IN (:queued,:running,:paused) AND tasksRemaining > :zero",
                                "ExpressionAttributeNames": {"#s": "status"},
                                "ExpressionAttributeValues": {**job_values, ":zero": 0}}},
                    {"Put": {"TableName": self.config["USAGE_TABLE"], "Item": usage,
                             "ConditionExpression": "attribute_not_exists(userId) AND attribute_not_exists(usageId)"}},
                ],
            )
        except Exception:
            latest = self._task(message)
            if (not latest or latest.get("owner") != owner or latest.get("status") != target_status
                    or interrupted and (latest.get("interruptCount") != interrupt_count
                                        or latest.get("queue") != target_queue)):
                raise
        if interrupted:
            self._return_interrupted(received, target_queue)
            return
        if status != TASK_PAUSED:
            self._finish_job(message)
        self._delete(received)

    def _return_interrupted(self, received, target_queue):
        if target_queue == "ondemand":
            # Gửi nguyên văn để tier/fingerprint vẫn là gói Spot đã mua.
            self.sqs.send_message(QueueUrl=self.config["SQS_ONDEMAND_URL"], MessageBody=received["Body"])
            self._delete(received)
        else:
            self._make_visible(received)

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
    from botocore.config import Config

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
    aws_config = Config(connect_timeout=3, read_timeout=5,
                        retries={"mode": "standard", "total_max_attempts": 2})
    Worker(dynamodb=boto3.resource("dynamodb", region_name=region, config=aws_config),
           s3=boto3.client("s3", region_name=region, config=aws_config),
           sqs=boto3.client("sqs", region_name=region, config=aws_config.merge(Config(read_timeout=30))),
           autoscaling=boto3.client("autoscaling", region_name=region, config=aws_config),
           config=config, queue=queue).run()


if __name__ == "__main__":
    main()
