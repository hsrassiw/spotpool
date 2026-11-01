import io
import re
import subprocess
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from worker.agent import JobPaused, JobCancelled
from test_tv2_week1 import JOB, WorkerContractTests

message_fixture = WorkerContractTests._message
worker_fixture = WorkerContractTests._worker
del WorkerContractTests


class PauseTransactions:
    """Mô phỏng các điều kiện giao dịch cần cho kịch bản PAUSED, không gọi AWS."""

    def __init__(self, db):
        self.db = db
        self.usage = []
        self.calls = []

    @staticmethod
    def allowed_states(update):
        if '#s = :cancelled' in update['ConditionExpression']:
            return {update['ExpressionAttributeValues'][':cancelled']}
        placeholders = re.search(r"#s IN \(([^)]+)\)", update["ConditionExpression"])[1]
        return {update["ExpressionAttributeValues"][key.strip()]
                for key in placeholders.split(",")}

    def transact_write_items(self, TransactItems, **kwargs):
        self.calls.append(TransactItems)
        job = self.db.Table("Jobs").item
        if len(TransactItems) == 2:
            job_update, task_update = (action["Update"] for action in TransactItems)
            jv, tv = job_update["ExpressionAttributeValues"], task_update["ExpressionAttributeValues"]
            task = self.db.Table("Tasks").rows[(JOB, task_update["Key"]["taskId"])]
            if ":cap" in jv:
                job_ok = (job["status"] in self.allowed_states(job_update)
                          and job["costCap"] == jv[":cap"]
                          and job["usedVcpuSeconds"] == jv[":used"])
            else:
                job_ok = job["status"] == "PAUSED"
            if ":owner" in tv:
                task_ok = task["status"] == "RUNNING" and task.get("owner") == tv[":owner"]
            else:
                task_ok = (task["status"] == "QUEUED"
                           or task["status"] == "RUNNING" and task["leaseUntil"] < tv[":now"])
            if not job_ok or not task_ok or task["fingerprint"] != tv[":fp"]:
                raise RuntimeError("TransactionCanceledException: ConditionalCheckFailed")
            job["status"], task["status"] = "PAUSED", "PAUSED"
            return

        task_update, job_update = (action["Update"] for action in TransactItems[:2])
        tv, jv = task_update["ExpressionAttributeValues"], job_update["ExpressionAttributeValues"]
        task = self.db.Table("Tasks").rows[(JOB, task_update["Key"]["taskId"])]
        usage = TransactItems[2]["Put"]["Item"]
        if (job["status"] not in self.allowed_states(job_update)
                or task["status"] != "RUNNING" or task["owner"] != tv[":owner"]
                or job["tasksRemaining"] <= 0
                or any(old["usageId"] == usage["usageId"] for old in self.usage)):
            raise RuntimeError("TransactionCanceledException: ConditionalCheckFailed")
        task["status"] = tv[":status"]
        job["usedVcpuSeconds"] += jv[":used"]
        if tv[":status"] == "PAUSED":
            job["status"] = "PAUSED"
        else:
            job["tasksRemaining"] -= 1
            job["tasksSucceeded" if tv[":status"] == "SUCCEEDED" else "tasksFailed"] += 1
        self.usage.append(usage)


