from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from telethon import TelegramClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config import apply_runtime_env, load_settings


def _target_label(entity: Any, fallback: str) -> str:
    return str(getattr(entity, "id", fallback))


async def send_message(
    *,
    account: str,
    chat: str,
    text: str,
    execute: bool = False,
) -> dict[str, Any]:
    """Resolve a target and optionally send one message from an authorized session."""
    settings = load_settings(account=account)
    apply_runtime_env(settings)
    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)

    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized; refusing interactive login")

        target: Any = int(chat) if chat.lstrip("-").isdigit() else chat
        entity = await client.get_entity(target)
        result: dict[str, Any] = {
            "account": settings.account,
            "chat_id": _target_label(entity, chat),
            "execute": execute,
        }
        if not execute:
            result["status"] = "dry_run"
            return result

        sent = await client.send_message(entity, text)
        verified = await client.get_messages(entity, ids=sent.id)
        if getattr(verified, "id", None) != sent.id:
            raise RuntimeError("Telegram did not return the sent message during read-back")
        result.update({"status": "sent", "message_id": sent.id})
        return result
    finally:
        await client.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(description="Send one Telegram message from an existing userbot session")
    parser.add_argument("--account", required=True, help="Account name from accounts/<name>.env")
    parser.add_argument("--chat", required=True, help="Chat id or username")
    parser.add_argument("--text", required=True, help="Message text")
    parser.add_argument("--execute", action="store_true", help="Actually send the message (default is dry-run)")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Output JSON")
    args = parser.parse_args()

    try:
        result = asyncio.run(send_message(account=args.account, chat=args.chat, text=args.text, execute=args.execute))
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"send_message failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if args.json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result["status"] == "dry_run":
        print(f"DRY-RUN: would send one message to chat={result['chat_id']} for account={result['account']}")
    else:
        print(f"Sent message id={result['message_id']} to chat={result['chat_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
