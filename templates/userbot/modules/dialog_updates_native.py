#!/usr/bin/env python3
"""Collect a freshness-checked live dialog slice without using semantic summaries."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from telethon import TelegramClient
from telethon.utils import get_peer_id

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config import apply_runtime_env, load_settings
from core.dialog_cursor_store import DialogCursorStore
from core.memory_store import memory_database_path
try:
    from modules.summarize_chat_native import (
        _collect_messages,
        _is_transcribable,
        _resolve_entity,
        _transcribe_records,
    )
except ModuleNotFoundError:
    from summarize_chat_native import (
        _collect_messages,
        _is_transcribable,
        _resolve_entity,
        _transcribe_records,
    )


def register(client: Any) -> None:
    """Direct CLI module; keep loader compatibility."""
    return None


def _peer_arg(value: str) -> str | int:
    try:
        return int(value)
    except ValueError:
        return value


def _chat_id(entity: Any) -> int:
    try:
        return int(get_peer_id(entity))
    except (TypeError, ValueError):
        value = getattr(entity, "id", None)
        if value is None:
            raise ValueError("resolved chat has no stable peer id")
        return int(value)


def _matches_content(record: dict[str, Any], content: str) -> bool:
    if content == "all":
        return True
    if content == "voice":
        return _is_transcribable(str(record.get("kind") or ""))
    return record.get("kind") == "text"


async def _latest_outgoing_id(client: TelegramClient, entity: Any, scan_limit: int) -> int:
    async for message in client.iter_messages(entity, limit=scan_limit):
        if bool(getattr(message, "out", False)) and not getattr(message, "action", None):
            return int(message.id)
    raise ValueError(f"no outgoing message was found in the latest {scan_limit} messages")


async def _collect_bounded(
    client: TelegramClient,
    entity: Any,
    *,
    sender_id: int,
    min_id: int | None,
    scan_limit: int,
) -> tuple[list[dict[str, Any]], bool]:
    records = await _collect_messages(
        client,
        entity,
        start_utc=None,
        end_utc=None,
        sender_id=sender_id,
        limit=scan_limit + 1,
        min_id=min_id,
    )
    overflow = len(records) > scan_limit
    return records[:scan_limit], overflow


async def collect_dialog_updates(
    client: TelegramClient,
    *,
    account: str,
    chat: str | int,
    sender_id: int,
    mode: str,
    content: str,
    latest_count: int | None,
    after_message_id: int | None,
    scan_limit: int,
    max_rounds: int,
    transcription_timeout: float,
    request_timeout: float,
    cursor_store: DialogCursorStore,
) -> dict[str, Any]:
    if content not in {"all", "voice", "text"}:
        raise ValueError("content must be all, voice, or text")
    if scan_limit <= 0 or scan_limit > 1000:
        raise ValueError("scan_limit must be between 1 and 1000")
    if max_rounds < 1 or max_rounds > 10:
        raise ValueError("max_rounds must be between 1 and 10")

    entity = await _resolve_entity(client, chat)
    resolved_chat_id = _chat_id(entity)
    cursor_before = cursor_store.get(
        account=account,
        chat_id=resolved_chat_id,
        sender_id=sender_id,
        content_scope=content,
    )

    anchor_id: int | None
    if mode == "latest":
        anchor_id = None
    elif mode == "after_message":
        anchor_id = after_message_id
    elif mode == "after_latest_outgoing":
        anchor_id = await _latest_outgoing_id(client, entity, scan_limit)
    elif mode == "unseen":
        if cursor_before is None:
            raise ValueError("no delivery cursor exists; initialize it with --latest")
        anchor_id = cursor_before
    else:
        raise ValueError(f"unsupported mode: {mode}")

    records, overflow = await _collect_bounded(
        client,
        entity,
        sender_id=sender_id,
        min_id=anchor_id,
        scan_limit=scan_limit,
    )
    records = [record for record in records if _matches_content(record, content)]
    if mode == "latest":
        count = latest_count or 1
        if count <= 0 or count > scan_limit:
            raise ValueError("latest count must be between 1 and scan_limit")
        records = records[-count:]

    if overflow:
        return {
            "status": "overflow",
            "complete": False,
            "mode": mode,
            "content": content,
            "anchor_message_id": anchor_id,
            "cursor_before": cursor_before,
            "cursor_advanced": False,
            "records": records,
        }

    seen_ids = {int(record["id"]) for record in records}
    for round_index in range(max_rounds):
        await _transcribe_records(
            client,
            chat,
            records,
            timeout=transcription_timeout,
            concurrency=1,
            request_timeout=request_timeout,
            retries=1,
        )
        incomplete = [
            record["id"]
            for record in records
            if _is_transcribable(str(record.get("kind") or ""))
            and not (record.get("transcription") or {}).get("complete")
        ]
        if incomplete:
            return {
                "status": "incomplete_transcription",
                "complete": False,
                "mode": mode,
                "content": content,
                "anchor_message_id": anchor_id,
                "cursor_before": cursor_before,
                "cursor_advanced": False,
                "incomplete_message_ids": incomplete,
                "records": records,
            }

        high_watermark = max(seen_ids, default=anchor_id or 0)
        newer, newer_overflow = await _collect_bounded(
            client,
            entity,
            sender_id=sender_id,
            min_id=high_watermark,
            scan_limit=scan_limit,
        )
        newer = [record for record in newer if _matches_content(record, content)]
        newer = [record for record in newer if int(record["id"]) not in seen_ids]
        if newer_overflow:
            return {
                "status": "overflow",
                "complete": False,
                "mode": mode,
                "content": content,
                "anchor_message_id": anchor_id,
                "cursor_before": cursor_before,
                "cursor_advanced": False,
                "records": records,
            }
        if not newer:
            if not records:
                cursor_after = None
                cursor_advanced = False
                if mode in {"after_message", "after_latest_outgoing"} and anchor_id:
                    cursor_after = cursor_store.advance(
                        account=account,
                        chat_id=resolved_chat_id,
                        sender_id=sender_id,
                        content_scope=content,
                        last_message_id=anchor_id,
                    )
                    cursor_advanced = True
                return {
                    "status": "empty",
                    "complete": True,
                    "mode": mode,
                    "content": content,
                    "anchor_message_id": anchor_id,
                    "cursor_before": cursor_before,
                    "cursor_after": cursor_after,
                    "cursor_advanced": cursor_advanced,
                    "records": [],
                }
            cursor_after = max(int(record["id"]) for record in records)
            cursor_store.advance(
                account=account,
                chat_id=resolved_chat_id,
                sender_id=sender_id,
                content_scope=content,
                last_message_id=cursor_after,
            )
            return {
                "status": "ok",
                "complete": True,
                "mode": mode,
                "content": content,
                "anchor_message_id": anchor_id,
                "cursor_before": cursor_before,
                "cursor_after": cursor_after,
                "cursor_advanced": True,
                "freshness_status": "current",
                "rounds": round_index + 1,
                "records": records,
            }
        records.extend(newer)
        records.sort(key=lambda item: int(item["id"]))
        seen_ids.update(int(record["id"]) for record in newer)

    return {
        "status": "moving_tail",
        "complete": False,
        "mode": mode,
        "content": content,
        "anchor_message_id": anchor_id,
        "cursor_before": cursor_before,
        "cursor_advanced": False,
        "freshness_status": "newer_messages_keep_arriving",
        "records": records,
    }


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    settings = load_settings(args.account)
    apply_runtime_env(settings)
    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized; refusing interactive login")
        mode = (
            "latest" if args.latest is not None
            else "after_message" if args.after_message_id is not None
            else "after_latest_outgoing" if args.after_latest_outgoing
            else "unseen"
        )
        path = Path(args.cursor_db) if args.cursor_db else memory_database_path(settings.data_dir)
        with DialogCursorStore(path) as store:
            return await collect_dialog_updates(
                client,
                account=args.account,
                chat=_peer_arg(args.chat),
                sender_id=args.sender_id,
                mode=mode,
                content=args.content,
                latest_count=args.latest,
                after_message_id=args.after_message_id,
                scan_limit=args.scan_limit,
                max_rounds=args.max_rounds,
                transcription_timeout=args.transcription_timeout,
                request_timeout=args.request_timeout,
                cursor_store=store,
            )
    finally:
        await client.disconnect()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect a freshness-checked live Telegram dialog slice"
    )
    parser.add_argument("--account", required=True)
    parser.add_argument("--chat", required=True)
    parser.add_argument("--sender-id", required=True, type=int)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--latest", type=int, metavar="N")
    selection.add_argument("--after-message-id", type=int)
    selection.add_argument("--after-latest-outgoing", action="store_true")
    selection.add_argument("--unseen", action="store_true")
    parser.add_argument("--content", choices=("all", "voice", "text"), default="all")
    parser.add_argument("--scan-limit", type=int, default=200)
    parser.add_argument("--max-rounds", type=int, default=3)
    parser.add_argument("--transcription-timeout", type=float, default=300)
    parser.add_argument("--request-timeout", type=float, default=30)
    parser.add_argument("--cursor-db", help="advanced/test SQLite path override")
    parser.add_argument("--json", action="store_true", help="output is always JSON")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        result = asyncio.run(_run(args))
    except Exception as exc:
        result = {"status": "error", "complete": False, "error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(result, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("complete") else 2


if __name__ == "__main__":
    sys.exit(main())
