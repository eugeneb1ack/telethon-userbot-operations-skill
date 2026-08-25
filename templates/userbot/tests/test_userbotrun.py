from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.runtime_lock import AccountRuntimeLock


class UserbotRunnerLifecycleTests(unittest.TestCase):
    @staticmethod
    def _wait_for_file(path: Path, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.is_file() and path.read_text(encoding="ascii").strip():
                return
            time.sleep(0.02)
        raise AssertionError(f"timed out waiting for {path}")

    @staticmethod
    def _process_exists(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True

    def test_sigterm_reaps_module_process_and_releases_account_lock(self) -> None:
        with tempfile.TemporaryDirectory(prefix="userbotrun_lifecycle_") as tmp:
            project = Path(tmp)
            module_dir = project / "modules"
            python_dir = project / "venv" / "bin"
            module_dir.mkdir(parents=True)
            python_dir.mkdir(parents=True)
            os.symlink(sys.executable, python_dir / "python")

            marker = project / "child.pid"
            module = module_dir / "wait_forever.py"
            module.write_text(
                """\
import argparse
import os
import signal
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--account", required=True)
parser.add_argument("--marker", required=True)
args = parser.parse_args()

def stop(_signum, _frame):
    raise SystemExit(0)

signal.signal(signal.SIGINT, stop)
Path(args.marker).write_text(f"{os.getpid()}\\n", encoding="ascii")
while True:
    time.sleep(1)
""",
                encoding="utf-8",
            )

            runner = subprocess.Popen(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "userbotrun.py"),
                    "--project-root",
                    str(project),
                    "--account",
                    "main",
                    "--timeout",
                    "60",
                    "modules/wait_forever.py",
                    "--",
                    "--marker",
                    str(marker),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            child_pid: int | None = None
            try:
                self._wait_for_file(marker)
                child_pid = int(marker.read_text(encoding="ascii").strip())
                os.kill(runner.pid, signal.SIGTERM)
                _stdout, stderr = runner.communicate(timeout=10)

                self.assertEqual(runner.returncode, 128 + signal.SIGTERM)
                payload = json.loads(stderr.strip().splitlines()[-1])
                self.assertEqual(payload["error"], "RunnerInterrupted")
                self.assertEqual(payload["signal"], "SIGTERM")
                self.assertEqual(payload["last_signal"], "SIGINT")
                self.assertFalse(self._process_exists(child_pid))
                self.assertFalse((project / "runtime" / "main" / "userbot.pid").exists())

                lock = AccountRuntimeLock(project / "runtime" / "main")
                lock.acquire()
                lock.release()
            finally:
                if runner.poll() is None:
                    runner.kill()
                    runner.wait(timeout=2)
                if child_pid is not None and self._process_exists(child_pid):
                    os.kill(child_pid, signal.SIGKILL)


if __name__ == "__main__":
    unittest.main()
