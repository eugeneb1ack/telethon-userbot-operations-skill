from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from telethon import TelegramClient, errors, types
from telethon.tl.types import Channel, Chat, User

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import apply_runtime_env, load_settings

MOSCOW = ZoneInfo("Europe/Moscow")


def register(client: Any) -> None:
    return None


def display_name(user: Any) -> str:
    name = " ".join(
        part
        for part in [getattr(user, "first_name", None), getattr(user, "last_name", None)]
        if part
    ).strip()
    return name or (f"@{user.username}" if getattr(user, "username", None) else f"id:{user.id}")


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
        if any(q in field.casefold() for field in fields):
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
                },
                ensure_ascii=False,
            )
        )

    return entity, {
        "dialog_id": int(dialog.id),
        "entity_id": int(getattr(entity, "id", 0) or 0),
        "title": dialog.name or getattr(entity, "title", None),
        "username": getattr(entity, "username", None),
        "type": type(entity).__name__,
    }


async def resolve_user(client: TelegramClient, query: str) -> User:
    try:
        entity = await client.get_entity(query)
    except (TypeError, ValueError) as exc:
        try:
            user_id = int(query)
        except ValueError:
            raise
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            if isinstance(entity, User) and entity.id == user_id:
                return entity
        raise RuntimeError(f"user_not_found_in_authorized_account_dialogs:{query}") from exc
    if not isinstance(entity, User):
        raise RuntimeError(f"target_is_not_user:{type(entity).__name__}")
    return entity


def _has_round_video(msg: Any) -> bool:
    if bool(getattr(msg, "video_note", None)):
        return True
    if bool(getattr(msg, "round", False)) or bool(getattr(msg, "round_message", False)):
        return True
    doc = getattr(msg, "document", None)
    for attr in getattr(doc, "attributes", []) or []:
        if type(attr).__name__ == "DocumentAttributeVideo" and getattr(attr, "round_message", False):
            return True
    return False


def classify_message(msg: Any) -> str:
    if _has_round_video(msg):
        return "кружок"
    if bool(getattr(msg, "voice", None)):
        return "голосовое"
    if bool(getattr(msg, "photo", None)):
        return "фото"
    if bool(getattr(msg, "sticker", None)):
        return "стикер"
    if bool(getattr(msg, "gif", None)):
        return "анимация"
    if bool(getattr(msg, "video", None)):
        return "видео"
    if bool(getattr(msg, "audio", None)):
        return "аудио"
    if bool(getattr(msg, "document", None)):
        return "документ"
    if bool(getattr(msg, "contact", None)):
        return "контакт"
    if bool(getattr(msg, "poll", None)):
        return "опрос"
    if bool(getattr(msg, "geo", None)) or bool(getattr(msg, "venue", None)):
        return "геолокация"
    if bool(getattr(msg, "web_preview", None)):
        return "ссылка"
    if getattr(msg, "message", None):
        return "текст"
    media = getattr(msg, "media", None)
    if media is not None:
        return type(media).__name__
    return "прочее"


def local_dt(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(MOSCOW)


async def collect(account: str, chat_query: str, username: str, hours: int) -> dict[str, Any]:
    settings = load_settings(account)
    apply_runtime_env(settings)
    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("account_not_authorized")
        chat, chat_meta = await resolve_chat(client, chat_query)
        user = await resolve_user(client, username)

        now = datetime.now(MOSCOW)
        since = now - timedelta(hours=hours)
        counts: Counter[str] = Counter()
        message_ids: list[int] = []
        oldest: datetime | None = None
        newest: datetime | None = None

        async for msg in client.iter_messages(chat, from_user=user, limit=None):
            if not getattr(msg, "date", None):
                continue
            dt = local_dt(msg.date)
            if dt < since:
                break
            if dt > now:
                continue
            kind = classify_message(msg)
            counts[kind] += 1
            message_ids.append(int(msg.id))
            oldest = dt if oldest is None or dt < oldest else oldest
            newest = dt if newest is None or dt > newest else newest

        return {
            "ok": True,
            "period": {
                "hours": hours,
                "timezone": "Europe/Moscow",
                "since": since.isoformat(),
                "until": now.isoformat(),
            },
            "chat": chat_meta,
            "author": {
                "id": int(user.id),
                "username": f"@{user.username}" if user.username else username,
                "name": display_name(user),
            },
            "total": sum(counts.values()),
            "by_kind": dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))),
            "oldest_message_local": oldest.isoformat() if oldest else None,
            "newest_message_local": newest.isoformat() if newest else None,
            "message_ids_newest_first": message_ids,
        }
    finally:
        await client.disconnect()