class WorkerPausedTests(unittest.TestCase):
    def make_worker(self, job_status="PAUSED", task_status="QUEUED"):
        message = message_fixture()
        db, sqs, worker = worker_fixture(message, task_status=task_status)
        db.Table("Jobs").item.update(status=job_status, tasksRemaining=1,
                                     tasksSucceeded=0, tasksFailed=0)
        db.Table("Tasks").rows[(JOB, message["taskId"])].update(
            owner="owner", leaseUntil=int(time.time()) + 120,
        )
        client = PauseTransactions(db)
        db.meta = types.SimpleNamespace(client=client)
        sqs.change_message_visibility = Mock()
        worker._finish_job = Mock()
        return message, db, sqs, worker

    def test_queued_task_is_paused_before_message_is_deleted(self):
        message, db, sqs, worker = self.make_worker()
        def delete(**kwargs):
            self.assertEqual("PAUSED", db.Table("Tasks").rows[(JOB, message["taskId"])]["status"])
        sqs.delete_message = Mock(side_effect=delete)
        worker._execute = Mock()
        worker.process({"ReceiptHandle": "receipt"}, message)
        sqs.delete_message.assert_called_once()
        worker._execute.assert_not_called()
        self.assertEqual([], db.meta.client.usage)

    def test_no_budget_pauses_job_and_task_together(self):
        message, db, sqs, worker = self.make_worker(job_status="RUNNING")
        db.Table("Jobs").item["usedVcpuSeconds"] = 100
        worker.process({"ReceiptHandle": "receipt"}, message)
        self.assertEqual("PAUSED", db.Table("Jobs").item["status"])
        self.assertEqual("PAUSED", db.Table("Tasks").rows[(JOB, message["taskId"])]["status"])
        self.assertEqual(1, db.Table("Jobs").item["tasksRemaining"])
        self.assertEqual([], db.meta.client.usage)
        self.assertEqual("receipt", sqs.deleted["ReceiptHandle"])

    def test_duplicate_does_not_pause_task_owned_by_active_worker(self):
        message, db, sqs, worker = self.make_worker(task_status="RUNNING")
        worker.process({"ReceiptHandle": "receipt"}, message)
        self.assertEqual("RUNNING", db.Table("Tasks").rows[(JOB, message["taskId"])]["status"])
        self.assertFalse(hasattr(sqs, "deleted"))

    def test_abandoned_task_can_be_paused_after_lease_expires(self):
        message, db, sqs, worker = self.make_worker(task_status="RUNNING")
        db.Table("Tasks").rows[(JOB, message["taskId"])]["leaseUntil"] = 1
        worker.process({"ReceiptHandle": "receipt"}, message)
        self.assertEqual("PAUSED", db.Table("Tasks").rows[(JOB, message["taskId"])]["status"])
        self.assertEqual("receipt", sqs.deleted["ReceiptHandle"])

    def test_topup_between_read_and_pause_keeps_message(self):
        for initial_status in ("RUNNING", "PAUSED"):
            with self.subTest(initial_status=initial_status):
                message, db, sqs, worker = self.make_worker(job_status=initial_status)
                job = db.Table("Jobs").item
                job["usedVcpuSeconds"] = 100
                before_topup = dict(job)
                def read_then_topup(_):
                    job.update(status="QUEUED", costCap=200)
                    return before_topup
                worker._job = read_then_topup
                worker.process({"ReceiptHandle": "receipt"}, message)
                self.assertEqual("QUEUED", job["status"])
                self.assertEqual("QUEUED", db.Table("Tasks").rows[(JOB, message["taskId"])]["status"])
                self.assertFalse(hasattr(sqs, "deleted"))

    def test_pause_after_claim_preserves_owner_condition(self):
        message, db, sqs, worker = self.make_worker(job_status="QUEUED")
        task = db.Table("Tasks").rows[(JOB, message["taskId"])]
        def claim(**kwargs):
            task.update(status="RUNNING", owner=kwargs["ExpressionAttributeValues"][":owner"])
            db.Table("Jobs").item["status"] = "PAUSED"
            return {"Attributes": {"attempt": 1}}
        db.Table("Tasks").update_item = claim
        db.Table("Jobs").update_item = Mock(side_effect=RuntimeError("ConditionalCheckFailedException"))
        worker._execute = Mock()
        worker.process({"ReceiptHandle": "receipt"}, message)
        self.assertEqual("PAUSED", task["status"])
        self.assertIn("#owner=:owner", db.meta.client.calls[0][1]["Update"]["ConditionExpression"])
        worker._execute.assert_not_called()
        self.assertEqual("receipt", sqs.deleted["ReceiptHandle"])

    def test_heartbeat_reports_pause_only_after_confirming_ownership(self):
        message, db, sqs, worker = self.make_worker(task_status="RUNNING")
        with self.assertRaises(JobPaused):
            worker._heartbeat(message, {"ReceiptHandle": "receipt"}, "owner")
        self.assertEqual("owner", db.Table("Tasks").last_update["ExpressionAttributeValues"][":owner"])
        sqs.change_message_visibility.assert_called_once()

    def test_lost_owner_on_paused_job_returns_false(self):
        message, db, sqs, worker = self.make_worker(task_status="RUNNING")
        db.Table("Tasks").update_item = Mock(side_effect=RuntimeError("ConditionalCheckFailedException"))
        self.assertFalse(worker._heartbeat(message, {"ReceiptHandle": "receipt"}, "old-owner"))
        sqs.change_message_visibility.assert_not_called()

    def run_container_until_heartbeat(self, heartbeat_result, cleanup_error=None):
        message, db, sqs, worker = self.make_worker(job_status="RUNNING", task_status="RUNNING")
        worker.config.update(VIDEO_JOB_IMAGE="test-image", TASK_MAX_SECONDS="100")
        worker.s3 = Mock()
        worker.s3.download_file.side_effect = lambda b, k, p, **kw: Path(p).write_bytes(b"input")
        worker._heartbeat = Mock(side_effect=[True, heartbeat_result])
        worker._finish = Mock()
        proc = Mock()
        proc.stdout = io.BytesIO(b"partial log")
        proc.poll.return_value = None
        proc.returncode = -9
        clock = [10]
        def wait(timeout):
            if timeout == 15:
                return 0
            clock[0] = 15
            raise subprocess.TimeoutExpired("docker", timeout)
        proc.wait.side_effect = wait
        uploaded = []
        worker.s3.upload_file.side_effect = lambda path, bucket, key: uploaded.append((key, Path(path).read_bytes()))
        with patch("worker.agent.subprocess.Popen", return_value=proc), \
             patch("worker.agent.subprocess.run", side_effect=cleanup_error) as stop, \
             patch("worker.agent.time.monotonic", side_effect=lambda: clock[0]), \
             patch("worker.agent.HEARTBEAT_SECONDS", 5):
            if cleanup_error:
                with self.assertRaises(subprocess.CalledProcessError):
                    worker._execute({"ReceiptHandle": "receipt"}, message, {}, "owner", 1, 100)
            else:
                worker._execute({"ReceiptHandle": "receipt"}, message, {}, "owner", 1, 100)
        self.assertEqual(["docker", "rm", "-f"], stop.call_args.args[0][:3])
        self.assertTrue(stop.call_args.kwargs["check"])
        return worker, uploaded

    def test_pause_during_container_stops_and_settles_elapsed_usage(self):
        worker, uploaded = self.run_container_until_heartbeat(JobPaused("Job paused"))
        args = worker._finish.call_args.args
        self.assertEqual(5, args[5])
        self.assertEqual("PAUSED", args[6])
        self.assertIsNone(args[7])
        self.assertEqual(1, len(uploaded))
        self.assertTrue(uploaded[0][0].endswith("/log.txt"))
        self.assertEqual(b"partial log", uploaded[0][1])

    def test_lost_lease_during_container_stops_without_settlement(self):
        worker, uploaded = self.run_container_until_heartbeat(False)
        worker._finish.assert_not_called()
        self.assertEqual([], uploaded)

    def test_cancellation_stops_container_and_records_elapsed_usage_and_log(self):
        worker, uploaded = self.run_container_until_heartbeat(JobCancelled('Job cancelled'))
        args = worker._finish.call_args.args
        self.assertEqual((5, 'CANCELLED'), args[5:7])
        self.assertEqual(1, len(uploaded))
        self.assertTrue(uploaded[0][0].endswith('/log.txt'))

    def test_failed_container_cleanup_does_not_settle(self):
        worker, _ = self.run_container_until_heartbeat(
            JobPaused("Job paused"), subprocess.CalledProcessError(1, "docker rm"),
        )
        worker._finish.assert_not_called()

    def test_pause_while_downloading_never_starts_container(self):
        message, db, sqs, worker = self.make_worker(task_status="RUNNING")
        worker.s3 = Mock()
        release_download = threading.Event()
        def download(bucket, key, path, **kwargs):
            if not release_download.wait(2):
                raise RuntimeError("Test download was not released")
            Path(path).write_bytes(b"input")
        def pause(*args):
            release_download.set()
            raise JobPaused("Job paused during download")
        worker.s3.download_file.side_effect = download
        worker._heartbeat = pause
        worker._finish = Mock()
        with patch("worker.agent.HEARTBEAT_SECONDS", 0.01), \
             patch("worker.agent.subprocess.Popen") as start:
            worker._execute({"ReceiptHandle": "receipt"}, message, {}, "owner", 1, 100)
        start.assert_not_called()
        self.assertEqual((0, "PAUSED"), worker._finish.call_args.args[5:7])

    def test_finishing_task_is_accepted_while_job_is_paused(self):
        for outcome in ("PAUSED", "SUCCEEDED", "FAILED"):
            with self.subTest(outcome=outcome):
                message, db, sqs, worker = self.make_worker(task_status="RUNNING")
                worker._finish(message, {"ReceiptHandle": "receipt"}, "owner", 1,
                               100, 5, outcome, None, "log", "")
                job = db.Table("Jobs").item
                self.assertEqual("PAUSED", job["status"])
                self.assertEqual(5, job["usedVcpuSeconds"])
                self.assertEqual(1 if outcome == "PAUSED" else 0, job["tasksRemaining"])
                self.assertEqual(outcome, db.meta.client.usage[0]["outcome"])
                self.assertEqual("receipt", sqs.deleted["ReceiptHandle"])

    def test_other_task_finishes_after_first_task_pauses_job(self):
        message, db, sqs, worker = self.make_worker(job_status="RUNNING", task_status="RUNNING")
        job = db.Table("Jobs").item
        job["tasksRemaining"] = 2
        other = {**message, "taskId": "task-0002"}
        db.Table("Tasks").rows[(JOB, other["taskId"])] = {"status": "RUNNING", "owner": "other-owner"}
        worker._finish(message, {"ReceiptHandle": "one"}, "owner", 1, 100, 5, "PAUSED", None, "log1", "")
        worker._finish(other, {"ReceiptHandle": "two"}, "other-owner", 1, 100, 3, "SUCCEEDED", "result", "log2", "")
        self.assertEqual("PAUSED", job["status"])
        self.assertEqual(1, job["tasksRemaining"])
        self.assertEqual(1, job["tasksSucceeded"])
        self.assertEqual(8, job["usedVcpuSeconds"])
        self.assertEqual(["PAUSED", "SUCCEEDED"], [row["outcome"] for row in db.meta.client.usage])


if __name__ == "__main__":
    unittest.main()
