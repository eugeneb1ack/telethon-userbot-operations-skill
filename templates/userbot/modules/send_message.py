from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from telethon import TelegramClient, errors

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config import apply_runtime_env, load_settings
from core.telegram_targets import entity_payload, resolve_entity


def register(client: TelegramClient) -> None:
    """Direct CLI helper; no event handler is attached."""


async def _send_once(
    client: TelegramClient,
    entity: Any,
    text: str,
    *,
    reply_to: int | None,
) -> Any:
    for attempt in range(2):
        try:
            return await client.send_message(entity, text, reply_to=reply_to)
        except errors.FloodWaitError as exc:
            if attempt:
                raise
            await asyncio.sleep(exc.seconds + 1)
    raise RuntimeError("unreachable")


async def send_message(
    *,
    account: str,
    chat: str,
    text: str,
    reply_to: int | None = None,
    execute: bool = False,
) -> dict[str, Any]:
    """Resolve a target and optionally send one message from an authorized session."""
    if not text.strip():
        raise ValueError("text must not be empty")
    if reply_to is not None and reply_to <= 0:
        raise ValueError("reply_to must be a positive message ID")

    settings = load_settings(account=account)
    apply_runtime_env(settings)
    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)

    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized; refusing interactive login")

        entity = await resolve_entity(client, chat)
        result: dict[str, Any] = {
            "ok": True,
            "account": settings.account,
            "dry_run": not execute,
            "target": entity_payload(entity, input_value=chat),
            "text": text,
            "reply_to": reply_to,
            "requested": {
                "text": text,
                "reply_to": reply_to,
            },
        }
        if not execute:
            if reply_to is not None:
                reply_target = await client.get_messages(entity, ids=reply_to)
                if getattr(reply_target, "id", None) != reply_to:
                    raise RuntimeError("Telegram did not return the requested reply target")
            result["status"] = "dry_run"
            return result

        sent = await _send_once(client, entity, text, reply_to=reply_to)
        verified = await client.get_messages(entity, ids=sent.id)
        if getattr(verified, "id", None) != sent.id:
            raise RuntimeError("Telegram did not return the sent message during read-back")
        if (getattr(verified, "message", None) or "") != text:
            raise RuntimeError("Telegram returned different message text during read-back")
        if reply_to is not None:
            verified_reply_id = getattr(
                getattr(verified, "reply_to", None),
                "reply_to_msg_id",
                None,
            )
            if verified_reply_id != reply_to:
                raise RuntimeError(
                    "Telegram did not preserve the requested reply target during read-back"
                )
        result.update(
            {
                "dry_run": False,
                "status": "sent",
                "message_id": int(sent.id),
                "verified": True,
            }
        )
        return result
    finally:
        await client.disconnect()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Send one Telegram message from an existing userbot session"
    )
    result.add_argument("--account", default="main")
    result.add_argument("--chat", required=True, help="Exact username/link or numeric dialog/entity ID")
    result.add_argument("--text", required=True, help="Message text")
    result.add_argument("--reply-to", type=int, help="Reply to this positive message ID")
    result.add_argument(
        "--execute",
        action="store_true",
        help="Actually send the message (default is dry-run)",
    )
    result.add_argument("--json", action="store_true", dest="json_output")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        result = asyncio.run(
            send_message(
                account=args.account,
                chat=args.chat,
                text=args.text,
                reply_to=args.reply_to,
                execute=args.execute,
            )
        )
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"send_message failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if args.json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result["status"] == "dry_run":
        print(
            "DRY-RUN: would send one message to "
            f"chat={result['target']['id']} for account={result['account']}"
        )
    else:
        print(f"Sent and verified message id={result['message_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
