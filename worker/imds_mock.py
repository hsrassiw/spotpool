"""IMDS giả để test thu hồi Spot (HM17), thay cho AWS FIS vì tài khoản Free không dùng được.

Chỉ chạy trên máy Spot khi apply với -var spot_interrupt_test=true. Mọi request được chuyển
tiếp tới IMDS thật, riêng spot/instance-action trả cảnh báo terminate khi có file TRIGGER,
nên cảnh báo thu hồi thật vẫn tới được worker.

Kích hoạt trên máy: touch /run/spotpool-interrupt (xem experiments/spot_interrupt.sh).
"""

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

LISTEN = ("127.0.0.1", 1338)
UPSTREAM = "http://169.254.169.254"
TRIGGER = "/run/spotpool-interrupt"
NOTICE_PATH = "/latest/meta-data/spot/instance-action"
# AWS báo trước 2 phút
WARNING_SECONDS = 120
FORWARD_HEADERS = ("X-aws-ec2-metadata-token", "X-aws-ec2-metadata-token-ttl-seconds")

opener = build_opener(ProxyHandler({}))


def fake_notice(trigger):
    try:
        fired_at = os.stat(trigger).st_mtime
    except FileNotFoundError:
        return None
    when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(fired_at + WARNING_SECONDS))
    return {"action": "terminate", "time": when}


class Handler(BaseHTTPRequestHandler):
    upstream = UPSTREAM
    trigger = TRIGGER

    def do_GET(self):
        status, body = self._forward("GET")
        # Token vẫn do IMDS thật kiểm tra: sai token thì giữ nguyên 401
        if self.path == NOTICE_PATH and status != 401:
            notice = fake_notice(self.trigger)
            if notice is not None:
                status, body = 200, json.dumps(notice).encode()
        self._reply(status, body)

    def do_PUT(self):
        self._reply(*self._forward("PUT"))

    def _forward(self, method):
        headers = {name: self.headers[name] for name in FORWARD_HEADERS if self.headers.get(name)}
        request = Request(self.upstream + self.path, method=method, headers=headers)
        try:
            with opener.open(request, timeout=2) as response:
                return response.status, response.read()
        except HTTPError as error:
            body = error.read()
            error.close()
            return error.code, body
        except OSError:
            return 502, b""

    def _reply(self, status, body):
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def main():
    ThreadingHTTPServer(LISTEN, Handler).serve_forever()


if __name__ == "__main__":
    main()
