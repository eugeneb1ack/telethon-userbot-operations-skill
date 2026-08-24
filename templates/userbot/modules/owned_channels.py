from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from telethon import TelegramClient
from telethon.tl.types import Channel

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import apply_runtime_env, load_settings


@dataclass(frozen=True)
class OwnedChannel:
    title: str
    username: str | None


def register(client: TelegramClient) -> None:
    """Direct CLI helper; no event handler is attached."""


def is_owned_broadcast_channel(dialog: Any) -> bool:
    """Return whether Telegram marks this broadcast-channel dialog as self-created."""
    entity = getattr(dialog, "entity", None)
    return bool(
        getattr(dialog, "is_channel", False)
        and isinstance(entity, Channel)
        and getattr(entity, "broadcast", False)
        and not getattr(entity, "megagroup", False)
        and getattr(entity, "creator", False)
    )


async def collect_owned_channels(client: TelegramClient) -> list[OwnedChannel]:
    channels: list[OwnedChannel] = []
    async for dialog in client.iter_dialogs():
        if not is_owned_broadcast_channel(dialog):
            continue
        entity = dialog.entity
        channels.append(
            OwnedChannel(
                title=entity.title or str(entity.id),
                username=entity.username,
            )
        )
    return sorted(channels, key=lambda item: item.title.casefold())


def result_payload(channels: list[OwnedChannel]) -> dict[str, Any]:
    return {
        "ok": True,
        "read_only": True,
        "matching_rule": "Telegram Channel.creator is true; broadcast channels only",
        "owned_channel_count": len(channels),
        "channels": [
            {"title": channel.title, "username": f"@{channel.username}" if channel.username else None}
            for channel in channels
        ],
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    settings = load_settings(args.account)
    apply_runtime_env(settings)
    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized; refusing interactive login")
        return result_payload(await collect_owned_channels(client))
    finally:
        await client.disconnect()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="List broadcast channels owned by the current Telegram account"
    )
    result.add_argument("--account", default="main")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        result = asyncio.run(run(args))
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"owned_channels failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
