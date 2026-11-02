"""Тесты Runner на реальных процессах. Кроссплатформенно: вместо printf/cat/sleep используется Python."""
import gzip
import sys
import tempfile
import unittest
from pathlib import Path

from verifier.runner import Runner

PY = sys.executable
ECHO = [PY, "-c", "import sys; sys.stdout.write('hello world')"]
CAT = [PY, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"]
SLEEP = [PY, "-c", "import time; time.sleep(30)"]
PRINT_SECRET = [PY, "-c", "import os, sys; sys.stdout.write(os.environ['MY_SECRET'])"]


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_stdout_to_gz_and_stdin_from_gz(self):
        out = self.dir / "o.sql.gz"
        r = Runner().run(ECHO, stdout_path=out)
        self.assertTrue(r.ok, r.stderr)
        self.assertEqual(gzip.open(out).read(), b"hello world")
        r = Runner().run(CAT, stdin_path=out)
        self.assertTrue(r.ok, r.stderr)
        self.assertEqual(r.stdout, "hello world")

    def test_corrupted_gzip_is_failure_not_silent_truncation(self):
        out = self.dir / "o.sql.gz"
        out.write_bytes(gzip.compress(b"x" * 100000))
        data = bytearray(out.read_bytes())
        data[len(data) // 2] ^= 0xFF
        out.write_bytes(bytes(data))
        r = Runner().run(CAT, stdin_path=out)
        self.assertFalse(r.ok)
        # причина именно в битом архиве, а не в «команда не найдена»
        self.assertNotEqual(r.returncode, 127)
        self.assertIn("ошибка чтения", r.stderr)

    def test_timeout(self):
        r = Runner().run(SLEEP, timeout=1)
        self.assertTrue(r.timed_out)
        self.assertFalse(r.ok)

    def test_missing_command(self):
        self.assertEqual(Runner().run(["no-such-binary-xyz"]).returncode, 127)

    def test_secret_passed_via_env(self):
        r = Runner().run(PRINT_SECRET, env={"MY_SECRET": "s3"})
        self.assertTrue(r.ok, r.stderr)
        self.assertEqual(r.stdout, "s3")


if __name__ == "__main__":
    unittest.main()