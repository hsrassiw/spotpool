"""Worker EC2 nhận task, chạy Docker và ghi kết quả."""

import json
import logging
import math
import os
import random
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
from worker import checkpoint
from worker.checkpoint import open_regular_file as open_result_file
from worker.spot import SpotInterrupted, SpotMonitor

LOG_LIMIT = 10 * 1024 * 1024
CHECKPOINT_TARGET = "/checkpoint"
VISIBILITY_SECONDS = 120
HEARTBEAT_SECONDS = 30
# Mỗi máy chạy 2 suất trên cùng ổ đĩa (8 GB, còn khoảng 5 GB trống). Giới hạn mỗi task để
# một task không làm đầy ổ và làm hỏng task của người khác: 2 × (500 MB + 1,5 GB) = 4 GB.
MAX_INPUT_BYTES = 500 * 1024 * 1024  # khớp services/api/start_job.py
MAX_WRITE_BYTES = 1536 * 1024 * 1024  # tổng /output và /checkpoint của một task
MAX_WRITE_ENTRIES = 20000
logger = logging.getLogger(__name__)


class LeaseLost(Exception):
    pass


class DiskQuotaExceeded(Exception):
    pass


def written_bytes(*folders):
    """Tổng dung lượng file trong các thư mục task ghi được, không đi theo symlink."""
    total, entries = 0, 0
    for folder in folders:
        if folder is None:
            continue
        stack = [str(folder)]
        while stack:
            try:
                items = os.scandir(stack.pop())
            except FileNotFoundError:
                continue  # Container vừa xóa thư mục này.
            with items:
                for item in items:
                    entries += 1
                    if entries > MAX_WRITE_ENTRIES:
                        raise DiskQuotaExceeded(
                            f"Task tạo quá {MAX_WRITE_ENTRIES} file và thư mục trong /output và /checkpoint")
                    try:
                        if item.is_dir(follow_symlinks=False):
                            stack.append(item.path)
                        elif item.is_file(follow_symlinks=False):
                            total += item.stat(follow_symlinks=False).st_size
                    except FileNotFoundError:
                        continue
    return total


def check_written(*folders):
    if written_bytes(*folders) > MAX_WRITE_BYTES:
        raise DiskQuotaExceeded(
            f"Task ghi quá {MAX_WRITE_BYTES // (1024 * 1024)} MB vào /output và /checkpoint")


class JobPaused(Exception):
    pass


class JobCancelled(Exception):
    pass


def retry_conflict(call, attempts=5):
    """Hai task cùng job xong cùng lúc thì cùng ghi item Job, DynamoDB hủy một bên
    với TransactionConflict. Giao dịch bị hủy chưa ghi gì nên thử lại là an toàn."""
    for attempt in range(attempts):
        try:
            return call()
        except Exception as error:
            text = str(error)
            if attempt == attempts - 1 or "TransactionConflict" not in text:
                raise
            time.sleep(random.uniform(0.05, 0.25) * (attempt + 1))


