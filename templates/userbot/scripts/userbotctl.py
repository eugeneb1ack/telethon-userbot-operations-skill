#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.userbotd import DEFAULT_IDLE_SECONDS, bounded_idle_seconds, safe_account, start


class GatewayUnavailable(RuntimeError):
    pass


def socket_path(project_root: Path, account: str) -> Path:
    override = os.getenv("USERBOT_SOCKET")
    if override:
        return Path(override).expanduser()
    return project_root / "runtime" / account / "userbot.sock"


async def request(path: Path, method: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(str(path)), timeout=3
        )
    except (FileNotFoundError, ConnectionRefusedError, asyncio.TimeoutError) as exc:
        raise GatewayUnavailable(
            f"gateway unavailable at {path}"
        ) from exc
    payload = {
        "id": uuid.uuid4().hex,
        "method": method,
        "params": params,
    }
    writer.write(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
    await writer.drain()
    try:
        raw = await asyncio.wait_for(reader.readline(), timeout=120)
    finally:
        writer.close()
        await writer.wait_closed()
    if not raw:
        raise RuntimeError("gateway closed the connection without a response")
    response = json.loads(raw)
    if not response.get("ok"):
        error = response.get("error") or {}
        raise RuntimeError(f"{error.get('type', 'GatewayError')}: {error.get('message', 'unknown error')}")
    return response["result"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fast local JSON client for the persistent Telethon gateway"
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
    )
    parser.add_argument("--account", default=os.getenv("USERBOT_ACCOUNT", "main"))
    parser.add_argument(
        "--no-auto-start",
        action="store_true",
        help="Fail instead of starting a short-lived gateway on demand",
    )
    parser.add_argument(
        "--idle-seconds",
        type=int,
        default=os.getenv("USERBOT_IDLE_SECONDS", str(DEFAULT_IDLE_SECONDS)),
        help="Auto-stop an on-demand gateway after local inactivity (10-3600)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("status")

    recent_dms = commands.add_parser("recent-dms")
    recent_dms.add_argument("--limit", type=int, default=3)
    recent_dms.add_argument("--dialogs-limit", type=int, default=100)
    recent_dms.add_argument("--messages-per-dialog", type=int, default=20)

    dialogs = commands.add_parser("dialogs")
    dialogs.add_argument(
        "--kind", choices=("personal", "groups", "channels", "bots", "all"), default="personal"
    )
    dialogs.add_argument("--limit", type=int, default=100)

    search = commands.add_parser("search")
    search.add_argument("--chat", required=True)
    search.add_argument("--query", required=True)
    search.add_argument("--limit", type=int, default=50)

    recent = commands.add_parser("recent")
    recent.add_argument("--chat", required=True)
    recent.add_argument("--limit", type=int, default=20)

    event_commands = commands.add_parser("events").add_subparsers(
        dest="event_command", required=True
    )
    event_list = event_commands.add_parser("list")
    event_list.add_argument("--limit", type=int, default=20)
    event_list.add_argument("--unread", action="store_true")
    event_show = event_commands.add_parser("show")
    event_show.add_argument("event_id")
    event_ack = event_commands.add_parser("ack")
    event_ack.add_argument("event_id")
    return parser


def rpc_for(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    if args.command == "status":
        return "status", {}
    if args.command == "recent-dms":
        return "recent_personal_incoming", {
            "limit": args.limit,
            "dialogs_limit": args.dialogs_limit,
            "messages_per_dialog": args.messages_per_dialog,
        }
    if args.command == "dialogs":
        return "dialogs.list", {"kind": args.kind, "limit": args.limit}
    if args.command == "search":
        return "messages.search", {
            "chat": args.chat,
            "query": args.query,
            "limit": args.limit,
        }
    if args.command == "recent":
        return "messages.recent", {"chat": args.chat, "limit": args.limit}
    if args.event_command == "list":
        return "events.list", {"limit": args.limit, "unread_only": args.unread}
    if args.event_command == "show":
        return "events.show", {"event_id": args.event_id}
    if args.event_command == "ack":
        return "events.acknowledge", {"event_id": args.event_id}
    raise AssertionError("unreachable")


def main() -> int:
    args = build_parser().parse_args()
    method, params = rpc_for(args)
    project_root = args.project_root.expanduser().resolve()
    try:
        account = safe_account(args.account)
        idle_seconds = bounded_idle_seconds(args.idle_seconds)
        path = socket_path(project_root, account)
        try:
            result = asyncio.run(request(path, method, params))
        except GatewayUnavailable:
            if args.no_auto_start:
                raise
            start(project_root, account, idle_seconds)
            result = asyncio.run(request(path, method, params))
    except Exception as exc:
        print(
            json.dumps(
                {"ok": False, "error": type(exc).__name__, "message": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
