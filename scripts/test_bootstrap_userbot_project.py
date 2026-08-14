#!/usr/bin/env python3
"""Offline regression test for bootstrap_userbot_project.py."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "scripts" / "bootstrap_userbot_project.py"


def run(destination: Path, *extra: str) -> tuple[int, dict]:
    completed = subprocess.run(
        [sys.executable, str(BOOTSTRAP), "--destination", str(destination), *extra],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode, json.loads(completed.stdout)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="telethon_userbot_bootstrap_test_") as tmp:
        destination = Path(tmp) / "userbot"
        code, plan = run(destination)
        assert code == 0, plan
        assert plan["dry_run"]
        assert not destination.exists()
        assert plan["template_file_count"] >= 40

        code, result = run(destination, "--execute")
        assert code == 0, result
        assert not result["dry_run"]
        assert (destination / "main.py").is_file()
        assert (destination / "core" / "config.py").is_file()
        assert (destination / "modules" / "search_messages.py").is_file()
        assert (destination / "scripts" / "userbot_module_registry.py").is_file()
        assert (destination / "accounts" / "main.env.example").is_file()
        assert not (destination / ".env").exists()
        assert not (destination / "runtime").exists()
        assert not any(path.suffix in {".session", ".sqlite3"} for path in destination.rglob("*"))

        code, blocked = run(destination, "--execute")
        assert code == 2, blocked
        assert blocked["error"] == "destination_exists"
    print("bootstrap_userbot_project_test=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
