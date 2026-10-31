import json
import os
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("JOBS_TABLE", "Jobs")
os.environ.setdefault("USERS_TABLE", "Users")
os.environ.setdefault("TASKS_TABLE", "Tasks")
os.environ.setdefault("INPUT_BUCKET", "input-test")
os.environ.setdefault("OUTPUT_BUCKET", "output-test")
os.environ.setdefault("USAGE_TABLE", "Usage")
os.environ.setdefault("PRICE_ONDEMAND", "6000")
os.environ.setdefault("PRICE_SPOT", "3000")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
sys.path.insert(0, str(Path(__file__).parents[1] / "services" / "api"))

from services.api import authorizer, get_job_status, rotate_key, submit_job


USER_ID = "11111111-1111-4111-8111-111111111111"
JOB_ID = "22222222-2222-4222-8222-222222222222"


class SubmitJobValidationTests(unittest.TestCase):
    def setUp(self):
        self.valid = {
            "tier": "spot",
            "kind": "script",
            "vcpu": 1,
            "costCap": 5000,
            "files": ["input.py"],
            "params": {"threshold": 0.75, "options": [1.5, None]},
        }

    def test_requires_integer_cost_cap_and_vcpu(self):
        for field, value in (("costCap", True), ("costCap", 5000.0), ("vcpu", True), ("vcpu", None)):
            with self.subTest(field=field, value=value):
                request = {**self.valid, field: value}
                with self.assertRaises(ValueError):
                    submit_job._validated_request(request)

    def test_requires_nonempty_array_of_nonempty_file_names(self):
        for files in ("input.py", [], [""], ["  "], [None]):
            with self.subTest(files=files):
                with self.assertRaises(ValueError):
                    submit_job._validated_request({**self.valid, "files": files})

    def test_nested_params_floats_become_decimal_values(self):
        request = submit_job._validated_request(self.valid)
        params = request[-1]
        self.assertEqual(Decimal("0.75"), params["threshold"])
        self.assertEqual(Decimal("1.5"), params["options"][0])

    def test_rejects_non_finite_param_numbers(self):
        with self.assertRaisesRegex(ValueError, "non-finite"):
            submit_job._validated_request({**self.valid, "params": {"value": float("nan")}})

    def test_invalid_request_returns_cors_headers(self):
        response = submit_job.lambda_handler({"body": json.dumps({**self.valid, "vcpu": True})}, None)
        self.assertEqual(400, response["statusCode"])
        self.assertEqual("*", response["headers"]["Access-Control-Allow-Origin"])

    def test_successful_request_persists_decimal_params_and_returns_cors(self):
        table = Mock()
        with (
            patch.object(submit_job.dynamodb, "Table", return_value=table),
            patch.object(submit_job, "generate_presigned_upload_url", return_value="https://upload"),
        ):
            response = submit_job.lambda_handler({
                "requestContext": {"authorizer": {"userId": USER_ID}},
                "body": json.dumps(self.valid),
            }, None)

        self.assertEqual(200, response["statusCode"])
        self.assertEqual("*", response["headers"]["Access-Control-Allow-Origin"])
        item = table.put_item.call_args.kwargs["Item"]
        self.assertEqual(Decimal("0.75"), item["params"]["threshold"])
        self.assertEqual(Decimal("1.5"), item["params"]["options"][0])


class CorsAndAuthorizerTests(unittest.TestCase):
    def test_rotate_key_success_response_has_cors_headers(self):
        with patch.object(rotate_key.dynamodb, "Table") as table:
            response = rotate_key.lambda_handler({
                "requestContext": {"authorizer": {"userId": USER_ID}},
            }, None)
        self.assertEqual(200, response["statusCode"])
        self.assertEqual("*", response["headers"]["Access-Control-Allow-Origin"])
        table.return_value.update_item.assert_called_once()

    def test_authorizer_propagates_database_errors_instead_of_caching_deny(self):
        with patch.object(authorizer.dynamodb, "Table") as table:
            table.return_value.query.side_effect = RuntimeError("DynamoDB unavailable")
            with self.assertRaisesRegex(RuntimeError, "DynamoDB unavailable"):
                authorizer.lambda_handler({
                    "authorizationToken": "Bearer valid-format-token",
                    "methodArn": "arn:aws:execute-api:region:account:api/prod/GET/jobs",
                }, None)


class InterruptedStatusTests(unittest.TestCase):
    def _response_for_task(self, status):
        jobs = Mock()
        jobs.get_item.return_value = {"Item": {
            "jobId": JOB_ID,
            "userId": USER_ID,
            "status": "SUCCEEDED",
            "taskCount": 1,
            "tasksSucceeded": 1,
            "tasksFailed": 0,
            "createdAt": 1,
        }}
        tasks = Mock()
        tasks.query.return_value = {"Items": [{
            "taskId": "task-0001",
            "status": status,
            "interruptCount": 1,
        }]}
        with patch.object(
            get_job_status.dynamodb, "Table",
            side_effect=lambda name: jobs if name == "Jobs" else tasks,
        ):
            response = get_job_status.lambda_handler({
                "requestContext": {"authorizer": {"userId": USER_ID}},
                "pathParameters": {"id": JOB_ID},
            }, None)
        return json.loads(response["body"])

    def test_completed_interrupted_task_does_not_mark_job_interrupted(self):
        self.assertFalse(self._response_for_task("SUCCEEDED")["interrupted"])

    def test_queued_or_running_interrupted_task_marks_job_interrupted(self):
        for status in ("QUEUED", "RUNNING"):
            with self.subTest(status=status):
                self.assertTrue(self._response_for_task(status)["interrupted"])


if __name__ == "__main__":
    unittest.main()
