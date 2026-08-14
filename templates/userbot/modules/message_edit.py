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
        "outgoing": bool(getattr(message, "out", False)),
        "has_media": bool(getattr(message, "media", None)),
        "text_preview": text.replace("\n", " ")[:160],
        "text_length": len(text),
    }


def parse_mode(value: str) -> str | None:
    return None if value == "plain" else value


async def _edit_with_one_flood_retry(
    client: TelegramClient,
    entity: Any,
    message_id: int,
    text: str,
    *,
    parse_mode_name: str | None,
    link_preview: bool,
) -> Any:
    for attempt in range(2):
        try:
            return await client.edit_message(
                entity,
                message_id,
                text,
                parse_mode=parse_mode_name,
                link_preview=link_preview,
            )
        except errors.FloodWaitError as exc:
            if attempt:
                raise
            await asyncio.sleep(exc.seconds + 1)
    raise RuntimeError("unreachable")


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if not args.text:
        raise ValueError("--text cannot be empty")
    settings = load_settings(args.account)
    apply_runtime_env(settings)
    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized; refusing interactive login")
        entity = await resolve_entity(client, args.chat)
        before = await client.get_messages(entity, ids=args.message_id)
        if before is None or getattr(before, "id", None) != args.message_id:
            raise ValueError(f"Message {args.message_id} was not found in the resolved chat")
        if not getattr(before, "out", False):
            raise PermissionError("Refusing to edit a message that is not outgoing from this account")

        plan = {
            "ok": True,
            "dry_run": not args.execute,
            "chat": entity_payload(entity, input_value=args.chat),
            "before": message_payload(before),
            "requested": {
                "text_preview": args.text.replace("\n", " ")[:160],
                "text_length": len(args.text),
                "parse_mode": args.parse_mode,
                "link_preview": not args.no_link_preview,
            },
        }
        if not args.execute:
            return plan

        try:
            edited = await _edit_with_one_flood_retry(
                client,
                entity,
                args.message_id,
                args.text,
                parse_mode_name=parse_mode(args.parse_mode),
                link_preview=not args.no_link_preview,
            )
            status = "edited"
        except errors.MessageNotModifiedError:
            edited = before
            status = "noop_already_matches"

        after = await client.get_messages(entity, ids=args.message_id)
        verified = (
            getattr(after, "id", None) == args.message_id
            and getattr(after, "message", None) == args.text
        )
        plan.update(
            {
                "dry_run": False,
                "status": status,
                "returned_message_id": getattr(edited, "id", None),
                "after": message_payload(after),
                "verified": verified,
            }
        )
        return plan
    finally:
        await client.disconnect()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Edit one outgoing Telegram message with dry-run and read-back verification"
    )
    result.add_argument("--account", default="main")
    result.add_argument("--chat", required=True, help="Exact username/link or numeric dialog/entity ID")
    result.add_argument("--message-id", required=True, type=int)
    result.add_argument("--text", required=True)
    result.add_argument(
        "--parse-mode",
        choices=("plain", "html", "markdown"),
        default="plain",
        help="Use html for <tg-emoji emoji-id=\"...\">visible text</tg-emoji>",
    )
    result.add_argument("--no-link-preview", action="store_true")
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
        print(f"message_edit failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
