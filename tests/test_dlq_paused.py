"""DLQ hoàn tất task và bộ đếm nguyên tử dù job đang PAUSED hoặc đã bị hủy, không gọi AWS."""

import json
import re
import types
import unittest
from unittest.mock import Mock, patch

from services.dispatcher.dlq import fail_exhausted, lambda_handler
from spotpool_core.contracts import fingerprint

USER = "11111111-1111-4111-8111-111111111111"
JOB = "22222222-2222-4222-8222-222222222222"
MESSAGE = {
    "schemaVersion": 1, "jobId": JOB, "taskId": "task-0001", "userId": USER,
    "tier": "spot", "kind": "script", "vcpu": 1, "image": "spotpool-script", "params": {},
    "input": {"bucket": "input-test", "key": f"uploads/{USER}/{JOB}/task-0001"},
    "output": {"bucket": "output-test", "key": f"results/{USER}/{JOB}/task-0001"},
}


def allowed_states(request):
    names = re.search(r"#s IN \(([^)]+)\)", request["ConditionExpression"])[1]
    return {request["ExpressionAttributeValues"][name.strip()] for name in names.split(",")}


class FakeTable:
    def __init__(self, db, name):
        self.db, self.name = db, name

    def get_item(self, **kwargs):
        return {"Item": dict(self.db.task if self.name == "Tasks" else self.db.job)}

    def update_item(self, **request):
        self.db.finalizations.append(request)
        job = self.db.job
        expression, values = request["ConditionExpression"], request["ExpressionAttributeValues"]
        failed_matches = job["tasksFailed"] == 0 if "tasksFailed=:zero" in expression else job["tasksFailed"] > 0
        if job["status"] not in allowed_states(request) or job["tasksRemaining"] != 0 or not failed_matches:
            raise RuntimeError("ConditionalCheckFailedException")
        job.update(status=values[":state"], finishedAt=values[":now"])


class AtomicDB:
    def __init__(self, *, status="PAUSED", task_status="QUEUED", remaining=2, lease=99):
        self.job = {"jobId": JOB, "userId": USER, "status": status, "tasksRemaining": remaining, "tasksFailed": 0, "usedVcpuSeconds": 123}
        self.task = {"jobId": JOB, "taskId": "task-0001", "status": task_status, "fingerprint": fingerprint(MESSAGE), "leaseUntil": lease}
        self.meta = types.SimpleNamespace(client=self)
        self.transactions, self.finalizations = [], []
        self.before_transaction = self.after_transaction = None

    def Table(self, name):
        return FakeTable(self, name)

    def transact_write_items(self, TransactItems):
        self.transactions.append(TransactItems)
        if self.before_transaction:
            self.before_transaction()
        task_update, job_update = (item["Update"] for item in TransactItems)
        tv, jv = task_update["ExpressionAttributeValues"], job_update["ExpressionAttributeValues"]
        task_ok = self.task["fingerprint"] == tv[":fp"] and (
            self.task["status"] == tv[":queued"] or self.task["status"] == tv[":running"] and self.task["leaseUntil"] < tv[":now"])
        job_ok = self.job["status"] in allowed_states(job_update) and self.job["tasksRemaining"] > jv[":zero"]
        if not task_ok or not job_ok:
            raise RuntimeError("TransactionCanceledException")
        self.task.update(status=tv[":status"], finishedAt=tv[":now"], error=tv[":error"])
        self.job["tasksRemaining"] += jv[":minusOne"]
        self.job["tasksFailed"] += jv[":one"]
        if self.after_transaction:
            self.after_transaction()


