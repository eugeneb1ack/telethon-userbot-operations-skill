from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.types import Channel, MessageReplyHeader, PeerChannel

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import apply_runtime_env, load_settings

logger = logging.getLogger(__name__)
MAX_LIMIT = 500_000


@dataclass
class DiscussionCandidate:
    entity: Channel
    comments_count: int = 0
    first_comment: datetime | None = None
    last_comment: datetime | None = None
    reply_channel_ids: set[int] = field(default_factory=set)
    direct_channels: dict[int, Channel] = field(default_factory=dict)
    linked_chat_id: int | None = None
    full_chat_ids: list[int] = field(default_factory=list)


def register(client: TelegramClient) -> None:
    """Read-only CLI helper; no event handler is attached."""


def is_comment_message(message: Any) -> bool:
    reply_to = getattr(message, "reply_to", None)
    return isinstance(reply_to, MessageReplyHeader) and getattr(reply_to, "reply_to_msg_id", None) is not None


def is_broadcast_channel(entity: Any) -> bool:
    return isinstance(entity, Channel) and bool(getattr(entity, "broadcast", False)) and not bool(
        getattr(entity, "megagroup", False)
    )


def remember_candidate(
    candidates: dict[int, DiscussionCandidate], message: Any, chat: Channel
) -> DiscussionCandidate:
    candidate = candidates.setdefault(chat.id, DiscussionCandidate(entity=chat))
    candidate.comments_count += 1
    date = getattr(message, "date", None)
    if date is not None:
        if candidate.first_comment is None or date < candidate.first_comment:
            candidate.first_comment = date
        if candidate.last_comment is None or date > candidate.last_comment:
            candidate.last_comment = date

    reply_to = getattr(message, "reply_to", None)
    reply_peer = getattr(reply_to, "reply_to_peer_id", None)
    if isinstance(reply_peer, PeerChannel):
        candidate.reply_channel_ids.add(reply_peer.channel_id)
    reply_chat = getattr(message, "reply_to_chat", None)
    if is_broadcast_channel(reply_chat):
        candidate.direct_channels[reply_chat.id] = reply_chat
    return candidate


def add_channel_hit(
    channels: dict[int, dict[str, Any]], channel: Channel, candidate: DiscussionCandidate
) -> None:
    row = channels.setdefault(
        channel.id,
        {
            "id": channel.id,
            "title": channel.title or str(channel.id),
            "username": channel.username,
            "comment_count": 0,
            "comment_chat_ids": set(),
            "comment_chat_titles": set(),
            "first_comment": None,
            "last_comment": None,
        },
    )
    row["comment_count"] += candidate.comments_count
    row["comment_chat_ids"].add(candidate.entity.id)
    row["comment_chat_titles"].add(candidate.entity.title or str(candidate.entity.id))
    if row["first_comment"] is None or (
        candidate.first_comment is not None and candidate.first_comment < row["first_comment"]
    ):
        row["first_comment"] = candidate.first_comment
    if row["last_comment"] is None or (
        candidate.last_comment is not None and candidate.last_comment > row["last_comment"]
    ):
        row["last_comment"] = candidate.last_comment


async def get_full_channel(client: TelegramClient, entity: Channel) -> Any | None:
    for attempt in range(2):
        try:
            return await client(GetFullChannelRequest(entity))
        except FloodWaitError as exc:
            if attempt:
                raise
            await asyncio.sleep(exc.seconds + 1)
        except (RPCError, ValueError, TypeError):
            logger.debug("Не удалось получить полную информацию о чате id=%s", entity.id, exc_info=True)
            return None
    return None


def find_broadcast_channel(chats: list[Any], channel_id: int) -> Channel | None:
    for entity in chats:
        if isinstance(entity, Channel) and entity.id == channel_id and is_broadcast_channel(entity):
            return entity
    return None


async def resolve_linked_channel(
    client: TelegramClient, candidate: DiscussionCandidate
) -> Channel | None:
    full = await get_full_channel(client, candidate.entity)
    if full is None:
        return None
    linked_chat_id = getattr(full.full_chat, "linked_chat_id", None)
    candidate.linked_chat_id = linked_chat_id
    candidate.full_chat_ids = [
        entity.id for entity in getattr(full, "chats", []) if isinstance(entity, Channel)
    ]
    if linked_chat_id is None:
        return None
    channel = find_broadcast_channel(getattr(full, "chats", []), linked_chat_id)
    if channel is not None:
        return channel
    try:
        entity = await client.get_entity(PeerChannel(linked_chat_id))
    except (RPCError, ValueError, TypeError):
        return None
    return entity if is_broadcast_channel(entity) else None


