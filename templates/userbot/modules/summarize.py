from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, List

import aiohttp
import speech_recognition as sr
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.tl.custom.message import Message

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_OPENROUTER_MODEL = "deepseek/deepseek-chat"
MAX_LIMIT = 1000
DEFAULT_BATCH_SIZE = 100


@dataclass
class SummaryMessage:
    msg_id: int
    sender_id: int | None
    sender_name: str
    text: str
    reply_to_msg_id: int | None
    reply_to_sender_id: int | None
    reply_to_sender_name: str | None
    timestamp: str


def _safe_sender_name(sender: Any) -> str:
    if sender is None:
        return "Unknown"

    first_name = getattr(sender, "first_name", None)
    last_name = getattr(sender, "last_name", None)
    username = getattr(sender, "username", None)
    title = getattr(sender, "title", None)

    full_name = " ".join(part for part in [first_name, last_name] if part).strip()
    if full_name:
        return full_name
    if username:
        return f"@{username}"
    if title:
        return title

    sender_id = getattr(sender, "id", None)
    return f"id:{sender_id}" if sender_id is not None else "Unknown"


def _to_iso(dt: datetime | None) -> str:
    if dt is None:
        return datetime.now(timezone.utc).isoformat()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _safe_unlink(path: Path | None) -> None:
    if not path:
        return
    try:
        path.unlink(missing_ok=True)
    except Exception:
        logger.warning("Failed to delete temporary media file: %s", path, exc_info=True)


def _transcribe_wav_with_google_web_speech(wav_path: Path, *, language: str) -> str | None:
    recognizer = sr.Recognizer()
    with sr.AudioFile(str(wav_path)) as source:
        audio_data = recognizer.record(source)

    # Uses Google Web Speech endpoint via SpeechRecognition.
    # key=None keeps it unauthenticated/free for light personal usage.
    text = recognizer.recognize_google(audio_data, key=None, language=language)
    return text.strip() or None


async def _google_speech_to_text_from_wav(wav_path: Path, *, language: str) -> str | None:
    try:
        return await asyncio.to_thread(
            _transcribe_wav_with_google_web_speech,
            wav_path,
            language=language,
        )
    except sr.UnknownValueError:
        return None
    except sr.RequestError as exc:
        logger.warning("Google Web Speech request failed: %s", exc)
        return None
    except Exception:
        logger.exception("Google Web Speech transcription failed")
        return None