class DLQPausedTests(unittest.TestCase):
    def exhaust(self, db):
        return fail_exhausted(MESSAGE, db, "Tasks", "Jobs", now=100)

    def test_paused_job_queued_task_is_failed_atomically_without_charging_or_unpausing_job(self):
        db = AtomicDB()
        self.assertTrue(self.exhaust(db))
        self.assertEqual("FAILED", db.task["status"])
        self.assertEqual(("PAUSED", 1, 1, 123), tuple(db.job[key] for key in ("status", "tasksRemaining", "tasksFailed", "usedVcpuSeconds")))
        self.assertNotIn("finishedAt", db.job)
        actions = db.transactions[0]
        self.assertEqual({"jobId": JOB, "taskId": "task-0001"}, actions[0]["Update"]["Key"])
        self.assertEqual(-1, actions[1]["Update"]["ExpressionAttributeValues"][":minusOne"])

    def test_last_exhausted_task_finalizes_paused_job_as_failed(self):
        db = AtomicDB(remaining=1)
        self.assertTrue(self.exhaust(db))
        self.assertEqual(("FAILED", 0, 1, 100), tuple(db.job[key] for key in ("status", "tasksRemaining", "tasksFailed", "finishedAt")))

    def test_expired_running_task_is_failed_but_live_lease_is_not_touched(self):
        expired = AtomicDB(task_status="RUNNING")
        self.assertTrue(self.exhaust(expired))
        for lease in (100, 101):
            with self.subTest(lease=lease):
                live = AtomicDB(task_status="RUNNING", lease=lease)
                self.assertFalse(self.exhaust(live))
                self.assertEqual("RUNNING", live.task["status"])
                self.assertEqual([], live.transactions)

    def test_already_paused_or_terminal_task_is_left_for_existing_resume_or_final_state(self):
        for status in ("PAUSED", "FAILED", "SUCCEEDED", "CANCELLED"):
            with self.subTest(status=status):
                db = AtomicDB(task_status=status)
                self.assertFalse(self.exhaust(db))
                self.assertEqual(status, db.task["status"])
                self.assertEqual([], db.transactions)

    def test_duplicate_delivery_cannot_decrement_counters_twice(self):
        db = AtomicDB()
        self.assertTrue(self.exhaust(db))
        self.assertFalse(self.exhaust(db))
        self.assertEqual((1, 1), (db.job["tasksRemaining"], db.job["tasksFailed"]))
        self.assertEqual(1, len(db.transactions))

    def test_finished_job_rejects_whole_transaction(self):
        for status in ("SUCCEEDED", "FAILED"):
            with self.subTest(status=status):
                db = AtomicDB(status=status)
                self.assertFalse(self.exhaust(db))
                self.assertEqual(("QUEUED", 2, 0), (db.task["status"], db.job["tasksRemaining"], db.job["tasksFailed"]))

    def test_cancelled_job_closes_stuck_task_so_billing_can_settle(self):
        # Billing chỉ quyết toán job bị hủy khi không còn task RUNNING.
        for task_status in ("RUNNING", "QUEUED"):
            with self.subTest(task_status=task_status):
                db = AtomicDB(status="CANCELLED", task_status=task_status)
                self.assertTrue(self.exhaust(db))
                self.assertEqual("CANCELLED", db.task["status"])
                self.assertIn("cancelled", db.task["error"])
                self.assertEqual(("CANCELLED", 1, 1), tuple(db.job[key] for key in ("status", "tasksRemaining", "tasksFailed")))
                self.assertEqual([], db.finalizations)
        live = AtomicDB(status="CANCELLED", task_status="RUNNING", lease=101)
        self.assertFalse(self.exhaust(live))
        self.assertEqual("RUNNING", live.task["status"])

    def test_concurrent_cancellation_retries_as_cancelled_task(self):
        db = AtomicDB()
        db.before_transaction = lambda: db.job.update(status="CANCELLED")
        self.assertTrue(self.exhaust(db))
        self.assertEqual("CANCELLED", db.task["status"])
        self.assertEqual(2, len(db.transactions))
        self.assertEqual((1, 1), (db.job["tasksRemaining"], db.job["tasksFailed"]))

    def test_paused_between_transaction_and_finalization_still_finishes_last_task(self):
        db = AtomicDB(status="RUNNING", remaining=1)
        db.after_transaction = lambda: db.job.update(status="PAUSED")
        self.assertTrue(self.exhaust(db))
        self.assertEqual("FAILED", db.job["status"])

    def test_batch_handler_resolves_paused_job_and_retries_real_database_failures(self):
        db = AtomicDB(remaining=1)
        boto3 = types.ModuleType("boto3")
        boto3.resource = Mock(return_value=db)
        event = {"Records": [{"body": json.dumps(MESSAGE), "messageId": "dlq-1"}]}
        with patch.dict("sys.modules", {"boto3": boto3}), \
                patch.dict("os.environ", {"TASKS_TABLE": "Tasks", "JOBS_TABLE": "Jobs"}), \
                patch("services.dispatcher.dlq.time.time", return_value=100):
            self.assertEqual({"batchItemFailures": []}, lambda_handler(event, None))
            self.assertEqual("FAILED", db.job["status"])
            db.task["status"] = "QUEUED"
            with patch.object(db, "transact_write_items", side_effect=RuntimeError("Database unavailable")):
                self.assertEqual({"batchItemFailures": [{"itemIdentifier": "dlq-1"}]}, lambda_handler(event, None))


if __name__ == "__main__":
    unittest.main()
