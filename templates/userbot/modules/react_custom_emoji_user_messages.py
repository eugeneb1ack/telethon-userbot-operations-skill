from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from telethon import TelegramClient, errors, functions, types
from telethon.tl.types import User

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import apply_runtime_env, load_settings
from modules.react_recent_user_messages import display_user, my_recent_reactions, resolve_chat

DEFAULT_DELAY = 0.9


def register(client: TelegramClient) -> None:
    """Direct CLI helper; no event handler is attached."""


def normalize_name(value: str) -> str:
    return " ".join(value.casefold().split())


def has_my_custom_reaction(message: Any, me_id: int, document_id: int) -> bool:
    reactions = getattr(getattr(message, "reactions", None), "recent_reactions", []) or []
    return any(
        getattr(getattr(item, "peer_id", None), "user_id", None) == me_id
        and getattr(getattr(item, "reaction", None), "document_id", None) == document_id
        for item in reactions
    )


async def _call_with_one_flood_retry(client: TelegramClient, request: Any) -> Any:
    for attempt in range(2):
        try:
            return await client(request)
        except errors.FloodWaitError as exc:
            if attempt:
                raise
            await asyncio.sleep(exc.seconds + 1)
    raise RuntimeError("unreachable")


async def resolve_target_user(client: TelegramClient, chat: Any, requested_name: str) -> User:
    wanted = normalize_name(requested_name)
    candidates: dict[int, User] = {}
    async for user in client.iter_participants(chat, search=requested_name, limit=100):
        if isinstance(user, User) and normalize_name(display_user(user)) == wanted:
            candidates[int(user.id)] = user

    if not candidates:
        async for user in client.iter_participants(chat):
            if isinstance(user, User) and normalize_name(display_user(user)) == wanted:
                candidates[int(user.id)] = user

    if len(candidates) != 1:
        raise RuntimeError(
            json.dumps(
                {
                    "error": "target_user_ambiguous_or_missing",
                    "requested_name": requested_name,
                    "candidate_ids": sorted(candidates),
                },
                ensure_ascii=False,
            )
        )
    return next(iter(candidates.values()))


async def resolve_pack_document(client: TelegramClient, short_name: str) -> dict[str, Any]:
    result = await _call_with_one_flood_retry(
        client,
        functions.messages.GetStickerSetRequest(
            stickerset=types.InputStickerSetShortName(short_name),
            hash=0,
        ),
    )
    sticker_set = getattr(result, "set", None)
    if not getattr(sticker_set, "emojis", False):
        raise RuntimeError(f"sticker set is not a custom emoji pack: {short_name}")
    documents = list(getattr(result, "documents", []) or [])
    if getattr(sticker_set, "count", None) != 1 or len(documents) != 1:
        raise RuntimeError("expected the requested text pack to contain exactly one emoji document")
    document_id = int(documents[0].id)
    resolved = await _call_with_one_flood_retry(
        client,
        functions.messages.GetCustomEmojiDocumentsRequest(document_id=[document_id]),
    )
    if not any(int(item.id) == document_id for item in resolved):
        raise RuntimeError("Telegram did not resolve the pack document as a custom emoji")
    return {
        "short_name": getattr(sticker_set, "short_name", short_name),
        "title": getattr(sticker_set, "title", None),
        "count": getattr(sticker_set, "count", None),
        "document_id": document_id,
    }


async def audit_batch(client: TelegramClient, chat: Any, message_ids: list[int], me_id: int, document_id: int) -> list[int]:
    if not message_ids:
        return []
    messages = await client.get_messages(chat, ids=message_ids)
    return [
        int(message.id)
        for message in messages
        if message is not None and has_my_custom_reaction(message, me_id, document_id)
    ]


