"""Real FFmpeg smoke test when ffmpeg and a POSIX shell are available."""
import os
from pathlib import Path
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