async def _extract_media_text_google_stt(client: TelegramClient, msg: Message) -> str | None:
    """
    Transcribes voice messages and video notes with free unauthenticated
    Google Web Speech (via SpeechRecognition).
    """
    is_voice = bool(getattr(msg, "voice", None))
    is_video_note = bool(getattr(msg, "video_note", None))
    if not (is_voice or is_video_note):
        return None

    media_kind = "видео-заметки" if is_video_note else "голосового сообщения"
    print(f"[#] Расшифровка {media_kind} (ID: {msg.id})...")

    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        return f"[{media_kind}: ffmpeg не найден]"

    language = os.getenv("GOOGLE_SPEECH_LANGUAGE", "ru-RU")

    with tempfile.TemporaryDirectory(prefix="summarize_media_") as tmp_dir:
        src_path_str = await client.download_media(msg, file=tmp_dir)
        src_path = Path(src_path_str) if src_path_str else None
        if not src_path:
            print(f"[!] Ошибка скачивания медиа для ID {msg.id}")
            return "[ошибка скачивания]"

        wav_path = Path(tmp_dir) / "speech_input.wav"

        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                [ffmpeg_bin, "-y", "-i", str(src_path), "-vn", "-ac", "1", "-ar", "16000", str(wav_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=120,
            )
            if proc.returncode != 0:
                return "[ошибка подготовки аудио]"

            text = await _google_speech_to_text_from_wav(wav_path, language=language)

            _safe_unlink(wav_path)
            _safe_unlink(src_path)

            if text:
                print(f"[+] Расшифровано (ID {msg.id}): {text[:50]}...")
                return text

            print(f"[-] Не удалось распознать речь в ID {msg.id}")
            return "[пустая расшифровка]"
        except Exception as exc:
            print(f"[!] Критическая ошибка транскрипции ID {msg.id}: {exc}")
            return "[ошибка расшифровки]"
        finally:
            _safe_unlink(wav_path)
            _safe_unlink(src_path)


async def fetch_messages(client: TelegramClient, entity: Any, limit: int = 100) -> List[SummaryMessage]:
    """
    Fetch chat messages in batches to reduce flood risk.
    """
    if limit <= 0:
        return []
    limit = min(limit, MAX_LIMIT)

    print(f"[*] Сбор последних {limit} сообщений...")
    batch_size = min(DEFAULT_BATCH_SIZE, limit)
    remaining = limit
    offset_id = 0

    collected: list[Message] = []

    while remaining > 0:
        current_batch = min(batch_size, remaining)
        batch: list[Message] = []

        async for msg in client.iter_messages(entity, limit=current_batch, offset_id=offset_id):
            if msg is None:
                continue
            batch.append(msg)

        if not batch:
            break

        collected.extend(batch)
        remaining -= len(batch)
        offset_id = min(m.id for m in batch if getattr(m, "id", None) is not None)

        if len(batch) < current_batch:
            break

    print(f"[+] Собрано {len(collected)} сообщений. Начинаю обработку...")
    # Oldest -> newest so reply chains are easier to read
    collected.sort(key=lambda m: m.id)

    interim: list[SummaryMessage] = []
    msg_sender_map: dict[int, tuple[int | None, str]] = {}

    for msg in collected:
        sender = await msg.get_sender()
        if bool(getattr(sender, "bot", False)):
            continue

        sender_id = getattr(sender, "id", None)
        sender_name = _safe_sender_name(sender)

        text = (msg.message or "").strip()
        if not text and msg.media:
            media_text = await _extract_media_text_google_stt(client, msg)
            if media_text:
                text = media_text
            else:
                text = "[медиа без описания]"

        reply_to_msg_id = msg.reply_to_msg_id

        item = SummaryMessage(
            msg_id=msg.id,
            sender_id=sender_id,
            sender_name=sender_name,
            text=text,
            reply_to_msg_id=reply_to_msg_id,
            reply_to_sender_id=None,
            reply_to_sender_name=None,
            timestamp=_to_iso(msg.date),
        )
        interim.append(item)
        msg_sender_map[msg.id] = (sender_id, sender_name)

    # Resolve "who replies to whom" inside fetched window
    for row in interim:
        if row.reply_to_msg_id and row.reply_to_msg_id in msg_sender_map:
            reply_sender_id, reply_sender_name = msg_sender_map[row.reply_to_msg_id]
            row.reply_to_sender_id = reply_sender_id
            row.reply_to_sender_name = reply_sender_name

    print("[+] Обработка сообщений завершена.")
    return interim



def _rows_to_payload(rows: Iterable[SummaryMessage]) -> list[dict[str, Any]]:
    return [
        {
            "sender_id": row.sender_id,
            "sender_name": row.sender_name,
            "text": row.text,
            "reply_to_msg_id": row.reply_to_msg_id,
            "reply_to_sender_id": row.reply_to_sender_id,
            "reply_to_sender_name": row.reply_to_sender_name,
            "timestamp": row.timestamp,
        }
        for row in rows
    ]


def _to_json(rows: Iterable[SummaryMessage]) -> str:
    payload = _rows_to_payload(rows)
    return json.dumps(payload, ensure_ascii=False)


def _payload_to_tsv(payload: Iterable[dict[str, Any]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, delimiter="\t", lineterminator="\n")
    writer.writerow(
        [
            "sender_id",
            "sender_name",
            "text",
            "reply_to_msg_id",
            "reply_to_sender_id",
            "reply_to_sender_name",
            "timestamp",
        ]
    )
    for row in payload:
        writer.writerow(
            [
                row.get("sender_id") if row.get("sender_id") is not None else "",
                row.get("sender_name") or "",
                row.get("text") or "",
                row.get("reply_to_msg_id") if row.get("reply_to_msg_id") is not None else "",
                row.get("reply_to_sender_id") if row.get("reply_to_sender_id") is not None else "",
                row.get("reply_to_sender_name") or "",
                row.get("timestamp") or "",
            ]
        )
    return out.getvalue().rstrip("\n")


def _to_tsv(rows: Iterable[SummaryMessage]) -> str:
    return _payload_to_tsv(_rows_to_payload(rows))


def _archive_transcript_payload(chat: str, payload: list[dict[str, Any]]) -> Path:
    project_root = Path(__file__).resolve().parent.parent
    transcripts_dir = Path(
        os.getenv("USERBOT_TRANSCRIPTS_DIR", project_root / "data" / "transcripts")
    )
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    safe_chat = re.sub(r"[^0-9A-Za-z_-]+", "_", str(chat)).strip("_") or "chat"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_path = transcripts_dir / f"{safe_chat}_{stamp}.json"
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


async def prepare_summary_payload(
    client: TelegramClient,
    chat: str,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    # Ensure env is loaded before transcription.
    project_root = Path(__file__).resolve().parent.parent
    env_path = Path(os.getenv("USERBOT_ENV_FILE", project_root / ".env"))
    load_dotenv(env_path, override=False)

    entity = await client.get_entity(chat)
    rows = await fetch_messages(client, entity, limit=limit)
    return _rows_to_payload(rows)


async def prepare_summary_data(
    client: TelegramClient,
    chat: str,
    *,
    limit: int = 100,
    as_json: bool = False,
) -> str:
    payload = await prepare_summary_payload(client, chat, limit=limit)
    return json.dumps(payload, ensure_ascii=False) if as_json else _payload_to_tsv(payload)


def _load_openrouter_credentials() -> tuple[str | None, str]:
    project_root = Path(__file__).resolve().parent.parent
    env_path = Path(os.getenv("USERBOT_ENV_FILE", project_root / ".env"))
    load_dotenv(env_path, override=False)

    api_key = os.getenv("Open_Router_Key")
    model = os.getenv("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL)
    return api_key, model


async def generate_summary(cleaned_data: str) -> str:
    api_key, model = _load_openrouter_credentials()

    if not api_key:
        return "Не найден Open_Router_Key в активном env-файле. Добавьте ключ OpenRouter и повторите."

    if not cleaned_data or not cleaned_data.strip():
        return "Недостаточно данных для суммаризации."

    print(f"[*] Отправка данных в OpenRouter (модель: {model})...")
    system_prompt = (
        "You are an expert conversation analyst. Produce a concise, high-quality summary "
        "of a chat log. Focus on: (1) key events and decisions, (2) emotional tone and "
        "relationship dynamics, (3) notable quotes or standout phrases. "
        "If confidence is low due to sparse/noisy data, state that clearly."
    )

    user_prompt = (
        "Summarize this chat transcript data.\n\n"
        "Output format:\n"
        "1) Key events (bullet points)\n"
        "2) Emotional tone\n"
        "3) Notable quotes (up to 3, preserve original language)\n"
        "4) One-sentence overall takeaway\n\n"
        "Chat data:\n"
        f"{cleaned_data}"
    )

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    timeout = aiohttp.ClientTimeout(total=90)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(OPENROUTER_URL, headers=headers, json=payload) as response:
                body_text = await response.text()

                if response.status != 200:
                    logger.warning("OpenRouter HTTP %s: %s", response.status, body_text[:800])
                    return (
                        f"Ошибка OpenRouter (HTTP {response.status}). "
                        "Проверьте ключ, модель и лимиты аккаунта."
                    )

                try:
                    data = json.loads(body_text)
                except json.JSONDecodeError:
                    logger.warning("OpenRouter returned non-JSON body: %s", body_text[:800])
                    return "OpenRouter вернул некорректный ответ (не JSON)."

                choices = data.get("choices") or []
                if not choices:
                    logger.warning("OpenRouter response has no choices: %s", data)
                    return "OpenRouter не вернул вариант ответа (choices пуст)."

                message = choices[0].get("message") or {}
                content = message.get("content")

                if isinstance(content, list):
                    merged = []
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text_part = part.get("text")
                            if text_part:
                                merged.append(text_part)
                    content = "\n".join(merged).strip() if merged else None

                if not isinstance(content, str) or not content.strip():
                    logger.warning("OpenRouter response missing text content: %s", data)
                    return "OpenRouter вернул пустой ответ."

                return content.strip()
    except asyncio.TimeoutError:
        logger.exception("OpenRouter request timed out")
        return "Таймаут запроса к OpenRouter. Попробуйте еще раз позже."
    except aiohttp.ClientError:
        logger.exception("OpenRouter client/network error")
        return "Сетевая ошибка при обращении к OpenRouter."
    except Exception:
        logger.exception("Unexpected error during OpenRouter summary generation")
        return "Неожиданная ошибка при генерации суммаризации. Проверьте логи."


def register(client: TelegramClient) -> None:
    @client.on(events.NewMessage(outgoing=True, pattern=r"^\.summarize(?:\s+(.+))?$"))
    async def summarize_handler(event: events.NewMessage.Event) -> None:
        if not event.raw_text:
            return

        parts = event.raw_text.strip().split()
        if len(parts) < 2:
            await event.reply(
                "Использование: `.summarize <chat_id|username> [limit] [--json]`\n"
                "Пример: `.summarize @mychat 200 --json`"
            )
            return

        chat = parts[1]
        limit = 100
        as_json = "--json" in parts

        for p in parts[2:]:
            if p.startswith("--"):
                continue
            if p.isdigit():
                limit = int(p)
                break

        try:
            summary_payload = await prepare_summary_payload(client, chat, limit=limit)
            archive_path = _archive_transcript_payload(chat, summary_payload)

            prepared = json.dumps(summary_payload, ensure_ascii=False) if as_json else _payload_to_tsv(summary_payload)
            summary = await generate_summary(prepared)

            # Telegram message length cap safety.
            if len(summary) > 3500:
                summary = summary[:3500] + "\n... [truncated]"

            notice = (
                "\n\n✅ Медиа-файлы (voice/video + WAV) очищаются сразу после транскрипции."
                f"\n🗂 Транскрипт архивирован локально: `{archive_path.as_posix()}`"
            )
            await event.reply(summary + notice)
        except Exception:
            logger.exception("Ошибка в summarize handler")
            await event.reply("Не удалось выполнить суммаризацию. Проверьте логи.")


async def _verification_run(
    chat: str,
    *,
    limit: int = 100,
    json_output: bool = False,
    do_summary: bool = False,
    account: str | None = None,
) -> None:
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from core.config import apply_runtime_env, load_settings

    settings = load_settings(account=account)
    apply_runtime_env(settings)
    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)

    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized; refusing interactive login")
        summary_payload = await prepare_summary_payload(client, chat, limit=limit)
        archive_path = _archive_transcript_payload(chat, summary_payload)

        prepared = json.dumps(summary_payload, ensure_ascii=False) if json_output else _payload_to_tsv(summary_payload)

        if do_summary:
            print(await generate_summary(prepared))
            print(f"\n--- Archive: {archive_path} ---")
        else:
            print(prepared)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Prepare chat messages and optionally generate final summary via OpenRouter DeepSeek",
    )
    parser.add_argument("--chat", required=True, help="Chat id or username, e.g. @mychat or -100123")
    parser.add_argument("--limit", type=int, default=100, help="How many recent messages to fetch (max 1000)")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Output JSON for prepared data mode")
    parser.add_argument(
        "--do-summary",
        action="store_true",
        dest="do_summary",
        help="Call OpenRouter DeepSeek and output final summary instead of raw prepared data",
    )
    parser.add_argument(
        "--account",
        help="Имя аккаунта из accounts/<name>.env",
    )
    args = parser.parse_args()

    try:
        asyncio.run(
            _verification_run(
                args.chat,
                limit=args.limit,
                json_output=args.json_output,
                do_summary=args.do_summary,
                account=args.account,
            )
        )
    except KeyboardInterrupt:
        print("Остановлено пользователем.")
    except Exception as exc:
        logger.exception("Verification run failed")
        raise SystemExit(f"Ошибка во время проверки модуля summarize: {exc}")
