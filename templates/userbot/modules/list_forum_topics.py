from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from telethon import TelegramClient, functions
from telethon.tl.types import Channel, Chat

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import apply_runtime_env, load_settings
from core.telegram_targets import entity_payload, resolve_entity

MAX_LIMIT = 100


def register(client: TelegramClient) -> None:
    """Direct CLI helper; retained for the dynamic module loader."""


def validate_args(args: argparse.Namespace) -> None:
    if not 1 <= args.limit <= MAX_LIMIT:
        raise ValueError(f"--limit must be between 1 and {MAX_LIMIT}")
    if args.query is not None and not args.query.strip():
        raise ValueError("--query must not be empty")


def iso_date(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def topic_record(topic: Any) -> dict[str, Any]:
    return {
        "id": getattr(topic, "id", None),
        "title": getattr(topic, "title", None),
        "date": iso_date(getattr(topic, "date", None)),
        "top_message_id": getattr(topic, "top_message", None),
        "unread_count": getattr(topic, "unread_count", 0),
        "unread_mentions_count": getattr(topic, "unread_mentions_count", 0),
        "closed": bool(getattr(topic, "closed", False)),
        "pinned": bool(getattr(topic, "pinned", False)),
        "hidden": bool(getattr(topic, "hidden", False)),
        "icon_emoji_id": getattr(topic, "icon_emoji_id", None),
    }


async def collect_topics(client: Any, chat: Any, *, query: str | None, limit: int) -> tuple[list[dict[str, Any]], int | None]:
    response = await client(
        functions.messages.GetForumTopicsRequest(
            peer=chat,
            q=query.strip() if query else None,
            offset_date=None,
            offset_id=0,
            offset_topic=0,
            limit=limit,
        )
    )
    topics = [topic_record(topic) for topic in getattr(response, "topics", [])]
    return topics, getattr(response, "count", None)


async def run(args: argparse.Namespace) -> dict[str, Any]:
    validate_args(args)
    settings = load_settings(args.account)
    apply_runtime_env(settings)
    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized; refusing interactive login")
        chat = await resolve_entity(client, args.chat)
        if not isinstance(chat, (Channel, Chat)):
            raise ValueError(f"Target is not a group/channel: {type(chat).__name__}")
        topics, reported_count = await collect_topics(
            client, chat, query=args.query, limit=args.limit
        )
        return {
            "ok": True,
            "read_only": True,
            "chat": entity_payload(chat, input_value=args.chat),
            "query": args.query,
            "limit": args.limit,
            "reported_topic_count": reported_count,
            "topic_count": len(topics),
            "may_be_truncated": bool(reported_count is not None and reported_count > len(topics)),
            "topics": topics,
        }
    finally:
        await client.disconnect()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="List Telegram forum topics in one group/channel")
    result.add_argument("--account", default="main")
    result.add_argument("--chat", required=True)
    result.add_argument("--query", help="Optional topic-title search, for example 'Генерал'")
    result.add_argument("--limit", type=int, default=MAX_LIMIT)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        result = asyncio.run(run(args))
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"list_forum_topics failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
