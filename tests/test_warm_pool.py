"""Warm pool gói Ổn định: worker chờ InService, dispatcher bật máy đầu tiên. Dùng client giả, không gọi AWS."""

import io
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError

from services.dispatcher.app import dispatch_record, start_first_instance
from tests.test_tv2_week1 import CONFIG, JOB, JOB_FIELDS, RECORD, USER, FakeDB, FakeS3, FakeSQS, manifest
from worker.spot import IMDS_BASE, InstanceMetadata
from worker.warm import READ_ATTEMPTS, complete_hook, wait_until_in_service

ASG = "spotpool-worker-ondemand"
INSTANCE = "i-0123456789abcdef0"
HOOK = "worker-ready"


def hook_call():
    return dict(LifecycleHookName=HOOK, AutoScalingGroupName=ASG,
                LifecycleActionResult="CONTINUE", InstanceId=INSTANCE)


class TargetStateTests(unittest.TestCase):
    def test_reads_state_with_token(self):
        opener = Mock()
        opener.open.side_effect = [io.BytesIO(b"token"), io.BytesIO(b"Warmed:Stopped\n")]
        self.assertEqual("Warmed:Stopped", InstanceMetadata(opener).target_lifecycle_state())
        request = opener.open.call_args.args[0]
        self.assertEqual(f"{IMDS_BASE}/meta-data/autoscaling/target-lifecycle-state", request.full_url)
        self.assertEqual("token", dict(request.header_items())["X-aws-ec2-metadata-token"])

    def test_404_means_not_in_an_asg(self):
        opener = Mock()
        opener.open.side_effect = [io.BytesIO(b"token"),
                                   HTTPError("url", 404, "Not Found", {}, io.BytesIO())]
        self.assertIsNone(InstanceMetadata(opener).target_lifecycle_state())


class WaitUntilInServiceTests(unittest.TestCase):
    def run_wait(self, states):
        metadata = Mock()
        metadata.target_lifecycle_state.side_effect = states
        autoscaling, sleep = Mock(), Mock()
        result = wait_until_in_service(metadata, autoscaling, HOOK, ASG, INSTANCE, sleep=sleep)
        return result, autoscaling, sleep

    def test_warm_pool_launch_reports_hook_then_waits_without_taking_tasks(self):
        # Máy mới vào warm pool: báo hook một lần, chờ tới khi (giả lập) được đưa vào InService
        result, autoscaling, sleep = self.run_wait(["Warmed:Stopped", "Warmed:Stopped", "InService"])
        self.assertEqual("InService", result)
        self.assertEqual([hook_call(), hook_call()],
                         [c.kwargs for c in autoscaling.complete_lifecycle_action.call_args_list])
        self.assertEqual(2, sleep.call_count)

    def test_started_from_warm_pool_reports_hook_and_returns_at_once(self):
        result, autoscaling, sleep = self.run_wait(["InService"])
        self.assertEqual("InService", result)
        autoscaling.complete_lifecycle_action.assert_called_once_with(**hook_call())
        sleep.assert_not_called()

    def test_instance_outside_asg_takes_tasks_without_hook(self):
        result, autoscaling, _ = self.run_wait([None])
        self.assertIsNone(result)
        autoscaling.complete_lifecycle_action.assert_not_called()

    def test_unreadable_metadata_does_not_block_forever(self):
        with self.assertLogs("worker.warm", level="ERROR"):
            result, autoscaling, sleep = self.run_wait([RuntimeError("timeout")] * READ_ATTEMPTS)
        self.assertIsNone(result)
        self.assertEqual(READ_ATTEMPTS - 1, sleep.call_count)
        autoscaling.complete_lifecycle_action.assert_not_called()

    def test_hook_already_completed_is_ignored(self):
        autoscaling = Mock()
        autoscaling.complete_lifecycle_action.side_effect = RuntimeError(
            "ValidationError: No active Lifecycle Action found with instance ID " + INSTANCE)
        complete_hook(autoscaling, HOOK, ASG, INSTANCE)  # không log lỗi, không ném ngoại lệ

    def test_other_hook_errors_are_logged_not_raised(self):
        autoscaling = Mock()
        autoscaling.complete_lifecycle_action.side_effect = RuntimeError("AccessDenied")
        with self.assertLogs("worker.warm", level="ERROR"):
            complete_hook(autoscaling, HOOK, ASG, INSTANCE)


class StartFirstInstanceTests(unittest.TestCase):
    def client(self, desired, max_size=4):
        autoscaling = Mock()
        autoscaling.describe_auto_scaling_groups.return_value = {
            "AutoScalingGroups": [{"DesiredCapacity": desired, "MaxSize": max_size}]}
        return autoscaling

    def test_empty_group_gets_one_instance_now(self):
        autoscaling = self.client(0)
        self.assertTrue(start_first_instance(autoscaling, ASG))
        autoscaling.set_desired_capacity.assert_called_once_with(
            AutoScalingGroupName=ASG, DesiredCapacity=1, HonorCooldown=False)

    def test_running_group_is_left_to_target_tracking(self):
        autoscaling = self.client(2)
        self.assertFalse(start_first_instance(autoscaling, ASG))
        autoscaling.set_desired_capacity.assert_not_called()

    def test_disabled_group_is_not_started(self):
        autoscaling = self.client(0, max_size=0)
        self.assertFalse(start_first_instance(autoscaling, ASG))
        autoscaling.set_desired_capacity.assert_not_called()

    def test_errors_fall_back_to_cloudwatch_alarm(self):
        autoscaling = Mock()
        autoscaling.describe_auto_scaling_groups.side_effect = RuntimeError("AccessDenied")
        with self.assertLogs("services.dispatcher.app", level="ERROR"):
            self.assertFalse(start_first_instance(autoscaling, ASG))


class DispatchStartsWorkerTests(unittest.TestCase):
    def dispatch(self, tier, config):
        db = FakeDB({**JOB_FIELDS, "jobId": JOB, "userId": USER, "status": "QUEUED",
                     "tier": tier, "taskCount": 2})
        sqs, autoscaling = FakeSQS(), Mock()
        autoscaling.describe_auto_scaling_groups.return_value = {
            "AutoScalingGroups": [{"DesiredCapacity": 0, "MaxSize": 4}]}
        count = dispatch_record(RECORD, s3=FakeS3(manifest(tier)), dynamodb=db, sqs=sqs,
                                config=config, autoscaling=autoscaling)
        return count, sqs, autoscaling

    def test_ondemand_job_starts_first_instance_after_queueing_tasks(self):
        count, sqs, autoscaling = self.dispatch("ondemand", {**CONFIG, "ONDEMAND_ASG": ASG})
        self.assertEqual(2, count)
        self.assertEqual(2, len(sqs.messages))
        autoscaling.set_desired_capacity.assert_called_once()

    def test_spot_job_keeps_cloudwatch_scaling(self):
        _, _, autoscaling = self.dispatch("spot", {**CONFIG, "ONDEMAND_ASG": ASG})
        autoscaling.describe_auto_scaling_groups.assert_not_called()

    def test_without_warm_pool_dispatcher_does_not_scale(self):
        _, _, autoscaling = self.dispatch("ondemand", CONFIG)
        autoscaling.describe_auto_scaling_groups.assert_not_called()


if __name__ == "__main__":
    unittest.main()
