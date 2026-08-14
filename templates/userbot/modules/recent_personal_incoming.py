from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from telethon import TelegramClient, errors

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import apply_runtime_env, load_settings

MAX_RESULTS = 10
MAX_DIALOGS = 500
MAX_MESSAGES_PER_DIALOG = 100
TELEGRAM_SERVICE_USER_ID = 777000


@dataclass(frozen=True)
class RecentIncoming:
    display_name: str
    username: str | None
    received_at: datetime


def register(client: TelegramClient) -> None:
    """Direct CLI helper; no event handler is attached."""


def validate_args(args: argparse.Namespace) -> None:
    if not 1 <= args.limit <= MAX_RESULTS:
        raise ValueError(f"--limit must be between 1 and {MAX_RESULTS}")
    if not 1 <= args.dialogs_limit <= MAX_DIALOGS:
        raise ValueError(f"--dialogs-limit must be between 1 and {MAX_DIALOGS}")
    if not 1 <= args.messages_per_dialog <= MAX_MESSAGES_PER_DIALOG:
        raise ValueError(f"--messages-per-dialog must be between 1 and {MAX_MESSAGES_PER_DIALOG}")


def is_personal_dialog(dialog: Any) -> bool:
    entity = getattr(dialog, "entity", None)
    return bool(
        getattr(dialog, "is_user", False)
        and entity is not None
        and not getattr(entity, "bot", False)
        and not getattr(entity, "is_self", False)
        and getattr(entity, "id", None) != TELEGRAM_SERVICE_USER_ID
    )


def display_name(entity: Any) -> str:
    name = " ".join(
        part
        for part in (getattr(entity, "first_name", None), getattr(entity, "last_name", None))
        if part
    ).strip()
    username = getattr(entity, "username", None)
    if name:
        return name
    if username:
        return f"@{username}"
    return "Пользователь без отображаемого имени"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def latest_incoming_for_dialog(
    client: TelegramClient,
    dialog: Any,
    *,
    messages_per_dialog: int,
) -> RecentIncoming | None:
    entity = dialog.entity
    async for message in client.iter_messages(entity, limit=messages_per_dialog):
        if getattr(message, "out", False):
            continue
        received_at = getattr(message, "date", None)
        if not isinstance(received_at, datetime):
            continue
        return RecentIncoming(
            display_name=display_name(entity),
            username=getattr(entity, "username", None),
            received_at=_as_utc(received_at),
        )
    return None


async def _collect_recent_incoming_once(
    client: TelegramClient,
    *,
    limit: int,
    dialogs_limit: int,
    messages_per_dialog: int,
) -> tuple[list[RecentIncoming], int]:
    records: list[RecentIncoming] = []
    scanned_dialogs = 0
    async for dialog in client.iter_dialogs(limit=dialogs_limit):
        if not is_personal_dialog(dialog):
            continue
        scanned_dialogs += 1
        record = await latest_incoming_for_dialog(
            client,
            dialog,
            messages_per_dialog=messages_per_dialog,
        )
        if record is not None:
            records.append(record)

    records.sort(key=lambda item: item.received_at, reverse=True)
    return records[:limit], scanned_dialogs


async def collect_recent_incoming(
    client: TelegramClient,
    *,
    limit: int,
    dialogs_limit: int,
    messages_per_dialog: int,
) -> tuple[list[RecentIncoming], int]:
    for attempt in range(2):
        try:
            return await _collect_recent_incoming_once(
                client,
                limit=limit,
                dialogs_limit=dialogs_limit,
                messages_per_dialog=messages_per_dialog,
            )
        except errors.FloodWaitError as exc:
            if attempt:
                raise
            await asyncio.sleep(exc.seconds + 1)
    raise AssertionError("unreachable")


def result_payload(
    records: list[RecentIncoming],
    *,
    scanned_dialogs: int,
    dialogs_limit: int,
) -> dict[str, Any]:
    return {
        "ok": True,
        "read_only": True,
        "scanned_personal_dialogs": scanned_dialogs,
        "dialogs_limit": dialogs_limit,
        "recent_incoming": [
            {
                "name": item.display_name,
                "username": f"@{item.username}" if item.username else None,
                "received_at": item.received_at.isoformat(),
            }
            for item in records
        ],
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    validate_args(args)
    settings = load_settings(args.account)
    apply_runtime_env(settings)
    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized; refusing interactive login")
        records, scanned_dialogs = await collect_recent_incoming(
            client,
            limit=args.limit,
            dialogs_limit=args.dialogs_limit,
            messages_per_dialog=args.messages_per_dialog,
        )
        return result_payload(
            records,
            scanned_dialogs=scanned_dialogs,
            dialogs_limit=args.dialogs_limit,
        )
    finally:
        await client.disconnect()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="List the latest incoming personal-dialog senders without message text"
    )
    result.add_argument("--account", default="main")
    result.add_argument("--limit", type=int, default=3, help="Result count; default: 3")
    result.add_argument(
        "--dialogs-limit",
        type=int,
        default=100,
        help="Maximum recent dialogs to inspect; default: 100",
    )
    result.add_argument(
        "--messages-per-dialog",
        type=int,
        default=20,
        help="Maximum recent messages inspected in each direct dialog; default: 20",
    )
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        result = asyncio.run(run(args))
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"recent_personal_incoming failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