def russian_count_form(count: int, one: str, few: str, many: str) -> str:
    mod100 = count % 100
    if 11 <= mod100 <= 14:
        return many
    mod10 = count % 10
    if mod10 == 1:
        return one
    if 2 <= mod10 <= 4:
        return few
    return many


def render_summary(result: dict[str, Any]) -> str:
    period = result["period"]
    chat = result["chat"]
    author = result["author"]
    since = datetime.fromisoformat(period["since"]).strftime("%d.%m.%Y %H:%M")
    until = datetime.fromisoformat(period["until"]).strftime("%d.%m.%Y %H:%M")
    lines = [
        "СВОДКА ПО СООБЩЕНИЯМ",
        "",
        f"Период: {since} — {until} (МСК), последние {period['hours']} часов",
        f"Чат: {chat['title']} (id {chat['entity_id']})",
        f"Автор: {author['name']} {author['username']} (id {author['id']})",
        "",
        f"Всего сообщений: {result['total']}",
        "",
        "Разбивка по типам:",
    ]
    for kind, count in result["by_kind"].items():
        lines.append(f"- {kind}: {count}")
    lines.extend(
        [
            "",
            "Каждый Telegram message посчитан отдельно; медиа с подписью считается одним сообщением. Кружки выделены отдельно.",
            f"Диапазон найденных сообщений: {result['oldest_message_local'] or 'нет'} — {result['newest_message_local'] or 'нет'}",
        ]
    )
    return "\n".join(lines) + "\n"


async def send_summary(
    account: str,
    chat_query: str,
    result: dict[str, Any],
    summary_path: Path,
) -> dict[str, Any]:
    settings = load_settings(account)
    apply_runtime_env(settings)
    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("account_not_authorized")
        chat, chat_meta = await resolve_chat(client, chat_query)
        period = result["period"]
        author = result["author"]
        since = datetime.fromisoformat(period["since"]).strftime("%d.%m.%Y %H:%M")
        until = datetime.fromisoformat(period["until"]).strftime("%d.%m.%Y %H:%M")
        message_word = russian_count_form(result["total"], "сообщение", "сообщения", "сообщений")
        text = (
            f"Вот сводка: за последние {period['hours']} часов "
            f"({since} — {until} МСК) у {author['name']} — {result['total']} {message_word}. "
            "В счёт вошли текст, медиа и кружки. Сводку прикрепил ниже."
        )
        text_message = await client.send_message(chat, text)
        file_message = await client.send_file(
            chat,
            str(summary_path),
            caption=f"Сводка по сообщениям {author['name']}",
        )
        verified = await client.get_messages(chat, ids=[text_message.id, file_message.id])
        verified_ids = [int(message.id) for message in verified if message is not None]
        return {
            "ok": True,
            "chat": chat_meta,
            "text_message_id": int(text_message.id),
            "file_message_id": int(file_message.id),
            "verified_message_ids": verified_ids,
            "file_name": summary_path.name,
        }
    finally:
        await client.disconnect()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Count a user's Telegram messages in a time window.")
    parser.add_argument("--account", default="main")
    parser.add_argument("--chat", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--hours", type=int, default=48)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = await collect(args.account, args.chat, args.user, args.hours)
    summary_text = render_summary(result)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(summary_text, encoding="utf-8")
    if args.send:
        if not args.output:
            raise RuntimeError("--send_requires_--output")
        result["send"] = await send_summary(args.account, args.chat, result, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else summary_text, end="")


if __name__ == "__main__":
    asyncio.run(main())
