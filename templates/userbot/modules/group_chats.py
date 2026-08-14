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
from telethon.tl.types import Channel, Chat

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GroupChat:
    id: int
    title: str
    participants_count: int | None
    username: str | None


async def list_group_chats(client: TelegramClient) -> List[GroupChat]:
    """Return group dialogs only (classic groups and megagroups; no DMs/channels/bots)."""
    groups: List[GroupChat] = []

    async for dialog in client.iter_dialogs():
        if not dialog.is_group:
            continue

        entity = dialog.entity

        # Classic groups.
        if isinstance(entity, Chat):
            groups.append(
                GroupChat(
                    id=entity.id,
                    title=entity.title or str(entity.id),
                    participants_count=getattr(entity, "participants_count", None),
                    username=None,
                )
            )
            continue

        # Supergroups (megagroups) are Channel entities with megagroup=True.
        if isinstance(entity, Channel) and getattr(entity, "megagroup", False):
            groups.append(
                GroupChat(
                    id=entity.id,
                    title=entity.title or str(entity.id),
                    participants_count=getattr(entity, "participants_count", None),
                    username=entity.username,
                )
            )

    groups.sort(key=lambda item: item.title.lower())
    return groups


def _format_group_chats(groups: List[GroupChat]) -> str:
    if not groups:
        return "Групповых чатов не найдено."

    groups_sorted = sorted(groups, key=lambda item: item.title.lower())
    public_groups = [group for group in groups_sorted if group.username]
    private_groups = [group for group in groups_sorted if not group.username]

    lines = [
        f"**Групповые чаты ({len(groups_sorted)})**",
        "",
        f"Публичные (с username): **{len(public_groups)}**",
        f"Приватные (без username): **{len(private_groups)}**",
        "",
    ]

    for idx, group in enumerate(groups_sorted, start=1):
        participants = (
            str(group.participants_count)
            if group.participants_count is not None
            else "неизвестно"
        )
        username_part = f" · @{group.username}" if group.username else ""
        lines.append(
            f"- {idx}. **{group.title}**{username_part} · участников: {participants} · `id: {group.id}`"
        )

    return "\n".join(lines).strip()


def _format_group_chats_json(groups: List[GroupChat]) -> str:
    groups_sorted = sorted(groups, key=lambda item: item.title.lower())
    payload = [
        {
            "id": group.id,
            "title": group.title,
            "participants_count": group.participants_count,
            "username": group.username,
        }
        for group in groups_sorted
    ]
    return json.dumps(payload, ensure_ascii=False)


def register(client: TelegramClient) -> None:
    @client.on(events.NewMessage(outgoing=True, pattern=r"^\.groups$"))
    async def groups_handler(event: events.NewMessage.Event) -> None:
        try:
            groups = await list_group_chats(client)
            await event.reply(_format_group_chats(groups))
        except Exception:
            logger.exception("Ошибка при получении списка групповых чатов")
            await event.reply("Не удалось получить список групповых чатов. Проверьте логи.")


async def _verification_run(*, json_output: bool = False, account: str | None = None) -> None:
    """Manual verification helper: run this file directly to print group chats."""
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
        groups = await list_group_chats(client)
        if json_output:
            print(_format_group_chats_json(groups))
        else:
            print(_format_group_chats(groups))
    finally:
        await client.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="List group chats from Telegram account")
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output chats as JSON for programmatic parsing",
    )
    parser.add_argument(
        "--account",
        help="Имя аккаунта из accounts/<name>.env",
    )
    args = parser.parse_args()

    try:
        asyncio.run(_verification_run(json_output=args.json_output, account=args.account))
    except KeyboardInterrupt:
        print("Остановлено пользователем.")
    except Exception as exc:
        logger.exception("Verification run failed")
        raise SystemExit(f"Ошибка во время проверки модуля: {exc}")