async def resolve_reply_channel(
    client: TelegramClient, candidate: DiscussionCandidate
) -> list[Channel]:
    channels = list(candidate.direct_channels.values())
    known_ids = {channel.id for channel in channels}
    for channel_id in sorted(candidate.reply_channel_ids - known_ids):
        try:
            entity = await client.get_entity(PeerChannel(channel_id))
        except (RPCError, ValueError, TypeError):
            continue
        if is_broadcast_channel(entity):
            channels.append(entity)
    return channels


def serialize_channel(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "username": row["username"],
        "comment_count": row["comment_count"],
        "comment_chat_ids": sorted(row["comment_chat_ids"]),
        "comment_chat_titles": sorted(row["comment_chat_titles"], key=str.casefold),
        "first_comment": row["first_comment"].isoformat() if row["first_comment"] else None,
        "last_comment": row["last_comment"].isoformat() if row["last_comment"] else None,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.limit is not None and not 1 <= args.limit <= MAX_LIMIT:
        raise ValueError(f"--limit must be between 1 and {MAX_LIMIT}")

    settings = load_settings(args.account)
    apply_runtime_env(settings)
    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized; refusing interactive login")
        me = await client.get_me()
        candidates: dict[int, DiscussionCandidate] = {}
        scanned_messages = 0
        outgoing_messages = 0
        scanned_group_dialogs = 0
        skipped_group_dialogs: list[dict[str, Any]] = []
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            if not isinstance(entity, Channel) or not bool(getattr(entity, "megagroup", False)):
                continue
            if args.limit is not None and scanned_messages >= args.limit:
                break
            scanned_group_dialogs += 1
            remaining = None if args.limit is None else args.limit - scanned_messages
            try:
                async for message in client.iter_messages(entity, from_user=me, limit=remaining, wait_time=1):
                    scanned_messages += 1
                    outgoing_messages += 1
                    if not is_comment_message(message):
                        continue
                    chat = getattr(message, "chat", None) or entity
                    if isinstance(chat, Channel) and bool(getattr(chat, "megagroup", False)):
                        remember_candidate(candidates, message, chat)
            except FloodWaitError:
                raise
            except RPCError as exc:
                fallback_remaining = None if args.limit is None else args.limit - scanned_messages
                try:
                    async for message in client.iter_messages(entity, limit=fallback_remaining, wait_time=1):
                        scanned_messages += 1
                        if not bool(getattr(message, "out", False)) and getattr(message, "sender_id", None) != me.id:
                            continue
                        outgoing_messages += 1
                        if not is_comment_message(message):
                            continue
                        chat = getattr(message, "chat", None) or entity
                        if isinstance(chat, Channel) and bool(getattr(chat, "megagroup", False)):
                            remember_candidate(candidates, message, chat)
                except FloodWaitError:
                    raise
                except RPCError as fallback_exc:
                    skipped_group_dialogs.append(
                        {
                            "id": entity.id,
                            "title": entity.title or str(entity.id),
                            "error": type(fallback_exc).__name__,
                            "filter_error": type(exc).__name__,
                        }
                    )

        channels: dict[int, dict[str, Any]] = {}
        unresolved_groups: list[dict[str, Any]] = []
        for candidate in candidates.values():
            resolved = await resolve_linked_channel(client, candidate)
            for channel in await resolve_reply_channel(client, candidate):
                add_channel_hit(channels, channel, candidate)
            if resolved is not None:
                add_channel_hit(channels, resolved, candidate)
            else:
                unresolved_groups.append(
                    {
                        "id": candidate.entity.id,
                        "title": candidate.entity.title or str(candidate.entity.id),
                        "comment_count": candidate.comments_count,
                        "reply_channel_ids": sorted(candidate.reply_channel_ids),
                        "linked_chat_id": candidate.linked_chat_id,
                        "full_chat_ids": sorted(candidate.full_chat_ids),
                    }
                )

        serialized = sorted(
            (serialize_channel(row) for row in channels.values()),
            key=lambda item: item["title"].casefold(),
        )
        return {
            "ok": True,
            "read_only": True,
            "scanned_messages": scanned_messages,
            "matched_outgoing_messages": outgoing_messages,
            "scanned_group_dialogs": scanned_group_dialogs,
            "discussion_groups_with_comments": len(candidates),
            "resolved_channel_count": len(serialized),
            "channels": serialized,
            "skipped_group_dialogs": skipped_group_dialogs,
            "unresolved_discussion_groups": sorted(unresolved_groups, key=lambda item: item["title"].casefold()),
            "matching_rule": "outgoing messages with Telegram reply_to in accessible megagroup dialogs linked to broadcast channels",
        }
    finally:
        await client.disconnect()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="List broadcast channels where the account wrote comments")
    result.add_argument("--account", default="main")
    result.add_argument("--limit", type=int, help="Maximum messages to scan; omit for full accessible history")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        result = asyncio.run(run(args))
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"comment_channels failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
