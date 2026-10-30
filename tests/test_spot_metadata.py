import io
import json
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from worker.spot import IMDS_BASE, InstanceMetadata, SpotMonitor

NOTICE = {"action": "terminate", "time": "2026-11-01T12:00:00Z"}


def response(data):
    return io.BytesIO(data)


def http_error(code):
    return HTTPError(f"{IMDS_BASE}/meta-data/spot/instance-action", code, "test", {}, io.BytesIO())


class MetadataTests(unittest.TestCase):
    def test_get_uses_token_and_reuses_it(self):
        opener = Mock()
        opener.open.side_effect = [response(b"test-token"), response(json.dumps(NOTICE).encode()),
                                   response(json.dumps(NOTICE).encode())]
        metadata = InstanceMetadata(opener)
        self.assertEqual(NOTICE, metadata.interruption_notice())
        self.assertEqual(NOTICE, metadata.interruption_notice())
        requests = [call.args[0] for call in opener.open.call_args_list]
        self.assertEqual(["PUT", "GET", "GET"], [request.get_method() for request in requests])
        self.assertEqual(f"{IMDS_BASE}/api/token", requests[0].full_url)
        self.assertEqual("21600", dict(requests[0].header_items())["X-aws-ec2-metadata-token-ttl-seconds"])
        self.assertEqual("test-token", dict(requests[1].header_items())["X-aws-ec2-metadata-token"])
        self.assertTrue(all(call.kwargs["timeout"] == 1 for call in opener.open.call_args_list))

    def test_404_is_no_notice(self):
        opener = Mock()
        opener.open.side_effect = [response(b"token"), http_error(404)]
        self.assertIsNone(InstanceMetadata(opener).interruption_notice())

    def test_401_refreshes_token_once(self):
        opener = Mock()
        opener.open.side_effect = [response(b"old"), http_error(401), response(b"new"),
                                   response(json.dumps(NOTICE).encode())]
        self.assertEqual(NOTICE, InstanceMetadata(opener).interruption_notice())
        request = opener.open.call_args.args[0]
        self.assertEqual("new", dict(request.header_items())["X-aws-ec2-metadata-token"])

    def test_second_401_propagates_instead_of_looping(self):
        opener = Mock()
        opener.open.side_effect = [response(b"old"), http_error(401), response(b"new"), http_error(401)]
        with self.assertRaises(HTTPError):
            InstanceMetadata(opener).interruption_notice()
        self.assertEqual(4, opener.open.call_count)

    def test_expired_token_is_renewed(self):
        opener = Mock()
        opener.open.side_effect = [response(b"new"), http_error(404)]
        metadata = InstanceMetadata(opener)
        metadata.token, metadata.token_expires_at = "expired", 0
        self.assertIsNone(metadata.interruption_notice())
        self.assertEqual("PUT", opener.open.call_args_list[0].args[0].get_method())

    def test_bad_notice_or_network_failure_does_not_look_like_404(self):
        for value in (b"not-json", b'{"action":"unknown","time":"now"}'):
            opener = Mock()
            opener.open.side_effect = [response(b"token"), response(value)]
            with self.assertRaises(ValueError):
                InstanceMetadata(opener).interruption_notice()
        opener.open.side_effect = URLError("IMDS unavailable")
        with self.assertRaises(URLError):
            InstanceMetadata(opener).interruption_notice()

    def test_default_opener_disables_environment_proxies(self):
        with patch("worker.spot.ProxyHandler") as proxy, patch("worker.spot.build_opener"):
            InstanceMetadata()
        proxy.assert_called_once_with({})


class MonitorTests(unittest.TestCase):
    def test_notice_at_start_notifies_without_starting_poll_thread(self):
        metadata, callback = Mock(), Mock()
        metadata.interruption_notice.return_value = NOTICE
        monitor = SpotMonitor(callback, metadata)
        monitor.start()
        monitor.stop()
        callback.assert_called_once_with(NOTICE)
        self.assertIsNone(monitor.thread)

    def test_monitor_retries_errors_and_notifies_once(self):
        metadata = Mock()
        metadata.interruption_notice.side_effect = [URLError("temporary"), None, NOTICE]
        notified = threading.Event()
        callback = Mock(side_effect=lambda notice: notified.set())
        monitor = SpotMonitor(callback, metadata, interval=0.01)
        with self.assertLogs("worker.spot", level="ERROR"):
            monitor.start()
            self.assertTrue(notified.wait(2))
            monitor.stop()
        callback.assert_called_once_with(NOTICE)
        self.assertFalse(monitor.thread.is_alive())


if __name__ == "__main__":
    unittest.main()
