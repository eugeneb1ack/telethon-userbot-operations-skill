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
from telethon.errors import RPCError
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.types import Channel

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChannelChat:
    id: int
    title: str
    participants_count: int | None
    username: str | None


async def _get_participants_count(client: TelegramClient, entity: Channel) -> int | None:
    """Best-effort participants count for broadcast channels."""
    participants_count = getattr(entity, "participants_count", None)
    if participants_count is not None:
        return participants_count

    try:
        full = await client(GetFullChannelRequest(entity))
        return getattr(full.full_chat, "participants_count", None)
    except RPCError:
        logger.debug("Не удалось получить participants_count для канала id=%s", entity.id)
    except Exception:
        logger.exception("Неожиданная ошибка при получении participants_count для id=%s", entity.id)

    return None


async def list_channels(client: TelegramClient) -> List[ChannelChat]:
    """Return broadcast channels only (exclude megagroups and other dialogs)."""
    channels: List[ChannelChat] = []

    async for dialog in client.iter_dialogs():
        if not dialog.is_channel:
            continue

        entity = dialog.entity
        if not isinstance(entity, Channel):
            continue

        # Exclude supergroups/megagroups (covered by group_chats.py).
        if getattr(entity, "megagroup", False):
            continue

        # Keep only broadcast channels.
        if not getattr(entity, "broadcast", False):
            continue

        channels.append(
            ChannelChat(
                id=entity.id,
                title=entity.title or str(entity.id),
                participants_count=await _get_participants_count(client, entity),
                username=entity.username,
            )
        )

    channels.sort(key=lambda item: item.title.lower())
    return channels


def _format_channels(channels: List[ChannelChat]) -> str:
    if not channels:
        return "Каналы не найдены."

    channels_sorted = sorted(channels, key=lambda item: item.title.lower())
    public_channels = [channel for channel in channels_sorted if channel.username]
    private_channels = [channel for channel in channels_sorted if not channel.username]

    lines = [
        f"**Каналы ({len(channels_sorted)})**",
        "",
        f"Публичные (с username): **{len(public_channels)}**",
        f"Приватные (без username): **{len(private_channels)}**",
        "",
    ]

    for idx, channel in enumerate(channels_sorted, start=1):
        participants = (
            str(channel.participants_count)
            if channel.participants_count is not None
            else "неизвестно"
        )
        username_part = f" · @{channel.username}" if channel.username else ""
        lines.append(
            f"- {idx}. **{channel.title}**{username_part} · подписчиков: {participants} · `id: {channel.id}`"
        )

    return "\n".join(lines).strip()


def _format_channels_json(channels: List[ChannelChat]) -> str:
    channels_sorted = sorted(channels, key=lambda item: item.title.lower())
    payload = [
        {
            "id": channel.id,
            "title": channel.title,
            "participants_count": channel.participants_count,
            "username": channel.username,
        }
        for channel in channels_sorted
    ]
    return json.dumps(payload, ensure_ascii=False)


def register(client: TelegramClient) -> None:
    @client.on(events.NewMessage(outgoing=True, pattern=r"^\.channels$"))
    async def channels_handler(event: events.NewMessage.Event) -> None:
        try:
            channels = await list_channels(client)
            await event.reply(_format_channels(channels))
        except Exception:
            logger.exception("Ошибка при получении списка каналов")
            await event.reply("Не удалось получить список каналов. Проверьте логи.")


async def _verification_run(*, json_output: bool = False, account: str | None = None) -> None:
    """Manual verification helper: run this file directly to print channels."""
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
        channels = await list_channels(client)
        if json_output:
            print(_format_channels_json(channels))
        else:
            print(_format_channels(channels))
    finally:
        await client.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="List channels from Telegram account")
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output channels as JSON for programmatic parsing",
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