def remaining_seconds(job, vcpu):
    price = int(job["unitPrice"])
    if price <= 0:
        raise ValueError("unitPrice must be positive")
    budget = int(job["costCap"]) * 3600 // price - int(job["usedVcpuSeconds"])
    return max(0, budget // vcpu)


def checkpoint_key(message, attempt):
    """Mỗi lượt chạy ghi checkpoint vào key riêng, giống result và log.

    Worker cũ mất quyền giữ task vẫn có thể ghi nốt vào key của lượt mình, nhưng
    không ghi được Tasks.checkpointAttempt nên lượt sau không đọc nhầm."""
    return f'{message["output"]["key"]}/{attempt}/checkpoint.tgz'


def docker_command(image_uri, vcpu, source, output, name, video_height=None, checkpoint_dir=None):
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
    if checkpoint_dir is not None:
        command.extend(["--mount", f"type=bind,source={checkpoint_dir},target={CHECKPOINT_TARGET}",
                        "--env", f"SPOTPOOL_CHECKPOINT_DIR={CHECKPOINT_TARGET}"])
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
        # Process cũ có thể chết trước khi trả suất/tắt bảo vệ. Chưa nhận task mới.
        self._set_instance_protection(False)
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
            if task.get('status') == TASK_RUNNING:
                if int(task.get('leaseUntil', 0)) >= int(time.time()):
                    return
                # Worker bị lỗi không thể báo thời gian đã chạy. Kết thúc lượt thử còn bỏ dở với chi phí bằng 0.
                self._finish(message, received, task['owner'], int(task['attempt']),
                             int(task.get('startedAt', time.time())), 0, TASK_CANCELLED, None,
                             task.get('logKey', ''), 'Cancelled after worker lease expired')
                return
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
            retry_conflict(lambda: self.db.Table(self.config["JOBS_TABLE"]).update_item(
                Key={"jobId": message["jobId"]},
                UpdateExpression="SET #s=:running, startedAt=if_not_exists(startedAt,:now)",
                ConditionExpression="#s=:queued",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":running": JOB_RUNNING, ":queued": JOB_QUEUED, ":now": now},
            ))
        except Exception as error:
            if "ConditionalCheckFailedException" not in str(error):
                raise
            current = self._job(message)
            if current and current.get("status") == JOB_PAUSED:
                if self._pause(message, current, owner=owner):
                    self._delete(received)
                return
            if current and current.get("status") in (JOB_CANCELLED, JOB_SUCCEEDED, JOB_FAILED):
                if current['status'] == JOB_CANCELLED:
                    self._finish(message, received, owner, int(claimed['attempt']), now, 0,
                                 TASK_CANCELLED, None, '', 'Cancelled before container started')
                    return
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
        restore_from = claimed.get("checkpointAttempt")
        self._execute(received, message, job, owner, int(claimed["attempt"]), allowance,
                      int(restore_from) if restore_from is not None else None,
                      claimed.get("checkpointSha256"))

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
            retry_conflict(lambda: self.db.meta.client.transact_write_items(TransactItems=[
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
            ]))
        except Exception as error:
            if "TransactionCanceledException" in str(error):
                # Job/task đã đổi hoặc giao dịch chưa ghi được: giữ message để retry.
                return False
            raise
        return True

    def _heartbeat(self, message, received, owner):
        now = int(time.time())
        job = self._job(message)
        if job and job.get('status') == JOB_CANCELLED:
            raise JobCancelled('Job cancelled by user')
        if not job or job.get("status") in (JOB_SUCCEEDED, JOB_FAILED):
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

    @staticmethod
    def _log_note(logfile, text):
        """Ghi một dòng của nền tảng vào log người dùng xem được trên web.

        Luồng chép log của container có thể đang ghi cùng lúc, nên chỉ ghi nối đuôi
        (O_APPEND) trong một lần write, không seek rồi ghi đè."""
        needs_newline = False
        with logfile.open("rb") as log:
            if log.seek(0, os.SEEK_END) > 0:
                log.seek(-1, os.SEEK_END)
                needs_newline = log.read(1) != b"\n"
        line = ("\n" if needs_newline else "") + f"[SpotPool] {text}\n"
        with logfile.open("ab") as log:
            log.write(line.encode("utf-8"))

    def _restore_checkpoint(self, message, attempt, expected_sha256, root, folder, logfile):
        archive = root / "restore.tgz"
        try:
            self.s3.download_file(message["output"]["bucket"], checkpoint_key(message, attempt),
                                  str(archive), Callback=self._check_interruption)
            # Mã băm được ghi cùng lệnh có điều kiện với checkpointAttempt, nên file hỏng hay
            # không phải bản đã ghi nhận thì không được giải nén.
            if checkpoint.file_sha256(archive) != expected_sha256:
                raise ValueError("Checkpoint SHA-256 does not match Tasks.checkpointSha256")
            count, size = checkpoint.unpack(archive, folder)
        except SpotInterrupted:
            raise
        except Exception:
            logger.exception("Cannot restore checkpoint: jobId=%s taskId=%s attempt=%s",
                             message["jobId"], message["taskId"], attempt)
            checkpoint.clear(folder)
            self._log_note(logfile, "Checkpoint đã lưu bị hỏng hoặc sai mã SHA-256, task chạy lại từ đầu.")
        else:
            self._log_note(logfile, f"Khôi phục checkpoint của lượt chạy {attempt}: "
                                    f"{count} file, {math.ceil(size / 1024)} KB.")
        finally:
            archive.unlink(missing_ok=True)

    def _save_checkpoint(self, message, owner, attempt, root, folder, logfile, state, final=False):
        """Lưu thư mục checkpoint lên S3 rồi ghi Tasks.checkpointAttempt.

        Lỗi khi lưu không làm hỏng task: lần sau lưu lại, hoặc lượt sau chạy từ checkpoint cũ."""
        archive = root / "save.tgz"
        try:
            # Trong try: cây quá lớn thì signature cũng báo CheckpointTooLarge.
            current = checkpoint.signature(folder)
            if not current or (not final and current == state["signature"]):
                return
            count, size, ignored = checkpoint.pack(folder, archive)
            if ignored and not state["ignored"]:
                state["ignored"] = True
                self._log_note(logfile, "Checkpoint không lưu symlink, file đặc biệt, tên lạ hoặc thư mục sâu quá "
                                        f"{checkpoint.MAX_DEPTH} cấp; bỏ qua: " + ", ".join(ignored[:5]))
            if count == 0:
                return
            if checkpoint.signature(folder) != current:
                # Script đổi file trong lúc đóng gói nên bản này có thể lẫn trạng thái cũ và mới.
                # Bỏ đi; state["signature"] không đổi nên nhịp tim sau lưu lại.
                return
            digest = checkpoint.file_sha256(archive)
            # Không truyền _check_interruption: lúc có thông báo thu hồi vẫn phải lưu cho xong.
            self.s3.upload_file(str(archive), message["output"]["bucket"], checkpoint_key(message, attempt))
            self.db.Table(self.config["TASKS_TABLE"]).update_item(
                Key={"jobId": message["jobId"], "taskId": message["taskId"]},
                UpdateExpression="SET checkpointAt=:now, checkpointAttempt=:attempt, checkpointSha256=:sha, "
                                 "checkpointBytes=:size, checkpointFiles=:count",
                ConditionExpression="#s=:running AND #owner=:owner",
                ExpressionAttributeNames={"#s": "status", "#owner": "owner"},
                ExpressionAttributeValues={":now": int(time.time()), ":attempt": attempt,
                                           ":sha": digest, ":size": size, ":count": count,
                                           ":running": TASK_RUNNING, ":owner": owner},
            )
        except checkpoint.CheckpointTooLarge:
            if not state["too_large"]:
                state["too_large"] = True
                self._log_note(logfile, f"Checkpoint vượt giới hạn {checkpoint.MAX_FILES} file hoặc "
                                        f"{checkpoint.MAX_BYTES // (1024 * 1024)} MB nên không được lưu.")
            return
        except Exception as error:
            if "ConditionalCheckFailedException" not in str(error):
                logger.exception("Cannot save checkpoint: jobId=%s taskId=%s",
                                 message["jobId"], message["taskId"])
                if final:
                    self._log_note(logfile, "Không lưu được checkpoint lần cuối.")
            return
        finally:
            archive.unlink(missing_ok=True)
        state["signature"] = current
        if final:
            self._log_note(logfile, f"Đã lưu checkpoint: {count} file, {math.ceil(size / 1024)} KB.")

    def _execute(self, received, message, job, owner, attempt, allowance, restore_from=None,
                 restore_sha256=None):
        start = int(time.time())
        result_key = f'{message["output"]["key"]}/{attempt}/result'
        log_key = f'{message["output"]["key"]}/{attempt}/log.txt'
        output_bucket = message["output"]["bucket"]
        status, error, elapsed = TASK_FAILED, "", 0
        # Trên EC2 là /var/lib/spotpool/work (ổ đĩa). /tmp của AL2023 là tmpfs, nằm trong RAM.
        work_dir = os.environ.get("SPOTPOOL_WORK_DIR") or None
        if work_dir:
            os.makedirs(work_dir, mode=0o711, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="spotpool-", dir=work_dir) as folder:
            root = Path(folder)
            root.chmod(0o755)
            source, outdir, logfile = root / "payload", root / "output", root / "log.txt"
            outdir.mkdir(mode=0o777)
            outdir.chmod(0o777)
            logfile.touch()
            checkpoint_dir, log_prefix = None, 0
            saved = {"signature": None, "too_large": False, "ignored": False}
            if message["image"] in (IMAGES["script"], IMAGES["video"]):
                # Job script tự ghi trạng thái; mẫu video lưu các đoạn đã nén xong.
                checkpoint_dir = root / "checkpoint"
                checkpoint_dir.mkdir(mode=0o777)
                checkpoint_dir.chmod(0o777)
            proc, reader, start_monotonic = None, None, None
            try:
                self._check_interruption()
                downloaded = [0]
                downloaded_lock = threading.Lock()

                def on_download(chunk):
                    # Người dùng có thể ghi đè file bằng link PUT cũ sau khi API đã kiểm tra,
                    # nên dừng tải ngay khi vượt giới hạn thay vì tin kích thước lúc start.
                    with downloaded_lock:
                        downloaded[0] += chunk
                        too_large = downloaded[0] > MAX_INPUT_BYTES
                    if too_large:
                        raise DiskQuotaExceeded(
                            f"File đầu vào lớn hơn {MAX_INPUT_BYTES // (1024 * 1024)} MB")
                    self._check_interruption()

                with ThreadPoolExecutor(max_workers=1) as downloader:
                    download = downloader.submit(
                        self.s3.download_file, message["input"]["bucket"],
                        message["input"]["key"], str(source), Callback=on_download,
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
                if source.stat().st_size > MAX_INPUT_BYTES:
                    raise DiskQuotaExceeded(f"File đầu vào lớn hơn {MAX_INPUT_BYTES // (1024 * 1024)} MB")
                source.chmod(0o644)
                if checkpoint_dir is not None and restore_from is not None:
                    self._restore_checkpoint(message, restore_from, restore_sha256, root,
                                             checkpoint_dir, logfile)
                log_prefix = logfile.stat().st_size
                image_uri = self.config["VIDEO_JOB_IMAGE"] if message["image"] == IMAGES["video"] else self.config["SCRIPT_JOB_IMAGE"]
                name = f"spotpool-{uuid.uuid4().hex}"
                video_height = None
                if message["image"] == IMAGES["video"]:
                    video_height = message["params"].get("height", 720)
                    if type(video_height) is not int or not 2 <= video_height <= 4320 or video_height % 2:
                        raise ValueError("Video height must be an even integer between 2 and 4320")
                # Chỉ truyền chiều cao vào container video; container chạy script vẫn giữ nguyên tham số.
                command = docker_command(
                    image_uri, message["vcpu"], source, outdir, name, video_height, checkpoint_dir,
                )
                start = int(time.time())
                start_monotonic = time.monotonic()
                proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                def copy_log():
                    with logfile.open("ab") as log:
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
                    # Mỗi giây một lần: ổ EBS ghi khoảng 125 MB/s, nên không vượt giới hạn nhiều.
                    check_written(outdir, checkpoint_dir)
                    if time.monotonic() >= next_heartbeat:
                        if not self._heartbeat(message, received, owner):
                            raise LeaseLost
                        next_heartbeat = time.monotonic() + HEARTBEAT_SECONDS
                        if checkpoint_dir is not None:
                            # Lưu định kỳ để máy chết đột ngột, không kịp báo, vẫn còn checkpoint.
                            self._save_checkpoint(message, owner, attempt, root, checkpoint_dir,
                                                  logfile, saved)
                check_written(outdir, checkpoint_dir)
            except SpotInterrupted as exc:
                status, error = OUTCOME_INTERRUPTED, str(exc)
            except JobPaused as exc:
                status, error = TASK_PAUSED, str(exc)
            except JobCancelled as exc:
                status, error = TASK_CANCELLED, str(exc)
            except LeaseLost:
                return
            except DiskQuotaExceeded as exc:
                status, error = TASK_FAILED, str(exc)
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
            if status in (TASK_PAUSED, TASK_CANCELLED, OUTCOME_INTERRUPTED):
                pass
            elif not error and proc is not None and proc.returncode == 0:
                try:
                    self._check_interruption()
                    try:
                        result = open_result_file(outdir / "result")
                    except (OSError, ValueError) as exc:
                        error = str(exc)[:1000]
                    else:
                        # Không mở lại theo đường dẫn sau kiểm tra: tránh symlink/race.
                        with result:
                            self.s3.upload_fileobj(result, output_bucket, result_key,
                                                   Callback=self._check_interruption)
                        status = TASK_SUCCEEDED
                except SpotInterrupted as exc:
                    status, error = OUTCOME_INTERRUPTED, str(exc)
            else:
                error = error or f"Container exit code {proc.returncode if proc else 'unknown'}; result file missing or invalid"
            if error and logfile.stat().st_size == log_prefix:
                with logfile.open("a", encoding="utf-8") as log:
                    log.write(error)
            if (checkpoint_dir is not None and start_monotonic is not None
                    and status in (OUTCOME_INTERRUPTED, TASK_PAUSED)):
                # Container đã dừng: lưu bản cuối để lượt sau (máy khác, hoặc sau khi nạp thêm) chạy tiếp.
                self._save_checkpoint(message, owner, attempt, root, checkpoint_dir, logfile,
                                      saved, final=True)
            self.s3.upload_file(str(logfile), output_bucket, log_key)
        self._finish(message, received, owner, attempt, start, elapsed, status,
                     result_key if status == TASK_SUCCEEDED else None, log_key, error[:1000])

    def _finish(self, message, received, owner, attempt, started_at, elapsed, status,
                result_key, log_key, error):
        current_job = self._job(message)
        cancelled_interruption = bool(current_job and current_job.get('status') == JOB_CANCELLED
                                      and status == OUTCOME_INTERRUPTED)
        if current_job and current_job.get('status') == JOB_CANCELLED:
            status, result_key = TASK_CANCELLED, None
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
            "outcome": OUTCOME_INTERRUPTED if cancelled_interruption else status, "vcpuSeconds": vcpu_seconds,
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
        job_condition = "#s IN (:queued,:running,:paused) AND tasksRemaining > :zero"
        if status == TASK_CANCELLED:
            job_values = {':used': 0 if cancelled_interruption else vcpu_seconds, ':one': 1, ':minusOne': -1, ':cancelled': JOB_CANCELLED}
            job_condition = '#s = :cancelled AND tasksRemaining > :zero'
        token = str(uuid.uuid5(uuid.NAMESPACE_URL, usage["usageId"] + owner))
        try:
            retry_conflict(lambda: self.db.meta.client.transact_write_items(
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
                                "ConditionExpression": job_condition,
                                "ExpressionAttributeNames": {"#s": "status"},
                                "ExpressionAttributeValues": {**job_values, ":zero": 0}}},
                    {"Put": {"TableName": self.config["USAGE_TABLE"], "Item": usage,
                             "ConditionExpression": "attribute_not_exists(userId) AND attribute_not_exists(usageId)"}},
                ],
            ))
        except Exception:
            latest = self._task(message)
            latest_job = self._job(message)
            if (status != TASK_CANCELLED and latest_job and latest_job.get('status') == JOB_CANCELLED
                    and latest and latest.get('status') == TASK_RUNNING and latest.get('owner') == owner):
                return self._finish(message, received, owner, attempt, started_at, elapsed,
                                    OUTCOME_INTERRUPTED if interrupted else TASK_CANCELLED,
                                    None, log_key, 'Job cancelled by user')
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
                retry_conflict(lambda: jobs.update_item(
                    Key={"jobId": message["jobId"]},
                    UpdateExpression="SET #s=:target, finishedAt=:now",
                    ConditionExpression=f"#s IN (:queued,:running) AND tasksRemaining=:zero AND {failed_condition}",
                    ExpressionAttributeNames={"#s": "status"},
                    ExpressionAttributeValues={":target": target, ":queued": JOB_QUEUED,
                                               ":running": JOB_RUNNING, ":zero": 0,
                                               ":now": int(time.time())},
                ))
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
