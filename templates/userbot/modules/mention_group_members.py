from __future__ import annotations

import argparse
import asyncio
import html
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from telethon import TelegramClient, errors
from telethon.tl.types import User, Channel, Chat
from core.config import apply_runtime_env, load_settings


def register(client):
    return None


def display_name(user: User) -> str:
    name = " ".join(
        part for part in [getattr(user, "first_name", None), getattr(user, "last_name", None)] if part
    ).strip()
    if name:
        return name
    if getattr(user, "username", None):
        return "@" + user.username
    return f"id{user.id}"


def link_for(user: User) -> str:
    return f'<a href="tg://user?id={user.id}">{html.escape(display_name(user))}</a>'


def chunk_mentions(prefix: str, links: list[str], max_raw: int = 3300) -> list[str]:
    chunks: list[str] = []
    cur = prefix
    for link in links:
        addition = ("" if cur.endswith("\n\n") else " ") + link
        if len(cur) + len(addition) > max_raw and cur != prefix:
            chunks.append(cur)
            cur = prefix + link
        else:
            cur += addition
    if cur.strip():
        chunks.append(cur)
    return chunks


async def resolve_chat(client: TelegramClient, query: str) -> tuple[Any, dict[str, Any]]:
    q = query.casefold().strip()
    matches = []
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
        raise SystemExit(json.dumps({"ok": False, "error": f"chat_not_found:{query}"}, ensure_ascii=False))
    exact = [m for m in matches if q == (m[0].name or "").casefold() or q == (getattr(m[1], "title", "") or "").casefold()]
    if len(exact) == 1:
        chosen = exact[0]
    elif len(matches) == 1:
        chosen = matches[0]
    else:
        raise SystemExit(
            json.dumps(
                {
                    "ok": False,
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
                },
                ensure_ascii=False,
            )
        )
    dialog, entity = chosen
    return entity, {
        "dialog_id": int(dialog.id),
        "title": dialog.name,
        "entity_id": getattr(entity, "id", None),
        "username": getattr(entity, "username", None),
        "type": type(entity).__name__,
    }


async def run(account: str, chat: str, text: str, execute: bool, limit: int = 0) -> dict[str, Any]:
    settings = load_settings(account)
    apply_runtime_env(settings)
    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            return {"ok": False, "error": "account_not_authorized"}
        me = await client.get_me()
        entity, chat_meta = await resolve_chat(client, chat)

        users: list[User] = []
        bots = deleted = self_count = scanned = 0
        try:
            async for p in client.iter_participants(entity, limit=(limit or None)):
                scanned += 1
                if not isinstance(p, User):
                    continue
                if getattr(p, "bot", False):
                    bots += 1
                    continue
                if getattr(p, "deleted", False):
                    deleted += 1
                    continue
                if p.id == me.id or getattr(p, "is_self", False):
                    self_count += 1
                    continue
                users.append(p)
        except errors.FloodWaitError as exc:
            return {"ok": False, "error": "flood_wait", "seconds": exc.seconds}

        users.sort(key=lambda u: (display_name(u).casefold(), u.id))
        chunks = chunk_mentions(text.strip() + "\n\n", [link_for(u) for u in users])
        payload = {
            "ok": True,
            "dry_run": not execute,
            "chat": chat_meta,
            "scanned_participants": scanned,
            "mention_count": len(users),
            "excluded_bots": bots,
            "excluded_deleted": deleted,
            "excluded_self": self_count,
            "chunks": len(chunks),
            "sample": [
                {"id": u.id, "name": display_name(u), "username": getattr(u, "username", None)} for u in users[:10]
            ],
        }
        if execute:
            sent = []
            for i, msg in enumerate(chunks, start=1):
                final = msg if len(chunks) == 1 else f"{text.strip()} ({i}/{len(chunks)})\n\n" + msg.split("\n\n", 1)[1]
                m = await client.send_message(entity, final, parse_mode="html")
                sent.append({"id": m.id, "text_len": len(final)})
                await asyncio.sleep(1.2)
            payload["sent_messages"] = sent
        return payload
    finally:
        await client.disconnect()


async def main() -> None:
    ap = argparse.ArgumentParser(description="Mention all non-bot members of a Telegram group")
    ap.add_argument("--account", default="main")
    ap.add_argument("--chat", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    print(json.dumps(await run(args.account, args.chat, args.text, args.execute, args.limit), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
