from __future__ import annotations

import fcntl
import os
from pathlib import Path
from typing import TextIO


class AccountRuntimeLock:
    """Own one account runtime and publish the exact process PID."""

    def __init__(self, runtime_dir: str | Path) -> None:
        self.runtime_dir = Path(runtime_dir)
        self.lock_path = self.runtime_dir / "userbot.lock"
        self.pid_path = self.runtime_dir / "userbot.pid"
        self._handle: TextIO | None = None
        self.pid = os.getpid()

    def acquire(self) -> None:
        if self._handle is not None:
            raise RuntimeError("account runtime lock is already held by this process")
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+", encoding="ascii")
        os.chmod(self.lock_path, 0o600)
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise RuntimeError(
                "another userbot process already owns this account session"
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(f"{self.pid}\n")
        handle.flush()
        os.fsync(handle.fileno())
        self.pid_path.write_text(f"{self.pid}\n", encoding="ascii")
        os.chmod(self.pid_path, 0o600)
        self._handle = handle

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            try:
                published_pid = int(self.pid_path.read_text(encoding="ascii").strip())
            except (FileNotFoundError, OSError, ValueError):
                published_pid = None
            if published_pid == self.pid:
                self.pid_path.unlink(missing_ok=True)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            self._handle = None

    def __enter__(self) -> "AccountRuntimeLock":
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()
