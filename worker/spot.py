"""Đọc IMDSv2 và theo dõi cảnh báo thu hồi Spot cho cả máy."""

import json
import logging
import os
import threading
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

logger = logging.getLogger(__name__)
POLL_SECONDS = 5
TOKEN_TTL_SECONDS = 21600
# Test HM17 trỏ sang IMDS giả trên máy (worker/imds_mock.py), bình thường để trống
IMDS_BASE = os.environ.get("SPOTPOOL_IMDS_BASE") or "http://169.254.169.254/latest"


class SpotInterrupted(Exception):
    pass


class InstanceMetadata:
    def __init__(self, opener=None):
        # Metadata là link-local, không gửi token qua HTTP proxy của môi trường.
        self.opener = opener if opener is not None else build_opener(ProxyHandler({}))
        self.token = None
        self.token_expires_at = 0

    def _token(self):
        if self.token is None or time.monotonic() >= self.token_expires_at:
            request = Request(
                f"{IMDS_BASE}/api/token", method="PUT",
                headers={"X-aws-ec2-metadata-token-ttl-seconds": str(TOKEN_TTL_SECONDS)},
            )
            with self.opener.open(request, timeout=1) as response:
                token = response.read().decode("utf-8").strip()
            if not token:
                raise ValueError("IMDSv2 returned an empty token")
            self.token = token
            self.token_expires_at = time.monotonic() + TOKEN_TTL_SECONDS - 60
        return self.token

    def interruption_notice(self):
        for attempt in range(2):
            request = Request(
                f"{IMDS_BASE}/meta-data/spot/instance-action",
                headers={"X-aws-ec2-metadata-token": self._token()},
            )
            try:
                with self.opener.open(request, timeout=1) as response:
                    notice = json.loads(response.read())
            except HTTPError as error:
                error.close()
                if error.code == 404:
                    return None
                if error.code == 401 and attempt == 0:
                    self.token = None
                    continue
                raise
            if (not isinstance(notice, dict)
                    or notice.get("action") not in ("stop", "terminate", "hibernate")
                    or not isinstance(notice.get("time"), str)):
                raise ValueError("Invalid Spot interruption notice")
            return notice
        raise RuntimeError("Unable to refresh IMDSv2 token")


class SpotMonitor:
    def __init__(self, on_notice, metadata=None, interval=POLL_SECONDS):
        self.on_notice = on_notice
        self.metadata = metadata if metadata is not None else InstanceMetadata()
        self.interval = interval
        self.stopped = threading.Event()
        self.thread = None

    def poll_once(self):
        try:
            notice = self.metadata.interruption_notice()
        except Exception:
            logger.exception("Cannot read Spot interruption notice; will retry")
            return False
        if notice is None:
            return False
        self.on_notice(notice)
        return True

    def start(self):
        # Đọc ngay trước khi worker nhận việc, kể cả khi systemd vừa restart.
        if self.poll_once():
            return
        self.thread = threading.Thread(target=self._run, name="spot-monitor", daemon=True)
        self.thread.start()

    def _run(self):
        delay = self.interval
        while not self.stopped.wait(delay):
            started = time.monotonic()
            if self.poll_once():
                return
            delay = max(0, self.interval - (time.monotonic() - started))

    def stop(self):
        self.stopped.set()
        if self.thread is not None:
            self.thread.join(timeout=5)
