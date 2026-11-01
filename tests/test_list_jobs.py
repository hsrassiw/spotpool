"""GET /jobs: quyền sở hữu, cursor và trang rỗng có LastEvaluatedKey."""

import base64
import importlib.util
import json
import os
import types
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch


class ListJobsTests(unittest.TestCase):
    def setUp(self):
        self.table = Mock()
        self.resource = Mock()
        self.resource.Table.return_value = self.table
        boto3 = types.ModuleType("boto3")
        boto3.resource = Mock(return_value=self.resource)
        conditions = types.ModuleType("boto3.dynamodb.conditions")
        conditions.Key = Mock()
        conditions.Attr = Mock()
        spec = importlib.util.spec_from_file_location("list_jobs_under_test", Path(__file__).parents[1] / "services/api/list_jobs.py")
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict("sys.modules", {"boto3": boto3, "boto3.dynamodb.conditions": conditions}), patch.dict(os.environ, {"JOBS_TABLE": "Jobs"}):
            spec.loader.exec_module(self.module)
        self.event = {"requestContext": {"authorizer": {"userId": "my-user"}}, "queryStringParameters": None}
        self.item = {"jobId": "job-1", "userId": "my-user", "status": "RUNNING", "tier": "spot", "kind": "script",
                     "files": ["first.py", "second.py"], "taskCount": Decimal(2), "tasksSucceeded": Decimal(1),
                     "tasksFailed": Decimal(0), "costCap": Decimal(5000), "createdAt": Decimal(1793491200),
                     "internalDetails": "do-not-return"}
        self.table.query.return_value = {"Items": [self.item]}

    def invoke(self, params=None):
        self.event["queryStringParameters"] = params
        return self.module.lambda_handler(self.event, None)

    @staticmethod
    def token(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    def test_queries_only_own_jobs_index_newest_first_and_returns_contract_fields(self):
        result = self.invoke({"userId": "someone-else"})
        self.assertEqual(200, result["statusCode"])
        body = json.loads(result["body"])
        job = body["jobs"][0]
        self.assertEqual({"jobId", "status", "tier", "kind", "name", "progress", "costCap", "chargedCredit", "createdAt"}, set(job))
        self.assertEqual("first.py", job["name"])
        self.assertEqual({"total": 2, "succeeded": 1, "failed": 0}, job["progress"])
        self.assertIsNone(job["chargedCredit"])
        self.assertIsNone(body["next"])
        self.assertNotIn("do-not-return", result["body"])
        query = self.table.query.call_args.kwargs
        self.assertEqual("userId-createdAt-index", query["IndexName"])
        self.module.Key.assert_called_once_with("userId")
        self.module.Key.return_value.eq.assert_called_once_with("my-user")
        self.assertIs(self.module.Key.return_value.eq.return_value, query["KeyConditionExpression"])
        self.assertFalse(query["ScanIndexForward"])
        self.assertEqual(20, query["Limit"])
        self.assertNotIn("ConsistentRead", query)
        self.resource.Table.assert_called_once_with("Jobs")
        self.table.scan.assert_not_called()

    def test_status_filter_and_limit_are_applied_with_maximum_50(self):
        self.invoke({"status": "PAUSED", "limit": "100"})
        query = self.table.query.call_args.kwargs
        self.module.Attr.assert_called_once_with("status")
        self.module.Attr.return_value.eq.assert_called_once_with("PAUSED")
        self.assertIs(self.module.Attr.return_value.eq.return_value, query["FilterExpression"])
        self.assertEqual(50, query["Limit"])

    def test_empty_filtered_page_still_returns_cursor_and_next_request_uses_native_key(self):
        key = {"userId": "my-user", "createdAt": Decimal(1793491200), "jobId": "job-1"}
        self.table.query.return_value = {"Items": [], "LastEvaluatedKey": key}
        body = json.loads(self.invoke({"status": "RUNNING"})["body"])
        self.assertEqual([], body["jobs"])
        self.assertIsInstance(body["next"], str)
        self.invoke({"next": body["next"], "limit": "5"})
        decoded = self.table.query.call_args.kwargs["ExclusiveStartKey"]
        self.assertEqual({**key, "createdAt": 1793491200}, decoded)
        self.assertIs(type(decoded["createdAt"]), int)

    def test_cross_user_or_malformed_cursors_are_rejected_before_query(self):
        bad = ["%%%", "", self.token([]), self.token({"userId": "other", "jobId": "j", "createdAt": 1}),
               self.token({"userId": "my-user", "jobId": "j", "createdAt": True}),
               self.token({"userId": "my-user", "jobId": "j", "createdAt": 1.5}),
               self.token({"userId": "my-user", "jobId": "j", "createdAt": 1, "extra": "x"})]
        for value in bad:
            with self.subTest(value=value):
                self.assertEqual(400, self.invoke({"next": value})["statusCode"])
        self.table.query.assert_not_called()

    def test_invalid_status_and_limit_are_rejected(self):
        for params in ({"status": "INTERRUPTED"}, {"status": "running"}, {"limit": "0"}, {"limit": "-1"}, {"limit": "1.5"}, {"limit": ""}):
            with self.subTest(params=params):
                self.assertEqual(400, self.invoke(params)["statusCode"])
        self.table.query.assert_not_called()

    def test_missing_identity_is_unauthorized_and_does_not_read_database(self):
        self.event = {"queryStringParameters": {"userId": "my-user"}}
        self.assertEqual(401, self.module.lambda_handler(self.event, None)["statusCode"])
        self.table.query.assert_not_called()

    def test_zero_charge_is_not_null_and_backend_failure_is_generic_with_cors(self):
        self.item["chargedCredit"] = Decimal(0)
        self.assertEqual(0, json.loads(self.invoke()["body"])["jobs"][0]["chargedCredit"])
        self.table.query.side_effect = RuntimeError("private database detail")
        with self.assertLogs(self.module.logger, level="ERROR"):
            result = self.invoke()
        self.assertEqual(500, result["statusCode"])
        self.assertNotIn("private database detail", result["body"])
        self.assertEqual("*", result["headers"]["Access-Control-Allow-Origin"])
        self.assertEqual("no-store", result["headers"]["Cache-Control"])


if __name__ == "__main__":
    unittest.main()
