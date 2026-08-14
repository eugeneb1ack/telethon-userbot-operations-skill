from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from telethon import TelegramClient, errors

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import apply_runtime_env, load_settings
from core.telegram_targets import entity_payload, resolve_entity


def register(client: TelegramClient) -> None:
    """Direct CLI helper; retained for the dynamic module loader."""


def message_payload(message: Any) -> dict[str, Any]:
    text = getattr(message, "message", None) or ""
    return {
        "id": getattr(message, "id", None),
        "pinned": bool(getattr(message, "pinned", False)),
        "has_media": bool(getattr(message, "media", None)),
        "text_preview": text.replace("\n", " ")[:160],
    }


def validate_args(args: argparse.Namespace) -> None:
    if args.action == "inspect" and args.execute:
        raise ValueError("inspect is read-only; remove --execute")
    if args.action != "pin" and (args.notify or args.pm_oneside):
        raise ValueError("--notify and --pm-oneside are valid only with --action pin")


async def _call_with_one_flood_retry(operation):
    for attempt in range(2):
        try:
            return await operation()
        except errors.FloodWaitError as exc:
            if attempt:
                raise
            await asyncio.sleep(exc.seconds + 1)
    raise RuntimeError("unreachable")


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
        message = await client.get_messages(chat, ids=args.message_id)
        if message is None or getattr(message, "id", None) != args.message_id:
            raise ValueError(f"Message {args.message_id} was not found in the resolved chat")
        plan = {
            "ok": True,
            "dry_run": not args.execute,
            "action": args.action,
            "chat": entity_payload(chat, input_value=args.chat),
            "message": message_payload(message),
            "options": {"notify": args.notify, "pm_oneside": args.pm_oneside},
        }
        if args.action == "inspect" or not args.execute:
            return plan

        if args.action == "pin":
            await _call_with_one_flood_retry(
                lambda: client.pin_message(chat, args.message_id, notify=args.notify, pm_oneside=args.pm_oneside)
            )
        elif args.action == "unpin":
            await _call_with_one_flood_retry(lambda: client.unpin_message(chat, args.message_id, notify=False))
        else:
            raise RuntimeError(f"Unsupported action: {args.action}")

        after = await client.get_messages(chat, ids=args.message_id)
        expected_pinned = args.action == "pin"
        verified = getattr(after, "id", None) == args.message_id and bool(getattr(after, "pinned", False)) == expected_pinned
        plan.update(
            {
                "dry_run": False,
                "after": message_payload(after),
                "verified": verified,
            }
        )
        return plan
    finally:
        await client.disconnect()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Inspect, pin, or unpin one exact Telegram message")
    result.add_argument("--account", default="main")
    result.add_argument("--chat", required=True)
    result.add_argument("--message-id", required=True, type=int)
    result.add_argument("--action", choices=("inspect", "pin", "unpin"), default="inspect")
    result.add_argument("--notify", action="store_true", help="Notify chat members when pinning")
    result.add_argument("--pm-oneside", action="store_true", help="Pin only for this account in a private chat")
    result.add_argument("--execute", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        result = asyncio.run(run(args))
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"pin_message failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
