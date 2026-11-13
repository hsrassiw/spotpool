"""POST /jobs/{id}/start từ chối file vượt 500 MB trước khi giữ credit, không gọi AWS."""

import importlib.util
import json
import os
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ENV = {"JOBS_TABLE": "Jobs", "USERS_TABLE": "Users", "INPUT_BUCKET": "in", "OUTPUT_BUCKET": "out"}


class StartJobSizeTests(unittest.TestCase):
    def setUp(self):
        self.table, self.client = Mock(), Mock()
        resource = Mock()
        resource.Table.return_value = self.table
        boto3 = types.ModuleType("boto3")
        boto3.resource = Mock(return_value=resource)
        boto3.client = Mock(return_value=self.client)
        filename = Path(__file__).parents[1] / "services/api/start_job.py"
        spec = importlib.util.spec_from_file_location("start_job_size_under_test", filename)
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict("sys.modules", {"boto3": boto3}), patch.dict(os.environ, ENV):
            spec.loader.exec_module(self.module)
        self.table.get_item.return_value = {"Item": {
            "jobId": "j1", "userId": "u1", "status": "UPLOADING", "taskCount": 2,
            "files": ["small.mp4", "big.mp4"], "tier": "spot", "kind": "video", "vcpu": 1,
        }}
        self.event = {"requestContext": {"authorizer": {"userId": "u1"}}, "pathParameters": {"id": "j1"}}

    def test_rejects_oversized_file_before_holding_credit(self):
        limit = self.module.MAX_INPUT_BYTES
        self.assertEqual(500 * 1024 * 1024, limit)
        self.client.head_object.side_effect = [{"ContentLength": limit}, {"ContentLength": limit + 1}]
        result = self.module.lambda_handler(self.event, None)
        self.assertEqual(400, result["statusCode"])
        body = json.loads(result["body"])
        self.assertEqual(["big.mp4"], body["tooLargeFiles"])
        self.assertEqual(limit, body["maxBytes"])
        self.client.transact_write_items.assert_not_called()
        self.client.put_object.assert_not_called()


if __name__ == "__main__":
    unittest.main()
