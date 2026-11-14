"""Thực nghiệm chạy lại ngày 14/11/2026 trên bản nộp (xem README.md cùng thư mục).

Chỉ gọi API công khai như người dùng; DynamoDB chỉ đọc để lấy mốc thời gian (cần quyền đọc AWS).

    export SPOTPOOL_API=https://<api-id>.execute-api.ap-southeast-1.amazonaws.com/prod
    python run.py <kịch bản> [tham số]   -> ghi results/<kịch bản>-<giờ>.json và in tóm tắt

Kịch bản: func-script, func-video, func-zip, func-cap [file nhãn], func-cancel, burst <nhãn>,
sample <ondemand|spot>, stats, merge, submit-only <nhãn> <gói> <loại> <file...>, wait-running <jobId> <giây>, watch <jobId>, status <jobId>.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
from boto3.dynamodb.conditions import Key

HERE = Path(__file__).parent
IN, OUT = HERE / "inputs", HERE / "results"
API = os.environ["SPOTPOOL_API"].rstrip("/")
KEYS = HERE / ".keys.json"  # khóa API của tài khoản thử; đã gitignore, không in ra
VN = timezone(timedelta(hours=7))
db = boto3.resource("dynamodb", region_name="ap-southeast-1")
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}


def now():
    return int(time.time())


def hhmmss(ts):
    return datetime.fromtimestamp(int(ts), VN).strftime("%H:%M:%S") if ts else "-"


def log(*parts):
    print(datetime.now(VN).strftime("%H:%M:%S"), *parts, flush=True)


def call(method, path, body=None, key=None, raw=None, url=None, headers=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    request = urllib.request.Request(url or API + path, data=data, method=method)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    if key:
        request.add_header("Authorization", f"Bearer {key}")
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                text = response.read().decode()
                return response.status, (json.loads(text) if text else None)
        except urllib.error.HTTPError as error:
            if error.code in (429, 502, 503, 504) and attempt < 4:
                time.sleep(2 ** attempt)
                continue
            return error.code, error.read().decode()[:300]
        except (urllib.error.URLError, TimeoutError):
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)


def new_user(label):
    status, body = call("POST", "/users", {"email": f"exp14-{label}-{now()}@example.com"})
    assert status == 201, (status, body)
    keys = json.loads(KEYS.read_text()) if KEYS.exists() else {}
    keys[body["userId"]] = body["apiKey"]
    KEYS.write_text(json.dumps(keys))
    return body["userId"], body["apiKey"]


def key_of(user_id):
    return json.loads(KEYS.read_text())[user_id]


def submit(key, tier, kind, files, cost_cap=50000, params=None):
    status, job = call("POST", "/jobs", {"tier": tier, "kind": kind, "vcpu": 1, "costCap": cost_cap,
                                         "files": [name for name in files], "params": params or {}}, key=key)
    assert status in (200, 201), (status, job)
    for upload, name in zip(job["uploads"], files):
        status, _ = call("PUT", "", raw=(IN / name).read_bytes(), url=upload["url"],
                         headers={"Content-Type": "application/octet-stream"})
        assert status == 200, ("upload", name, status)
    status, body = call("POST", f"/jobs/{job['jobId']}/start", {}, key=key)
    assert status == 200, (status, body)
    log("submitted", tier, kind, job["jobId"][:8], len(files), "file")
    return job["jobId"]


def api_job(key, job_id):
    status, job = call("GET", f"/jobs/{job_id}", key=key)
    assert status == 200, (status, job)
    return job


def wait(key, job_id, until=TERMINAL, timeout=3600, on_poll=None):
    seen, deadline = None, time.time() + timeout
    while time.time() < deadline:
        job = api_job(key, job_id)
        state = (job["status"], tuple((t["status"], t["interruptCount"]) for t in job["tasks"]))
        if state != seen:
            log(job_id[:8], job["status"], " ".join(f"{t['status'][:4]}/{t['interruptCount']}" for t in job["tasks"]))
            seen = state
        if on_poll:
            on_poll(job)
        if job["status"] in until:
            return job
        time.sleep(2)
    raise TimeoutError(job_id)


def snapshot(user_id, job_ids):
    """Bản ghi Jobs, Tasks, Usage của các job (đọc trực tiếp DynamoDB, ConsistentRead)."""
    jobs, tasks = [], []
    for job_id in job_ids:
        jobs.append(db.Table("Jobs").get_item(Key={"jobId": job_id}, ConsistentRead=True)["Item"])
        tasks += db.Table("Tasks").query(KeyConditionExpression=Key("jobId").eq(job_id), ConsistentRead=True)["Items"]
    usage = db.Table("Usage").query(KeyConditionExpression=Key("userId").eq(user_id), ConsistentRead=True)["Items"]
    user = db.Table("Users").get_item(Key={"userId": user_id}, ConsistentRead=True)["Item"]
    keep = ("userId", "creditAvailable", "creditHeld")
    return {"jobs": jobs, "tasks": tasks, "usage": [u for u in usage if u.get("jobId") in job_ids],
            "user": {k: user.get(k) for k in keep}}


def fetch_text(url, limit=4000):
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read(limit).decode("utf-8", "replace")


def outputs(key, job_id, results=True):
    job = api_job(key, job_id)
    out = {}
    for task in job["tasks"]:
        item = {"fileName": task["fileName"], "status": task["status"]}
        if task.get("logUrl"):
            item["log"] = fetch_text(task["logUrl"])
        if results and task.get("resultUrl"):
            if job["kind"] == "video":
                target = OUT / f"{job_id[:8]}_{task['taskId']}.mp4"
                urllib.request.urlretrieve(task["resultUrl"], target)
                item["resultFile"] = target.name
            else:
                item["result"] = fetch_text(task["resultUrl"])
        out[task["taskId"]] = item
    return out


PRIVATE_FIELDS = {"input", "output", "logKey", "resultKey", "fingerprint", "owner"}


def public(value):
    """Bỏ key S3 (chứa account ID) và đường dẫn máy cá nhân trước khi lưu vào repo."""
    if isinstance(value, dict):
        return {k: public(v) for k, v in value.items() if k not in PRIVATE_FIELDS}
    if isinstance(value, list):
        return [public(v) for v in value]
    if isinstance(value, Path):
        return value.name
    return value


def save(name, data):
    path = OUT / f"{name}-{datetime.now(VN).strftime('%H%M%S')}.json"
    path.write_text(json.dumps(public(data), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    log("saved", path.name)
    return path


def summarize(snap):
    for job in snap["jobs"]:
        q, s, f = (int(job[k]) if k in job else None for k in ("queuedAt", "startedAt", "finishedAt"))
        log(f"job {job['jobId'][:8]} {job['tier']} {job['status']} queued {hhmmss(q)} start {hhmmss(s)} "
            f"(chờ {s - q if s and q else '-'} s) end {hhmmss(f)} (job {f - q if f and q else '-'} s) "
            f"used {job.get('usedVcpuSeconds')} billable {job.get('billableVcpuSeconds')} charged {job.get('chargedCredit')}")
    for task in sorted(snap["tasks"], key=lambda t: (t["jobId"], t["taskId"])):
        log(f"  {task['jobId'][:8]} {task['taskId']} {task.get('fileName')} {task['status']} attempt {task.get('attempt')} "
            f"interrupts {task.get('interruptCount')} start {hhmmss(task.get('startedAt'))} end {hhmmss(task.get('finishedAt'))}")
    for usage in sorted(snap["usage"], key=lambda u: str(u.get("usageId"))):
        log(f"  usage {str(usage.get('jobId'))[:8]} {usage.get('taskId')} {usage.get('outcome')} {usage.get('vcpuSeconds')} vCPU-s")
    log("user", snap["user"])


# ---------- kịch bản ----------

def run_simple(label, tier, kind, files, params=None, cost_cap=50000, results=True):
    user_id, key = new_user(label)
    job_id = submit(key, tier, kind, files, cost_cap, params)
    wait(key, job_id)
    time.sleep(3)
    snap = snapshot(user_id, [job_id])
    summarize(snap)
    save(label, {"label": label, "snapshot": snap, "outputs": outputs(key, job_id, results)})
    return job_id


def func_script():
    return run_simple("func-script", "ondemand", "script", ["calc_primes.py", "calc_matrix.py", "fail_on_purpose.py"])


def func_video():
    return run_simple("func-video", "ondemand", "video", ["clip_1080p_20s.mp4", "clip_960p_15s.mp4"], {"height": 720})


def func_zip():
    return run_simple("func-zip", "ondemand", "script", ["project_hinh.zip"])


def func_cap(file_name="busy_90s.py", label="func-cap"):
    """Trần 50 credit (30 vCPU-giây ở gói Ổn định); PAUSED thì nạp 300. Mặc định script 90 giây không checkpoint."""
    user_id, key = new_user(label)
    job_id = submit(key, "ondemand", "script", [file_name], cost_cap=50)
    paused = wait(key, job_id, until=TERMINAL | {"PAUSED"})
    timeline = {"pausedSeenAt": now(), "pausedStatus": paused["status"], "pausedUsed": paused.get("usedVcpuSeconds")}
    snap_paused = snapshot(user_id, [job_id])
    if paused["status"] == "PAUSED":
        status, body = call("POST", f"/jobs/{job_id}/topup", {"amount": 300, "requestId": str(uuid.uuid4())}, key=key)
        timeline.update({"topupAt": now(), "topupStatus": status, "topupBody": body})
        log("topup", status, body)
        resumed = wait(key, job_id, until={"RUNNING"} | TERMINAL)
        timeline["runningAgainSeenAt"] = now()
        log("running again after topup", resumed["status"])
        wait(key, job_id)
    time.sleep(3)
    snap = snapshot(user_id, [job_id])
    summarize(snap)
    save(label, {"label": label, "timeline": timeline, "snapshotPaused": snap_paused, "snapshot": snap,
                 "outputs": outputs(key, job_id)})


def func_cancel():
    label = "func-cancel"
    user_id, key = new_user(label)
    job_id = submit(key, "ondemand", "script", ["busy_360s.py", "busy_360s.py"])
    job = wait(key, job_id, until={"RUNNING"} | TERMINAL)
    running_at = now()
    while any(t["status"] != "RUNNING" for t in job["tasks"]):
        time.sleep(1)
        job = api_job(key, job_id)
    time.sleep(15)
    status, body = call("POST", f"/jobs/{job_id}/cancel", {}, key=key)
    cancel_at = now()
    log("cancel", status, body)
    first = api_job(key, job_id)["status"]
    # Chờ task dừng ở nhịp tim kế tiếp và job được quyết toán.
    deadline = time.time() + 300
    while time.time() < deadline:
        items = db.Table("Tasks").query(KeyConditionExpression=Key("jobId").eq(job_id), ConsistentRead=True)["Items"]
        job_item = db.Table("Jobs").get_item(Key={"jobId": job_id}, ConsistentRead=True)["Item"]
        if all(t["status"] != "RUNNING" for t in items) and "settledAt" in job_item:
            break
        time.sleep(2)
    stopped_at = now()
    snap = snapshot(user_id, [job_id])
    summarize(snap)
    save(label, {"label": label, "timeline": {"allRunningAt": running_at, "cancelAt": cancel_at, "cancelStatus": status,
                                              "statusRightAfterCancel": first, "tasksStoppedSeenAt": stopped_at},
                 "snapshot": snap, "outputs": outputs(key, job_id)})


def submit_only(label, tier, kind, *files):
    user_id, key = new_user(label)
    job_id = submit(key, tier, kind, list(files), params={"height": 720} if kind == "video" else None)
    state = HERE / ".pending.json"
    pending = json.loads(state.read_text()) if state.exists() else {}
    pending[job_id] = {"label": label, "userId": user_id, "submittedAt": now()}
    state.write_text(json.dumps(pending))
    print(job_id)


def watch(job_id):
    """Theo dõi job đã nộp bằng submit_only tới khi kết thúc rồi lưu số liệu."""
    meta = json.loads((HERE / ".pending.json").read_text())[job_id]
    key = key_of(meta["userId"])
    wait(key, job_id)
    time.sleep(3)
    snap = snapshot(meta["userId"], [job_id])
    summarize(snap)
    save(meta["label"], {"label": meta["label"], "meta": meta, "snapshot": snap, "outputs": outputs(key, job_id)})


def status(job_id):
    meta = json.loads((HERE / ".pending.json").read_text())[job_id]
    job = api_job(key_of(meta["userId"]), job_id)
    log(job_id[:8], meta["label"], job["status"], job.get("usedVcpuSeconds"),
        [(t["taskId"], t["status"], t["interruptCount"], hhmmss(t.get("checkpointAt"))) for t in job["tasks"]])


def burst(label):
    """Hai job gửi cùng lúc, mỗi gói một job, mỗi job 12 task × 60 giây chiếm trọn 1 vCPU."""
    users = {tier: new_user(f"{label}-{tier}") for tier in ("ondemand", "spot")}
    jobs = {tier: submit(users[tier][1], tier, "script", ["busy_60s.py"] * 12) for tier in users}
    for tier, job_id in jobs.items():
        wait(users[tier][1], job_id)
    time.sleep(3)
    data = {"label": label}
    for tier, job_id in jobs.items():
        snap = snapshot(users[tier][0], [job_id])
        summarize(snap)
        data[tier] = snap
    save(label, data)


def sample(tier):
    """Một job ngắn để đo thời gian chờ (Bảng 3.4)."""
    return run_simple(f"sample-{tier}", tier, "script", ["short.py"], results=False)




def wait_running(job_id, extra="0"):
    """Đợi mọi task của job RUNNING rồi đợi thêm extra giây (để thu hồi giữa chừng)."""
    meta = json.loads((HERE / ".pending.json").read_text())[job_id]
    key = key_of(meta["userId"])
    while True:
        job = api_job(key, job_id)
        if job["tasks"] and all(t["status"] == "RUNNING" for t in job["tasks"]):
            break
        if job["status"] in TERMINAL:
            raise SystemExit(f"{job_id[:8]} đã kết thúc: {job['status']}")
        time.sleep(3)
    log(job_id[:8], "all tasks RUNNING")
    time.sleep(int(extra))
    status(job_id)


def stats():
    """Tóm tắt kết quả đã lưu: thời gian chờ từng task của các lần co giãn và các lượt chạy của kịch bản Spot."""
    import glob
    import statistics
    for path in sorted(glob.glob(str(OUT / "burst*.json"))):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for tier in ("ondemand", "spot"):
            snap = data[tier]
            job = snap["jobs"][0]
            queued = int(job["queuedAt"])
            waits = sorted(int(t["startedAt"]) - queued for t in snap["tasks"])
            machines = sorted({(u.get("instanceType"), u.get("az")) for u in snap["usage"]})
            print(Path(path).name, tier, f"chờ {waits[0]} / {statistics.median(waits)} / {waits[-1]} s",
                  f"job {int(job['finishedAt']) - queued} s", f"{job['usedVcpuSeconds']} vCPU-s",
                  f"{job['chargedCredit']} credit", machines)
    for path in sorted(glob.glob(str(OUT / "spot-*.json"))) + sorted(glob.glob(str(OUT / "func-cap*.json"))):
        snap = json.loads(Path(path).read_text(encoding="utf-8"))["snapshot"]
        print(Path(path).name)
        for usage in sorted(snap["usage"], key=lambda u: (str(u.get("taskId")), int(u.get("attempt") or 0))):
            if usage.get("taskId"):
                print("  ", usage["taskId"], "lượt", usage.get("attempt"), usage.get("outcome"), usage.get("vcpuSeconds"),
                      "vCPU-s", hhmmss(usage.get("startedAt")), "->", hhmmss(usage.get("finishedAt")),
                      usage.get("instanceType"), usage.get("az"))


def merge():
    """Gộp mọi file trong results/ thành ket-qua.json (một mục cho mỗi lần chạy, theo thứ tự thời gian)."""
    runs = []
    for path in sorted(OUT.glob("*.json"), key=lambda p: p.stat().st_mtime):
        runs.append({"file": path.stem, **json.loads(path.read_text(encoding="utf-8"))})
    target = HERE / "ket-qua.json"
    target.write_text(json.dumps(public(runs), ensure_ascii=False, indent=1), encoding="utf-8")
    log("merged", len(runs), "runs ->", target.name)


if __name__ == "__main__":
    name, args = sys.argv[1], sys.argv[2:]
    globals()[name.replace("-", "_")](*args)
