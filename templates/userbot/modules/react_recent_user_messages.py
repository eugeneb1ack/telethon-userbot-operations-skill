from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from telethon import TelegramClient, errors, functions, types
from telethon.tl.types import Channel, Chat, User

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import apply_runtime_env, load_settings


DEFAULT_DELAY = 0.9


def register(client):
    return None


def display_user(user: Any) -> str:
    name = " ".join(
        part for part in [getattr(user, "first_name", None), getattr(user, "last_name", None)] if part
    ).strip()
    if name:
        return name
    username = getattr(user, "username", None)
    return f"@{username}" if username else f"id:{getattr(user, 'id', '?')}"


async def resolve_chat(client: TelegramClient, query: str) -> tuple[Any, dict[str, Any]]:
    q = query.casefold().strip()
    matches: list[tuple[Any, Any]] = []
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if not getattr(dialog, "is_group", False) and not isinstance(entity, (Channel, Chat)):
            continue
        fields = [
            dialog.name or "",
            getattr(entity, "title", "") or "",
            getattr(entity, "username", "") or "",
            str(dialog.id),
            str(getattr(entity, "id", "")),
        ]
        if q in " ".join(fields).casefold():
            matches.append((dialog, entity))

    if not matches:
        raise RuntimeError(f"chat_not_found:{query}")

    exact = [
        item
        for item in matches
        if q in {
            (item[0].name or "").casefold(),
            (getattr(item[1], "title", "") or "").casefold(),
            (getattr(item[1], "username", "") or "").casefold(),
        }
    ]
    if len(exact) == 1:
        dialog, entity = exact[0]
    elif len(matches) == 1:
        dialog, entity = matches[0]
    else:
        raise RuntimeError(
            json.dumps(
                {
                    "error": "ambiguous_chat",
                    "matches": [
                        {
                            "dialog_id": int(d.id),
                            "title": d.name,
                            "entity_id": getattr(e, "id", None),
                            "username": getattr(e, "username", None),
                        }
                        for d, e in matches[:20]
                    ],
                    "query": query,
                },
                ensure_ascii=False,
            )
        )

    return entity, {
        "dialog_id": int(dialog.id),
        "title": dialog.name,
        "entity_id": getattr(entity, "id", None),
        "username": getattr(entity, "username", None),
        "type": type(entity).__name__,
    }


async def verify_reaction(
    client: TelegramClient,
    chat: Any,
    message_id: int,
    me_id: int,
    emoji: str,
) -> str:
    try:
        result = await client(
            functions.messages.GetMessageReactionsListRequest(
                peer=chat,
                id=message_id,
                limit=100,
                reaction=types.ReactionEmoji(emoticon=emoji),
            )
        )
        for item in getattr(result, "reactions", []) or []:
            peer_id = getattr(item, "peer_id", None)
            user_id = getattr(peer_id, "user_id", None)
            reaction = getattr(item, "reaction", None)
            if getattr(item, "my", False) and user_id == me_id and getattr(reaction, "emoticon", None) == emoji:
                return "verified"
        return "not_verified"
    except Exception as exc:
        # Recent reactions are a useful fallback if Telegram rejects the list request.
        try:
            message = await client.get_messages(chat, ids=message_id)
            reactions = getattr(getattr(message, "reactions", None), "recent_reactions", []) or []
            for item in reactions:
                peer_id = getattr(item, "peer_id", None)
                reaction = getattr(item, "reaction", None)
                if (
                    getattr(peer_id, "user_id", None) == me_id
                    and getattr(reaction, "emoticon", None) == emoji
                ):
                    return "verified_fallback"
        except Exception:
            pass
        return f"verification_error:{type(exc).__name__}"


def has_my_recent_reaction(message: Any, me_id: int, emoji: str) -> bool:
    reactions = getattr(getattr(message, "reactions", None), "recent_reactions", []) or []
    return any(
        getattr(getattr(item, "peer_id", None), "user_id", None) == me_id
        and getattr(getattr(item, "reaction", None), "emoticon", None) == emoji
        for item in reactions
    )


def my_recent_reactions(message: Any, me_id: int) -> list[Any]:
    return [
        item.reaction
        for item in (getattr(getattr(message, "reactions", None), "recent_reactions", []) or [])
        if getattr(getattr(item, "peer_id", None), "user_id", None) == me_id
    ]


async def audit_recent_batch(
    client: TelegramClient,
    chat: Any,
    message_ids: list[int],
    me_id: int,
    emoji: str,
) -> dict[str, Any]:
    messages = await client.get_messages(chat, ids=message_ids)
    verified = [
        int(message.id)
        for message in messages
        if message is not None and has_my_recent_reaction(message, me_id, emoji)
    ]
    missing = [message_id for message_id in message_ids if message_id not in set(verified)]
    return {"verified_ids": verified, "not_confirmed_ids": missing}


