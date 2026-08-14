from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

from telethon import TelegramClient
from telethon.tl.types import Channel, Chat, User

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import apply_runtime_env, load_settings
from core.telegram_targets import entity_payload, resolve_entity

MAX_LIMIT = 1_000


def register(client: TelegramClient) -> None:
    """Direct CLI helper; retained for the dynamic module loader."""


def validate_args(args: argparse.Namespace) -> None:
    if not 1 <= args.limit <= MAX_LIMIT:
        raise ValueError(f"--limit must be between 1 and {MAX_LIMIT}")
    if args.output and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", args.output):
        raise ValueError("--output must be a simple safe filename")


def safe_output_path(settings: Any, output_name: str) -> Path:
    path = Path(settings.data_dir) / "participants" / output_name
    if path.suffix != ".json":
        path = path.with_suffix(".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def display_name(user: User) -> str:
    name = " ".join(part for part in (user.first_name, user.last_name) if part).strip()
    return name or (f"@{user.username}" if user.username else f"id:{user.id}")


def participant_record(user: User) -> dict[str, Any]:
    return {
        "name": display_name(user),
        "username": f"@{user.username}" if user.username else None,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    validate_args(args)
    settings = load_settings(args.account)
    apply_runtime_env(settings)
    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized; refusing interactive login")
        chat = await resolve_entity(client, args.chat)
        if not isinstance(chat, (Channel, Chat)):
            raise ValueError(f"Target is not a group/channel: {type(chat).__name__}")

        members: list[dict[str, Any]] = []
        bots = deleted = scanned = 0
        async for participant in client.iter_participants(chat, limit=args.limit):
            scanned += 1
            if not isinstance(participant, User):
                continue
            if participant.bot and not args.include_bots:
                bots += 1
                continue
            if participant.deleted and not args.include_deleted:
                deleted += 1
                continue
            members.append(participant_record(participant))
        members.sort(key=lambda item: item["name"].casefold())
        known_total = getattr(chat, "participants_count", None)
        result = {
            "ok": True,
            "read_only": True,
            "chat": entity_payload(chat, input_value=args.chat),
            "limit": args.limit,
            "scanned_count": scanned,
            "member_count": len(members),
            "excluded_bots": bots,
            "excluded_deleted": deleted,
            "known_participant_count": known_total,
            "may_be_truncated": bool(known_total and scanned >= args.limit and known_total > scanned),
            "members": members,
        }
        if args.output:
            output_path = safe_output_path(settings, args.output)
            output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            result["output_path"] = str(output_path)
        return result
    finally:
        await client.disconnect()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="List bounded group/channel participants without writing to Telegram")
    result.add_argument("--account", default="main")
    result.add_argument("--chat", required=True)
    result.add_argument("--limit", type=int, default=500)
    result.add_argument("--include-bots", action="store_true")
    result.add_argument("--include-deleted", action="store_true")
    result.add_argument("--output", help="Optional safe .json filename under runtime/<account>/data/participants")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        result = asyncio.run(run(args))
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"list_group_members failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
