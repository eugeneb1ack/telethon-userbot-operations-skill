from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from telethon import TelegramClient, errors

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import apply_runtime_env, load_settings
from core.telegram_targets import entity_payload, parse_message_ids, resolve_entity


def register(client: TelegramClient) -> None:
    """Direct CLI helper; retained for the dynamic module loader."""


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else [value]


def source_message_payload(message: Any) -> dict[str, Any]:
    text = getattr(message, "message", None) or ""
    return {
        "id": getattr(message, "id", None),
        "sender_id": getattr(message, "sender_id", None),
        "has_media": bool(getattr(message, "media", None)),
        "text_preview": text.replace("\n", " ")[:120],
    }


async def _forward_with_one_flood_retry(
    client: TelegramClient,
    destination: Any,
    message_ids: list[int],
    source: Any,
    *,
    silent: bool,
    drop_author: bool,
    drop_media_captions: bool,
) -> list[Any]:
    for attempt in range(2):
        try:
            return _as_list(
                await client.forward_messages(
                    destination,
                    message_ids,
                    from_peer=source,
                    silent=silent,
                    drop_author=drop_author,
                    drop_media_captions=drop_media_captions,
                )
            )
        except errors.FloodWaitError as exc:
            if attempt:
                raise
            await asyncio.sleep(exc.seconds + 1)
    raise RuntimeError("unreachable")


async def run(args: argparse.Namespace) -> dict[str, Any]:
    message_ids = parse_message_ids(args.message_ids)
    if args.drop_media_captions and not args.drop_author:
        raise ValueError("--drop-media-captions requires --drop-author")

    settings = load_settings(args.account)
    apply_runtime_env(settings)
    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized; refusing interactive login")
        source = await resolve_entity(client, args.source_chat)
        destination = await resolve_entity(client, args.destination_chat)
        source_messages = _as_list(await client.get_messages(source, ids=message_ids))
        by_id = {getattr(message, "id", None): message for message in source_messages if message is not None}
        missing_ids = [message_id for message_id in message_ids if message_id not in by_id]
        if missing_ids:
            raise ValueError(f"Source messages were not found: {missing_ids}")

        plan = {
            "ok": True,
            "dry_run": not args.execute,
            "source": entity_payload(source, input_value=args.source_chat),
            "destination": entity_payload(destination, input_value=args.destination_chat),
            "message_ids": message_ids,
            "messages": [source_message_payload(by_id[message_id]) for message_id in message_ids],
            "options": {
                "silent": args.silent,
                "drop_author": args.drop_author,
                "drop_media_captions": args.drop_media_captions,
            },
        }
        if not args.execute:
            return plan

        forwarded = await _forward_with_one_flood_retry(
            client,
            destination,
            message_ids,
            source,
            silent=args.silent,
            drop_author=args.drop_author,
            drop_media_captions=args.drop_media_captions,
        )
        forwarded_ids = [getattr(message, "id", None) for message in forwarded]
        if len(forwarded_ids) != len(message_ids) or any(message_id is None for message_id in forwarded_ids):
            raise RuntimeError("Telegram returned an incomplete forwarded-message result")
        read_back = _as_list(await client.get_messages(destination, ids=forwarded_ids))
        verified_ids = {getattr(message, "id", None) for message in read_back if message is not None}
        plan.update(
            {
                "dry_run": False,
                "forwarded_message_ids": forwarded_ids,
                "verified_message_ids": [message_id for message_id in forwarded_ids if message_id in verified_ids],
                "verified": set(forwarded_ids) == verified_ids,
            }
        )
        return plan
    finally:
        await client.disconnect()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Forward exact frozen Telegram message IDs with dry-run and destination read-back"
    )
    result.add_argument("--account", default="main")
    result.add_argument("--source-chat", required=True)
    result.add_argument("--destination-chat", required=True)
    result.add_argument("--message-ids", required=True, help="Comma-separated positive message IDs")
    result.add_argument("--silent", action="store_true")
    result.add_argument("--drop-author", action="store_true", help="Forward as copies where Telegram permits")
    result.add_argument("--drop-media-captions", action="store_true")
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
        print(f"forward_messages failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
