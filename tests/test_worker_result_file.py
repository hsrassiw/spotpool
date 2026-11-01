"""Result không được đi theo symlink hoặc mở lại path sau khi kiểm tra."""

import errno
import io
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from worker.agent import open_result_file
from test_tv2_week1 import WorkerContractTests

message_fixture = WorkerContractTests._message
worker_fixture = WorkerContractTests._worker
del WorkerContractTests


class ResultFileTests(unittest.TestCase):
    def test_regular_binary_file_is_readable_and_closed_by_context(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "result"
            path.write_bytes(b"\x00result\xff")
            with open_result_file(path) as result:
                self.assertEqual(b"\x00result\xff", result.read())
            self.assertTrue(result.closed)

    def test_lstat_symlink_or_directory_is_rejected_before_open(self):
        for mode in (stat.S_IFLNK, stat.S_IFDIR, stat.S_IFIFO):
            with self.subTest(mode=mode), patch.object(Path, "lstat", return_value=Mock(st_mode=mode)), \
                    patch("worker.agent.os.open") as opening:
                with self.assertRaisesRegex(ValueError, "regular file"):
                    open_result_file(Path("result"))
                opening.assert_not_called()

    def test_real_symlink_to_existing_or_missing_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            secret = root / "host-file.txt"
            secret.write_text("simulated host data", encoding="utf-8")
            for target in (secret, root / "missing"):
                link = root / "result"
                try:
                    link.symlink_to(target)
                except OSError as error:
                    self.skipTest(f"OS cannot create test symlink: {error}")
                try:
                    with patch("worker.agent.os.open") as opening:
                        with self.assertRaisesRegex(ValueError, "regular file"):
                            open_result_file(link)
                        opening.assert_not_called()
                finally:
                    link.unlink()

    def test_open_uses_nofollow_when_supported_and_propagates_link_race(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "result"
            path.write_bytes(b"safe")
            nofollow = getattr(os, "O_NOFOLLOW", 0x10000000)
            with patch("worker.agent.os.O_NOFOLLOW", nofollow, create=True), \
                    patch("worker.agent.os.open", side_effect=OSError(errno.ELOOP, "Symlink replaced result")) as opening:
                with self.assertRaises(OSError):
                    open_result_file(path)
                self.assertEqual(nofollow, opening.call_args.args[1] & nofollow)

    def test_replacement_between_lstat_and_open_is_rejected_and_descriptor_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path, replacement = root / "result", root / "replacement"
            path.write_bytes(b"safe")
            replacement.write_bytes(b"simulated host data")
            actual_open = os.open
            descriptors = []

            def swap_then_open(name, flags):
                path.rename(root / "original")
                replacement.rename(path)
                descriptor = actual_open(name, flags)
                descriptors.append(descriptor)
                return descriptor

            with patch("worker.agent.os.open", side_effect=swap_then_open):
                with self.assertRaisesRegex(ValueError, "changed"):
                    open_result_file(path)
            with self.assertRaises(OSError):
                os.fstat(descriptors[0])

    def test_validated_descriptor_stays_readable_without_reopening_path(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "result"
            path.write_bytes(b"safe output")
            with open_result_file(path) as result:
                if os.name == "posix":
                    path.rename(root / "validated-output")
                    path.write_bytes(b"replacement data")
                with patch.object(Path, "open", side_effect=AssertionError("Path must not be reopened")):
                    self.assertEqual(b"safe output", result.read())

    def make_execution(self):
        message = message_fixture()
        _db, _sqs, worker = worker_fixture(message)
        worker.config.update(VIDEO_JOB_IMAGE="test-image", TASK_MAX_SECONDS="100")
        worker.s3 = Mock()
        worker.s3.download_file.side_effect = lambda b, k, p, **kwargs: Path(p).write_bytes(b"input")
        worker._heartbeat = Mock(return_value=True)
        worker._finish = Mock()
        proc = Mock(stdout=io.BytesIO(b"container log"), returncode=0)
        proc.poll.return_value = 0

        def start(command, **kwargs):
            mount = next(value for value in command if "target=/output" in value)
            output = mount.split("source=", 1)[1].split(",target=", 1)[0]
            (Path(output) / "result").write_bytes(b"safe output")
            return proc

        return message, worker, start

    def test_execute_uploads_the_validated_file_object_and_closes_it(self):
        message, worker, start = self.make_execution()
        uploaded = []
        worker.s3.upload_fileobj.side_effect = lambda result, bucket, key, **kwargs: uploaded.append(result.read())
        with patch("worker.agent.subprocess.Popen", side_effect=start):
            worker._execute({"ReceiptHandle": "receipt"}, message, {}, "owner", 1, 100)
        self.assertEqual([b"safe output"], uploaded)
        self.assertTrue(worker.s3.upload_fileobj.call_args.args[0].closed)
        self.assertEqual("SUCCEEDED", worker._finish.call_args.args[6])
        self.assertEqual(1, worker.s3.upload_file.call_count)  # Chỉ log dùng đường dẫn.

    def test_unsafe_result_fails_task_and_uploads_log_without_result(self):
        message, worker, start = self.make_execution()
        with patch("worker.agent.subprocess.Popen", side_effect=start), \
                patch("worker.agent.open_result_file", side_effect=ValueError("Symlink result rejected")):
            worker._execute({"ReceiptHandle": "receipt"}, message, {}, "owner", 1, 100)
        worker.s3.upload_fileobj.assert_not_called()
        self.assertEqual("FAILED", worker._finish.call_args.args[6])
        self.assertIsNone(worker._finish.call_args.args[7])
        self.assertIn("Symlink", worker._finish.call_args.args[9])
        self.assertTrue(worker.s3.upload_file.call_args.args[2].endswith("/log.txt"))

    def test_s3_failure_closes_file_and_keeps_message_for_retry(self):
        message, worker, start = self.make_execution()
        worker.s3.upload_fileobj.side_effect = RuntimeError("S3 unavailable")
        with patch("worker.agent.subprocess.Popen", side_effect=start):
            with self.assertRaisesRegex(RuntimeError, "S3 unavailable"):
                worker._execute({"ReceiptHandle": "receipt"}, message, {}, "owner", 1, 100)
        self.assertTrue(worker.s3.upload_fileobj.call_args.args[0].closed)
        worker._finish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