async def run(
    *,
    account: str,
    chat_query: str,
    user_name: str,
    pack_short_name: str,
    limit: int,
    execute: bool,
    delay: float,
    frozen_ids: list[int] | None = None,
    audit_only: bool = False,
) -> dict[str, Any]:
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    if delay < 0:
        raise ValueError("delay must be >= 0")

    settings = load_settings(account)
    apply_runtime_env(settings)
    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized; refusing interactive login")
        me = await client.get_me()
        chat, chat_meta = await resolve_chat(client, chat_query)
        user = await resolve_target_user(client, chat, user_name)
        pack = await resolve_pack_document(client, pack_short_name)

        if frozen_ids is None:
            target_messages = [
                message
                async for message in client.iter_messages(chat, from_user=user, limit=limit)
                if message is not None
            ]
            frozen = False
        else:
            loaded = await client.get_messages(chat, ids=frozen_ids)
            target_messages = [
                message
                for message in loaded
                if message is not None and getattr(message, "sender_id", None) == int(user.id)
            ]
            frozen = True
        target_ids = [int(message.id) for message in target_messages]
        messages_by_id = {int(message.id): message for message in target_messages}
        plan: dict[str, Any] = {
            "ok": True,
            "dry_run": not execute,
            "requested_count": limit,
            "target_count": len(target_ids),
            "chat": chat_meta,
            "user": {
                "id": int(user.id),
                "name": display_user(user),
                "username": f"@{user.username}" if user.username else None,
            },
            "pack": pack,
            "message_ids_newest_first": target_ids,
            "newest_message_id": target_ids[0] if target_ids else None,
            "oldest_message_id": target_ids[-1] if target_ids else None,
            "frozen_ids": frozen,
        }
        if not execute and not audit_only:
            return plan

        if audit_only:
            verified_ids = await audit_batch(client, chat, target_ids, int(me.id), pack["document_id"])
            plan.update(
                {
                    "audit_only": True,
                    "verified_count": len(verified_ids),
                    "not_verified_count": len(target_ids) - len(verified_ids),
                    "verified_message_ids": verified_ids,
                }
            )
            return plan

        applied: list[int] = []
        skipped_existing: list[int] = []
        errors_out: list[dict[str, Any]] = []
        custom_reaction = types.ReactionCustomEmoji(document_id=pack["document_id"])
        for message_id in target_ids:
            message = messages_by_id[message_id]
            if has_my_custom_reaction(message, int(me.id), pack["document_id"]):
                skipped_existing.append(message_id)
                continue
            reactions = my_recent_reactions(message, int(me.id))
            reactions.append(custom_reaction)
            try:
                await _call_with_one_flood_retry(
                    client,
                    functions.messages.SendReactionRequest(
                        peer=chat,
                        msg_id=message_id,
                        reaction=reactions,
                    ),
                )
                applied.append(message_id)
                await asyncio.sleep(delay)
            except Exception as exc:
                errors_out.append({"message_id": message_id, "error": type(exc).__name__, "detail": str(exc)})

        verified_ids = await audit_batch(client, chat, target_ids, int(me.id), pack["document_id"])
        plan.update(
            {
                "dry_run": False,
                "applied_count": len(applied),
                "applied_message_ids": applied,
                "skipped_existing_count": len(skipped_existing),
                "skipped_existing_message_ids": skipped_existing,
                "errors": errors_out,
                "verified_count": len(verified_ids),
                "not_verified_count": len(target_ids) - len(verified_ids),
                "verified_message_ids": verified_ids,
            }
        )
        return plan
    finally:
        await client.disconnect()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="React to one user's recent messages with a custom emoji")
    result.add_argument("--account", default="main")
    result.add_argument("--chat", required=True)
    result.add_argument("--user-name", required=True)
    result.add_argument("--pack-short-name", required=True)
    result.add_argument("--limit", type=int, default=100)
    result.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    result.add_argument("--message-ids", help="Comma-separated frozen message IDs, newest first")
    result.add_argument("--audit", action="store_true", help="Verify frozen IDs without writing")
    result.add_argument("--execute", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    if args.audit and args.execute:
        parser().error("--audit and --execute are mutually exclusive")
    frozen_ids = [int(value) for value in args.message_ids.split(",") if value.strip()] if args.message_ids else None
    try:
        result = asyncio.run(
            run(
                account=args.account,
                chat_query=args.chat,
                user_name=args.user_name,
                pack_short_name=args.pack_short_name,
                limit=args.limit,
                execute=args.execute,
                delay=args.delay,
                frozen_ids=frozen_ids,
                audit_only=args.audit,
            )
        )
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"react_custom_emoji_user_messages failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
