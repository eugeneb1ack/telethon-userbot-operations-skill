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

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config import apply_runtime_env, load_settings
try:
    from modules.transcribe_audio_native import Result, transcribe_message
except ModuleNotFoundError:
    from transcribe_audio_native import Result, transcribe_message

LOCAL_TZ = ZoneInfo("Europe/Moscow")
MAX_LAST_MESSAGES = 1000


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
) -> list[dict[str, Any]]:
    raw: list[Message] = []
    async for message in client.iter_messages(entity, limit=limit, reply_to=topic_id):
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
        for offset in range(0, len(missing_reply_ids), 100):
            ids = list(missing_reply_ids)[offset : offset + 100]
            for reply in await client.get_messages(entity, ids=ids):
                if not reply:
                    continue
                reply_sender = await reply.get_sender()
                reply_sender_id = getattr(reply, "sender_id", None) or getattr(reply_sender, "id", None)
                if reply_sender_id not in author_aliases:
                    author_aliases[reply_sender_id] = f"A{len(author_aliases) + 1}"
                for record in records:
                    if record["reply_to"] and record["reply_to"]["id"] == reply.id:
                        record["reply_to"]["author"] = {
                            "alias": author_aliases[reply_sender_id],
                            "id": reply_sender_id,
                            "name": _safe_name(reply_sender, reply_sender_id),
                            "username": getattr(reply_sender, "username", None),
                        }

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

    lines = legend + ["", "Сообщения (хронологически; ↳ означает reply):"]
    for record in records:
        text = record["text"]
        if record["transcription"] is not None:
            transcript = record["transcription"]
            text = transcript.get("text") or f"⟦{record['kind']}, расшифровка недоступна⟧"
            text = f"[{record['kind']}] {text}"
        elif not text:
            # Empty non-audio media is omitted unless it is a reply target.
            if not any(r.get("reply_to", {}).get("id") == record["id"] for r in records if r.get("reply_to")):
                continue
            text = f"⟦{record['kind']} без подписи⟧"

        reply = record.get("reply_to")
        reply_part = ""
        if reply:
            reply_author = (reply.get("author") or {}).get("alias")
            reply_part = f" ↳#{reply['id']}" + (f" ({reply_author})" if reply_author else "")
        lines.append(f"[{record['time']}] {record['author']['alias']} #{record['id']}{reply_part}: {text}")

    return "\n".join(lines), list(authors.values())


def _window_bounds(args: argparse.Namespace) -> tuple[datetime, datetime, str, str]:
    if args.last_hours is not None:
        if args.last_hours <= 0:
            raise ValueError("--last-hours must be positive")
        end_local = datetime.now(LOCAL_TZ)
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


async def _run(args: argparse.Namespace) -> int:
    settings = load_settings(args.account)
    apply_runtime_env(settings)
    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized")
        entity = await _resolve_entity(client, _peer_arg(str(args.chat)))
        if args.last_messages is not None:
            records = await _collect_messages(
                client,
                entity,
                start_utc=None,
                end_utc=None,
                sender_id=args.sender_id,
                topic_id=args.topic_id,
                limit=args.last_messages,
            )
            start_local_iso = records[0]["time"] if records else None
            end_local_iso = records[-1]["time"] if records else None
            window = {
                "mode": "last_messages",
                "requested_count": args.last_messages,
                "since_local": start_local_iso,
                "until_local": end_local_iso,
            }
        else:
            start_utc, end_utc, start_local_iso, end_local_iso = _window_bounds(args)
            records = await _collect_messages(
                client,
                entity,
                start_utc=start_utc,
                end_utc=end_utc,
                sender_id=args.sender_id,
                topic_id=args.topic_id,
            )
            window = {
                "mode": "time_window",
                "since_local": start_local_iso,
                "until_local": end_local_iso,
            }

        transcribable_count = sum(1 for record in records if _is_transcribable(record["kind"]))
        compact, authors = _compact_lines(records)
        payload = {
            "schema": "telegram_chat_summary.v1",
            "chat_id": args.chat,
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
        if args.last_messages is not None:
            label = f"last-{args.last_messages}-messages"
        else:
            label = args.date or f"last-{args.last_hours:g}h"
        output_path = Path(
            args.output
            or (Path(settings.transcripts_dir) / f"{str(args.chat).replace('-', 'm')}_{label}_native.json")
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
        context_path.write_text(compact, encoding="utf-8")
        if args.do_summary:
            print(compact)
            print(f"\n[agent context: {context_path}]")
        elif args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(compact)
        return 0
    finally:
        await client.disconnect()


async def _run_from_archive(args: argparse.Namespace) -> int:
    path = Path(args.summary_from)
    payload = json.loads(path.read_text(encoding="utf-8"))
    compact, _ = _compact_lines(payload.get("messages") or [])
    context_path = path.with_suffix(".context.txt")
    context_path.write_text(compact, encoding="utf-8")
    print(compact)
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
    parser.add_argument("--json", action="store_true", help="Print compact JSON instead of summary text")
    parser.add_argument("--do-summary", action="store_true", help="Emit compact context for the current agent")
    args = parser.parse_args()
    try:
        if args.summary_from:
            return asyncio.run(_run_from_archive(args))
        if not args.account:
            parser.error("--account is required unless --summary-from is used")
        if not args.chat:
            parser.error("--chat is required unless --summary-from is used")
        if args.date is None and args.last_hours is None and args.last_messages is None:
            parser.error("one of --date, --last-hours or --last-messages is required unless --summary-from is used")
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
