from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List

from telethon import TelegramClient, events
from telethon.tl.types import User

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BotChat:
    id: int
    display_name: str
    username: str | None


def _display_name(user: User) -> str:
    full_name = " ".join(part for part in [user.first_name, user.last_name] if part).strip()
    if full_name:
        return full_name
    if user.username:
        return f"@{user.username}"
    return str(user.id)


async def list_bot_chats(client: TelegramClient) -> List[BotChat]:
    """Return dialogs with bot users only (no humans, groups, or channels)."""
    bots: List[BotChat] = []

    async for dialog in client.iter_dialogs():
        # Keep only direct user dialogs.
        if not dialog.is_user:
            continue

        entity = dialog.entity
        if not isinstance(entity, User):
            continue

        # Keep only bot users and exclude self dialog defensively.
        if not entity.bot or entity.is_self:
            continue

        bots.append(
            BotChat(
                id=entity.id,
                display_name=_display_name(entity),
                username=entity.username,
            )
        )

    bots.sort(key=lambda item: item.display_name.lower())
    return bots


def _format_bot_chats(bots: List[BotChat]) -> str:
    if not bots:
        return "Чатов с ботами не найдено."

    bots_sorted = sorted(bots, key=lambda item: item.display_name.lower())
    with_username = [bot for bot in bots_sorted if bot.username]
    without_username = [bot for bot in bots_sorted if not bot.username]

    lines = [
        f"**Боты ({len(bots_sorted)})**",
        "",
        f"С username: **{len(with_username)}**",
        f"Без username: **{len(without_username)}**",
        "",
    ]

    for idx, bot in enumerate(bots_sorted, start=1):
        username_part = f" — @{bot.username}" if bot.username else ""
        lines.append(f"- {idx}. **{bot.display_name}**{username_part} · `id: {bot.id}`")

    return "\n".join(lines).strip()


def _format_bot_chats_json(bots: List[BotChat]) -> str:
    bots_sorted = sorted(bots, key=lambda item: item.display_name.lower())
    payload = [
        {
            "id": bot.id,
            "name": bot.display_name,
            "username": bot.username,
        }
        for bot in bots_sorted
    ]
    return json.dumps(payload, ensure_ascii=False)


def register(client: TelegramClient) -> None:
    @client.on(events.NewMessage(outgoing=True, pattern=r"^\.bots$"))
    async def bots_handler(event: events.NewMessage.Event) -> None:
        try:
            bots = await list_bot_chats(client)
            await event.reply(_format_bot_chats(bots))
        except Exception:
            logger.exception("Ошибка при получении списка чатов с ботами")
            await event.reply("Не удалось получить список чатов с ботами. Проверьте логи.")


async def _verification_run(*, json_output: bool = False, account: str | None = None) -> None:
    """Manual verification helper: run this file directly to print bot chats."""
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from core.config import load_settings

    settings = load_settings(account=account)
    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)

    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized; refusing interactive login")
        bots = await list_bot_chats(client)
        if json_output:
            print(_format_bot_chats_json(bots))
        else:
            print(_format_bot_chats(bots))
    finally:
        await client.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="List bot chats from Telegram account")
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output bot chats as JSON for programmatic parsing",
    )
    parser.add_argument("--account", help="Имя аккаунта из accounts/<name>.env")
    args = parser.parse_args()

    try:
        asyncio.run(_verification_run(json_output=args.json_output, account=args.account))
    except KeyboardInterrupt:
        print("Остановлено пользователем.")
    except Exception as exc:
        logger.exception("Verification run failed")
        raise SystemExit(f"Ошибка во время проверки модуля: {exc}")
