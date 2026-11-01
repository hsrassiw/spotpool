import io
import json
import subprocess
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from spotpool_core.contracts import OUTCOME_INTERRUPTED
from worker.agent import LeaseLost, Worker
from worker.spot import SpotInterrupted
from test_spot_metadata import NOTICE
from test_worker_paused import PauseTransactions, WorkerPausedTests

worker_fixture = WorkerPausedTests.make_worker
del WorkerPausedTests


class InterruptionTransactions(PauseTransactions):
    def __init__(self, db):
        super().__init__(db)
        self.lose_response = False

    def transact_write_items(self, TransactItems, **kwargs):
        if len(TransactItems) != 3 or TransactItems[2]["Put"]["Item"]["outcome"] != OUTCOME_INTERRUPTED:
            return super().transact_write_items(TransactItems, **kwargs)
        self.calls.append(TransactItems)
        task_update, job_update = (action["Update"] for action in TransactItems[:2])
        values = task_update["ExpressionAttributeValues"]
        task = self.db.Table("Tasks").rows[(task_update["Key"]["jobId"], task_update["Key"]["taskId"])]
        job = self.db.Table("Jobs").item
        usage = TransactItems[2]["Put"]["Item"]
        if (job["status"] not in self.allowed_states(job_update)
                or task["status"] != "RUNNING" or task["owner"] != values[":owner"]
                or any(old["usageId"] == usage["usageId"] for old in self.usage)):
            raise RuntimeError("TransactionCanceledException")
        assert job_update["ExpressionAttributeValues"][":used"] == 0
        assert "tasksRemaining" not in job_update["UpdateExpression"]
        task.update(status=values[":status"], queue=values[":queue"], interruptCount=values[":interrupts"])
        self.usage.append(usage)
        if self.lose_response:
            self.lose_response = False
            raise RuntimeError("Response lost after commit")


