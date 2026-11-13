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

from services.api import authorizer, create_user, get_job_status, rotate_key, submit_job


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
            "params": {"threshold": 1, "options": [2, None]},
        }

    def test_requires_integer_cost_cap_and_vcpu(self):
        for field, value in (("costCap", True), ("costCap", None), ("costCap", 5000.0), ("vcpu", 1.0), ("vcpu", True), ("vcpu", None)):
            with self.subTest(field=field, value=value):
                request = {**self.valid, field: value}
                with self.assertRaises(ValueError):
                    submit_job._validated_request(request)

    def test_requires_nonempty_array_of_nonempty_file_names(self):
        for files in ("input.py", [], [""], ["  "], [None]):
            with self.subTest(files=files):
                with self.assertRaises(ValueError):
                    submit_job._validated_request({**self.valid, "files": files})

    def test_converts_nested_params_floats(self):
        for params in ({"value": 0.75}, {"options": [1, {"value": 1.5}]}, {"value": 1.0}):
            with self.subTest(params=params):
                converted = submit_job._validated_request({**self.valid, "params": params})[-1]
                expected = json.loads(json.dumps(params), parse_float=Decimal)
                self.assertEqual(expected, converted)

    def test_rejects_non_finite_param_numbers(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "finite"):
                submit_job._validated_request({**self.valid, "params": {"value": value}})

    def test_invalid_request_returns_cors_headers(self):
        response = submit_job.lambda_handler({"body": json.dumps({**self.valid, "vcpu": True})}, None)
        self.assertEqual(400, response["statusCode"])
        self.assertEqual("*", response["headers"]["Access-Control-Allow-Origin"])

    def test_successful_request_persists_integer_params_and_returns_cors(self):
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
        self.assertEqual(self.valid["params"], item["params"])

    def test_float_params_are_persisted_as_decimal(self):
        with (
            patch.object(submit_job.dynamodb, "Table") as table,
            patch.object(submit_job, "generate_presigned_upload_url", return_value="https://upload"),
        ):
            response = submit_job.lambda_handler({
                "requestContext": {"authorizer": {"userId": USER_ID}},
                "body": json.dumps({**self.valid, "params": {"nested": [0.1]}}),
            }, None)
        self.assertEqual(200, response["statusCode"])
        params = table.return_value.put_item.call_args.kwargs["Item"]["params"]
        self.assertIsInstance(params["nested"][0], Decimal)
        self.assertEqual(Decimal("0.1"), params["nested"][0])


class CorsAndAuthorizerTests(unittest.TestCase):
    def test_invalid_or_unknown_key_raises_unauthorized(self):
        for token in (None, 123, "", "Basic key", "Bearer ", "Bearer unknown"):
            with self.subTest(token=token), patch.object(authorizer.dynamodb, "Table") as table:
                table.return_value.query.return_value = {"Items": []}
                with self.assertRaisesRegex(Exception, "^Unauthorized$"):
                    authorizer.lambda_handler({"authorizationToken": token}, None)

    def test_allow_policy_is_scoped_to_api_stage(self):
        with patch.object(authorizer.dynamodb, "Table") as table:
            table.return_value.query.return_value = {"Items": [{"userId": USER_ID}]}
            response = authorizer.lambda_handler({
                "authorizationToken": "Bearer valid-key",
                "methodArn": "arn:aws:execute-api:region:account:api/prod/GET/jobs/123",
            }, None)
        self.assertEqual("arn:aws:execute-api:region:account:api/prod/*/*",
                         response["policyDocument"]["Statement"][0]["Resource"])
        self.assertEqual(USER_ID, response["context"]["userId"])

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


