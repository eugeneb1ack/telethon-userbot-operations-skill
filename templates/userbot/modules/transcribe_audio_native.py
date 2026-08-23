#!/usr/bin/env python3
"""Transcribe one Telegram audio message through Telegram's native MTProto API.

This deliberately does not download the media or call a third-party STT provider.
The caller must provide the original Telegram peer and message id, which makes it
safe to use from a reply-aware guest-agent workflow.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from telethon import TelegramClient, functions, types, utils
from telethon.events import Raw

from core.config import apply_runtime_env, load_settings


def register(client: Any) -> None:
    """This is a direct helper, not an event module; keep loader compatibility."""
    return None


@dataclass
class Result:
    status: str
    complete: bool
    source: str = "telegram_native"
    chat: str | int | None = None
    message_id: int | None = None
    sender_id: int | None = None
    sender_name: str | None = None
    sender_username: str | None = None
    outgoing: bool = False
    reply_to_message_id: int | None = None
    media_kind: str | None = None
    duration_seconds: int | None = None
    transcription_id: int | None = None
    text: str = ""
    pending: bool = False
    trial_remains: int | None = None
    error: str | None = None


def _peer_arg(value: str) -> str | int:
    """Keep usernames usable while converting Bot API-style numeric ids."""
    try:
        return int(value)
    except ValueError:
        return value


def _media_info(message: types.Message) -> tuple[str, int | None]:
    if getattr(message, "voice", False):
        kind = "voice"
    elif getattr(message, "video_note", False):
        kind = "video_note"
    elif getattr(message, "audio", False):
        kind = "audio"
    else:
        raise ValueError("message is not a voice, audio file, or video note")

    file_obj = getattr(message, "file", None)
    duration = getattr(file_obj, "duration", None)
    return kind, int(duration) if duration is not None else None


async def _resolve_input_entity(client: TelegramClient, chat: str | int) -> Any:
    try:
        return await client.get_input_entity(chat)
    except (TypeError, ValueError) as exc:
        if not isinstance(chat, int):
            raise
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            if chat in {getattr(dialog, "id", None), getattr(entity, "id", None)}:
                return await client.get_input_entity(entity)
        raise ValueError(f"chat {chat} was not found in the authorized account dialogs") from exc


def _result_from_response(
    response: Any,
    *,
    chat: str | int,
    message_id: int,
    sender_id: int | None,
    sender_name: str | None,
    sender_username: str | None,
    outgoing: bool,
    reply_to_message_id: int | None,
    media_kind: str,
    duration_seconds: int | None,
) -> Result:
    return Result(
        status="ok" if not getattr(response, "pending", False) else "pending",
        complete=not getattr(response, "pending", False),
        chat=chat,
        message_id=message_id,
        sender_id=sender_id,
        sender_name=sender_name,
        sender_username=sender_username,
        outgoing=outgoing,
        reply_to_message_id=reply_to_message_id,
        media_kind=media_kind,
        duration_seconds=duration_seconds,
        transcription_id=getattr(response, "transcription_id", None),
        text=getattr(response, "text", "") or "",
        pending=bool(getattr(response, "pending", False)),
        trial_remains=getattr(response, "trial_remains_num", getattr(response, "trial_remains", None)),
    )


async def transcribe_message(
    client: TelegramClient,
    *,
    chat: str | int,
    message_id: int,
    timeout: float,
    request_timeout: float = 30.0,
    expected_sender_id: int | None = None,
) -> Result:
    """Transcribe one message using an already-connected client."""
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if request_timeout <= 0:
        raise ValueError("request_timeout must be positive")
    entity = await _resolve_input_entity(client, chat)
    message = await client.get_messages(entity, ids=message_id)
    if not message:
        raise ValueError(f"message {message_id} was not found in the requested chat")

    sender = await message.get_sender()
    sender_id = getattr(message, "sender_id", None) or getattr(sender, "id", None)
    sender_name = " ".join(
        part for part in (getattr(sender, "first_name", ""), getattr(sender, "last_name", "")) if part
    ).strip() or None
    sender_username = getattr(sender, "username", None)
    outgoing = bool(getattr(message, "out", False))
    reply_to_message_id = getattr(getattr(message, "reply_to", None), "reply_to_msg_id", None)
    if expected_sender_id is not None and sender_id != expected_sender_id:
        raise ValueError(
            "sender mismatch: "
            f"expected {expected_sender_id}, got {sender_id}; "
            f"outgoing={outgoing}, message_id={message_id}"
        )
    media_kind, duration = _media_info(message)

    updates: asyncio.Queue[Any] = asyncio.Queue()

    async def on_update(update: Any) -> None:
        if getattr(update, "msg_id", None) != message_id:
            return
        try:
            same_peer = utils.get_peer_id(getattr(update, "peer", None)) == utils.get_peer_id(entity)
        except (TypeError, ValueError):
            same_peer = False
        if not same_peer:
            return
        await updates.put(update)

    client.add_event_handler(on_update, Raw(types.UpdateTranscribedAudio))
    try:
        request = functions.messages.TranscribeAudioRequest(
            peer=entity,
            msg_id=message_id,
        )
        try:
            response = await asyncio.wait_for(client(request), timeout=request_timeout)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"native transcription request timed out after {request_timeout:g}s"
            ) from exc
        result = _result_from_response(
            response,
            chat=chat,
            message_id=message_id,
            sender_id=sender_id,
            sender_name=sender_name,
            sender_username=sender_username,
            outgoing=outgoing,
            reply_to_message_id=reply_to_message_id,
            media_kind=media_kind,
            duration_seconds=duration,
        )
        if result.complete:
            return result

        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                result.status = "pending_timeout"
                result.complete = False
                return result
            update = await asyncio.wait_for(updates.get(), timeout=remaining)
            transcription_id = getattr(update, "transcription_id", None)
            if result.transcription_id is not None and transcription_id != result.transcription_id:
                continue

            result.transcription_id = transcription_id or result.transcription_id
            result.text = getattr(update, "text", "") or result.text
            result.pending = bool(getattr(update, "pending", False))
            result.trial_remains = getattr(update, "trial_remains_num", getattr(update, "trial_remains", None))
            result.complete = not result.pending
            result.status = "ok" if result.complete else "pending"
            if result.complete:
                return result
    finally:
        client.remove_event_handler(on_update, Raw(types.UpdateTranscribedAudio))


async def transcribe(
    *,
    account: str,
    chat: str | int,
    message_id: int,
    timeout: float,
    request_timeout: float = 30.0,
    expected_sender_id: int | None = None,
) -> Result:
    settings = load_settings(account)
    apply_runtime_env(settings)
    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)

    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized; refusing interactive login")
        return await transcribe_message(
            client,
            chat=chat,
            message_id=message_id,
            timeout=timeout,
            request_timeout=request_timeout,
            expected_sender_id=expected_sender_id,
        )
    finally:
        await client.disconnect()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Transcribe one Telegram audio message via messages.transcribeAudio"
    )
    parser.add_argument("--account", required=True, help="userbot account profile, e.g. main")
    parser.add_argument("--chat", required=True, help="Telegram chat id or username")
    parser.add_argument("--message-id", required=True, type=int)
    parser.add_argument(
        "--sender-id",
        type=int,
        help="expected original sender id; refuse to transcribe a message from another user",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300,
        help="seconds to wait for the final native transcription (default: 300)",
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=30,
        help="seconds to wait for TranscribeAudioRequest itself (default: 30)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="kept for a stable skill-facing CLI; output is always JSON",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    chat = _peer_arg(args.chat)
    try:
        result = asyncio.run(
            transcribe(
                account=args.account,
                chat=chat,
                message_id=args.message_id,
                timeout=args.timeout,
                request_timeout=args.request_timeout,
                expected_sender_id=args.sender_id,
            )
        )
    except Exception as exc:  # JSON is easier for the guest agent to handle than mixed logs.
        result = Result(
            status="error",
            complete=False,
            chat=chat,
            message_id=args.message_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        print(json.dumps(asdict(result), ensure_ascii=False))
        return 1

    print(json.dumps(asdict(result), ensure_ascii=False))
    return 0 if result.complete else 2


if __name__ == "__main__":
    sys.exit(main())
