import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from worker import imds_mock, spot
from worker.spot import InstanceMetadata


class FakeImds(BaseHTTPRequestHandler):
    """Đóng vai IMDS thật: cấp token, chưa có cảnh báo thu hồi."""

    def do_PUT(self):
        self._reply(200, b"real-token")

    def do_GET(self):
        if self.headers.get("X-aws-ec2-metadata-token") != "real-token":
            self._reply(401, b"")
        elif self.path == "/latest/meta-data/instance-id":
            self._reply(200, b"i-0123")
        else:
            self._reply(404, b"")

    def _reply(self, status, body):
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class ImdsMockTests(unittest.TestCase):
    def setUp(self):
        self.upstream = serve(FakeImds)
        self.trigger = Path(tempfile.mkdtemp()) / "interrupt"
        handler = type("Handler", (imds_mock.Handler,), {
            "upstream": f"http://127.0.0.1:{self.upstream.server_port}", "trigger": str(self.trigger),
        })
        self.mock = serve(handler)
        self.addCleanup(self.upstream.shutdown)
        self.addCleanup(self.mock.shutdown)
        base = f"http://127.0.0.1:{self.mock.server_port}/latest"
        self.patcher = patch("worker.spot.IMDS_BASE", base)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_no_trigger_passes_through_real_imds(self):
        self.assertIsNone(InstanceMetadata().interruption_notice())

    def test_trigger_returns_terminate_notice_two_minutes_ahead(self):
        self.trigger.touch()
        os.utime(self.trigger, (1762300000, 1762300000))
        notice = InstanceMetadata().interruption_notice()
        self.assertEqual({"action": "terminate", "time": "2025-11-04T23:48:40Z"}, notice)

    def test_other_metadata_and_token_check_come_from_real_imds(self):
        self.trigger.touch()
        metadata = InstanceMetadata()
        metadata.token, metadata.token_expires_at = "wrong", float("inf")
        # Token sai: IMDS thật trả 401, worker tự lấy token mới rồi mới đọc cảnh báo
        self.assertEqual("terminate", metadata.interruption_notice()["action"])
        self.assertEqual("real-token", metadata.token)

    def test_monitor_sees_trigger_on_next_poll(self):
        seen = []
        monitor = spot.SpotMonitor(seen.append, interval=0.05)
        monitor.start()
        self.addCleanup(monitor.stop)
        self.trigger.touch()
        monitor.thread.join(timeout=2)
        self.assertEqual("terminate", seen[0]["action"])


class ImdsBaseTests(unittest.TestCase):
    def imds_base(self, value):
        env = {**os.environ, "SPOTPOOL_IMDS_BASE": value}
        code = "from worker.spot import IMDS_BASE; print(IMDS_BASE)"
        return subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                              text=True, check=True).stdout.strip()

    def test_env_overrides_metadata_address(self):
        self.assertEqual("http://127.0.0.1:1338/latest", self.imds_base("http://127.0.0.1:1338/latest"))
        self.assertEqual("http://169.254.169.254/latest", self.imds_base(""))


if __name__ == "__main__":
    unittest.main()
