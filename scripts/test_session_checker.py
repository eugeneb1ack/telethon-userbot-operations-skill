#!/usr/bin/env python3
"""Offline regression test for verify_userbot_session.py.

Creates a throwaway fake userbot project and a harmless SQLite session fixture.
No Telegram client is imported or contacted.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "verify_userbot_session.py"


def write_fake_project(root: Path, *, with_session: bool) -> Path:
    (root / "core").mkdir(parents=True)
    (root / "core" / "__init__.py").write_text("", encoding="utf-8")
    session_name = root / "runtime" / "main" / "sessions" / "main"
    config = (
        "from types import SimpleNamespace\n"
        "def load_settings(account):\n"
        f"    return SimpleNamespace(account=account, session_name={str(session_name)!r}, api_id=1, api_hash='x')\n"
    )
    (root / "core" / "config.py").write_text(config, encoding="utf-8")
    session_file = Path(f"{session_name}.session")
    if with_session:
        session_file.parent.mkdir(parents=True)
        with sqlite3.connect(session_file) as connection:
            connection.execute("CREATE TABLE sessions (dc_id INTEGER)")
        session_file.chmod(0o600)
    return session_file


def run(root: Path) -> tuple[int, dict]:
    completed = subprocess.run(
        [sys.executable, str(CHECKER), "--project-root", str(root), "--account", "main"],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode, json.loads(completed.stdout)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="telethon_skill_session_test_") as tmp:
        project = Path(tmp) / "project"
        session_file = write_fake_project(project, with_session=True)
        code, payload = run(project)
        assert code == 0, payload
        assert payload["session"]["present"]
        assert payload["session"]["sqlite_readable"]
        assert payload["session"]["safe_permissions"]
        assert payload["session"]["path"] == "runtime/main/sessions/main.session"
        session_file.unlink()
        code, payload = run(project)
        assert code == 2, payload
        assert not payload["session"]["present"]
        assert "next_step" in payload
    print("session_checker_test=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
