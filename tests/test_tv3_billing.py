import unittest

from services.api.billing import _charge_for_job, _transaction


JOB = {
    "jobId": "22222222-2222-4222-8222-222222222222",
    "userId": "11111111-1111-4111-8111-111111111111",
    "status": "FAILED",
    "queuedAt": 100,
    "costCap": 5000,
    "unitPrice": 3000,
}


class BillingTests(unittest.TestCase):
    def test_charge_rounds_up_and_never_exceeds_cap(self):
        usage = [{"outcome": "SUCCEEDED", "vcpuSeconds": 1}]
        self.assertEqual(1, _charge_for_job({**JOB, "unitPrice": 1}, usage, False))
        self.assertEqual(5000, _charge_for_job(
            JOB, [{"outcome": "SUCCEEDED", "vcpuSeconds": 10000}], False,
        ))

    def test_free_failure_quota_exempts_only_failed_attempts(self):
        usage = [
            {"outcome": "SUCCEEDED", "vcpuSeconds": 3600},
            {"outcome": "FAILED", "vcpuSeconds": 3600},
            {"outcome": "PAUSED", "vcpuSeconds": 1800},
            {"outcome": "INTERRUPTED", "vcpuSeconds": 7200},
        ]
        self.assertEqual(4500, _charge_for_job(JOB, usage, True))
        self.assertEqual(7500, _charge_for_job({**JOB, "costCap": 10000}, usage, False))

    def test_same_day_quota_update_is_conditioned_on_observed_count(self):
        user = {"errorsDay": "2026-10-31", "errorsToday": 2}
        actions = _transaction(
            JOB, user, "Users", "Jobs", 100, "2026-10-31", 200, True,
        )
        update = actions[0]["Update"]
        self.assertIn("errorsToday = :next", update["UpdateExpression"])
        self.assertIn("errorsToday = :previous", update["ConditionExpression"])
        self.assertEqual({"N": "3"}, update["ExpressionAttributeValues"][":next"])

    def test_new_day_resets_failure_quota_count(self):
        user = {"errorsDay": "2026-10-30", "errorsToday": 3}
        actions = _transaction(
            JOB, user, "Users", "Jobs", 100, "2026-10-31", 200, True,
        )
        update = actions[0]["Update"]
        self.assertIn("errorsToday = :one_error", update["UpdateExpression"])
        self.assertIn("attribute_not_exists(errorsDay)", update["ConditionExpression"])
        self.assertEqual({"N": "1"}, update["ExpressionAttributeValues"][":one_error"])

    def test_transaction_refunds_held_credit_and_marks_job_settled_atomically(self):
        actions = _transaction(
            JOB, {}, "Users", "Jobs", 1200, "2026-10-31", 200, False,
        )
        self.assertEqual(["Update", "Update"], [next(iter(action)) for action in actions])
        user_update, job_update = (action["Update"] for action in actions)
        self.assertIn("creditHeld = creditHeld - :cap", user_update["UpdateExpression"])
        self.assertIn("creditAvailable = creditAvailable + :refund", user_update["UpdateExpression"])
        self.assertIn("runningJobs = runningJobs - :one", user_update["UpdateExpression"])
        self.assertEqual({"N": "3800"}, user_update["ExpressionAttributeValues"][":refund"])
        self.assertIn("attribute_not_exists(settledAt)", job_update["ConditionExpression"])
        self.assertIn("chargedCredit = :charge", job_update["UpdateExpression"])


if __name__ == "__main__":
    unittest.main()
