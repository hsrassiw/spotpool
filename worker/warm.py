"""Máy gói Ổn định trong warm pool: cài sẵn rồi tắt, chỉ nhận task khi ASG đưa máy vào InService."""

import logging
import time

logger = logging.getLogger(__name__)
POLL_SECONDS = 5
READ_ATTEMPTS = 6  # khoảng 30 s không đọc được IMDS thì vẫn nhận task, để máy không đứng im


def complete_hook(autoscaling, hook, asg, instance_id):
    """Báo ASG máy đã sẵn sàng: máy mới vào warm pool thì ASG tắt nó, máy ra khỏi warm pool thì vào InService."""
    try:
        autoscaling.complete_lifecycle_action(
            LifecycleHookName=hook, AutoScalingGroupName=asg,
            LifecycleActionResult="CONTINUE", InstanceId=instance_id,
        )
        logger.info("Completed lifecycle hook %s for %s", hook, instance_id)
    except Exception as error:
        if "No active Lifecycle Action" in str(error):
            return  # worker khởi động lại sau khi đã báo, hoặc máy bật lúc chưa có hook
        logger.exception("Cannot complete lifecycle hook %s; ASG continues after heartbeat timeout", hook)


def wait_until_in_service(metadata, autoscaling, hook, asg, instance_id, sleep=time.sleep, interval=POLL_SECONDS):
    """Chặn tới khi máy được đưa vào InService. Máy đang vào warm pool thì báo hook rồi chờ bị tắt."""
    reported = None
    failures = 0
    while True:
        try:
            state = metadata.target_lifecycle_state()
        except Exception:
            failures += 1
            if failures >= READ_ATTEMPTS:
                logger.exception("Cannot read target lifecycle state; taking tasks anyway")
                return None
            sleep(interval)
            continue
        if state is None:
            return None  # máy không thuộc ASG
        if state != reported:
            complete_hook(autoscaling, hook, asg, instance_id)
            reported = state
            logger.info("Target lifecycle state: %s", state)
        if state == "InService":
            return state
        sleep(interval)
