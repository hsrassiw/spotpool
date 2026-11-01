"""Kiểm tra quyền sở hữu và trường công khai của GET /users/me, không gọi AWS."""

import importlib.util
import json
import os
import types
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch


class CurrentUserTests(unittest.TestCase):
    def setUp(self):
        self.table = Mock()
        resource = Mock()
        resource.Table.return_value = self.table
        boto3 = types.ModuleType("boto3")
        boto3.resource = Mock(return_value=resource)
        filename = Path(__file__).parents[1] / "services/api/get_current_user.py"
        spec = importlib.util.spec_from_file_location("current_user_under_test", filename)
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict("sys.modules", {"boto3": boto3}), patch.dict(os.environ, {"USERS_TABLE": "Users"}):
            spec.loader.exec_module(self.module)
        self.event = {"requestContext": {"authorizer": {"userId": "my-user"}}}
        self.item = {
            "userId": "my-user", "email": "khue@example.com", "role": "user",
            "creditAvailable": Decimal(35000), "creditHeld": Decimal(15000),
            "runningJobs": Decimal(1), "maxConcurrentJobs": Decimal(2), "createdAt": Decimal(1793491200),
            "apiKeyHash": "never-return-this", "internalFlag": "private",
        }
        self.table.get_item.return_value = {"Item": self.item}

    def test_reads_only_authorizer_identity_and_returns_public_fields(self):
        self.event.update(queryStringParameters={"userId": "other-user"}, body='{"userId":"other-user"}')
        result = self.module.lambda_handler(self.event, None)
        self.assertEqual(200, result["statusCode"])
        body = json.loads(result["body"])
        self.assertEqual(set(self.module.PUBLIC_FIELDS), set(body))
        self.assertEqual("my-user", body["userId"])
        self.assertEqual(35000, body["creditAvailable"])
        self.assertIsInstance(body["creditAvailable"], int)
        self.assertNotIn("never-return-this", result["body"])
        request = self.table.get_item.call_args.kwargs
        self.assertEqual({"userId": "my-user"}, request["Key"])
        self.assertTrue(request["ConsistentRead"])
        self.assertNotIn("apiKeyHash", request["ExpressionAttributeNames"].values())
        self.assertEqual(set(self.module.PUBLIC_FIELDS), set(request["ExpressionAttributeNames"].values()))

    def test_missing_authorizer_is_rejected_without_database_access(self):
        for event in ({}, {"requestContext": None}, {"requestContext": {"authorizer": {}}}):
            with self.subTest(event=event):
                self.assertEqual(401, self.module.lambda_handler(event, None)["statusCode"])
        self.table.get_item.assert_not_called()

    def test_missing_user_is_404(self):
        self.table.get_item.return_value = {}
        self.assertEqual(404, self.module.lambda_handler(self.event, None)["statusCode"])

    def test_backend_failure_is_generic_and_has_cors(self):
        self.table.get_item.side_effect = RuntimeError("Internal database details")
        with self.assertLogs(self.module.logger, level="ERROR"):
            result = self.module.lambda_handler(self.event, None)
        self.assertEqual(500, result["statusCode"])
        self.assertNotIn("Internal database details", result["body"])
        self.assertEqual("*", result["headers"]["Access-Control-Allow-Origin"])
        self.assertEqual("no-store", result["headers"]["Cache-Control"])

    def test_zero_credit_and_admin_role_are_preserved(self):
        self.item.update(creditAvailable=Decimal(0), creditHeld=Decimal(0), role="admin")
        result = self.module.lambda_handler(self.event, None)
        body = json.loads(result["body"])
        self.assertEqual(0, body["creditAvailable"])
        self.assertEqual("admin", body["role"])

    def test_each_missing_public_field_returns_null_without_500_or_secret_leak(self):
        for field in self.module.PUBLIC_FIELDS:
            with self.subTest(field=field):
                self.table.get_item.return_value = {"Item": {key: value for key, value in self.item.items() if key != field}}
                result = self.module.lambda_handler(self.event, None)
                self.assertEqual(200, result["statusCode"])
                body = json.loads(result["body"])
                self.assertIsNone(body[field])
                self.assertEqual(set(self.module.PUBLIC_FIELDS), set(body))
                self.assertNotIn("never-return-this", result["body"])

    def test_explicit_null_numeric_fields_do_not_raise_during_integer_conversion(self):
        self.item.update({field: None for field in self.module.NUMBER_FIELDS})
        result = self.module.lambda_handler(self.event, None)
        self.assertEqual(200, result["statusCode"])
        body = json.loads(result["body"])
        self.assertTrue(all(body[field] is None for field in self.module.NUMBER_FIELDS))


if __name__ == "__main__":
    unittest.main()
