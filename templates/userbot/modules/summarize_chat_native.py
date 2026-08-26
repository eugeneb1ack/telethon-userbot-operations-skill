#!/usr/bin/env python3
"""Date-bounded, sender-aware Telegram chat summarizer.

The collector keeps provenance small and explicit:
- author id/name/username and outgoing direction;
- local timestamp;
- text or native Telegram transcription;
- reply target id and resolved target author.

Voice, audio and video notes use Telegram's native messages.TranscribeAudioRequest.
No media download is needed for the native path.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import OrderedDict
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.custom.message import Message
from telethon.tl.types import DocumentAttributeAudio
from telethon.utils import get_peer_id

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config import apply_runtime_env, load_settings
from core.memory_store import memory_database_path
from core.summary_store import MAX_TAIL_MARKERS, SummaryStore, scope_key
try:
    from modules.transcribe_audio_native import Result, transcribe_message
except ModuleNotFoundError:
    from transcribe_audio_native import Result, transcribe_message

LOCAL_TZ = ZoneInfo("Europe/Moscow")
MAX_LAST_MESSAGES = 1000
TAIL_VALIDATION_MARKERS = MAX_TAIL_MARKERS


def register(client: Any) -> None:
    """Direct CLI module; keep compatibility with the userbot module loader."""
    return None


def _safe_name(sender: Any, sender_id: int | None) -> str:
    if sender is None:
        return f"id:{sender_id}" if sender_id is not None else "Unknown"
    full = " ".join(
        part for part in (getattr(sender, "first_name", None), getattr(sender, "last_name", None)) if part
    ).strip()
    if full:
        return full
    title = getattr(sender, "title", None)
    if title:
        return title
    username = getattr(sender, "username", None)
    if username:
        return f"@{username}"
    return f"id:{sender_id}" if sender_id is not None else "Unknown"


def _kind(message: Message) -> str:
    if getattr(message, "voice", False):
        return "voice"
    if getattr(message, "video_note", False):
        return "video_note"
    if getattr(message, "audio", False):
        return "audio"
    if getattr(message, "photo", False):
        return "photo"
    if getattr(message, "video", False):
        return "video"
    if getattr(message, "document", None):
        return "document"
    return "text"


def _is_transcribable(kind: str) -> bool:
    return kind in {"voice", "audio", "video_note"}


def _duration(message: Message) -> int | None:
    value = getattr(getattr(message, "file", None), "duration", None)
    if value is None:
        return None
    try:
        return round(float(value))
    except (TypeError, ValueError):
        return None


def _local_time(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _clean_text(value: str | None, limit: int = 1000) -> str:
    text = re.sub(r"\s+", " ", (value or "")).strip()
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _peer_arg(value: str) -> str | int:
    try:
        return int(value)
    except ValueError:
        return value


async def _resolve_entity(client: TelegramClient, chat: str | int) -> Any:
    try:
        return await client.get_input_entity(chat)
    except (TypeError, ValueError):
        if not isinstance(chat, int):
            raise
        async for dialog in client.iter_dialogs():
            if getattr(dialog.entity, "id", None) == chat:
                return dialog.entity
        raise ValueError(f"chat {chat} was not found in the authorized account dialogs")


async def _server_sender_filter(
    client: TelegramClient, sender_id: int | None, topic_id: int | None
) -> Any | None:
    if sender_id is None or topic_id is not None:
        return None
    try:
        return await client.get_input_entity(sender_id)
    except (TypeError, ValueError):
        # The numeric sender may not yet have an access hash in the local
        # entity cache. Fall back to the bounded client-side assertion.
        return None


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start_local = datetime.combine(day, time.min, tzinfo=LOCAL_TZ)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


async def _collect_messages(
    client: TelegramClient,
    entity: Any,
    *,
    start_utc: datetime | None,
    end_utc: datetime | None,
    sender_id: int | None,
    topic_id: int | None = None,
    limit: int | None = None,
    min_id: int | None = None,
) -> list[dict[str, Any]]:
    raw: list[Message] = []
    iterator_options: dict[str, Any] = {"limit": limit, "reply_to": topic_id}
    # Telethon maps from_user to Telegram's server-side messages.Search filter.
    # Forum reply collection does not support that filter, so topic scans keep
    # the defensive client-side sender check below.
    sender_filter = await _server_sender_filter(client, sender_id, topic_id)
    if sender_filter is not None:
        iterator_options["from_user"] = sender_filter
    if end_utc is not None:
        iterator_options["offset_date"] = end_utc
    if min_id is not None:
        iterator_options["min_id"] = min_id
    async for message in client.iter_messages(entity, **iterator_options):
        if not message.date:
            continue
        if start_utc is not None and message.date < start_utc:
            break
        if end_utc is not None and message.date >= end_utc:
            continue
        if getattr(message, "action", None):
            continue
        actual_sender_id = getattr(message, "sender_id", None)
        if sender_id is not None and actual_sender_id != sender_id:
            continue
        raw.append(message)

    raw.sort(key=lambda item: item.id)
    sender_cache: dict[int, Any] = {}
    author_aliases: OrderedDict[int, str] = OrderedDict()
    records: list[dict[str, Any]] = []

    for message in raw:
        actual_sender_id = getattr(message, "sender_id", None)
        sender = sender_cache.get(actual_sender_id) if actual_sender_id is not None else None
        if sender is None:
            sender = await message.get_sender()
            if actual_sender_id is not None:
                sender_cache[actual_sender_id] = sender
        if actual_sender_id is None:
            actual_sender_id = getattr(sender, "id", None)

        if actual_sender_id not in author_aliases:
            author_aliases[actual_sender_id] = f"A{len(author_aliases) + 1}"
        alias = author_aliases[actual_sender_id]
        kind = _kind(message)
        reply_to = getattr(message, "reply_to_msg_id", None)
        record = {
            "id": message.id,
            "time": _local_time(message.date),
            "author": {
                "alias": alias,
                "id": actual_sender_id,
                "name": _safe_name(sender, actual_sender_id),
                "username": getattr(sender, "username", None),
                "outgoing": bool(getattr(message, "out", False)),
            },
            "reply_to": {"id": reply_to} if reply_to else None,
            "kind": kind,
            "duration_seconds": _duration(message) if _is_transcribable(kind) else None,
            "text": _clean_text(message.message),
            "transcription": None,
        }
        records.append(record)

    by_id = {record["id"]: record for record in records}
    missing_reply_ids = {
        record["reply_to"]["id"]
        for record in records
        if record["reply_to"] and record["reply_to"]["id"] not in by_id
    }
    if missing_reply_ids:
        # Resolve only author metadata for outside-window reply targets.
        reply_records: dict[int, list[dict[str, Any]]] = {}
        for record in records:
            if record["reply_to"] and record["reply_to"]["id"] in missing_reply_ids:
                reply_records.setdefault(record["reply_to"]["id"], []).append(record)
        ordered_reply_ids = sorted(missing_reply_ids)
        for offset in range(0, len(ordered_reply_ids), 100):
            ids = ordered_reply_ids[offset : offset + 100]
            for reply in await client.get_messages(entity, ids=ids):
                if not reply:
                    continue
                reply_sender = await reply.get_sender()
                reply_sender_id = getattr(reply, "sender_id", None) or getattr(reply_sender, "id", None)
                if reply_sender_id not in author_aliases:
                    author_aliases[reply_sender_id] = f"A{len(author_aliases) + 1}"
                author = {
                    "alias": author_aliases[reply_sender_id],
                    "id": reply_sender_id,
                    "name": _safe_name(reply_sender, reply_sender_id),
                    "username": getattr(reply_sender, "username", None),
                }
                for record in reply_records.get(reply.id, []):
                    record["reply_to"]["author"] = author

    for record in records:
        target = by_id.get(record["reply_to"]["id"]) if record["reply_to"] else None
        if target:
            record["reply_to"]["author"] = {
                "alias": target["author"]["alias"],
                "id": target["author"]["id"],
                "name": target["author"]["name"],
                "username": target["author"]["username"],
            }

    return records


def _utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _message_marker(message: Message) -> dict[str, Any]:
    document = getattr(message, "document", None)
    photo = getattr(message, "photo", None)
    marker_source = {
        "id": int(message.id),
        "sender_id": getattr(message, "sender_id", None),
        "date": _utc_iso(getattr(message, "date", None)),
        "edit_date": _utc_iso(getattr(message, "edit_date", None)),
        "outgoing": bool(getattr(message, "out", False)),
        "reply_to": getattr(message, "reply_to_msg_id", None),
        "kind": _kind(message),
        "text": message.message or "",
        "document_id": getattr(document, "id", None),
        "photo_id": getattr(photo, "id", None),
    }
    serialized = json.dumps(
        marker_source, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        "id": int(message.id),
        "fingerprint": hashlib.sha256(serialized).hexdigest()[:24],
    }


async def _collect_recent_markers(
    client: TelegramClient,
    entity: Any,
    *,
    start_utc: datetime | None,
    end_utc: datetime | None,
    sender_id: int | None,
    topic_id: int | None,
    limit: int = TAIL_VALIDATION_MARKERS,
) -> list[dict[str, Any]]:
    markers: list[dict[str, Any]] = []
    iterator_options: dict[str, Any] = {"limit": None, "reply_to": topic_id}
    sender_filter = await _server_sender_filter(client, sender_id, topic_id)
    if sender_filter is not None:
        iterator_options["from_user"] = sender_filter
    if end_utc is not None:
        iterator_options["offset_date"] = end_utc
    async for message in client.iter_messages(entity, **iterator_options):
        message_date = getattr(message, "date", None)
        if message_date is None:
            continue
        if start_utc is not None and message_date < start_utc:
            break
        if end_utc is not None and message_date >= end_utc:
            continue
        if getattr(message, "action", None):
            continue
        if sender_id is not None and getattr(message, "sender_id", None) != sender_id:
            continue
        markers.append(_message_marker(message))
        if len(markers) >= limit:
            break
    markers.sort(key=lambda item: item["id"])
    return markers


async def _transcribe_records(
    client: TelegramClient,
    chat: str | int,
    records: list[dict[str, Any]],
    *,
    timeout: float,
    concurrency: int,
    request_timeout: float,
    retries: int,
    progress_path: Path | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    targets = [
        record
        for record in records
        if _is_transcribable(record["kind"])
        and not (record.get("transcription") or {}).get("complete")
    ]
    if not targets:
        return

    if timeout <= 0:
        raise ValueError("transcription timeout must be positive")
    if request_timeout <= 0:
        raise ValueError("transcription request timeout must be positive")
    if retries < 0:
        raise ValueError("transcription retries must be >= 0")

    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    for record in targets:
        queue.put_nowait(record)
    worker_count = min(max(1, concurrency), len(targets))
    for _ in range(worker_count):
        queue.put_nowait(None)

    completed = 0
    progress_lock = asyncio.Lock()

    def append_progress(event: dict[str, Any]) -> None:
        if progress_path is None:
            return
        progress_path.parent.mkdir(parents=True, exist_ok=True)
        with progress_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    async def worker(worker_id: int) -> None:
        nonlocal completed
        while True:
            record = await queue.get()
            if record is None:
                queue.task_done()
                return

            result: Result | None = None
            error: str | None = None
            attempts = 0
            try:
                for attempt in range(retries + 1):
                    attempts = attempt + 1
                    try:
                        result = await transcribe_message(
                            client,
                            chat=chat,
                            message_id=record["id"],
                            timeout=timeout,
                            request_timeout=request_timeout,
                            expected_sender_id=record["author"]["id"],
                        )
                        break
                    except FloodWaitError as exc:
                        error = f"FloodWaitError: {exc.seconds}s"
                        if attempt >= retries:
                            break
                        wait_seconds = max(float(exc.seconds), 2.0 * (2**attempt))
                        append_progress(
                            {
                                "event": "retry",
                                "message_id": record["id"],
                                "attempt": attempts,
                                "worker": worker_id,
                                "reason": error,
                                "sleep_seconds": wait_seconds,
                            }
                        )
                        await asyncio.sleep(wait_seconds)
                    except (TimeoutError, asyncio.TimeoutError, ConnectionError, OSError) as exc:
                        error = f"{type(exc).__name__}: {exc}"
                        if attempt >= retries:
                            break
                        wait_seconds = 2.0 * (2**attempt)
                        append_progress(
                            {
                                "event": "retry",
                                "message_id": record["id"],
                                "attempt": attempts,
                                "worker": worker_id,
                                "reason": error,
                                "sleep_seconds": wait_seconds,
                            }
                        )
                        await asyncio.sleep(wait_seconds)
                    except ValueError as exc:
                        # Bad message/entity/sender input is deterministic; do not
                        # put it back into the queue and repeat the same failure.
                        error = f"{type(exc).__name__}: {exc}"
                        break
                    except Exception as exc:  # keep one bad recording from killing the day
                        error = f"{type(exc).__name__}: {exc}"
                        if attempt >= retries:
                            break
                        wait_seconds = 2.0 * (2**attempt)
                        append_progress(
                            {
                                "event": "retry",
                                "message_id": record["id"],
                                "attempt": attempts,
                                "worker": worker_id,
                                "reason": error,
                                "sleep_seconds": wait_seconds,
                            }
                        )
                        await asyncio.sleep(wait_seconds)

                if result is not None:
                    record["transcription"] = {
                        "status": result.status,
                        "complete": result.complete,
                        "source": result.source,
                        "text": _clean_text(result.text, limit=4000),
                        "sender_id": result.sender_id,
                        "outgoing": result.outgoing,
                        "reply_to_message_id": result.reply_to_message_id,
                        "transcription_id": result.transcription_id,
                        "pending": result.pending,
                        "trial_remains": result.trial_remains,
                    }
                else:
                    record["transcription"] = {
                        "status": "error",
                        "complete": False,
                        "source": "telegram_native",
                        "text": "",
                        "error": error or "unknown transcription error",
                    }

                async with progress_lock:
                    completed += 1
                    event = {
                        "event": "complete",
                        "message_id": record["id"],
                        "kind": record["kind"],
                        "status": record["transcription"]["status"],
                        "complete": record["transcription"]["complete"],
                        "attempts": attempts,
                        "worker": worker_id,
                        "completed": completed,
                        "total": len(targets),
                    }
                    append_progress(event)
                    print(
                        f"transcribed {completed}/{len(targets)} id={record['id']} "
                        f"kind={record['kind']} status={record['transcription']['status']}",
                        file=sys.stderr,
                        flush=True,
                    )
                    if on_progress is not None:
                        on_progress(record)
            finally:
                queue.task_done()

    await asyncio.gather(*(worker(worker_id) for worker_id in range(worker_count)))
    await queue.join()


def _compact_lines(records: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    authors: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for record in records:
        author = record["author"]
        authors.setdefault(
            author["alias"],
            {"alias": author["alias"], "id": author["id"], "name": author["name"], "username": author["username"], "outgoing": author["outgoing"]},
        )

    legend = ["Участники:"]
    for author in authors.values():
        marker = " [owner/outgoing]" if author["outgoing"] else ""
        username = f" @{author['username']}" if author.get("username") else ""
        legend.append(f"{author['alias']} = {author['name']}{username} (id {author['id']}){marker}")

    reply_target_ids = {
        record["reply_to"]["id"]
        for record in records
        if record.get("reply_to")
    }
    lines = legend + ["", "Сообщения (хронологически; ↳ означает reply):"]
    for record in records:
        text = record["text"]
        if record["transcription"] is not None:
            transcript = record["transcription"]
            text = transcript.get("text") or f"⟦{record['kind']}, расшифровка недоступна⟧"
            text = f"[{record['kind']}] {text}"
        elif not text:
            # Empty non-audio media is omitted unless it is a reply target.
            if record["id"] not in reply_target_ids:
                continue
            text = f"⟦{record['kind']} без подписи⟧"

        reply = record.get("reply_to")
        reply_part = ""
        if reply:
            reply_author = (reply.get("author") or {}).get("alias")
            reply_part = f" ↳#{reply['id']}" + (f" ({reply_author})" if reply_author else "")
        lines.append(f"[{record['time']}] {record['author']['alias']} #{record['id']}{reply_part}: {text}")

    return "\n".join(lines), list(authors.values())


def _parse_local_boundary(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LOCAL_TZ)
    return parsed.astimezone(LOCAL_TZ)


def _window_bounds(
    args: argparse.Namespace, captured_end_utc: datetime
) -> tuple[datetime, datetime, str, str]:
    if args.since is not None:
        start_local = _parse_local_boundary(args.since)
        end_local = _parse_local_boundary(args.until)
        if start_local >= end_local:
            raise ValueError("--since must be earlier than --until")
        return (
            start_local.astimezone(timezone.utc),
            end_local.astimezone(timezone.utc),
            start_local.isoformat(),
            end_local.isoformat(),
        )
    if args.last_hours is not None:
        if args.last_hours <= 0:
            raise ValueError("--last-hours must be positive")
        end_local = captured_end_utc.astimezone(LOCAL_TZ)
        start_local = end_local - timedelta(hours=args.last_hours)
        return (
            start_local.astimezone(timezone.utc),
            end_local.astimezone(timezone.utc),
            start_local.isoformat(),
            end_local.isoformat(),
        )
    day = date.fromisoformat(args.date)
    start_utc, end_utc = _day_bounds(day)
    start_local = start_utc.astimezone(LOCAL_TZ)
    end_local = end_utc.astimezone(LOCAL_TZ)
    return start_utc, end_utc, start_local.isoformat(), end_local.isoformat()


def _collection_parameters(
    args: argparse.Namespace,
) -> tuple[datetime | None, datetime, dict[str, Any], dict[str, Any], str]:
    captured_end_utc = datetime.now(timezone.utc)
    if args.last_messages is not None:
        request = {"mode": "last_messages", "count": args.last_messages}
        window = {
            "mode": "last_messages",
            "requested_count": args.last_messages,
            "since_local": None,
            "until_local": captured_end_utc.astimezone(LOCAL_TZ).isoformat(),
        }
        return None, captured_end_utc, window, request, f"last-{args.last_messages}-messages"

    start_utc, end_utc, start_local_iso, end_local_iso = _window_bounds(
        args, captured_end_utc
    )
    window = {
        "mode": "time_window",
        "since_local": start_local_iso,
        "until_local": end_local_iso,
    }
    if args.since is not None:
        request = {"mode": "range", "since": start_local_iso, "until": end_local_iso}
        raw_label = f"range-{start_local_iso}-{end_local_iso}"
        label = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw_label).strip("-")[:120]
    elif args.last_hours is not None:
        request = {"mode": "last_hours", "hours": args.last_hours}
        label = f"last-{args.last_hours:g}h"
    else:
        request = {"mode": "date", "date": args.date}
        label = args.date
    return start_utc, end_utc, window, request, label


def _record_local_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LOCAL_TZ)
    return parsed.astimezone(LOCAL_TZ)


def _memory_state(
    snapshot: dict[str, Any] | None,
    current_markers: list[dict[str, Any]],
    *,
    request: dict[str, Any],
    window_start: str | None,
    force_refresh: bool,
) -> str:
    if snapshot is None:
        return "miss"
    if force_refresh:
        return "refresh"

    if request["mode"] == "last_hours" and snapshot["source_message_count"]:
        first_message = _record_local_datetime(snapshot.get("first_message_time"))
        current_start = _record_local_datetime(window_start)
        if first_message is None or current_start is None or first_message < current_start:
            return "refresh"

    stored_markers = snapshot.get("tail_markers") or []
    if not stored_markers:
        return "hit" if not current_markers and not snapshot["source_message_count"] else "refresh"
    if not current_markers:
        return "refresh"

    stored_last_id = snapshot.get("last_message_id")
    current_last_id = current_markers[-1]["id"]
    if not isinstance(stored_last_id, int) or current_last_id < stored_last_id:
        return "refresh"

    current_by_id = {marker["id"]: marker["fingerprint"] for marker in current_markers}
    stored_by_id = {marker["id"]: marker["fingerprint"] for marker in stored_markers}
    if stored_last_id not in current_by_id:
        # More than the bounded tail changed since the checkpoint, so a safe
        # incremental merge is no longer possible.
        return "refresh"
    current_first_id = current_markers[0]["id"]
    for message_id, fingerprint in stored_by_id.items():
        current_fingerprint = current_by_id.get(message_id)
        if current_fingerprint is None and message_id >= current_first_id:
            return "refresh"
        if current_fingerprint is not None and current_fingerprint != fingerprint:
            return "refresh"

    if current_last_id == stored_last_id:
        return "hit" if current_markers == stored_markers else "refresh"

    if (
        request["mode"] == "last_messages"
        and snapshot["source_message_count"] >= int(request["count"])
    ):
        return "refresh"
    return "delta"


def _source_checkpoint(
    *,
    records: list[dict[str, Any]],
    snapshot: dict[str, Any] | None,
    state: str,
    markers: list[dict[str, Any]],
    window: dict[str, Any],
) -> dict[str, Any]:
    incremental = state == "delta" and snapshot is not None
    if incremental:
        message_count = int(snapshot["source_message_count"]) + len(records)
        first_message_id = snapshot.get("first_message_id")
        first_message_time = snapshot.get("first_message_time")
    else:
        message_count = len(records)
        first_message_id = records[0]["id"] if records else None
        first_message_time = records[0]["time"] if records else None

    last_message_id = records[-1]["id"] if records else None
    last_message_time = records[-1]["time"] if records else None
    if incremental and not records:
        last_message_id = snapshot.get("last_message_id")
        last_message_time = snapshot.get("last_message_time")

    return {
        "message_count": message_count,
        "first_message_id": first_message_id,
        "last_message_id": last_message_id,
        "first_message_time": first_message_time,
        "last_message_time": last_message_time,
        "window_start": window.get("since_local"),
        "window_end": window.get("until_local"),
        "tail_markers": markers,
        "validation": {
            "mode": "recent_tail",
            "marker_count": len(markers),
            "maximum_marker_count": TAIL_VALIDATION_MARKERS,
        },
    }


def _agent_context(compact: str, memory: dict[str, Any] | None) -> str:
    if not memory:
        return compact
    previous = memory.get("previous_summary")
    if not previous:
        return compact
    return (
        "Ранее сохранённая структурированная сводка:\n"
        + json.dumps(previous, ensure_ascii=False, indent=2)
        + "\n\nНовые сообщения после сохранённого курсора:\n"
        + compact
    )


def _summary_store_path(settings: Any, args: argparse.Namespace) -> Path:
    return Path(args.memory_db) if args.memory_db else memory_database_path(settings.data_dir)


def _print_cache_hit(snapshot: dict[str, Any], *, as_json: bool) -> None:
    payload = {
        "schema": "telegram_dialog_memory_cache.v1",
        "cache_status": "hit",
        "summary": snapshot["summary"],
        "source_message_count": snapshot["source_message_count"],
        "last_message_id": snapshot["last_message_id"],
        "revision": snapshot["revision"],
        "validation": "recent_tail",
    }
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(json.dumps(snapshot["summary"], ensure_ascii=False, indent=2))
        print(
            f"\n[summary memory: cache_hit; revision={snapshot['revision']}; "
            f"messages={snapshot['source_message_count']}; validation=recent_tail]"
        )


async def _run(args: argparse.Namespace) -> int:
    settings = load_settings(args.account)
    apply_runtime_env(settings)
    start_utc, end_utc, window, request, label = _collection_parameters(args)
    memory_enabled = args.do_summary and not args.no_memory
    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized")
        entity = await _resolve_entity(client, _peer_arg(str(args.chat)))
        resolved_chat_id = int(get_peer_id(entity))
        account_name = settings.account or "default"
        snapshot: dict[str, Any] | None = None
        current_markers: list[dict[str, Any]] = []
        memory_state: str | None = None
        identifier: str | None = None

        if memory_enabled:
            identifier = scope_key(
                account=account_name,
                chat_id=resolved_chat_id,
                topic_id=args.topic_id,
                sender_id=args.sender_id,
                request=request,
            )
            with SummaryStore(_summary_store_path(settings, args)) as store:
                snapshot = store.get(identifier)
            marker_limit = (
                min(TAIL_VALIDATION_MARKERS, args.last_messages)
                if args.last_messages is not None
                else TAIL_VALIDATION_MARKERS
            )
            current_markers = await _collect_recent_markers(
                client,
                entity,
                start_utc=start_utc,
                end_utc=end_utc,
                sender_id=args.sender_id,
                topic_id=args.topic_id,
                limit=marker_limit,
            )
            memory_state = _memory_state(
                snapshot,
                current_markers,
                request=request,
                window_start=window.get("since_local"),
                force_refresh=args.force_refresh,
            )
            if memory_state == "hit" and snapshot is not None:
                with SummaryStore(_summary_store_path(settings, args)) as store:
                    store.mark_validated(identifier)
                _print_cache_hit(snapshot, as_json=args.json)
                return 0

        collection_limit = args.last_messages if args.last_messages is not None else None
        min_id = (
            int(snapshot["last_message_id"])
            if memory_state == "delta"
            and snapshot is not None
            and snapshot.get("last_message_id") is not None
            else None
        )
        records = await _collect_messages(
            client,
            entity,
            start_utc=start_utc,
            end_utc=end_utc,
            sender_id=args.sender_id,
            topic_id=args.topic_id,
            limit=collection_limit,
            min_id=min_id,
        )

        if memory_state == "delta" and snapshot is not None:
            last_messages_overflow = (
                args.last_messages is not None
                and int(snapshot["source_message_count"]) + len(records) > args.last_messages
            )
            if not records or last_messages_overflow:
                memory_state = "refresh"
                records = await _collect_messages(
                    client,
                    entity,
                    start_utc=start_utc,
                    end_utc=end_utc,
                    sender_id=args.sender_id,
                    topic_id=args.topic_id,
                    limit=collection_limit,
                )

        if args.last_messages is not None:
            if memory_state == "delta" and snapshot is not None:
                window["since_local"] = snapshot.get("first_message_time")
            else:
                window["since_local"] = records[0]["time"] if records else None
            window["until_local"] = records[-1]["time"] if records else None

        memory: dict[str, Any] | None = None
        if memory_enabled:
            if memory_state is None or identifier is None:
                raise RuntimeError("summary memory planning did not produce a preparation")
            checkpoint = _source_checkpoint(
                records=records,
                snapshot=snapshot,
                state=memory_state,
                markers=current_markers,
                window=window,
            )
            memory = {
                "status": memory_state,
                "commit_required": True,
                "previous_summary": (
                    snapshot["summary"] if memory_state == "delta" and snapshot else None
                ),
                "preparation": {
                    "scope_key": identifier,
                    "scope": {
                        "account": account_name,
                        "chat_id": resolved_chat_id,
                        "topic_id": args.topic_id,
                        "sender_id": args.sender_id,
                        "request": request,
                    },
                    "base_revision": int(snapshot["revision"]) if snapshot else 0,
                    "prepared_at": datetime.now(timezone.utc).isoformat(),
                    "source": checkpoint,
                },
            }

        transcribable_count = sum(1 for record in records if _is_transcribable(record["kind"]))
        compact, authors = _compact_lines(records)
        payload = {
            "schema": "telegram_chat_summary.v2",
            "chat_id": args.chat,
            "resolved_chat_id": resolved_chat_id,
            "topic_id": args.topic_id,
            "date": args.date,
            "window": window,
            "timezone": "Europe/Moscow",
            "message_count": len(records),
            "transcribable_count": transcribable_count,
            "transcribed_complete_count": 0,
            "authors": authors,
            "messages": records,
        }
        if memory is not None:
            payload["memory"] = memory
        safe_chat = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(args.chat)).strip("-") or "chat"
        output_path = Path(
            args.output
            or (Path(settings.transcripts_dir) / f"{safe_chat}_{label}_native.json")
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if args.resume and output_path.exists():
            previous = json.loads(output_path.read_text(encoding="utf-8"))
            if (
                str(previous.get("chat_id")) != str(args.chat)
                or previous.get("date") != args.date
                or previous.get("window") != window
            ):
                raise ValueError("--resume archive does not match --chat/--date/--window")
            previous_by_id = {item.get("id"): item for item in previous.get("messages", [])}
            for record in records:
                previous_record = previous_by_id.get(record.get("id"))
                previous_transcription = (previous_record or {}).get("transcription")
                if previous_transcription and previous_transcription.get("complete"):
                    record["transcription"] = previous_transcription
        progress_path = Path(args.progress_log) if args.progress_log else output_path.with_suffix(".progress.jsonl")

        def persist_archive(_record: dict[str, Any] | None = None) -> None:
            payload["transcribed_complete_count"] = sum(
                1
                for item in payload["messages"]
                if item["transcription"] and item["transcription"].get("complete")
            )
            serialized = json.dumps(payload, ensure_ascii=False, indent=2)
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=output_path.parent,
                prefix=f".{output_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(serialized)
                temporary_path = Path(handle.name)
            os.replace(temporary_path, output_path)

        # Write an initial archive before STT starts. If the process is stopped
        # by a hard timeout, the metadata and completed transcription records
        # remain available for inspection/resume instead of disappearing.
        persist_archive()
        if progress_path.exists() and not args.resume:
            progress_path.unlink()
        if not args.metadata_only:
            append_mode = "a" if args.resume else "w"
            with progress_path.open(append_mode, encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "event": "start",
                            "chat_id": args.chat,
                            "date": args.date,
                            "message_count": len(records),
                            "transcribable_count": transcribable_count,
                            "concurrency": args.concurrency,
                            "timeout_seconds": args.transcription_timeout,
                            "request_timeout_seconds": args.transcription_request_timeout,
                            "retries": args.transcription_retries,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
            )
            await _transcribe_records(
                client,
                entity,
                records,
                timeout=args.transcription_timeout,
                concurrency=args.concurrency,
                request_timeout=args.transcription_request_timeout,
                retries=args.transcription_retries,
                progress_path=progress_path,
                on_progress=persist_archive,
            )
            persist_archive()
            with progress_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "event": "finish",
                            "transcribable_count": transcribable_count,
                            "transcribed_complete_count": payload["transcribed_complete_count"],
                            "error_count": sum(
                                1
                                for item in records
                                if (item.get("transcription") or {}).get("status") == "error"
                            ),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )

        context_path = output_path.with_suffix(".context.txt")
        compact, _ = _compact_lines(records)
        agent_context = _agent_context(compact, memory)
        context_path.write_text(agent_context, encoding="utf-8")
        if args.do_summary:
            print(agent_context)
            print(f"\n[agent context: {context_path}]")
            if memory is not None:
                print(
                    f"[summary memory: status={memory['status']}; commit_required=true; "
                    f"archive={output_path}]"
                )
        elif args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(compact)
        return 0
    finally:
        await client.disconnect()


def _commit_summary(args: argparse.Namespace) -> int:
    archive_path = Path(args.summary_from)
    summary_path = Path(args.commit_summary)
    payload = json.loads(archive_path.read_text(encoding="utf-8"))
    memory = payload.get("memory")
    preparation = memory.get("preparation") if isinstance(memory, dict) else None
    if not isinstance(preparation, dict) or not memory.get("commit_required"):
        raise ValueError("archive has no pending summary-memory preparation")

    document = json.loads(summary_path.read_text(encoding="utf-8"))
    settings = load_settings(args.account)
    apply_runtime_env(settings)
    with SummaryStore(_summary_store_path(settings, args)) as store:
        snapshot = store.commit(preparation, document)
    print(
        json.dumps(
            {
                "status": "committed",
                "scope_key": snapshot["scope_key"],
                "revision": snapshot["revision"],
                "source_message_count": snapshot["source_message_count"],
                "database": str(_summary_store_path(settings, args)),
            },
            ensure_ascii=False,
        )
    )
    return 0


async def _run_from_archive(args: argparse.Namespace) -> int:
    path = Path(args.summary_from)
    payload = json.loads(path.read_text(encoding="utf-8"))
    compact, _ = _compact_lines(payload.get("messages") or [])
    memory = payload.get("memory") if isinstance(payload.get("memory"), dict) else None
    agent_context = _agent_context(compact, memory)
    context_path = path.with_suffix(".context.txt")
    context_path.write_text(agent_context, encoding="utf-8")
    print(agent_context)
    print(f"\n[agent context: {context_path}]")
    print(
        f"[provenance: messages={payload.get('message_count')}, "
        f"transcribable={payload.get('transcribable_count')}, "
        f"complete={payload.get('transcribed_complete_count')}, "
        f"timezone={payload.get('timezone', 'Europe/Moscow')}]"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize a Telegram chat or forum topic")
    parser.add_argument("--account")
    parser.add_argument("--chat", help="Telegram chat id or username")
    parser.add_argument("--topic-id", type=int, help="Forum topic id; use the topic's root message id")
    window_group = parser.add_mutually_exclusive_group()
    window_group.add_argument("--date", help="Local date YYYY-MM-DD in Europe/Moscow")
    window_group.add_argument("--last-hours", type=float, help="Floating local-time window ending now, e.g. 24")
    window_group.add_argument("--last-messages", type=int, help="Newest messages to collect, maximum 1000")
    window_group.add_argument(
        "--since", help="Inclusive local ISO date/time for a stable custom range"
    )
    parser.add_argument("--until", help="Exclusive local ISO date/time paired with --since")
    parser.add_argument("--sender-id", type=int, help="Only include messages from this exact sender id")
    parser.add_argument("--transcription-timeout", type=float, default=180)
    parser.add_argument(
        "--transcription-request-timeout",
        type=float,
        default=30,
        help="Seconds to wait for each TranscribeAudioRequest (default: 30)",
    )
    parser.add_argument("--transcription-retries", type=int, default=2)
    parser.add_argument("--concurrency", type=int, default=1, help="Queued native STT workers (default: 1)")
    parser.add_argument("--metadata-only", action="store_true", help="Collect authors/replies without STT")
    parser.add_argument("--output", help="Compact JSON archive path")
    parser.add_argument("--progress-log", help="JSONL progress log path; defaults beside the archive")
    parser.add_argument("--resume", action="store_true", help="Reuse completed transcriptions from an existing matching archive")
    parser.add_argument("--summary-from", help="Generate summary from an existing compact JSON archive")
    parser.add_argument(
        "--commit-summary",
        help="Commit a telegram_dialog_memory.v1 JSON file using --summary-from as preparation",
    )
    parser.add_argument("--memory-db", help="Override the account-local unified memory SQLite path")
    parser.add_argument(
        "--force-refresh", action="store_true", help="Ignore a saved summary and rebuild the window"
    )
    parser.add_argument(
        "--no-memory", action="store_true", help="Disable summary-memory lookup for this collection"
    )
    parser.add_argument("--json", action="store_true", help="Print compact JSON instead of summary text")
    parser.add_argument("--do-summary", action="store_true", help="Emit compact context for the current agent")
    args = parser.parse_args()
    try:
        if args.commit_summary:
            if not args.summary_from:
                parser.error("--commit-summary requires --summary-from <archive>")
            if not args.account:
                parser.error("--account is required with --commit-summary")
            return _commit_summary(args)
        if args.summary_from:
            return asyncio.run(_run_from_archive(args))
        if not args.account:
            parser.error("--account is required unless --summary-from is used")
        if not args.chat:
            parser.error("--chat is required unless --summary-from is used")
        if args.since is not None and args.until is None:
            parser.error("--since requires --until")
        if args.until is not None and args.since is None:
            parser.error("--until requires --since")
        if (
            args.date is None
            and args.last_hours is None
            and args.last_messages is None
            and args.since is None
        ):
            parser.error(
                "one of --date, --last-hours, --last-messages or --since/--until "
                "is required unless --summary-from is used"
            )
        if args.last_messages is not None and not 1 <= args.last_messages <= MAX_LAST_MESSAGES:
            parser.error(f"--last-messages must be between 1 and {MAX_LAST_MESSAGES}")
        if args.topic_id is not None and args.topic_id <= 0:
            parser.error("--topic-id must be positive")
        if args.transcription_timeout <= 0 or args.transcription_request_timeout <= 0:
            parser.error("transcription timeouts must be positive")
        if args.transcription_retries < 0:
            parser.error("--transcription-retries must be >= 0")
        if args.concurrency < 1:
            parser.error("--concurrency must be >= 1")
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
