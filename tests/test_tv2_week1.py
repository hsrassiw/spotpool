import io
import json
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

from services.dispatcher.app import dispatch_record
from services.dispatcher.dlq import fail_exhausted
from spotpool_core.contracts import canonical_json, fingerprint, validate_message
from worker.agent import Worker, docker_command, remaining_seconds

USER = "11111111-1111-4111-8111-111111111111"
JOB = "22222222-2222-4222-8222-222222222222"


def manifest(tier="spot", count=2):
    return {
        "schemaVersion": 1, "jobId": JOB, "userId": USER, "tier": tier,
        "kind": "video", "vcpu": 1, "image": "spotpool-video", "params": {},
        "outputBucket": "output-test",
        "inputs": [{"taskId": f"task-{i:04d}", "fileName": f"video{i}.mp4",
                    "bucket": "input-test", "key": f"uploads/{USER}/{JOB}/task-{i:04d}"}
                   for i in range(1, count + 1)],
    }


class FakeS3:
    def __init__(self, value):
        self.value = value

    def get_object(self, **kwargs):
        return {"Body": io.BytesIO(json.dumps(self.value).encode())}


class FakeTable:
    def __init__(self, item=None):
        self.item = item
        self.rows = {}

    def get_item(self, Key, **kwargs):
        if "taskId" in Key:
            return {"Item": self.rows.get((Key["jobId"], Key["taskId"]))}
        return {"Item": self.item}

    def put_item(self, Item, **kwargs):
        key = (Item["jobId"], Item["taskId"])
        if key in self.rows:
            raise RuntimeError("ConditionalCheckFailedException")
        self.rows[key] = Item

    def update_item(self, **kwargs):
        self.last_update = kwargs
        return {"Attributes": {"attempt": 1}}


class FakeDB:
    def __init__(self, job):
        self.tables = {"Jobs": FakeTable(job), "Tasks": FakeTable()}

    def Table(self, name):
        return self.tables[name]


class FakeTransactionClient:
    def transact_write_items(self, **kwargs):
        self.last_transaction = kwargs


class FakeSQS:
    def __init__(self):
        self.messages = []

    def send_message(self, **kwargs):
        self.messages.append(kwargs)

    def delete_message(self, **kwargs):
        self.deleted = kwargs


CONFIG = {"INPUT_BUCKET": "input-test", "OUTPUT_BUCKET": "output-test",
          "JOBS_TABLE": "Jobs", "TASKS_TABLE": "Tasks",
          "SQS_SPOT_URL": "spot-url", "SQS_ONDEMAND_URL": "ondemand-url"}
RECORD = {"s3": {"bucket": {"name": "input-test"},
                 "object": {"key": f"submissions/{USER}/{JOB}/manifest.json"}}}
JOB_FIELDS = {"kind": "video", "vcpu": 1, "image": "spotpool-video", "params": {},
              "files": ["video1.mp4", "video2.mp4"]}


class DispatcherTests(unittest.TestCase):
    def test_dispatch_and_replay_are_idempotent_at_task_level(self):
        data = manifest()
        db = FakeDB({**JOB_FIELDS, "jobId": JOB, "userId": USER, "status": "QUEUED",
                     "tier": "spot", "taskCount": 2})
        sqs = FakeSQS()
        for _ in range(2):
            self.assertEqual(2, dispatch_record(RECORD, s3=FakeS3(data), dynamodb=db,
                                                sqs=sqs, config=CONFIG, now=100))
        self.assertEqual(2, len(db.tables["Tasks"].rows))
        self.assertEqual(4, len(sqs.messages))
        self.assertTrue(all(sent["QueueUrl"] == "spot-url" for sent in sqs.messages))
        first = json.loads(sqs.messages[0]["MessageBody"])
        validate_message(first)
        self.assertEqual(fingerprint(first), db.tables["Tasks"].rows[(JOB, "task-0001")]["fingerprint"])
        self.assertEqual(canonical_json(first), sqs.messages[2]["MessageBody"])

    def test_manifest_key_mismatch_is_rejected_before_writes(self):
        data = manifest()
        data["inputs"][0]["key"] = "uploads/other/file"
        db = FakeDB({**JOB_FIELDS, "jobId": JOB, "userId": USER, "status": "QUEUED",
                     "tier": "spot", "taskCount": 2})
        sqs = FakeSQS()
        with self.assertRaises(ValueError):
            dispatch_record(RECORD, s3=FakeS3(data), dynamodb=db,
                            sqs=sqs, config=CONFIG)
        self.assertEqual({}, db.tables["Tasks"].rows)
        self.assertEqual([], sqs.messages)

    def test_terminal_job_is_not_queued(self):
        db = FakeDB({**JOB_FIELDS, "jobId": JOB, "userId": USER, "status": "CANCELLED",
                     "tier": "spot", "taskCount": 2})
        sqs = FakeSQS()
        self.assertEqual(0, dispatch_record(RECORD, s3=FakeS3(manifest()),
                                            dynamodb=db, sqs=sqs, config=CONFIG))
        self.assertFalse(sqs.messages)


