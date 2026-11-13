import hashlib
import io
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from spotpool_core.contracts import OUTCOME_INTERRUPTED, fingerprint
from worker import checkpoint
from worker.agent import JobPaused, Worker, checkpoint_key, docker_command
from test_worker_paused import WorkerPausedTests

worker_fixture = WorkerPausedTests.make_worker
del WorkerPausedTests


def tar_bytes(entries):
    """entries: list of (TarInfo, data or None)."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for info, data in entries:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return buffer.getvalue()


def regular(name, data):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    return info, data


class PackUnpackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source, self.target = self.root / "source", self.root / "target"
        self.source.mkdir()
        self.target.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_round_trip_keeps_regular_files_and_skips_partial_writes(self):
        (self.source / "state.json").write_bytes(b'{"step": 7}')
        (self.source / "model.bin").write_bytes(b"\x00" * 2048)
        (self.source / "state.json.tmp").write_bytes(b"half written")
        (self.source / "ckpt-500" / "optim").mkdir(parents=True)
        (self.source / "ckpt-500" / "optim" / "adam.pt").write_bytes(b"adam")
        (self.source / "ckpt-500" / "weights.pt.tmp").write_bytes(b"half")
        (self.source / "empty").mkdir()
        count, size, ignored = checkpoint.pack(self.source, self.root / "a.tgz")
        self.assertEqual((3, 2048 + 11 + 4, []), (count, size, ignored))
        self.assertEqual((3, 2063), checkpoint.unpack(self.root / "a.tgz", self.target))
        self.assertEqual(['ckpt-500', 'model.bin', 'state.json'], sorted(os.listdir(self.target)))
        self.assertEqual(b'{"step": 7}', (self.target / "state.json").read_bytes())
        self.assertEqual(b"adam", (self.target / "ckpt-500" / "optim" / "adam.pt").read_bytes())
        self.assertEqual(["optim"], os.listdir(self.target / "ckpt-500"))

    def test_signature_changes_only_when_saved_files_change(self):
        (self.source / "state.json").write_bytes(b"1")
        first = checkpoint.signature(self.source)
        (self.source / "state.json.tmp").write_bytes(b"ignored")
        self.assertEqual(first, checkpoint.signature(self.source))
        (self.source / "state.json").write_bytes(b"22")
        second = checkpoint.signature(self.source)
        self.assertNotEqual(first, second)
        (self.source / "sub").mkdir()
        self.assertEqual(second, checkpoint.signature(self.source))  # Thư mục rỗng không được lưu.
        (self.source / "sub" / "inner").write_bytes(b"3")
        self.assertNotEqual(second, checkpoint.signature(self.source))
        self.assertEqual((), checkpoint.signature(self.target))

    def test_signature_sees_replace_with_same_size_and_mtime(self):
        target = self.source / "state.json"
        target.write_bytes(b"1")
        before = checkpoint.signature(self.source)
        tmp = self.source / "state.json.tmp"
        tmp.write_bytes(b"2")
        mtime = target.stat().st_mtime_ns
        os.utime(tmp, ns=(mtime, mtime))
        os.replace(tmp, target)
        self.assertEqual(mtime, target.stat().st_mtime_ns)
        self.assertNotEqual(before, checkpoint.signature(self.source))

    def test_subdirectories_are_saved_but_symlinks_are_not_followed(self):
        (self.source / "nested" / "deeper").mkdir(parents=True)
        (self.source / "nested" / "inside").write_bytes(b"x")
        (self.source / "nested" / "deeper" / "more").write_bytes(b"yz")
        outside = self.root / "host"
        outside.mkdir()
        (outside / "secret").write_bytes(b"host file")
        try:
            (self.source / "link").symlink_to(outside / "secret")
            (self.source / "nested" / "dirlink").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            names = []  # Windows không cho tạo symlink nếu thiếu quyền.
        else:
            names = ["link", "nested/dirlink"]
        count, size, ignored = checkpoint.pack(self.source, self.root / "a.tgz")
        self.assertEqual((2, 3, names), (count, size, ignored))
        with tarfile.open(self.root / "a.tgz") as tar:
            self.assertEqual(["nested/deeper/more", "nested/inside"], tar.getnames())

    def test_folders_deeper_than_the_limit_are_ignored(self):
        (self.source / "a" / "b").mkdir(parents=True)
        (self.source / "a" / "kept").write_bytes(b"1")
        (self.source / "a" / "b" / "lost").write_bytes(b"2")
        with patch.object(checkpoint, "MAX_DEPTH", 1):
            count, size, ignored = checkpoint.pack(self.source, self.root / "a.tgz")
            self.assertEqual((1, ["a/b/"]), (count, ignored))
            self.assertEqual(((("a/kept",)),), tuple((entry[0],) for entry in checkpoint.signature(self.source)))

    @unittest.skipUnless(checkpoint.DIR_FD, "Chỉ Linux mở từng cấp theo descriptor của thư mục cha")
    def test_folder_swapped_for_symlink_mid_walk_is_not_followed(self):
        (self.source / "data").mkdir()
        (self.source / "data" / "state").write_bytes(b"mine")
        outside = self.root / "host"
        outside.mkdir()
        (outside / "state").write_bytes(b"host secret")
        scandir, calls = checkpoint._scandir, []

        def racing(directory):
            calls.append(directory)
            if len(calls) == 2:
                # Worker vừa kiểm tra và mở data, sắp liệt kê nó: script tráo data thành symlink ra ngoài.
                # Đi theo đường dẫn thì lúc này sẽ liệt kê và đọc file của máy chủ.
                (self.source / "data" / "state").unlink()
                (self.source / "data").rmdir()
                (self.source / "data").symlink_to(outside, target_is_directory=True)
            return scandir(directory)

        with patch.object(checkpoint, "_scandir", racing):
            count, size, ignored = checkpoint.pack(self.source, self.root / "a.tgz")
        self.assertEqual(2, len(calls))
        # Descriptor vẫn trỏ thư mục data cũ (đã bị xóa, rỗng), không phải thư mục bị tráo vào.
        self.assertEqual((0, 0), (count, size))
        with tarfile.open(self.root / "a.tgz") as tar:
            self.assertEqual([], tar.getnames())

    def test_pack_rejects_too_many_bytes_or_files(self):
        (self.source / "a").write_bytes(b"12345")
        (self.source / "b").write_bytes(b"12345")
        with patch.object(checkpoint, "MAX_BYTES", 8):
            with self.assertRaises(checkpoint.CheckpointTooLarge):
                checkpoint.pack(self.source, self.root / "a.tgz")
        with patch.object(checkpoint, "MAX_FILES", 1):
            with self.assertRaises(checkpoint.CheckpointTooLarge):
                checkpoint.pack(self.source, self.root / "a.tgz")
            with self.assertRaises(checkpoint.CheckpointTooLarge):
                checkpoint.signature(self.source)
        (self.source / "many").mkdir()
        for index in range(5):
            (self.source / "many" / f"{index}.tmp").write_bytes(b"")
        with patch.object(checkpoint, "MAX_ENTRIES", 6):
            with self.assertRaises(checkpoint.CheckpointTooLarge):
                checkpoint.signature(self.source)  # Cả file .tmp cũng tính vào số mục phải duyệt.

    def test_unpack_rejects_unsafe_archives(self):
        link = tarfile.TarInfo("state.json")
        link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
        folder = tarfile.TarInfo("folder")
        folder.type = tarfile.DIRTYPE
        cases = {
            "parent": [regular("../escape", b"x")],
            "nested parent": [regular("a/../../escape", b"x")],
            "dot": [regular("a/./b", b"x")],
            "empty part": [regular("a//b", b"x")],
            "trailing slash": [regular("a/", b"x")],
            "absolute": [regular("/etc/cron.d/x", b"x")],
            "backslash": [regular("..\\escape", b"x")],
            "symlink": [(link, None)],
            "directory": [(folder, None)],
            "duplicate": [regular("same", b"1"), regular("same", b"2")],
            "file then folder": [regular("a", b"1"), regular("a/b", b"2")],
            "folder then file": [regular("a/b", b"1"), regular("a", b"2")],
            "partial": [regular("state.tmp", b"x")],
            "partial folder": [regular("dir.tmp/state", b"x")],
            "too deep": [regular("/".join("d" * (checkpoint.MAX_DEPTH + 1)) + "/f", b"x")],
        }
        for label, entries in cases.items():
            with self.subTest(label):
                archive = self.root / f"{label}.tgz"
                archive.write_bytes(tar_bytes(entries))
                with self.assertRaises(ValueError):
                    checkpoint.unpack(archive, self.target)
                self.assertFalse((self.root / "escape").exists())
                self.assertEqual([], os.listdir(self.target))

    def test_unpack_rejects_archives_over_the_limits(self):
        archive = self.root / "big.tgz"
        archive.write_bytes(tar_bytes([regular("a", b"123"), regular("b", b"456")]))
        with patch.object(checkpoint, "MAX_FILES", 1):
            with self.assertRaises(checkpoint.CheckpointTooLarge):
                checkpoint.unpack(archive, self.target)
        with patch.object(checkpoint, "MAX_BYTES", 5):
            with self.assertRaises(checkpoint.CheckpointTooLarge):
                checkpoint.unpack(archive, self.target)
        self.assertEqual([], os.listdir(self.target))

    @unittest.skipIf(os.name == "nt", "Windows không cho tạo tên file có dấu \\")
    def test_pack_skips_names_that_unpack_would_reject(self):
        (self.source / "a\\b").write_bytes(b"x")
        (self.source / "state.json").write_bytes(b"1")
        count, size, ignored = checkpoint.pack(self.source, self.root / "a.tgz")
        self.assertEqual((1, ["a\\b"]), (count, ignored))
        self.assertEqual((1, 1), checkpoint.unpack(self.root / "a.tgz", self.target))

    def test_log_note_appends_after_existing_output(self):
        log = self.root / "log.txt"
        for before, expected in ((b"", b"[SpotPool] x\n"), (b"out", b"out\n[SpotPool] x\n"),
                                 (b"out\n", b"out\n[SpotPool] x\n")):
            log.write_bytes(before)
            Worker._log_note(log, "x")
            self.assertEqual(expected, log.read_bytes())
        # Mở bằng O_APPEND nên luồng chép log đang ghi cùng lúc không bị ghi đè.
        with log.open("ab") as writer:
            Worker._log_note(log, "y")
            writer.write(b"later\n")
        self.assertTrue(log.read_bytes().endswith(b"[SpotPool] y\nlater\n"))

    def test_clear_removes_partly_restored_files(self):
        (self.target / "a").write_bytes(b"1")
        (self.target / "sub" / "deeper").mkdir(parents=True)
        (self.target / "sub" / "deeper" / "b").write_bytes(b"2")
        checkpoint.clear(self.target)
        self.assertEqual([], os.listdir(self.target))


class DockerCommandTests(unittest.TestCase):
    def test_checkpoint_folder_is_mounted_only_when_given(self):
        plain = docker_command("img", 1, "/w/payload", "/w/output", "name")
        self.assertFalse(any("/checkpoint" in part for part in plain))
        command = docker_command("img", 1, "/w/payload", "/w/output", "name", checkpoint_dir="/w/checkpoint")
        self.assertIn("type=bind,source=/w/checkpoint,target=/checkpoint", command)
        self.assertIn("SPOTPOOL_CHECKPOINT_DIR=/checkpoint", command)
        # Các cờ cách ly giữ nguyên, image và hai đối số vẫn đứng cuối.
        self.assertEqual(["img", "/input/payload", "/output/result"], command[-3:])
        for flag in ("--network", "--read-only", "--cap-drop"):
            self.assertIn(flag, command)


class WorkerCheckpointTests(unittest.TestCase):
    def make_worker(self, kind="script"):
        message, db, sqs, worker = worker_fixture(self, job_status="RUNNING", task_status="RUNNING")
        if kind == "script":
            message.update(kind="script", image="spotpool-script")
            db.Table("Tasks").rows[(message["jobId"], message["taskId"])]["fingerprint"] = fingerprint(message)
        worker.config.update(VIDEO_JOB_IMAGE="video-image", SCRIPT_JOB_IMAGE="script-image",
                             TASK_MAX_SECONDS="1000")
        worker.s3 = Mock()
        # Nhịp tim thật dừng ở thông báo thu hồi trước khi worker kịp lưu định kỳ.
        worker._heartbeat = Mock(side_effect=lambda *args: worker._check_interruption() or True)
        worker._finish = Mock()
        self.uploads, self.stored = {}, {}

        def download(bucket, key, path, Callback=None):
            if key == message["input"]["key"]:
                Path(path).write_bytes(b"print('hi')")
            elif key in self.stored:
                Path(path).write_bytes(self.stored[key])
            else:
                raise RuntimeError("404 Not Found")

        def upload(path, bucket, key, **kwargs):
            self.uploads.setdefault(key, []).append(Path(path).read_bytes())

        worker.s3.download_file.side_effect = download
        worker.s3.upload_file.side_effect = upload
        self.task_updates = Mock()
        db.Table("Tasks").update_item = self.task_updates
        return message, worker

    def run_container(self, worker, message, steps, restore_from=None, attempt=2, restore_sha256=None):
        """Mỗi phần tử của steps chạy ở một lần proc.wait; 'exit' kết thúc container với mã 0."""
        state = {"rc": None, "mounts": {}, "seen": None}
        clock = [0]
        proc = Mock()
        proc.stdout = io.BytesIO(b"script log\n")
        proc.poll.side_effect = lambda: state["rc"]
        proc.returncode = 0
        remaining = list(steps)

        def start(command, **kwargs):
            for part in command:
                if part.startswith("type=bind,source="):
                    source, target = part[len("type=bind,source="):].split(",target=", 1)
                    state["mounts"][target.split(",")[0]] = Path(source)
            folder = state["mounts"].get("/checkpoint")
            state["seen"] = sorted(os.listdir(folder)) if folder else None
            state["command"] = command
            return proc

        def wait(timeout):
            if timeout == 15:
                return 0
            clock[0] += 31
            action = remaining.pop(0) if remaining else "exit"
            if action == "exit":
                (state["mounts"]["/output"] / "result").write_bytes(b"done")
                state["rc"] = 0
                return 0
            action(state["mounts"])
            raise subprocess.TimeoutExpired("docker", timeout)

        proc.wait.side_effect = wait
        with patch("worker.agent.subprocess.Popen", side_effect=start), \
             patch("worker.agent.subprocess.run") as state["docker_run"], \
             patch("worker.agent.time.monotonic", side_effect=lambda: clock[0]):
            worker._execute({"ReceiptHandle": "r"}, message, {}, "owner", attempt, 1000, restore_from,
                            restore_sha256)
        return state

    def store_checkpoint(self, message, attempt, data):
        self.stored[checkpoint_key(message, attempt)] = data
        return hashlib.sha256(data).hexdigest()

    def log_text(self, message, attempt=2):
        return self.uploads[f'{message["output"]["key"]}/{attempt}/log.txt'][-1].decode("utf-8")

    @staticmethod
    def write_state(text):
        def action(mounts):
            target = mounts["/checkpoint"] / "state.json"
            tmp = target.with_name("state.json.tmp")
            tmp.write_text(text)
            os.replace(tmp, target)
        return action

    def test_restores_previous_attempt_before_container_starts(self):
        message, worker = self.make_worker()
        digest = self.store_checkpoint(message, 1, tar_bytes([regular("state.json", b'{"step": 40}'),
                                                              regular("ckpt-40/model.bin", b"weights")]))
        state = self.run_container(worker, message, ["exit"], restore_from=1, restore_sha256=digest)
        self.assertEqual(["ckpt-40", "state.json"], state["seen"])
        self.assertIn("SPOTPOOL_CHECKPOINT_DIR=/checkpoint", state["command"])
        self.assertIn("Khôi phục checkpoint của lượt chạy 1: 2 file", self.log_text(message))
        self.assertIn("script log", self.log_text(message))
        self.assertEqual("SUCCEEDED", worker._finish.call_args.args[6])

    def test_corrupt_checkpoint_starts_from_scratch(self):
        message, worker = self.make_worker()
        digest = self.store_checkpoint(message, 1, b"not a tar file")
        with self.assertLogs("worker.agent", level="ERROR"):
            state = self.run_container(worker, message, ["exit"], restore_from=1, restore_sha256=digest)
        self.assertEqual([], state["seen"])
        self.assertIn("bị hỏng hoặc sai mã SHA-256", self.log_text(message))
        self.assertEqual("SUCCEEDED", worker._finish.call_args.args[6])

    def test_checkpoint_with_wrong_or_missing_sha256_is_not_unpacked(self):
        for recorded in ("0" * 64, None):
            with self.subTest(recorded=recorded):
                message, worker = self.make_worker()
                # Archive hợp lệ nhưng không phải bản Tasks đã ghi nhận (bị thay hoặc hỏng trên đường).
                self.store_checkpoint(message, 1, tar_bytes([regular("state.json", b'{"step": 40}')]))
                with self.assertLogs("worker.agent", level="ERROR") as logs:
                    state = self.run_container(worker, message, ["exit"], restore_from=1,
                                               restore_sha256=recorded)
                self.assertEqual([], state["seen"])
                self.assertIn("SHA-256 does not match", "\n".join(logs.output))
                self.assertIn("sai mã SHA-256, task chạy lại từ đầu", self.log_text(message))
                self.assertEqual("SUCCEEDED", worker._finish.call_args.args[6])

    def test_saves_periodically_only_when_files_change(self):
        message, worker = self.make_worker()
        self.run_container(worker, message, [self.write_state("1"), lambda mounts: None,
                                             self.write_state("22"), "exit"])
        saved = self.uploads[checkpoint_key(message, 2)]
        self.assertEqual(2, len(saved))
        last = Path(tempfile.mkdtemp()) / "last.tgz"
        last.write_bytes(saved[-1])
        with tarfile.open(last) as tar:
            self.assertEqual(b"22", tar.extractfile("state.json").read())
        update = self.task_updates.call_args.kwargs
        self.assertEqual("#s=:running AND #owner=:owner", update["ConditionExpression"])
        self.assertEqual((2, "owner", 1), (update["ExpressionAttributeValues"][":attempt"],
                                           update["ExpressionAttributeValues"][":owner"],
                                           update["ExpressionAttributeValues"][":count"]))
        self.assertEqual(hashlib.sha256(saved[-1]).hexdigest(), update["ExpressionAttributeValues"][":sha"])

    def test_files_changed_while_packing_are_saved_on_next_heartbeat(self):
        message, worker = self.make_worker()
        pack, raced = checkpoint.pack, []

        def racing_pack(folder, archive):
            result = pack(folder, archive)
            if not raced:
                # Script ghi trạng thái mới, cùng kích thước, ngay sau khi worker đọc xong.
                raced.append(True)
                self.write_state("2")({"/checkpoint": Path(folder)})
            return result

        with patch.object(checkpoint, "pack", side_effect=racing_pack):
            self.run_container(worker, message, [self.write_state("1"), lambda mounts: None, "exit"])
        saved = self.uploads[checkpoint_key(message, 2)]
        self.assertEqual(1, len(saved))  # Bản đóng gói lúc file đang đổi bị bỏ, không tải lên.
        last = Path(tempfile.mkdtemp()) / "last.tgz"
        last.write_bytes(saved[0])
        with tarfile.open(last) as tar:
            self.assertEqual(b"2", tar.extractfile("state.json").read())
        self.task_updates.assert_called_once()

    def test_interruption_saves_final_checkpoint_before_requeue(self):
        message, worker = self.make_worker()

        def interrupt(mounts):
            self.write_state('{"step": 90}')(mounts)
            worker._draining.set()

        def finish(*args):
            # Checkpoint phải có trên S3 và được ghi vào Tasks trước khi task quay lại hàng đợi.
            self.assertIn(checkpoint_key(message, 2), self.uploads)
            self.task_updates.assert_called_once()

        worker._finish.side_effect = finish
        self.run_container(worker, message, [interrupt])
        self.assertEqual(OUTCOME_INTERRUPTED, worker._finish.call_args.args[6])
        self.assertIn("Đã lưu checkpoint: 1 file", self.log_text(message))

    def test_cost_cap_pause_saves_checkpoint_for_topup(self):
        message, worker = self.make_worker()
        # Nhịp tim đầu tiên là trước khi chạy container; lần thứ hai thấy job đã hết trần.
        worker._heartbeat.side_effect = [True, JobPaused("Job paused because its cost cap was reached")]
        self.run_container(worker, message, [self.write_state("5")])
        self.assertEqual("PAUSED", worker._finish.call_args.args[6])
        self.assertEqual(1, len(self.uploads[checkpoint_key(message, 2)]))

    def test_lost_lease_while_saving_does_not_fail_the_task(self):
        message, worker = self.make_worker()
        self.task_updates.side_effect = RuntimeError("ConditionalCheckFailedException")
        self.run_container(worker, message, [self.write_state("1"), "exit"])
        self.assertEqual("SUCCEEDED", worker._finish.call_args.args[6])

    def test_script_without_checkpoint_files_uploads_nothing(self):
        message, worker = self.make_worker()
        worker._heartbeat.side_effect = [True, JobPaused("paused")]
        self.run_container(worker, message, [lambda mounts: None])
        self.assertNotIn(checkpoint_key(message, 2), self.uploads)
        self.task_updates.assert_not_called()
        self.assertNotIn("[SpotPool]", self.log_text(message))

    def test_too_large_checkpoint_is_reported_once(self):
        message, worker = self.make_worker()
        with patch.object(checkpoint, "MAX_BYTES", 2):
            self.run_container(worker, message, [self.write_state("123"), self.write_state("4567"), "exit"])
        self.assertNotIn(checkpoint_key(message, 2), self.uploads)
        self.assertEqual(1, self.log_text(message).count("vượt giới hạn"))

    def test_video_job_restores_and_saves_encoded_segments(self):
        message, worker = self.make_worker(kind="video")
        digest = self.store_checkpoint(message, 1, tar_bytes([regular("video.meta", b"meta"),
                                                              regular("seg_0000.mp4", b"segment 0")]))
        worker._heartbeat.side_effect = [True, JobPaused("paused")]

        def encode_next(mounts):
            (mounts["/checkpoint"] / "seg_0001.mp4.tmp").write_bytes(b"half")  # Đoạn đang nén dở.
            (mounts["/checkpoint"] / "seg_0001.mp4").write_bytes(b"segment 1")

        state = self.run_container(worker, message, [encode_next], restore_from=1, restore_sha256=digest)
        self.assertEqual(["seg_0000.mp4", "video.meta"], state["seen"])
        self.assertIn("SPOTPOOL_CHECKPOINT_DIR=/checkpoint", state["command"])
        self.assertIn("SPOTPOOL_VIDEO_HEIGHT=720", state["command"])
        self.assertEqual("PAUSED", worker._finish.call_args.args[6])
        with tarfile.open(fileobj=io.BytesIO(self.uploads[checkpoint_key(message, 2)][-1])) as tar:
            self.assertEqual(["seg_0000.mp4", "seg_0001.mp4", "video.meta"], sorted(tar.getnames()))

    def test_process_passes_saved_attempt_from_claim(self):
        message, worker = self.make_worker()
        tasks = worker.db.Table("Tasks")
        tasks.rows[(message["jobId"], message["taskId"])]["status"] = "QUEUED"
        tasks.update_item = Mock(return_value={"Attributes": {"attempt": 3, "checkpointAttempt": 2,
                                                              "checkpointSha256": "ab" * 32}})
        worker.db.Table("Jobs").item["status"] = "RUNNING"
        worker._execute = Mock()
        worker.process({"ReceiptHandle": "r", "Body": "{}"}, message)
        self.assertEqual((3, 100, 2, "ab" * 32), worker._execute.call_args.args[4:8])

    # ---------- Giới hạn dung lượng của mỗi task ----------

    def test_task_writing_too_much_is_failed_and_container_removed(self):
        message, worker = self.make_worker()

        def flood(mounts):
            (mounts["/output"] / "junk").write_bytes(b"x" * 80)
            (mounts["/checkpoint"] / "more").write_bytes(b"x" * 80)

        with patch("worker.agent.MAX_WRITE_BYTES", 100):
            state = self.run_container(worker, message, [flood, flood])
        self.assertEqual("FAILED", worker._finish.call_args.args[6])
        self.assertIn("/output và /checkpoint", worker._finish.call_args.args[9])
        # Container đang chạy bị xóa ngay, kết quả không được tải lên.
        self.assertEqual(["docker", "rm", "-f", state["command"][4]], state["docker_run"].call_args.args[0])
        worker.s3.upload_fileobj.assert_not_called()

    def test_oversized_input_fails_before_container_starts(self):
        message, worker = self.make_worker()
        with patch("worker.agent.MAX_INPUT_BYTES", 5), \
                patch("worker.agent.subprocess.Popen") as popen:
            worker._execute({"ReceiptHandle": "r"}, message, {}, "owner", 2, 1000)
        popen.assert_not_called()
        self.assertEqual("FAILED", worker._finish.call_args.args[6])
        self.assertIn("File đầu vào lớn hơn", worker._finish.call_args.args[9])

    def test_download_stops_as_soon_as_input_grows_past_limit(self):
        message, worker = self.make_worker()
        chunks = []

        def download(bucket, key, path, Callback=None):
            for _ in range(10):
                Callback(4)
                chunks.append(4)
            Path(path).write_bytes(b"x" * 40)

        worker.s3.download_file.side_effect = download
        with patch("worker.agent.MAX_INPUT_BYTES", 10), \
                patch("worker.agent.subprocess.Popen") as popen:
            worker._execute({"ReceiptHandle": "r"}, message, {}, "owner", 2, 1000)
        popen.assert_not_called()
        self.assertEqual(2, len(chunks))  # lần gọi thứ 3 (12 byte) bị chặn
        self.assertEqual("FAILED", worker._finish.call_args.args[6])

    def test_work_directory_comes_from_environment(self):
        message, worker = self.make_worker()
        with tempfile.TemporaryDirectory() as base:
            work = Path(base) / "work"
            with patch.dict(os.environ, {"SPOTPOOL_WORK_DIR": str(work)}):
                state = self.run_container(worker, message, [])
            self.assertTrue(work.is_dir())
            self.assertEqual(work.resolve(), state["mounts"]["/output"].parent.parent.resolve())
            self.assertEqual([], os.listdir(work))  # thư mục của task đã được xóa
        self.assertEqual("SUCCEEDED", worker._finish.call_args.args[6])


class WrittenBytesTests(unittest.TestCase):
    def test_counts_nested_files(self):
        from worker.agent import written_bytes
        with tempfile.TemporaryDirectory() as base:
            out, cp = Path(base) / "out", Path(base) / "cp"
            (out / "a" / "b").mkdir(parents=True)
            cp.mkdir()
            (out / "a" / "b" / "f").write_bytes(b"x" * 7)
            (out / "g").write_bytes(b"x" * 3)
            (cp / "h").write_bytes(b"x" * 5)
            self.assertEqual(15, written_bytes(out, cp, None))

    def test_does_not_follow_symlinks(self):
        from worker.agent import written_bytes
        with tempfile.TemporaryDirectory() as base:
            out, outside = Path(base) / "out", Path(base) / "outside"
            out.mkdir()
            outside.mkdir()
            (outside / "big").write_bytes(b"x" * 1000)
            try:
                (out / "link").symlink_to(outside, target_is_directory=True)
                (out / "file-link").symlink_to(outside / "big")
            except OSError:
                self.skipTest("Không tạo được symlink trên máy này")
            self.assertEqual(0, written_bytes(out))

    def test_too_many_entries_is_a_quota_error(self):
        from worker.agent import DiskQuotaExceeded, written_bytes
        with tempfile.TemporaryDirectory() as base:
            for index in range(6):
                (Path(base) / str(index)).write_bytes(b"")
            with patch("worker.agent.MAX_WRITE_ENTRIES", 5):
                with self.assertRaises(DiskQuotaExceeded):
                    written_bytes(base)


if __name__ == "__main__":
    unittest.main()
