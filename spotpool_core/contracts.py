"""Giá trị dùng chung theo docs/contracts.md, phiên bản 1."""

import hashlib
import json
import re
from typing import Any

SCHEMA_VERSION = 1
TIERS = frozenset({"ondemand", "spot"})
KINDS = frozenset({"video", "script"})
IMAGES = {"video": "spotpool-video", "script": "spotpool-script"}
JOB_FINAL = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})
TASK_FINAL = JOB_FINAL
TASK_QUEUED = "QUEUED"
TASK_RUNNING = "RUNNING"
TASK_PAUSED = "PAUSED"
TASK_SUCCEEDED = "SUCCEEDED"
TASK_FAILED = "FAILED"
TASK_CANCELLED = "CANCELLED"
OUTCOME_INTERRUPTED = "INTERRUPTED"
JOB_UPLOADING = "UPLOADING"
JOB_QUEUED = "QUEUED"
JOB_RUNNING = "RUNNING"
JOB_PAUSED = "PAUSED"
JOB_SUCCEEDED = "SUCCEEDED"
JOB_FAILED = "FAILED"
JOB_CANCELLED = "CANCELLED"
TASK_ID_RE = re.compile(r"task-(\d{4})\Z")
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z", re.I)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(message: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(message).encode("utf-8")).hexdigest()


def validate_message(message: dict[str, Any]) -> None:
    required = {"schemaVersion", "jobId", "taskId", "userId", "tier", "kind", "vcpu", "image", "params", "input", "output"}
    if set(message) != required or message["schemaVersion"] != SCHEMA_VERSION:
        raise ValueError("Unsupported task message schema")
    if not UUID_RE.fullmatch(message["jobId"]) or not UUID_RE.fullmatch(message["userId"]):
        raise ValueError("Invalid jobId or userId")
    match = TASK_ID_RE.fullmatch(message["taskId"])
    if not match or not 1 <= int(match.group(1)) <= 100:
        raise ValueError("Invalid taskId")
    if message["tier"] not in TIERS or message["kind"] not in KINDS:
        raise ValueError("Invalid tier or kind")
    if type(message["vcpu"]) is not int or message["vcpu"] not in (1, 2):
        raise ValueError("Invalid vcpu")
    if message["image"] != IMAGES[message["kind"]] or not isinstance(message["params"], dict):
        raise ValueError("Invalid image or params")
    for name in ("input", "output"):
        location = message[name]
        if not isinstance(location, dict) or set(location) != {"bucket", "key"}:
            raise ValueError(f"Invalid {name} location")
        if not all(isinstance(v, str) and v for v in location.values()):
            raise ValueError(f"Invalid {name} location")
    prefix = f'{message["userId"]}/{message["jobId"]}/{message["taskId"]}'
    if message["input"]["key"] != f"uploads/{prefix}":
        raise ValueError("Invalid input key")
    if message["output"]["key"] != f"results/{prefix}":
        raise ValueError("Invalid output key")