class WorkerContractTests(unittest.TestCase):
    def test_budget_limits_execution_time(self):
        job = {"unitPrice": 3600, "costCap": 100, "usedVcpuSeconds": 20}
        self.assertEqual(80, remaining_seconds(job, 1))
        self.assertEqual(40, remaining_seconds(job, 2))
        self.assertEqual(0, remaining_seconds({**job, "usedVcpuSeconds": 100}, 1))

    def test_docker_is_isolated_and_scales_memory(self):
        command = docker_command("example/image:1", 2, "/tmp/source", "/tmp/out", "test")
        for flag in ("--network", "none", "--read-only", "--cap-drop", "ALL",
                     "--user", "65534:65534", "--cpus", "2", "--memory", "3072m"):
            self.assertIn(flag, command)

    def test_video_height_is_passed_as_a_container_environment_value(self):
        command = docker_command(
            "example/video:1", 1, "/tmp/source", "/tmp/out", "test",
            video_height=720,
        )
        self.assertIn("--env", command)
        self.assertIn("SPOTPOOL_VIDEO_HEIGHT=720", command)
        self.assertEqual("example/video:1", command[-3])

    def test_worker_routes_message_to_current_task_queue(self):
        message = self._message()
        db, sqs, worker = self._worker(message, task_status="QUEUED", queue="ondemand")
        received = {"Body": canonical_json(message), "ReceiptHandle": "receipt"}
        worker.process(received, message)
        self.assertEqual("ondemand-url", sqs.messages[0]["QueueUrl"])
        self.assertEqual(received["Body"], sqs.messages[0]["MessageBody"])
        self.assertEqual("receipt", sqs.deleted["ReceiptHandle"])

    def test_worker_claims_once_before_execution(self):
        message = self._message()
        db, sqs, worker = self._worker(message)
        calls = []
        worker._execute = lambda *args: calls.append(args)
        worker.process({"Body": canonical_json(message), "ReceiptHandle": "receipt"}, message)
        self.assertEqual(1, len(calls))
        condition = db.Table("Tasks").last_update["ConditionExpression"]
        self.assertIn("fingerprint=:fp", condition)
        self.assertIn("leaseUntil<:now", condition)
        self.assertEqual("RUNNING", db.Table("Jobs").last_update["ExpressionAttributeValues"][":running"])

    def test_finish_records_task_job_and_usage_atomically(self):
        message = self._message()
        for status in ("SUCCEEDED", "FAILED", "PAUSED"):
            with self.subTest(status=status):
                db, sqs, worker = self._worker(message)
                db.meta = types.SimpleNamespace(client=FakeTransactionClient())
                worker._finish(message, {"ReceiptHandle": "receipt"}, "owner", 1,
                               100, 3, status, "result" if status == "SUCCEEDED" else None,
                               "log", "")
                actions = db.meta.client.last_transaction["TransactItems"]
                self.assertEqual(["Update", "Update", "Put"], [next(iter(a)) for a in actions])
                task_update = actions[0]["Update"]
                self.assertEqual({"jobId": JOB, "taskId": "task-0001"}, task_update["Key"])
                self.assertEqual(status, task_update["ExpressionAttributeValues"][":status"])
                self.assertEqual("owner", task_update["ExpressionAttributeValues"][":owner"])
                if status == "SUCCEEDED":
                    self.assertEqual("result", task_update["ExpressionAttributeValues"][":result"])
                job_values = actions[1]["Update"]["ExpressionAttributeValues"]
                self.assertEqual(3, job_values[":used"])
                self.assertIs(type(job_values[":used"]), int)
                self.assertEqual(0, job_values[":zero"])
                usage = actions[2]["Put"]["Item"]
                self.assertEqual(USER, usage["userId"])
                self.assertEqual(3, usage["vcpuSeconds"])
                self.assertEqual(status, usage["outcome"])
                job_expression = actions[1]["Update"]["UpdateExpression"]
                self.assertIn("usedVcpuSeconds", job_expression)
                self.assertEqual(status != "PAUSED", "tasksRemaining" in job_expression)
                self.assertEqual("receipt", sqs.deleted["ReceiptHandle"])

    def test_thread_failure_logs_traceback_and_releases_capacity(self):
        message = self._message()
        _, sqs, worker = self._worker(message)
        worker.process = Mock(side_effect=RuntimeError("transaction failed"))
        self.assertTrue(worker._reserve(1))
        with self.assertLogs("worker.agent", level="ERROR") as captured:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(worker._process_reserved, {}, message, 1)
                with self.assertRaisesRegex(RuntimeError, "transaction failed"):
                    future.result(timeout=5)
        self.assertEqual(0, worker._used_slots)
        self.assertFalse(hasattr(sqs, "deleted"))
        self.assertIn(JOB, captured.output[0])
        self.assertIn(message["taskId"], captured.output[0])
        self.assertIn("Traceback", captured.output[0])
        self.assertIsInstance(captured.records[0].exc_info[1], RuntimeError)

    def test_dlq_transaction_uses_native_python_values(self):
        message = self._message()
        for status in ("QUEUED", "RUNNING"):
            with self.subTest(status=status):
                db, _, _ = self._worker(message, task_status=status)
                db.tables["Tasks"].rows[(JOB, "task-0001")]["leaseUntil"] = 99
                db.meta = types.SimpleNamespace(client=FakeTransactionClient())
                self.assertTrue(fail_exhausted(message, db, "Tasks", "Jobs", now=100))
                actions = db.meta.client.last_transaction["TransactItems"]
                self.assertEqual(["Update", "Update"], [next(iter(a)) for a in actions])
                task_update, job_update = (action["Update"] for action in actions)
                self.assertEqual({"jobId": JOB, "taskId": "task-0001"}, task_update["Key"])
                self.assertEqual("FAILED", task_update["ExpressionAttributeValues"][":failed"])
                self.assertEqual(100, task_update["ExpressionAttributeValues"][":now"])
                self.assertEqual(fingerprint(message), task_update["ExpressionAttributeValues"][":fp"])
                self.assertEqual({"jobId": JOB}, job_update["Key"])
                self.assertEqual(-1, job_update["ExpressionAttributeValues"][":minusOne"])
                self.assertEqual(1, job_update["ExpressionAttributeValues"][":one"])

    def test_dlq_does_not_fail_task_with_active_lease(self):
        message = self._message()
        db, _, _ = self._worker(message, task_status="RUNNING")
        db.tables["Tasks"].rows[(JOB, "task-0001")]["leaseUntil"] = 200
        db.meta = types.SimpleNamespace(client=FakeTransactionClient())
        self.assertFalse(fail_exhausted(message, db, "Tasks", "Jobs", now=100))
        self.assertFalse(hasattr(db.meta.client, "last_transaction"))

    @staticmethod
    def _message():
        return {
            "schemaVersion": 1, "jobId": JOB, "taskId": "task-0001", "userId": USER,
            "tier": "spot", "kind": "video", "vcpu": 1, "image": "spotpool-video",
            "params": {}, "input": {"bucket": "input-test", "key": f"uploads/{USER}/{JOB}/task-0001"},
            "output": {"bucket": "output-test", "key": f"results/{USER}/{JOB}/task-0001"},
        }

    @staticmethod
    def _worker(message, task_status="QUEUED", queue="spot"):
        job = {"jobId": JOB, "userId": USER, "status": "QUEUED", "costCap": 100,
               "unitPrice": 3600, "usedVcpuSeconds": 0}
        db = FakeDB(job)
        db.tables["Tasks"].rows[(JOB, "task-0001")] = {
            "fingerprint": fingerprint(message), "status": task_status, "queue": queue,
        }
        sqs = FakeSQS()
        worker = Worker(dynamodb=db, s3=None, sqs=sqs,
                        config={**CONFIG, "USAGE_TABLE": "Usage"}, queue="spot")
        return db, sqs, worker


if __name__ == "__main__":
    unittest.main()
