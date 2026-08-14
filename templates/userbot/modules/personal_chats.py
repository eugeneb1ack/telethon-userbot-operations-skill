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
class PersonalChat:
    user_id: int
    display_name: str
    username: str | None


def _display_name(user: User) -> str:
    full_name = " ".join(part for part in [user.first_name, user.last_name] if part).strip()
    if full_name:
        return full_name
    if user.username:
        return f"@{user.username}"
    return str(user.id)


async def list_personal_chats(client: TelegramClient) -> List[PersonalChat]:
    """Return direct user dialogs only (no bots, groups, or channels)."""
    personal_chats: List[PersonalChat] = []

    async for dialog in client.iter_dialogs():
        # Keep only direct user dialogs.
        if not dialog.is_user:
            continue

        entity = dialog.entity
        if not isinstance(entity, User):
            continue

        # Exclude bot accounts and self dialog.
        if entity.bot or entity.is_self:
            continue

        personal_chats.append(
            PersonalChat(
                user_id=entity.id,
                display_name=_display_name(entity),
                username=entity.username,
            )
        )

    personal_chats.sort(key=lambda item: item.display_name.lower())
    return personal_chats


def _format_personal_chats(chats: List[PersonalChat]) -> str:
    if not chats:
        return "Личных чатов не найдено."

    # Defensive sort to keep output alphabetical regardless of input source.
    chats_sorted = sorted(chats, key=lambda item: item.display_name.lower())

    with_username = [chat for chat in chats_sorted if chat.username]
    without_username = [chat for chat in chats_sorted if not chat.username]

    lines = [
        f"**Личные чаты ({len(chats_sorted)})**",
        "",
        f"С username: **{len(with_username)}**",
        f"Без username: **{len(without_username)}**",
        "",
    ]

    if with_username:
        lines.append("**Пользователи с username**")
        for idx, chat in enumerate(with_username, start=1):
            lines.append(
                f"- {idx}. **{chat.display_name}** — @{chat.username} · `id: {chat.user_id}`"
            )
        lines.append("")

    if without_username:
        lines.append("**Пользователи без username**")
        for idx, chat in enumerate(without_username, start=1):
            lines.append(f"- {idx}. **{chat.display_name}** · `id: {chat.user_id}`")

    return "\n".join(lines).strip()


def _format_personal_chats_json(chats: List[PersonalChat]) -> str:
    chats_sorted = sorted(chats, key=lambda item: item.display_name.lower())
    payload = [
        {
            "user_id": chat.user_id,
            "display_name": chat.display_name,
            "username": chat.username,
        }
        for chat in chats_sorted
    ]
    return json.dumps(payload, ensure_ascii=False)


def register(client: TelegramClient) -> None:
    @client.on(events.NewMessage(outgoing=True, pattern=r"^\.dms$"))
    async def dms_handler(event: events.NewMessage.Event) -> None:
        try:
            chats = await list_personal_chats(client)
            await event.reply(_format_personal_chats(chats))
        except Exception:
            logger.exception("Ошибка при получении списка личных чатов")
            await event.reply("Не удалось получить список личных чатов. Проверьте логи.")


async def _verification_run(*, account: str | None = None, json_output: bool = False) -> None:
    """Manual verification helper: run this file directly to print direct chats."""
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

        chats = await list_personal_chats(client)
        if json_output:
            print(_format_personal_chats_json(chats))
        else:
            print(_format_personal_chats(chats))
    finally:
        await client.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="List personal chats from Telegram account")
    parser.add_argument(
        "--account",
        help="Имя аккаунта из accounts/<name>.env (например: main, second)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output chats as JSON for programmatic parsing",
    )
    args = parser.parse_args()

    try:
        asyncio.run(_verification_run(account=args.account, json_output=args.json_output))
    except KeyboardInterrupt:
        print("Остановлено пользователем.")
    except Exception as exc:
        logger.exception("Verification run failed")
        raise SystemExit(f"Ошибка во время проверки модуля: {exc}")
