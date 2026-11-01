"""Kiểm tra bảo vệ scale-in bằng client giả, không gọi AWS thật."""

import os
import sys
import threading
import types
import unittest
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from unittest.mock import Mock, call, patch

from worker.agent import Worker, main


class WorkerProtectionTests(unittest.TestCase):
    def make_worker(self, queue="spot"):
        autoscaling = Mock()
        worker = Worker(
            dynamodb=Mock(), s3=Mock(), sqs=Mock(), autoscaling=autoscaling,
            config={"SQS_SPOT_URL": "spot-url", "SQS_ONDEMAND_URL": "ondemand-url",
                    "SPOTPOOL_INSTANCE_ID": "i-0123456789abcdef0",
                    "SPOTPOOL_ASG": f"spotpool-worker-{queue}"}, queue=queue,
        )
        return worker, autoscaling

    def protection_call(self, protected, queue="spot"):
        return call(InstanceIds=["i-0123456789abcdef0"],
                    AutoScalingGroupName=f"spotpool-worker-{queue}",
                    ProtectedFromScaleIn=protected)

    def test_main_supplies_autoscaling_client_using_existing_aws_config(self):
        boto3 = types.ModuleType("boto3")
        clients = {name: Mock() for name in ("s3", "sqs", "autoscaling")}
        boto3.client = Mock(side_effect=lambda service, **kwargs: clients[service])
        boto3.resource = Mock()
        config_module = types.ModuleType("botocore.config")
        config_module.Config = Mock()
        environment = {name: "test-value" for name in (
            "JOBS_TABLE", "TASKS_TABLE", "USAGE_TABLE", "SQS_ONDEMAND_URL", "SQS_SPOT_URL",
            "VIDEO_JOB_IMAGE", "SCRIPT_JOB_IMAGE", "TASK_MAX_SECONDS",
        )}
        environment.update(AWS_DEFAULT_REGION="ap-southeast-1", SPOTPOOL_QUEUE="spot")
        with patch.dict(sys.modules, {"boto3": boto3, "botocore.config": config_module}), \
                patch.dict(os.environ, environment, clear=True), \
                patch("worker.agent.Worker") as worker_class, \
                patch("worker.agent.logging.basicConfig"):
            main()
        self.assertIn(call("autoscaling", region_name="ap-southeast-1",
                           config=config_module.Config.return_value), boto3.client.call_args_list)
        self.assertIs(clients["autoscaling"], worker_class.call_args.kwargs["autoscaling"])
        worker_class.return_value.run.assert_called_once_with()

    def test_two_single_slots_remain_protected_until_both_are_free(self):
        for queue in ("spot", "ondemand"):
            with self.subTest(queue=queue):
                worker, client = self.make_worker(queue)
                self.assertTrue(worker._reserve(1))
                self.assertTrue(worker._reserve(1))
                client.set_instance_protection.assert_has_calls([self.protection_call(True, queue)])
                worker._release(1)
                self.assertEqual(1, client.set_instance_protection.call_count)
                worker._release(1)
                self.assertEqual([self.protection_call(True, queue), self.protection_call(False, queue)],
                                 client.set_instance_protection.call_args_list)
                self.assertEqual(0, worker._used_slots)

    def test_double_slot_and_next_task_cycle(self):
        worker, client = self.make_worker()
        self.assertTrue(worker._reserve(2))
        self.assertFalse(worker._reserve(1))
        worker._release(2)
        self.assertTrue(worker._reserve(1))
        worker._release(1)
        self.assertEqual([self.protection_call(value) for value in (True, False, True, False)],
                         client.set_instance_protection.call_args_list)

    def test_draining_worker_does_not_reserve_or_enable_protection(self):
        worker, client = self.make_worker()
        with self.assertLogs("worker.agent", level="WARNING"):
            worker.request_interruption({"action": "terminate", "time": "2026-10-30T12:00:00Z"})
        self.assertFalse(worker._reserve(1))
        self.assertEqual(0, worker._used_slots)
        client.set_instance_protection.assert_not_called()

    def test_aws_failures_log_and_do_not_stop_processing_or_leak_capacity(self):
        worker, client = self.make_worker()
        client.set_instance_protection.side_effect = RuntimeError("AccessDenied")
        worker.process = Mock()
        with self.assertLogs("worker.agent", level="ERROR") as captured:
            self.assertTrue(worker._reserve(1))
            worker._process_reserved({}, {"jobId": "job", "taskId": "task"}, 1)
        worker.process.assert_called_once()
        self.assertEqual(0, worker._used_slots)
        self.assertEqual([self.protection_call(True), self.protection_call(False)],
                         client.set_instance_protection.call_args_list)
        self.assertEqual(2, len(captured.records))
        self.assertTrue(all(record.exc_info for record in captured.records))

    def test_task_failure_still_disables_protection_in_finally(self):
        worker, client = self.make_worker()
        worker.process = Mock(side_effect=RuntimeError("Task failed"))
        self.assertTrue(worker._reserve(1))
        with self.assertLogs("worker.agent", level="ERROR"):
            with self.assertRaisesRegex(RuntimeError, "Task failed"):
                worker._process_reserved({}, {"jobId": "job", "taskId": "task"}, 1)
        self.assertEqual(0, worker._used_slots)
        self.assertEqual([self.protection_call(True), self.protection_call(False)],
                         client.set_instance_protection.call_args_list)

    def test_missing_identity_logs_and_keeps_running(self):
        worker, client = self.make_worker()
        for field in ("instance_id", "asg_name"):
            with self.subTest(field=field):
                original = getattr(worker, field)
                setattr(worker, field, None)
                with self.assertLogs("worker.agent", level="WARNING"):
                    self.assertTrue(worker._reserve(1))
                    worker._release(1)
                setattr(worker, field, original)
        self.assertEqual(0, worker._used_slots)
        client.set_instance_protection.assert_not_called()

    def test_concurrent_reservations_enable_protection_once(self):
        worker, client = self.make_worker()
        start = threading.Barrier(3)

        def reserve():
            start.wait(timeout=3)
            return worker._reserve(1)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(reserve) for _ in range(2)]
            start.wait(timeout=3)
            self.assertEqual([True, True], [future.result(timeout=3) for future in futures])
        self.assertEqual(2, worker._used_slots)
        client.set_instance_protection.assert_called_once_with(
            **self.protection_call(True).kwargs,
        )

    def test_new_task_waits_until_old_disable_finishes_then_enables_again(self):
        worker, client = self.make_worker()
        self.assertTrue(worker._reserve(1))
        disabling, allow_disable, reserve_started = (threading.Event() for _ in range(3))
        remote = {"protected": True}

        def set_protection(**kwargs):
            if not kwargs["ProtectedFromScaleIn"]:
                disabling.set()
                if not allow_disable.wait(timeout=3):
                    raise RuntimeError("Test did not release disable call")
            remote["protected"] = kwargs["ProtectedFromScaleIn"]

        def reserve():
            reserve_started.set()
            return worker._reserve(1)

        client.set_instance_protection.side_effect = set_protection
        with ThreadPoolExecutor(max_workers=2) as pool:
            release = pool.submit(worker._release, 1)
            try:
                self.assertTrue(disabling.wait(timeout=3))
                reservation = pool.submit(reserve)
                self.assertTrue(reserve_started.wait(timeout=3))
                with self.assertRaises(FutureTimeout):
                    reservation.result(timeout=0.05)
            finally:
                allow_disable.set()
            release.result(timeout=3)
            self.assertTrue(reservation.result(timeout=3))
        self.assertEqual(1, worker._used_slots)
        self.assertTrue(remote["protected"])
        self.assertEqual([self.protection_call(value) for value in (True, False, True)],
                         client.set_instance_protection.call_args_list)

    def test_slow_aws_call_does_not_block_spot_notice(self):
        worker, client = self.make_worker()
        enabling, allow_enable = threading.Event(), threading.Event()

        def set_protection(**kwargs):
            enabling.set()
            if not allow_enable.wait(timeout=3):
                raise RuntimeError("Test did not release enable call")

        client.set_instance_protection.side_effect = set_protection
        with ThreadPoolExecutor(max_workers=2) as pool:
            reservation = pool.submit(worker._reserve, 1)
            try:
                self.assertTrue(enabling.wait(timeout=3))
                with self.assertLogs("worker.agent", level="WARNING"):
                    notice = pool.submit(worker.request_interruption,
                                         {"action": "terminate", "time": "2026-10-30T12:00:00Z"})
                    notice.result(timeout=1)
                self.assertTrue(worker._draining.is_set())
            finally:
                allow_enable.set()
            self.assertTrue(reservation.result(timeout=3))
        self.assertFalse(worker._reserve(1))
        worker._release(1)


if __name__ == "__main__":
    unittest.main()