class WorkerSpotTests(unittest.TestCase):
    def make_worker(self, previous_interrupts=0):
        message, db, sqs, worker = worker_fixture(self, job_status="RUNNING", task_status="RUNNING")
        task = db.Table("Tasks").rows[(message["jobId"], message["taskId"])]
        task.update(attempt=1, interruptCount=previous_interrupts)
        db.Table("Jobs").item["usedVcpuSeconds"] = 20
        db.meta.client = InterruptionTransactions(db)
        db.tables["Usage"] = Mock()
        db.tables["Usage"].get_item.side_effect = lambda Key, **kw: {
            "Item": next((row for row in db.meta.client.usage if row["usageId"] == Key["usageId"]), None)
        }
        received = {"ReceiptHandle": "receipt", "Body": json.dumps(message, indent=2)}
        return message, received, db, sqs, worker

    def finish_interrupted(self, worker, message, received):
        worker._finish(message, received, "owner", 1, 100, 13,
                       OUTCOME_INTERRUPTED, None, "log", "Spot interrupted")

    def test_first_two_interruptions_requeue_without_charging(self):
        for previous in (0, 1):
            with self.subTest(previous=previous):
                message, received, db, sqs, worker = self.make_worker(previous)
                self.finish_interrupted(worker, message, received)
                task = worker._task(message)
                self.assertEqual(("QUEUED", "spot", previous + 1),
                                 (task["status"], task["queue"], task["interruptCount"]))
                self.assertEqual(20, db.Table("Jobs").item["usedVcpuSeconds"])
                self.assertEqual(1, db.Table("Jobs").item["tasksRemaining"])
                self.assertEqual(OUTCOME_INTERRUPTED, db.meta.client.usage[0]["outcome"])
                self.assertEqual(13, db.meta.client.usage[0]["vcpuSeconds"])
                sqs.change_message_visibility.assert_called_once_with(
                    QueueUrl="spot-url", ReceiptHandle="receipt", VisibilityTimeout=0,
                )
                self.assertEqual([], sqs.messages)
                self.assertFalse(hasattr(sqs, "deleted"))
                worker._finish_job.assert_not_called()

    def test_third_interruption_sends_original_body_before_deleting(self):
        message, received, db, sqs, worker = self.make_worker(2)
        sent = []
        def send(**kwargs):
            self.assertEqual(3, worker._task(message)["interruptCount"])
            self.assertFalse(hasattr(sqs, "deleted"))
            sent.append(kwargs)
        sqs.send_message = send
        self.finish_interrupted(worker, message, received)
        self.assertEqual("ondemand", worker._task(message)["queue"])
        self.assertEqual([{"QueueUrl": "ondemand-url", "MessageBody": received["Body"]}], sent)
        self.assertEqual("spot", db.meta.client.usage[0]["tier"])
        self.assertEqual("receipt", sqs.deleted["ReceiptHandle"])

    def test_failed_send_keeps_source_message_and_next_worker_routes_it(self):
        message, received, db, sqs, worker = self.make_worker(2)
        sqs.send_message = Mock(side_effect=RuntimeError("SQS unavailable"))
        with self.assertRaisesRegex(RuntimeError, "SQS unavailable"):
            self.finish_interrupted(worker, message, received)
        self.assertFalse(hasattr(sqs, "deleted"))
        sqs.send_message = Mock()
        replacement = Worker(dynamodb=db, s3=None, sqs=sqs, config=worker.config, queue="spot")
        replacement.process(received, message)
        sqs.send_message.assert_called_once_with(QueueUrl="ondemand-url", MessageBody=received["Body"])
        self.assertEqual(1, len(db.meta.client.usage))
        self.assertEqual(3, worker._task(message)["interruptCount"])

    def test_retry_after_send_failure_does_not_count_interruption_twice(self):
        message, received, db, sqs, worker = self.make_worker(2)
        sqs.send_message = Mock(side_effect=[RuntimeError("temporary"), {}])
        with self.assertRaises(RuntimeError):
            self.finish_interrupted(worker, message, received)
        self.finish_interrupted(worker, message, received)
        self.assertEqual(1, len(db.meta.client.calls))
        self.assertEqual(1, len(db.meta.client.usage))
        self.assertEqual(3, worker._task(message)["interruptCount"])

    def test_lost_transaction_response_recovers_without_duplicate_usage(self):
        message, received, db, sqs, worker = self.make_worker()
        db.meta.client.lose_response = True
        self.finish_interrupted(worker, message, received)
        self.assertEqual(1, len(db.meta.client.usage))
        self.assertEqual(1, worker._task(message)["interruptCount"])
        sqs.change_message_visibility.assert_called_once()

    def test_old_owner_cannot_record_an_interruption(self):
        message, received, db, sqs, worker = self.make_worker()
        worker._task(message)["owner"] = "replacement-owner"
        with self.assertRaises(LeaseLost):
            self.finish_interrupted(worker, message, received)
        self.assertEqual([], db.meta.client.calls)
        self.assertFalse(hasattr(sqs, "deleted"))

    def test_resumed_paused_attempt_is_not_mistaken_for_committed_interruption(self):
        message, received, db, sqs, worker = self.make_worker(1)
        worker._task(message)["status"] = "QUEUED"
        db.meta.client.usage.append({"usageId": f'{message["jobId"]}#{message["taskId"]}#1',
                                     "outcome": "PAUSED"})
        with self.assertRaises(LeaseLost):
            self.finish_interrupted(worker, message, received)
        self.assertEqual([], db.meta.client.calls)
        sqs.change_message_visibility.assert_not_called()

    def test_cancelled_job_is_not_requeued_by_interruption(self):
        message, received, db, sqs, worker = self.make_worker()
        db.Table("Jobs").item["status"] = "CANCELLED"
        with self.assertRaisesRegex(RuntimeError, "TransactionCanceledException"):
            self.finish_interrupted(worker, message, received)
        self.assertEqual([], db.meta.client.usage)
        self.assertEqual("RUNNING", worker._task(message)["status"])
        sqs.change_message_visibility.assert_not_called()

    def test_both_tasks_on_interrupted_instance_are_returned(self):
        message, received, db, sqs, worker = self.make_worker()
        other = {**message, "taskId": "task-0002"}
        db.Table("Jobs").item["tasksRemaining"] = 2
        db.Table("Tasks").rows[(message["jobId"], other["taskId"])] = {
            **worker._task(message), "owner": "other-owner",
        }
        self.finish_interrupted(worker, message, received)
        worker._finish(other, {"ReceiptHandle": "other", "Body": json.dumps(other)},
                       "other-owner", 1, 100, 8, OUTCOME_INTERRUPTED, None, "other-log", "Spot")
        self.assertEqual(2, len(db.meta.client.usage))
        self.assertEqual(2, db.Table("Jobs").item["tasksRemaining"])
        self.assertEqual(20, db.Table("Jobs").item["usedVcpuSeconds"])
        self.assertEqual("QUEUED", worker._task(other)["status"])

    def test_notice_during_long_poll_returns_unclaimed_message(self):
        message, received, db, sqs, worker = self.make_worker()
        def receive(**kwargs):
            worker.request_interruption(NOTICE)
            return {"Messages": [received]}
        sqs.receive_message = Mock(side_effect=receive)
        worker.process = Mock()
        worker.spot_monitor = Mock()
        worker._wait_for_shutdown = Mock()
        with self.assertLogs("worker.agent", level="WARNING"):
            worker.run()
        worker.process.assert_not_called()
        worker.spot_monitor.start.assert_called_once()
        worker.spot_monitor.stop.assert_called_once()
        worker._wait_for_shutdown.assert_called_once_with()
        sqs.change_message_visibility.assert_called_once()
        self.assertEqual([], db.meta.client.usage)

    def test_notice_wakes_worker_waiting_for_free_capacity(self):
        message, received, db, sqs, worker = self.make_worker()
        worker._used_slots = 2
        worker.spot_monitor = Mock()
        worker._wait_for_shutdown = Mock()
        worker.spot_monitor.start.side_effect = lambda: worker.request_interruption(NOTICE)
        sqs.receive_message = Mock()
        with self.assertLogs("worker.agent", level="WARNING"):
            worker.run()
        sqs.receive_message.assert_not_called()
        self.assertFalse(worker._reserve(1))
        worker._wait_for_shutdown.assert_called_once_with()

    def test_task_not_claimed_if_notice_arrives_before_process(self):
        message, received, db, sqs, worker = self.make_worker()
        worker._draining.set()
        worker._task = Mock()
        worker.process(received, message)
        worker._task.assert_not_called()
        sqs.change_message_visibility.assert_called_once()

    def test_ondemand_worker_does_not_start_imds_monitor(self):
        message, received, db, sqs, old = self.make_worker()
        worker = Worker(dynamodb=db, s3=None, sqs=sqs, config=old.config, queue="ondemand")
        worker._wait_for_shutdown = Mock()
        def receive(**kwargs):
            worker._draining.set()
            return {}
        sqs.receive_message = receive
        with patch("worker.agent.SpotMonitor") as monitor:
            worker.run()
        monitor.assert_not_called()

    def test_notice_before_claim_returns_message_without_changing_task(self):
        for stage in ("task_read", "job_read", "table_lookup"):
            with self.subTest(stage=stage):
                message, received, db, sqs, worker = self.make_worker()
                task = worker._task(message)
                task["status"] = "QUEUED"
                job = worker._job(message)
                worker._task = Mock(return_value=task)
                worker._job = Mock(return_value=job)
                worker._execute = Mock()
                tasks = db.Table("Tasks")
                tasks.update_item = Mock()

                def interrupt_and_return(value):
                    worker.request_interruption(NOTICE)
                    return value

                if stage == "task_read":
                    worker._task.side_effect = lambda _: interrupt_and_return(task)
                elif stage == "job_read":
                    worker._job.side_effect = lambda _: interrupt_and_return(job)
                else:
                    original_table = db.Table
                    db.Table = Mock(side_effect=lambda name: interrupt_and_return(original_table(name)))
                with self.assertLogs("worker.agent", level="WARNING"):
                    worker.process(received, message)
                tasks.update_item.assert_not_called()
                worker._execute.assert_not_called()
                sqs.change_message_visibility.assert_called_once_with(
                    QueueUrl=worker.queue_url, ReceiptHandle=received["ReceiptHandle"], VisibilityTimeout=0,
                )
                self.assertEqual("QUEUED", task["status"])
                self.assertEqual(1, task["attempt"])
                self.assertEqual([], db.meta.client.usage)

    def test_run_waits_for_both_task_threads_before_parking(self):
        message, received, db, sqs, worker = self.make_worker()
        other = {**message, "taskId": "task-0002",
                 "input": {**message["input"],
                           "key": message["input"]["key"].replace(message["taskId"], "task-0002")},
                 "output": {**message["output"],
                            "key": message["output"]["key"].replace(message["taskId"], "task-0002")}}
        worker.spot_monitor = Mock()
        both_running, allow_finish = threading.Barrier(3), threading.Event()
        finished = []
        sqs.receive_message = Mock(side_effect=[
            {"Messages": [received]},
            {"Messages": [{"Body": json.dumps(other), "ReceiptHandle": "other"}]},
        ])

        def process(receipt, body):
            both_running.wait(timeout=3)
            if not allow_finish.wait(timeout=3):
                raise RuntimeError("Test did not release task threads")
            finished.append(body["taskId"])

        def park():
            self.assertEqual(2, len(finished))
            self.assertEqual(0, worker._used_slots)
            worker.spot_monitor.stop.assert_called_once_with()

        worker.process = Mock(side_effect=process)
        worker._wait_for_shutdown = Mock(side_effect=park)
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(worker.run)
            try:
                both_running.wait(timeout=3)
                with self.assertLogs("worker.agent", level="WARNING"):
                    worker.request_interruption(NOTICE)
                worker._wait_for_shutdown.assert_not_called()
                self.assertFalse(running.done())
            finally:
                allow_finish.set()
            running.result(timeout=3)
        worker._wait_for_shutdown.assert_called_once_with()
        self.assertEqual(2, sqs.receive_message.call_count)

    def test_notice_on_start_parks_without_receiving_messages(self):
        message, received, db, sqs, worker = self.make_worker()
        worker.spot_monitor = Mock()
        worker.spot_monitor.start.side_effect = lambda: worker.request_interruption(NOTICE)
        worker._wait_for_shutdown = Mock()
        sqs.receive_message = Mock()
        with self.assertLogs("worker.agent", level="WARNING"):
            worker.run()
        sqs.receive_message.assert_not_called()
        worker._wait_for_shutdown.assert_called_once_with()
        worker.spot_monitor.stop.assert_called_once_with()

    def test_normal_run_return_does_not_park(self):
        message, received, db, sqs, worker = self.make_worker()
        worker.spot_monitor = Mock()
        worker._poll_tasks = Mock()
        worker._wait_for_shutdown = Mock()
        worker.run()
        worker._wait_for_shutdown.assert_not_called()
        worker.spot_monitor.stop.assert_called_once_with()

    def test_shutdown_wait_blocks_even_when_draining_is_already_set(self):
        message, received, db, sqs, worker = self.make_worker()
        worker._draining.set()
        with patch("worker.agent.threading.Event") as event_class:
            # Giả lập hệ điều hành dừng process, không ngủ thật trong test.
            event_class.return_value.wait.side_effect = KeyboardInterrupt
            with self.assertLogs("worker.agent", level="WARNING"):
                with self.assertRaises(KeyboardInterrupt):
                    worker._wait_for_shutdown()
        event_class.assert_called_once_with()
        event_class.return_value.wait.assert_called_once_with()

    def test_notice_during_container_stops_before_next_heartbeat(self):
        message, received, db, sqs, worker = self.make_worker()
        worker.s3 = Mock()
        worker.config.update(VIDEO_JOB_IMAGE="image", TASK_MAX_SECONDS="100")
        worker.s3.download_file.side_effect = lambda b, k, p, **kw: Path(p).write_bytes(b"input")
        worker._heartbeat = Mock(return_value=True)
        worker._finish = Mock()
        proc, clock = Mock(), [10]
        proc.stdout = io.BytesIO(b"log")
        proc.poll.return_value = None
        proc.returncode = -9
        def wait(timeout):
            if timeout == 15:
                return 0
            clock[0] = 11
            worker._draining.set()
            raise subprocess.TimeoutExpired("docker", timeout)
        proc.wait.side_effect = wait
        with patch("worker.agent.subprocess.Popen", return_value=proc), \
             patch("worker.agent.subprocess.run") as stop, \
             patch("worker.agent.time.monotonic", side_effect=lambda: clock[0]):
            worker._execute(received, message, {}, "owner", 1, 100)
        stop.assert_called_once()
        self.assertEqual((1, OUTCOME_INTERRUPTED), worker._finish.call_args.args[5:7])
        self.assertIsNone(worker._finish.call_args.args[7])
        worker._heartbeat.assert_called_once()

    def test_notice_during_download_aborts_without_starting_docker(self):
        message, received, db, sqs, worker = self.make_worker()
        worker.s3 = Mock()
        worker._finish = Mock()
        def download(bucket, key, path, Callback):
            worker._draining.set()
            Callback(1)
        worker.s3.download_file.side_effect = download
        with patch("worker.agent.subprocess.Popen") as start:
            worker._execute(received, message, {}, "owner", 1, 100)
        start.assert_not_called()
        self.assertEqual((0, OUTCOME_INTERRUPTED), worker._finish.call_args.args[5:7])

    def test_notice_during_result_upload_records_interruption(self):
        message, received, db, sqs, worker = self.make_worker()
        worker.s3 = Mock()
        worker.config.update(VIDEO_JOB_IMAGE="image", TASK_MAX_SECONDS="100")
        worker.s3.download_file.side_effect = lambda b, k, p, **kw: Path(p).write_bytes(b"input")
        worker._heartbeat = Mock(return_value=True)
        worker._finish = Mock()
        proc = Mock()
        proc.stdout, proc.returncode = io.BytesIO(b"log"), 0
        proc.poll.return_value = 0
        def start(command, **kwargs):
            mount = next(value for value in command if "target=/output" in value)
            output = mount.split("source=", 1)[1].split(",target=", 1)[0]
            (Path(output) / "result").write_bytes(b"result")
            return proc
        def upload(path, bucket, key, **kwargs):
            if "Callback" in kwargs:
                worker._draining.set()
                kwargs["Callback"](1)
        worker.s3.upload_fileobj.side_effect = upload
        with patch("worker.agent.subprocess.Popen", side_effect=start):
            worker._execute(received, message, {}, "owner", 1, 100)
        self.assertEqual(OUTCOME_INTERRUPTED, worker._finish.call_args.args[6])
        self.assertIsNone(worker._finish.call_args.args[7])
        self.assertEqual(1, worker.s3.upload_fileobj.call_count)
        self.assertEqual(1, worker.s3.upload_file.call_count)


if __name__ == "__main__":
    unittest.main()
