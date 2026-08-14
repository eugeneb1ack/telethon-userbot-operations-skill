#!/usr/bin/env python3
"""Create a new local userbot project from this skill's safe source template.

The default mode is a dry-run. --execute only creates a destination that does
not already exist, so it cannot overwrite a working userbot project.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "userbot"
FORBIDDEN_NAMES = {".env", "accounts", "runtime", "data", "venv", ".venv", "__pycache__"}
FORBIDDEN_SUFFIXES = {".session", ".session-journal", ".sqlite3", ".db"}


def template_files() -> list[Path]:
    if not TEMPLATE.is_dir():
        raise ValueError("template_missing")
    files: list[Path] = []
    for path in sorted(TEMPLATE.rglob("*")):
        relative = path.relative_to(TEMPLATE)
        if path.is_symlink() or not path.is_file():
            continue
        if path.name in FORBIDDEN_NAMES or path.suffix in FORBIDDEN_SUFFIXES:
            raise ValueError(f"unsafe_template_path:{relative}")
        files.append(relative)
    if not files:
        raise ValueError("template_empty")
    return files


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Safely bootstrap a new local Telethon userbot project from the installed userbot skill"
    )
    result.add_argument(
        "--destination",
        default=os.getenv("USERBOT_ROOT", str(Path.home() / "Documents" / "telethon-userbot")),
        help="New destination path; it must not already exist",
    )
    result.add_argument(
        "--execute",
        action="store_true",
        help="Actually create the new project; without it this command only prints a plan",
    )
    return result


def payload(destination: Path, files: list[Path], *, dry_run: bool, ok: bool = True, error: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": ok,
        "dry_run": dry_run,
        "destination_exists": destination.exists(),
        "template_file_count": len(files),
        "required_next_step": "Run python3 scripts/setup_account.py --account main locally, then run ./run.sh --account main.",
    }
    if error:
        result["error"] = error
    return result


def copy_template(destination: Path, files: list[Path]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir(mode=0o755)
    for relative in files:
        source = TEMPLATE / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    account_dir = destination / "accounts"
    account_dir.mkdir(mode=0o700)
    example = destination / "ACCOUNT.env.example"
    target = account_dir / "main.env.example"
    shutil.copy2(example, target)
    target.chmod(0o600)
    example.unlink()


def main() -> int:
    args = parser().parse_args()
    destination = Path(args.destination).expanduser()
    try:
        files = template_files()
    except ValueError as exc:
        print(json.dumps(payload(destination, [], dry_run=not args.execute, ok=False, error=str(exc)), ensure_ascii=False))
        return 1

    if destination.exists():
        print(json.dumps(payload(destination, files, dry_run=not args.execute, ok=False, error="destination_exists"), ensure_ascii=False))
        return 2
    if not args.execute:
        print(json.dumps(payload(destination, files, dry_run=True), ensure_ascii=False, indent=2))
        return 0

    try:
        copy_template(destination, files)
    except Exception as exc:
        if destination.exists():
            shutil.rmtree(destination)
        print(json.dumps(payload(destination, files, dry_run=False, ok=False, error=f"bootstrap_error:{type(exc).__name__}"), ensure_ascii=False))
        return 1

    print(json.dumps(payload(destination, files, dry_run=False), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
