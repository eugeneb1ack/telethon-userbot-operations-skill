from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp
from telethon import TelegramClient, events

from core.event_store import EventStore
from core.telegram_targets import entity_payload, resolve_entity
from modules.recent_personal_incoming import collect_recent_incoming, result_payload

logger = logging.getLogger(__name__)

MAX_REQUEST_BYTES = 256 * 1024
MAX_DIALOGS = 500
MAX_MESSAGES = 500
TELEGRAM_SERVICE_USER_ID = 777000


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def validate_webhook_url(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    url = value.strip()
    parsed = urlparse(url)
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme == "https" and parsed.hostname:
        return url
    if parsed.scheme == "http" and parsed.hostname in local_hosts:
        return url
    raise ValueError("USERBOT_WEBHOOK_URL must use HTTPS or local HTTP")


@dataclass(frozen=True)
class GatewayOptions:
    enabled: bool
    socket_path: Path
    database_path: Path
    preview_chars: int
    webhook_url: str | None
    webhook_secret: str | None


def load_gateway_options(settings: Any) -> GatewayOptions:
    runtime_dir = Path(settings.runtime_dir)
    webhook_url = validate_webhook_url(os.getenv("USERBOT_WEBHOOK_URL"))
    webhook_secret = os.getenv("USERBOT_WEBHOOK_SECRET") or None
    if webhook_url and (webhook_secret is None or len(webhook_secret) < 32):
        raise ValueError("USERBOT_WEBHOOK_SECRET must contain at least 32 characters")
    return GatewayOptions(
        enabled=_env_bool("USERBOT_GATEWAY_ENABLED", True),
        socket_path=runtime_dir / "userbot.sock",
        database_path=runtime_dir / "events.sqlite3",
        preview_chars=_env_int(
            "USERBOT_EVENT_PREVIEW_CHARS", 160, minimum=0, maximum=500
        ),
        webhook_url=webhook_url,
        webhook_secret=webhook_secret,
    )


def webhook_signature(secret: str, timestamp: str, body: bytes) -> str:
    signed = timestamp.encode("ascii") + b"." + body
    return "sha256=" + hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()


def _display_name(entity: Any, fallback: str) -> str:
    name = " ".join(
        part
        for part in (getattr(entity, "first_name", None), getattr(entity, "last_name", None))
        if part
    ).strip()
    if name:
        return name
    username = getattr(entity, "username", None)
    return f"@{username}" if username else fallback


class UserbotGateway:
    def __init__(
        self,
        client: TelegramClient,
        settings: Any,
        options: GatewayOptions,
        *,
        idle_timeout_seconds: int = 0,
    ) -> None:
        self.client = client
        self.settings = settings
        self.options = options
        self.account = settings.account or "legacy"
        self.store = EventStore(options.database_path)
        self.server: asyncio.AbstractServer | None = None
        self.webhook_task: asyncio.Task[None] | None = None
        self.idle_task: asyncio.Task[None] | None = None
        self.webhook_wakeup = asyncio.Event()
        self._stopping = False
        self._stopped = False
        self.idle_timeout_seconds = idle_timeout_seconds
        self._last_local_activity = time.monotonic()
        self._active_requests = 0

    async def start(self) -> None:
        self.options.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.options.socket_path.exists() and not self.options.socket_path.is_socket():
            raise RuntimeError(f"refusing to replace non-socket path: {self.options.socket_path}")
        if self.options.socket_path.is_socket():
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_unix_connection(str(self.options.socket_path)),
                    timeout=0.25,
                )
            except (ConnectionRefusedError, FileNotFoundError, asyncio.TimeoutError):
                self.options.socket_path.unlink(missing_ok=True)
            else:
                writer.close()
                await writer.wait_closed()
                raise RuntimeError(
                    f"another gateway already owns socket: {self.options.socket_path}"
                )
        self.server = await asyncio.start_unix_server(
            self._handle_connection,
            path=str(self.options.socket_path),
            limit=MAX_REQUEST_BYTES,
        )
        self.options.socket_path.chmod(0o600)
        self.client.add_event_handler(self._on_new_message, events.NewMessage(incoming=True))
        if self.options.webhook_url:
            self.webhook_task = asyncio.create_task(
                self._webhook_worker(), name="userbot-webhook"
            )
        if self.idle_timeout_seconds:
            self.idle_task = asyncio.create_task(
                self._idle_worker(), name="userbot-idle-shutdown"
            )
        logger.info(
            "Local gateway ready for account=%s webhook=%s",
            self.account,
            "enabled" if self.options.webhook_url else "disabled",
        )

    async def stop(self) -> None:
        if self._stopped:
            return
        self._stopping = True
        self.client.remove_event_handler(self._on_new_message)
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        if self.webhook_task is not None:
            self.webhook_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.webhook_task
        if self.idle_task is not None and self.idle_task is not asyncio.current_task():
            self.idle_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.idle_task
        self.store.close()
        with contextlib.suppress(FileNotFoundError):
            self.options.socket_path.unlink()
        self._stopped = True

    async def _idle_worker(self) -> None:
        while not self._stopping:
            elapsed = time.monotonic() - self._last_local_activity
            remaining = self.idle_timeout_seconds - elapsed
            if self._active_requests == 0 and remaining <= 0:
                logger.info(
                    "Idle timeout reached for account=%s after %ss; disconnecting",
                    self.account,
                    self.idle_timeout_seconds,
                )
                await self.client.disconnect()
                return
            await asyncio.sleep(max(0.1, min(1.0, remaining)))

    async def _on_new_message(self, event: events.NewMessage.Event) -> None:
        try:
            payload = await self._event_payload(event)
            if payload is None:
                return
            inserted, _ = self.store.add_event(payload)
            if inserted:
                self.webhook_wakeup.set()
        except Exception:
            logger.exception("Failed to persist incoming Telegram event")

    async def _event_payload(self, event: events.NewMessage.Event) -> dict[str, Any] | None:
        sender = await event.get_sender()
        if sender is not None and (
            getattr(sender, "is_self", False)
            or getattr(sender, "bot", False)
            or getattr(sender, "id", None) == TELEGRAM_SERVICE_USER_ID
        ):
            return None

        kind: str | None = None
        if event.is_private:
            kind = "direct_message"
        elif event.is_reply:
            replied = await event.get_reply_message()
            if replied is not None and getattr(replied, "out", False):
                kind = "reply"
        if kind is None and bool(getattr(event.message, "mentioned", False)):
            kind = "mention"
        if kind is None:
            return None

        chat = await event.get_chat()
        chat_title = getattr(chat, "title", None) or _display_name(
            chat, f"chat:{event.chat_id}"
        )
        sender_name = _display_name(sender, f"id:{event.sender_id}")
        raw_text = (getattr(event, "raw_text", None) or "").replace("\n", " ").strip()
        if not raw_text and getattr(event.message, "media", None) is not None:
            raw_text = "[media]"
        preview = raw_text[: self.options.preview_chars] if self.options.preview_chars else ""
        occurred_at = getattr(event.message, "date", None)
        if not isinstance(occurred_at, datetime):
            occurred_at = datetime.now(timezone.utc)
        elif occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=timezone.utc)

        return {
            "account": self.account,
            "kind": kind,
            "chat_id": int(event.chat_id),
            "message_id": int(event.id),
            "sender_id": event.sender_id,
            "chat_title": chat_title,
            "sender_name": sender_name,
            "preview": preview,
            "occurred_at": occurred_at.astimezone(timezone.utc).isoformat(),
            "webhook_status": "pending" if self.options.webhook_url else "disabled",
        }

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        request_id: Any = None
        response: dict[str, Any] = {
            "id": None,
            "ok": False,
            "error": {"type": "GatewayError", "message": "request cancelled"},
        }
        self._active_requests += 1
        self._last_local_activity = time.monotonic()
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=10)
            if not raw or len(raw) > MAX_REQUEST_BYTES:
                raise ValueError("request is empty or too large")
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError("request must be a JSON object")
            request_id = request.get("id")
            method = request.get("method")
            params = request.get("params") or {}
            if not isinstance(method, str) or not isinstance(params, dict):
                raise ValueError("method must be a string and params must be an object")
            result = await self.dispatch(method, params)
            response = {"id": request_id, "ok": True, "result": result}
        except Exception as exc:
            response = {
                "id": request_id,
                "ok": False,
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }
        finally:
            try:
                writer.write(
                    json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n"
                )
                with contextlib.suppress(Exception):
                    await writer.drain()
            finally:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
                self._active_requests -= 1
                self._last_local_activity = time.monotonic()

    async def dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "status":
            return {
                "account": self.account,
                "authorized": bool(await self.client.is_user_authorized()),
                "connected": self.client.is_connected(),
                "webhook_enabled": bool(self.options.webhook_url),
                "pid": os.getpid(),
                "idle_timeout_seconds": self.idle_timeout_seconds,
            }
        if method == "events.list":
            events_list = self.store.list_events(
                limit=int(params.get("limit", 20)),
                unread_only=bool(params.get("unread_only", False)),
            )
            return {"events": events_list, "count": len(events_list)}
        if method == "events.show":
            record = self.store.get_event(str(params.get("event_id") or ""))
            if record is None:
                raise ValueError("unknown event_id")
            return record
        if method == "events.acknowledge":
            record = self.store.acknowledge(str(params.get("event_id") or ""))
            if record is None:
                raise ValueError("unknown event_id")
            return record
        if method == "recent_personal_incoming":
            limit = int(params.get("limit", 3))
            dialogs_limit = int(params.get("dialogs_limit", 100))
            messages_per_dialog = int(params.get("messages_per_dialog", 20))
            if not 1 <= limit <= 10:
                raise ValueError("limit must be between 1 and 10")
            records, scanned = await collect_recent_incoming(
                self.client,
                limit=limit,
                dialogs_limit=dialogs_limit,
                messages_per_dialog=messages_per_dialog,
            )
            return result_payload(
                records, scanned_dialogs=scanned, dialogs_limit=dialogs_limit
            )
        if method == "dialogs.list":
            return await self._list_dialogs(params)
        if method == "messages.search":
            return await self._search_messages(params)
        if method == "messages.recent":
            return await self._recent_messages(params)
        raise ValueError(f"unknown method: {method}")

    async def _list_dialogs(self, params: dict[str, Any]) -> dict[str, Any]:
        kind = str(params.get("kind", "personal"))
        if kind not in {"personal", "groups", "channels", "bots", "all"}:
            raise ValueError("kind must be personal, groups, channels, bots, or all")
        limit = int(params.get("limit", 100))
        if not 1 <= limit <= MAX_DIALOGS:
            raise ValueError(f"limit must be between 1 and {MAX_DIALOGS}")
        records: list[dict[str, Any]] = []
        async for dialog in self.client.iter_dialogs(limit=limit):
            entity = dialog.entity
            is_bot = bool(getattr(entity, "bot", False))
            if getattr(dialog, "is_user", False):
                current_kind = "bots" if is_bot else "personal"
                if getattr(entity, "is_self", False):
                    continue
            elif getattr(dialog, "is_group", False):
                current_kind = "groups"
            elif getattr(dialog, "is_channel", False):
                current_kind = "channels"
            else:
                continue
            if kind != "all" and current_kind != kind:
                continue
            records.append(
                {
                    "kind": current_kind,
                    "id": getattr(entity, "id", None),
                    "title": getattr(dialog, "name", None)
                    or _display_name(entity, f"id:{getattr(entity, 'id', None)}"),
                    "username": getattr(entity, "username", None),
                    "unread_count": int(getattr(dialog, "unread_count", 0) or 0),
                }
            )
        return {"read_only": True, "kind": kind, "count": len(records), "dialogs": records}

    @staticmethod
    async def _message_payload(message: Any) -> dict[str, Any]:
        sender = await message.get_sender()
        text = (getattr(message, "message", None) or "").replace("\n", " ")
        date = getattr(message, "date", None)
        return {
            "id": getattr(message, "id", None),
            "date": date.isoformat() if isinstance(date, datetime) else None,
            "sender_id": getattr(message, "sender_id", None),
            "sender": _display_name(sender, f"id:{getattr(message, 'sender_id', None)}"),
            "outgoing": bool(getattr(message, "out", False)),
            "has_media": getattr(message, "media", None) is not None,
            "text_preview": text[:280],
        }

    async def _search_messages(self, params: dict[str, Any]) -> dict[str, Any]:
        chat_input = str(params.get("chat") or "").strip()
        query = str(params.get("query") or "").strip()
        limit = int(params.get("limit", 50))
        if not chat_input or not query:
            raise ValueError("chat and query are required")
        if not 1 <= limit <= MAX_MESSAGES:
            raise ValueError(f"limit must be between 1 and {MAX_MESSAGES}")
        chat = await resolve_entity(self.client, chat_input)
        records = [
            await self._message_payload(message)
            async for message in self.client.iter_messages(chat, search=query, limit=limit)
        ]
        return {
            "read_only": True,
            "chat": entity_payload(chat, input_value=chat_input),
            "query": query,
            "count": len(records),
            "messages": records,
        }

    async def _recent_messages(self, params: dict[str, Any]) -> dict[str, Any]:
        chat_input = str(params.get("chat") or "").strip()
        limit = int(params.get("limit", 20))
        if not chat_input:
            raise ValueError("chat is required")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        chat = await resolve_entity(self.client, chat_input)
        records = [
            await self._message_payload(message)
            async for message in self.client.iter_messages(chat, limit=limit)
        ]
        return {
            "read_only": True,
            "chat": entity_payload(chat, input_value=chat_input),
            "count": len(records),
            "messages": records,
        }

    async def _webhook_worker(self) -> None:
        assert self.options.webhook_url is not None
        assert self.options.webhook_secret is not None
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            while not self._stopping:
                record = self.store.next_webhook_event()
                if record is None:
                    self.webhook_wakeup.clear()
                    try:
                        await asyncio.wait_for(self.webhook_wakeup.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        pass
                    continue
                envelope = {"version": 1, "type": "telegram.event", "event": record}
                body = json.dumps(
                    envelope, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
                timestamp = str(int(datetime.now(timezone.utc).timestamp()))
                headers = {
                    "Content-Type": "application/json",
                    "User-Agent": "telethon-userbot-gateway/1",
                    "X-Userbot-Event": record["id"],
                    "X-Userbot-Timestamp": timestamp,
                    "X-Userbot-Signature": webhook_signature(
                        self.options.webhook_secret, timestamp, body
                    ),
                }
                try:
                    async with session.post(
                        self.options.webhook_url, data=body, headers=headers
                    ) as response:
                        if not 200 <= response.status < 300:
                            raise RuntimeError(f"HTTP {response.status}")
                    self.store.mark_webhook_sent(record["id"])
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    attempts = self.store.webhook_attempts(record["id"]) + 1
                    delay = min(300, 2 ** min(attempts, 8))
                    next_attempt = datetime.now(timezone.utc) + timedelta(seconds=delay)
                    terminal = self.store.mark_webhook_failed(
                        record["id"],
                        error=f"{type(exc).__name__}: {exc}",
                        next_attempt_at=next_attempt.isoformat(),
                    )
                    if terminal:
                        logger.error(
                            "Webhook delivery stopped for event=%s attempts=%s error=%s",
                            record["id"],
                            attempts,
                            type(exc).__name__,
                        )
                    else:
                        logger.warning(
                            "Webhook delivery failed for event=%s retry_in=%ss error=%s",
                            record["id"],
                            delay,
                            type(exc).__name__,
                        )
