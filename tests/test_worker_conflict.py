"""Hai task cùng job xong cùng lúc: DynamoDB hủy một giao dịch vì TransactionConflict.

Chạy thử 12 task trên AWS ngày 11/11 gặp lỗi này: worker bỏ message, task chạy lại từ đầu
sau khi hết visibility timeout. Worker phải thử lại giao dịch thay vì coi task là lỗi.
"""
import unittest
from unittest.mock import Mock, patch

from worker.agent import retry_conflict
import test_worker_paused as paused

CONFLICT = RuntimeError("TransactionCanceledException: Transaction cancelled, please refer "
                        "cancellation reasons for specific reasons [None, TransactionConflict, None]")


class RetryConflictTests(unittest.TestCase):
    def test_retries_conflict_then_returns_result(self):
        call = Mock(side_effect=[CONFLICT, CONFLICT, "ok"])
        with patch("worker.agent.time.sleep"):
            self.assertEqual("ok", retry_conflict(call))
        self.assertEqual(3, call.call_count)

    def test_other_errors_are_not_retried(self):
        call = Mock(side_effect=RuntimeError("TransactionCanceledException: [None, ConditionalCheckFailed, None]"))
        with patch("worker.agent.time.sleep"), self.assertRaises(RuntimeError):
            retry_conflict(call)
        self.assertEqual(1, call.call_count)

    def test_gives_up_after_attempts(self):
        call = Mock(side_effect=CONFLICT)
        with patch("worker.agent.time.sleep"), self.assertRaises(RuntimeError):
            retry_conflict(call, attempts=3)
        self.assertEqual(3, call.call_count)


class FinishConflictTests(unittest.TestCase):
    def test_finish_retries_conflict_and_counts_task_once(self):
        message, db, sqs, worker = paused.WorkerPausedTests.make_worker(
            paused.WorkerPausedTests(), job_status="RUNNING", task_status="RUNNING")
        client = db.meta.client
        original = client.transact_write_items
        calls = []

        def conflict_once(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise CONFLICT
            return original(**kwargs)

        client.transact_write_items = conflict_once
        with patch("worker.agent.time.sleep"):
            worker._finish(message, {"ReceiptHandle": "receipt"}, "owner", 1, 100, 5,
                           "SUCCEEDED", "result", "log", "")
        job = db.Table("Jobs").item
        self.assertEqual(2, len(calls))
        self.assertEqual(calls[0]["ClientRequestToken"], calls[1]["ClientRequestToken"])
        self.assertEqual("SUCCEEDED", db.Table("Tasks").rows[(paused.JOB, message["taskId"])]["status"])
        self.assertEqual(0, job["tasksRemaining"])
        self.assertEqual(5, job["usedVcpuSeconds"])
        self.assertEqual(1, len(client.usage))
        self.assertEqual("receipt", sqs.deleted["ReceiptHandle"])


if __name__ == "__main__":
    unittest.main()
