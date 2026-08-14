#!/usr/bin/env python3
"""Check a local Telethon session file without exposing its contents.

Default mode is offline: it reads no Telegram network data and never starts an
interactive login. --online only performs connect() + is_user_authorized().
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import sqlite3
import stat
import sys
from pathlib import Path
from typing import Any


def session_file_from_name(session_name: str) -> Path:
    path = Path(session_name)
    return path if path.suffix == ".session" else Path(f"{path}.session")


def safe_relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return "<outside-project-root>"


def session_metadata(path: Path, root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": safe_relative(path, root),
        "present": path.is_file(),
        "sqlite_readable": False,
        "safe_permissions": None,
        "tables": [],
    }
    if not path.is_file():
        return result

    mode = stat.S_IMODE(path.stat().st_mode)
    result["safe_permissions"] = not bool(mode & (stat.S_IRWXG | stat.S_IRWXO))
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            ).fetchall()
        result["sqlite_readable"] = True
        result["tables"] = [row[0] for row in rows]
    except sqlite3.Error as exc:
        result["sqlite_error"] = type(exc).__name__
    return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Check a local Telethon session file without printing credentials or session contents"
    )
    result.add_argument(
        "--project-root",
        default=os.getenv("USERBOT_ROOT", str(Path.home() / "Documents" / "telethon-userbot")),
        help="Path to the userbot project (default: $USERBOT_ROOT or ~/Documents/telethon-userbot)",
    )
    result.add_argument("--account", default="main")
    result.add_argument(
        "--online",
        action="store_true",
        help="Also connect and check authorization; never starts an interactive login",
    )
    return result


def load_project_settings(project_root: Path, account: str) -> Any:
    if not project_root.is_dir():
        raise ValueError("project_root_not_found")
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    config = importlib.import_module("core.config")
    return config.load_settings(account)


async def online_authorization(settings: Any) -> tuple[bool, str | None]:
    from telethon import TelegramClient

    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)
    try:
        await client.connect()
        return bool(await client.is_user_authorized()), None
    except Exception as exc:
        return False, type(exc).__name__
    finally:
        await client.disconnect()


def main() -> int:
    args = parser().parse_args()
    root = Path(args.project_root).expanduser()
    try:
        settings = load_project_settings(root, args.account)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"settings_error:{type(exc).__name__}"}, ensure_ascii=False))
        return 1

    session_file = session_file_from_name(settings.session_name)
    payload: dict[str, Any] = {
        "ok": True,
        "account": getattr(settings, "account", args.account) or args.account,
        "session": session_metadata(session_file, root),
        "online_check": None,
    }
    if not payload["session"]["present"]:
        payload["ok"] = False
        payload["next_step"] = "Run the trusted project launcher locally and complete Telegram login manually."
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2

    if args.online:
        authorized, error = asyncio.run(online_authorization(settings))
        payload["online_check"] = {"authorized": authorized, "error": error}
        payload["ok"] = bool(payload["session"]["sqlite_readable"]) and authorized

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