class CreateUserValidationTests(unittest.TestCase):
    def test_invalid_body_or_email_returns_400_without_database_write(self):
        for body in (None, "", "{", "null", "[]", "{}", '{"email": 123}',
                     '{"email": false}', '{"email": []}', '{"email": "  "}'):
            with self.subTest(body=body), patch.object(create_user.dynamodb, "Table") as table:
                response = create_user.lambda_handler({"body": body}, None)
                self.assertEqual(400, response["statusCode"])
                self.assertEqual("*", response["headers"]["Access-Control-Allow-Origin"])
                table.assert_not_called()

    def test_valid_email_creates_user(self):
        with patch.object(create_user.dynamodb, "Table") as table:
            response = create_user.lambda_handler({"body": '{"email": "user@example.com"}'}, None)
        self.assertEqual(201, response["statusCode"])
        table.return_value.put_item.assert_called_once()


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


class ResultFileNameTests(unittest.TestCase):
    def _result_link_args(self, kind, file_name):
        jobs = Mock()
        jobs.get_item.return_value = {"Item": {"jobId": JOB_ID, "userId": USER_ID, "status": "SUCCEEDED",
                                               "kind": kind, "createdAt": 1}}
        tasks = Mock()
        tasks.query.return_value = {"Items": [{"taskId": "task-0001", "status": "SUCCEEDED", "fileName": file_name,
                                               "resultKey": "results/u/j/task-0001/1/result"}]}
        with patch.object(get_job_status.dynamodb, "Table", side_effect=lambda name: jobs if name == "Jobs" else tasks), \
             patch.object(get_job_status, "generate_presigned_download_url", return_value="https://signed") as sign:
            get_job_status.lambda_handler({"requestContext": {"authorizer": {"userId": USER_ID}},
                                           "pathParameters": {"id": JOB_ID}}, None)
        return sign.call_args.kwargs

    def test_script_result_opens_as_utf8_text_named_after_the_script(self):
        args = self._result_link_args("script", "test_checkpoint.py")
        self.assertEqual("test_checkpoint-ketqua.txt", args["filename"])
        self.assertEqual("text/plain; charset=utf-8", args["content_type"])

    def test_video_result_opens_as_mp4(self):
        args = self._result_link_args("video", "video_150s.mov")
        self.assertEqual("video_150s-ketqua.mp4", args["filename"])
        self.assertEqual("video/mp4", args["content_type"])

    def test_log_opens_as_utf8_text_named_after_the_task_file(self):
        jobs = Mock()
        jobs.get_item.return_value = {"Item": {"jobId": JOB_ID, "userId": USER_ID, "status": "FAILED",
                                               "kind": "script", "createdAt": 1}}
        tasks = Mock()
        tasks.query.return_value = {"Items": [{"taskId": "task-0001", "status": "FAILED", "fileName": "bài_1.py",
                                               "logKey": "results/u/j/task-0001/1/log.txt"}]}
        with patch.object(get_job_status.dynamodb, "Table", side_effect=lambda name: jobs if name == "Jobs" else tasks),              patch.object(get_job_status, "generate_presigned_download_url", return_value="https://signed") as sign:
            get_job_status.lambda_handler({"requestContext": {"authorizer": {"userId": USER_ID}},
                                           "pathParameters": {"id": JOB_ID}}, None)
        args = sign.call_args.kwargs
        self.assertEqual("bài_1-log.txt", args["filename"])
        self.assertEqual("text/plain; charset=utf-8", args["content_type"])

    def test_disposition_keeps_vietnamese_name_and_escapes_quotes(self):
        from services.api import helper
        value = helper.content_disposition('bài "1".txt')
        self.assertTrue(value.startswith('inline; filename="b_i__1_.txt"; '))
        self.assertIn("filename*=UTF-8''b%C3%A0i%20%221%22.txt", value)

    def test_signed_link_carries_name_and_type(self):
        from services.api import helper
        from botocore.credentials import Credentials
        with patch.object(helper.s3_client._request_signer, "_credentials", Credentials("AKIATEST", "secret")):
            url = helper.generate_presigned_download_url("output-test", "k/result", filename="a-ketqua.txt",
                                                         content_type="text/plain; charset=utf-8")
        self.assertIn("response-content-type=text%2Fplain", url)
        self.assertIn("response-content-disposition=inline", url)


if __name__ == "__main__":
    unittest.main()