async def run(
    account: str,
    chat_query: str,
    username: str,
    limit: int,
    emoji: str,
    execute: bool,
    delay: float,
    frozen_ids: list[int] | None = None,
    audit_only: bool = False,
    preserve_existing: bool = True,
) -> dict[str, Any]:
    settings = load_settings(account)
    apply_runtime_env(settings)
    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            return {"ok": False, "error": "account_not_authorized"}

        me = await client.get_me()
        chat, chat_meta = await resolve_chat(client, chat_query)
        user = await client.get_entity(username)
        if not isinstance(user, User):
            return {
                "ok": False,
                "error": "target_is_not_user",
                "target": {"input": username, "type": type(user).__name__},
            }

        if frozen_ids is None:
            messages = [message async for message in client.iter_messages(chat, from_user=user, limit=limit)]
            target_messages = [message for message in messages if message is not None]
            frozen = False
        else:
            frozen_messages = await client.get_messages(chat, ids=frozen_ids)
            target_messages = [
                message
                for message in frozen_messages
                if message is not None and getattr(message, "sender_id", None) == int(user.id)
            ]
            frozen = True
        target_ids = [int(message.id) for message in target_messages]
        messages_by_id = {int(message.id): message for message in target_messages}
        plan = {
            "ok": True,
            "dry_run": not execute,
            "emoji": emoji,
            "preserve_existing": preserve_existing,
            "requested_count": limit,
            "target_count": len(target_ids),
            "chat": chat_meta,
            "user": {
                "id": int(user.id),
                "username": f"@{user.username}" if user.username else None,
                "name": display_user(user),
            },
            "message_ids_newest_first": target_ids,
            "newest_message_id": target_ids[0] if target_ids else None,
            "oldest_message_id": target_ids[-1] if target_ids else None,
            "frozen_ids": frozen,
        }
        if not execute and not audit_only:
            return plan

        if audit_only:
            audit = await audit_recent_batch(client, chat, target_ids, int(me.id), emoji)
            plan.update(
                {
                    "audit_only": True,
                    "verified_count": len(audit["verified_ids"]),
                    "verified_direct_count": len(audit["verified_ids"]),
                    "verified_fallback_count": 0,
                    "not_verified_count": len(audit["not_confirmed_ids"]),
                    "verified_message_ids": audit["verified_ids"],
                    "not_confirmed_message_ids": audit["not_confirmed_ids"],
                }
            )
            return plan

        applied: list[int] = []
        skipped_existing: list[int] = []
        errors_out: list[dict[str, Any]] = []
        for message_id in target_ids:
            message = messages_by_id[message_id]
            reactions = my_recent_reactions(message, int(me.id)) if preserve_existing else []
            if has_my_recent_reaction(message, int(me.id), emoji):
                skipped_existing.append(message_id)
                continue
            reactions.append(types.ReactionEmoji(emoticon=emoji))
            attempt = 0
            while True:
                attempt += 1
                try:
                    await client(
                        functions.messages.SendReactionRequest(
                            peer=chat,
                            msg_id=message_id,
                            reaction=reactions,
                        )
                    )
                    applied.append(message_id)
                    await asyncio.sleep(delay)
                    break
                except errors.FloodWaitError as exc:
                    if attempt >= 2:
                        errors_out.append({"message_id": message_id, "error": "flood_wait", "seconds": exc.seconds})
                        break
                    await asyncio.sleep(exc.seconds)
                except Exception as exc:
                    errors_out.append({"message_id": message_id, "error": type(exc).__name__, "detail": str(exc)})
                    break

        audit = await audit_recent_batch(client, chat, applied, int(me.id), emoji)
        verified = len(audit["verified_ids"])
        verified_fallback = 0
        not_verified = len(audit["not_confirmed_ids"])
        verification_errors: list[dict[str, Any]] = []

        plan.update(
            {
                "applied_count": len(applied),
                "applied_message_ids": applied,
                "skipped_existing_count": len(skipped_existing),
                "skipped_existing_message_ids": skipped_existing,
                "errors": errors_out,
                "verified_count": verified + verified_fallback,
                "verified_direct_count": verified,
                "verified_fallback_count": verified_fallback,
                "not_verified_count": not_verified,
                "verified_message_ids": audit["verified_ids"],
                "not_confirmed_message_ids": audit["not_confirmed_ids"],
                "verification_errors": verification_errors,
            }
        )
        return plan
    finally:
        await client.disconnect()


async def main() -> None:
    parser = argparse.ArgumentParser(description="React to a user's recent messages in a Telegram group")
    parser.add_argument("--account", default="main")
    parser.add_argument("--chat", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--emoji", default="🤡")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    parser.add_argument(
        "--message-ids",
        help="Comma-separated frozen message IDs, newest first; skips recollecting the moving latest-N window",
    )
    parser.add_argument("--audit", action="store_true", help="Verify the emoji on frozen IDs without writing")
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Replace your existing reactions instead of preserving them",
    )
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.audit and args.execute:
        parser.error("--audit and --execute are mutually exclusive")
    if args.limit < 1:
        parser.error("--limit must be >= 1")
    if args.delay < 0:
        parser.error("--delay must be >= 0")
    frozen_ids = [int(value) for value in args.message_ids.split(",") if value.strip()] if args.message_ids else None
    result = await run(
        account=args.account,
        chat_query=args.chat,
        username=args.username,
        limit=args.limit,
        emoji=args.emoji,
        execute=args.execute,
        delay=args.delay,
        frozen_ids=frozen_ids,
        audit_only=args.audit,
        preserve_existing=not args.replace_existing,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
