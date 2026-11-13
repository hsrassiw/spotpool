"""Real FFmpeg smoke test when ffmpeg and a POSIX shell are available."""
import os
from pathlib import Path
import re
import shutil
import shlex
import subprocess
import tempfile
import unittest

FFMPEG = os.environ.get('SPOTPOOL_TEST_FFMPEG') or shutil.which('ffmpeg')
if not FFMPEG:
    try:
        import imageio_ffmpeg
        FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        pass
SHELL = os.environ.get('SPOTPOOL_TEST_SHELL') or shutil.which('sh')
ENTRYPOINT = Path(__file__).resolve().parents[1] / 'job-templates' / 'video' / 'entrypoint.sh'


@unittest.skipUnless(FFMPEG and SHELL, 'Real FFmpeg smoke test requires ffmpeg and sh')
class VideoTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bin_dir = tempfile.TemporaryDirectory()
        wrapper = Path(cls.bin_dir.name) / 'ffmpeg'
        wrapper.write_text('#!/bin/sh\nexec ' + shlex.quote(str(FFMPEG).replace('\\', '/')) + ' "$@"\n', encoding='utf-8')
        wrapper.chmod(0o755)

    @classmethod
    def tearDownClass(cls):
        cls.bin_dir.cleanup()

    def env(self, height):
        return {**os.environ, 'PATH': self.bin_dir.name + os.pathsep + os.environ.get('PATH', ''),
                'SPOTPOOL_VIDEO_HEIGHT': height}

    def test_rejects_invalid_height_before_reading_input(self):
        for height in ('', '3', '0', '99999999999999999999999999', 'abc', '720;exit 0'):
            result = subprocess.run([SHELL, str(ENTRYPOINT), 'missing', 'unused'],
                                    env=self.env(height), capture_output=True, text=True)
            self.assertEqual(2, result.returncode, result.stderr)

    def test_encodes_mp4_to_extensionless_worker_output(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / 'payload', Path(folder) / 'result'
            subprocess.run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
                'testsrc=size=160x120:rate=10', '-t', '0.4', '-pix_fmt', 'yuv444p', '-f', 'mp4', str(source)],
                check=True, capture_output=True)
            result = subprocess.run([SHELL, str(ENTRYPOINT), str(source), str(output)],
                                    env=self.env('0080'), capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertGreater(output.stat().st_size, 0)
            self.assertIn(b'ftyp', output.read_bytes()[:32])
            probe = subprocess.run([FFMPEG, '-hide_banner', '-i', str(output)], capture_output=True, text=True)
            self.assertIn('h264', probe.stderr)
            self.assertIn('106x80', probe.stderr)
            # Đầu vào yuv444p vẫn phải ra yuv420p, nhiều trình duyệt và điện thoại không phát được 4:4:4
            self.assertIn('yuv420p', probe.stderr)


@unittest.skipUnless(FFMPEG and SHELL, 'Real FFmpeg smoke test requires ffmpeg and sh')
class VideoCheckpointTests(unittest.TestCase):
    """Có SPOTPOOL_CHECKPOINT_DIR: nén từng đoạn vào thư mục checkpoint, bị dừng thì chạy tiếp."""

    @classmethod
    def setUpClass(cls):
        VideoTemplateTests.setUpClass.__func__(cls)
        cls.source_dir = tempfile.TemporaryDirectory()
        # 7 giây, 30000/1001 khung hình/giây (không chia hết cho độ dài đoạn) và có tiếng.
        cls.source = Path(cls.source_dir.name) / 'payload'
        subprocess.run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
            'testsrc=size=160x120:rate=30000/1001', '-f', 'lavfi', '-i', 'sine=frequency=440',
            '-t', '7', '-shortest', '-pix_fmt', 'yuv420p', '-c:v', 'libx264', '-c:a', 'aac', '-f', 'mp4',
            str(cls.source)], check=True, capture_output=True)
        cls.source_frames = cls.frames(cls.source)

    @classmethod
    def tearDownClass(cls):
        cls.source_dir.cleanup()
        cls.bin_dir.cleanup()

    @staticmethod
    def frames(path):
        probe = subprocess.run([FFMPEG, '-hide_banner', '-i', str(path), '-map', '0:v:0', '-f', 'null', '-'],
                               capture_output=True, text=True)
        return int(re.findall(r'frame=\s*(\d+)', probe.stderr)[-1])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.checkpoint = Path(self.folder.name) / 'checkpoint'
        self.checkpoint.mkdir()

    def run_entrypoint(self, output, height='0080', segment='2', source=None):
        env = {**os.environ, 'PATH': self.bin_dir.name + os.pathsep + os.environ.get('PATH', ''),
               'SPOTPOOL_VIDEO_HEIGHT': height, 'SPOTPOOL_VIDEO_SEGMENT_SECONDS': segment,
               'SPOTPOOL_CHECKPOINT_DIR': str(self.checkpoint)}
        result = subprocess.run([SHELL, str(ENTRYPOINT), str(source or self.source), str(output)],
                                env=env, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(0, result.returncode, result.stderr)
        return result.stdout

    def saved(self):
        return sorted(path.name for path in self.checkpoint.iterdir())

    def test_encodes_segments_into_checkpoint_and_keeps_every_frame(self):
        output = Path(self.folder.name) / 'result'
        log = self.run_entrypoint(output)
        self.assertIn('Chia video 7 giây thành 3 đoạn', log)
        self.assertIn('Xong đoạn 3/3.', log)
        # Không còn file *.tmp: nền tảng chỉ lưu các đoạn đã nén xong.
        self.assertEqual(['seg_0000.mp4', 'seg_0001.mp4', 'seg_0002.mp4', 'video.meta'], self.saved())
        self.assertEqual(self.source_frames, self.frames(output))
        probe = subprocess.run([FFMPEG, '-hide_banner', '-i', str(output)], capture_output=True, text=True)
        self.assertIn('h264', probe.stderr)
        self.assertIn('106x80', probe.stderr)
        self.assertIn('aac', probe.stderr)

    def test_resumes_after_the_last_saved_segment(self):
        first = Path(self.folder.name) / 'first'
        self.run_entrypoint(first)
        # Giống lượt bị thu hồi khi đang nén đoạn cuối: đoạn đó chưa kịp lưu, còn file nén dở.
        (self.checkpoint / 'seg_0002.mp4').rename(self.checkpoint / 'seg_0002.mp4.tmp')
        kept = (self.checkpoint / 'seg_0000.mp4').stat().st_mtime_ns
        output = Path(self.folder.name) / 'result'
        log = self.run_entrypoint(output)
        self.assertIn('Tiếp tục từ đoạn 3/3 (đã nén xong 2 đoạn', log)
        self.assertNotIn('Xong đoạn 1/3.', log)
        self.assertEqual(kept, (self.checkpoint / 'seg_0000.mp4').stat().st_mtime_ns)
        self.assertEqual(self.source_frames, self.frames(output))

    def test_discards_segments_encoded_with_other_parameters(self):
        self.run_entrypoint(Path(self.folder.name) / 'first', height='0080')
        output = Path(self.folder.name) / 'result'
        log = self.run_entrypoint(output, height='0060')
        self.assertIn('Chia video 7 giây thành 3 đoạn', log)
        self.assertIn('Xong đoạn 1/3.', log)
        probe = subprocess.run([FFMPEG, '-hide_banner', '-i', str(output)], capture_output=True, text=True)
        self.assertIn('80x60', probe.stderr)

    def test_short_video_is_encoded_in_one_pass(self):
        output = Path(self.folder.name) / 'result'
        log = self.run_entrypoint(output, segment='4')
        self.assertNotIn('đoạn', log)
        self.assertEqual([], self.saved())
        self.assertEqual(self.source_frames, self.frames(output))

    def test_rejects_invalid_segment_length(self):
        for segment in ('', '0', 'abc', '99999'):
            env = {**os.environ, 'SPOTPOOL_VIDEO_SEGMENT_SECONDS': segment}
            result = subprocess.run([SHELL, str(ENTRYPOINT), 'missing', 'unused'], env=env,
                                    capture_output=True, text=True)
            self.assertEqual(2, result.returncode, result.stderr)
