"""Lỗi 500 của API không được lộ chi tiết nội bộ (ARN, tên bảng) cho người dùng."""

import importlib.util
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

API = Path(__file__).parents[1] / "services/api"
SECRET = "arn:aws:dynamodb:ap-southeast-1:123456789012:table/Jobs AccessDenied"
ENV = {"JOBS_TABLE": "Jobs", "TASKS_TABLE": "Tasks", "USERS_TABLE": "Users",
       "INPUT_BUCKET": "in", "OUTPUT_BUCKET": "out"}


def load(name):
    table = Mock()
    for method in ("get_item", "put_item", "update_item", "query"):
        getattr(table, method).side_effect = RuntimeError(SECRET)
    resource = Mock()
    resource.Table.return_value = table
    boto3 = types.ModuleType("boto3")
    boto3.resource = Mock(return_value=resource)
    boto3.client = Mock(return_value=Mock())
    spec = importlib.util.spec_from_file_location(f"{name}_errors_under_test", API / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict("sys.modules", {"boto3": boto3}), patch.dict(os.environ, ENV), \
            patch.object(sys, "path", [str(API)] + sys.path):
        sys.modules.pop("helper", None)
        spec.loader.exec_module(module)
    sys.modules.pop("helper", None)
    return module


class ApiErrorTests(unittest.TestCase):
    def check(self, name, event, message):
        module = load(name)
        with self.assertLogs(level="ERROR") as logs:
            result = module.lambda_handler(event, None)
        self.assertEqual(500, result["statusCode"])
        self.assertEqual({"error": message}, json.loads(result["body"]))
        self.assertNotIn("arn:aws", result["body"])
        # Chi tiết lỗi vẫn nằm trong CloudWatch Logs để nhóm điều tra.
        self.assertIn(SECRET, "\n".join(logs.output))

    def test_handlers_hide_internal_errors(self):
        owner = {"requestContext": {"authorizer": {"userId": "u1"}}, "pathParameters": {"id": "j1"}}
        cases = [
            ("cancel_job", owner, "Could not cancel job"),
            ("get_job_status", owner, "Could not load job"),
            ("start_job", owner, "Could not start job"),
            ("rotate_key", owner, "Could not rotate API key"),
            ("create_user", {"body": json.dumps({"email": "a@example.com"})}, "Could not create user"),
        ]
        for name, event, message in cases:
            with self.subTest(name):
                self.check(name, event, message)


if __name__ == "__main__":
    unittest.main()
