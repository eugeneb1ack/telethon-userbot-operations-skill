from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from telethon import TelegramClient
from telethon.tl.types import User

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import apply_runtime_env, load_settings
from core.telegram_targets import entity_payload, resolve_entity

MAX_LIMIT = 500


def register(client: TelegramClient) -> None:
    """Direct CLI helper; retained for the dynamic module loader."""


def parse_datetime(value: str | None, option: str) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{option} must be ISO-8601 with timezone, e.g. 2026-08-14T12:00:00+03:00") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{option} must include an explicit timezone offset")
    return parsed.astimezone(timezone.utc)


def validate_args(args: argparse.Namespace) -> tuple[datetime | None, datetime | None]:
    if not 1 <= args.limit <= MAX_LIMIT:
        raise ValueError(f"--limit must be between 1 and {MAX_LIMIT}")
    after = parse_datetime(args.after, "--after")
    before = parse_datetime(args.before, "--before")
    if after and before and after > before:
        raise ValueError("--after must be earlier than or equal to --before")
    if not any((args.query, args.from_user, after, before)):
        raise ValueError("Provide at least one filter: --query, --from-user, --after, or --before")
    if args.output and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", args.output):
        raise ValueError("--output must be a simple safe filename")
    return after, before


def safe_output_path(settings: Any, output_name: str) -> Path:
    path = Path(settings.data_dir) / "searches" / output_name
    if path.suffix not in {".json", ".jsonl"}:
        path = path.with_suffix(".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def sender_label(sender: Any, sender_id: int | None) -> str | None:
    if sender is None:
        return f"id:{sender_id}" if sender_id is not None else None
    name = " ".join(part for part in (getattr(sender, "first_name", None), getattr(sender, "last_name", None)) if part).strip()
    return name or (f"@{sender.username}" if getattr(sender, "username", None) else f"id:{sender_id}")


def message_record(message: Any, sender: Any) -> dict[str, Any]:
    text = getattr(message, "message", None) or ""
    date = getattr(message, "date", None)
    return {
        "id": getattr(message, "id", None),
        "date": date.isoformat() if date else None,
        "sender_id": getattr(message, "sender_id", None),
        "sender": sender_label(sender, getattr(message, "sender_id", None)),
        "outgoing": bool(getattr(message, "out", False)),
        "has_media": bool(getattr(message, "media", None)),
        "text_preview": text.replace("\n", " ")[:280],
        "text_length": len(text),
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    after, before = validate_args(args)
    settings = load_settings(args.account)
    apply_runtime_env(settings)
    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized; refusing interactive login")
        chat = await resolve_entity(client, args.chat)
        sender = await resolve_entity(client, args.from_user) if args.from_user else None
        if sender is not None and not isinstance(sender, User):
            raise ValueError(f"--from-user resolved to {type(sender).__name__}, not a user")

        records: list[dict[str, Any]] = []
        examined = 0
        sender_cache: dict[int | None, Any] = {}
        async for message in client.iter_messages(
            chat,
            search=args.query or None,
            from_user=sender,
            limit=args.limit,
        ):
            examined += 1
            date = getattr(message, "date", None)
            if date is not None and date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            if before and date and date.astimezone(timezone.utc) > before:
                continue
            if after and date and date.astimezone(timezone.utc) < after:
                break
            sender_id = getattr(message, "sender_id", None)
            if sender_id not in sender_cache:
                sender_cache[sender_id] = await message.get_sender()
            records.append(message_record(message, sender_cache[sender_id]))

        result = {
            "ok": True,
            "read_only": True,
            "chat": entity_payload(chat, input_value=args.chat),
            "filters": {
                "query": args.query or None,
                "from_user": entity_payload(sender, input_value=args.from_user) if sender else None,
                "after": after.isoformat() if after else None,
                "before": before.isoformat() if before else None,
                "limit": args.limit,
            },
            "examined_count": examined,
            "result_count": len(records),
            "messages": records,
        }
        if args.output:
            output_path = safe_output_path(settings, args.output)
            if output_path.suffix == ".jsonl":
                output_path.write_text(
                    "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
                    encoding="utf-8",
                )
            else:
                output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            result["output_path"] = str(output_path)
        return result
    finally:
        await client.disconnect()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Search a bounded Telegram chat window and export compact metadata/previews")
    result.add_argument("--account", default="main")
    result.add_argument("--chat", required=True)
    result.add_argument("--query", help="Text search query")
    result.add_argument("--from-user", help="Optional exact user filter")
    result.add_argument("--after", help="ISO-8601 lower timestamp bound with timezone")
    result.add_argument("--before", help="ISO-8601 upper timestamp bound with timezone")
    result.add_argument("--limit", type=int, default=100)
    result.add_argument("--output", help="Optional safe .json or .jsonl filename under runtime/<account>/data/searches")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        result = asyncio.run(run(args))
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"search_messages failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
