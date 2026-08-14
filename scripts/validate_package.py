#!/usr/bin/env python3
"""Validate the distributable skill package without network or Telegram I/O."""

from __future__ import annotations

import py_compile
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "SKILL.md",
    "README.md",
    "INSTALL.md",
    "SECURITY.md",
    ".gitignore",
    "references/operation-playbook.md",
    "references/module-authoring.md",
    "references/session-bootstrap.md",
    "scripts/telethon_api_inventory.py",
    "scripts/verify_userbot_session.py",
    "scripts/test_session_checker.py",
)
FORBIDDEN_NAMES = {".env", "accounts", "runtime", "data", "venv", ".venv", "__pycache__"}
FORBIDDEN_SUFFIXES = {".session", ".session-journal", ".sqlite3", ".db"}


def main() -> int:
    missing = [relative for relative in REQUIRED if not (ROOT / relative).is_file()]
    if missing:
        print(f"Missing required files: {', '.join(missing)}", file=sys.stderr)
        return 1

    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    if not skill.startswith("---\n") or "\nname: telethon-userbot-operations\n" not in skill:
        print("SKILL.md frontmatter is missing or has the wrong name", file=sys.stderr)
        return 1

    blocked = []
    for path in ROOT.rglob("*"):
        if ".git" in path.parts:
            continue
        if path.name in FORBIDDEN_NAMES or path.suffix in FORBIDDEN_SUFFIXES:
            blocked.append(str(path.relative_to(ROOT)))
    if blocked:
        print(f"Forbidden runtime or secret-bearing paths: {', '.join(sorted(blocked))}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="telethon_skill_validate_") as tmp:
        compile_dir = Path(tmp)
        for script in (ROOT / "scripts").glob("*.py"):
            py_compile.compile(str(script), cfile=str(compile_dir / f"{script.name}c"), doraise=True)

    print("package_validation=ok")
    print(f"root={ROOT}")
    print(f"required_files={len(REQUIRED)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
