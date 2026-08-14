from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import re
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


def validate_subdir(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
        raise ValueError("--output-subdir must be a simple safe directory name")
    return value


def media_kind(message: Any) -> str:
    if getattr(message, "voice", False):
        return "voice"
    if getattr(message, "video_note", False):
        return "video_note"
    if getattr(message, "photo", None):
        return "photo"
    if getattr(message, "video", False):
        return "video"
    if getattr(message, "audio", False):
        return "audio"
    if getattr(message, "document", None):
        return "document"
    return "unknown_media"


def media_extension(message: Any) -> str:
    file_info = getattr(message, "file", None)
    extension = getattr(file_info, "ext", None)
    if extension:
        return extension if extension.startswith(".") else f".{extension}"
    name = getattr(file_info, "name", None)
    if name and Path(name).suffix:
        return Path(name).suffix.lower()
    mime_type = getattr(file_info, "mime_type", None)
    return mimetypes.guess_extension(mime_type or "") or ""


def media_payload(message: Any, destination: Path) -> dict[str, Any]:
    file_info = getattr(message, "file", None)
    return {
        "message_id": getattr(message, "id", None),
        "kind": media_kind(message),
        "original_name": getattr(file_info, "name", None),
        "mime_type": getattr(file_info, "mime_type", None),
        "size": getattr(file_info, "size", None),
        "destination": str(destination),
        "destination_exists": destination.exists(),
    }


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else [value]


async def _download_with_one_flood_retry(client: TelegramClient, message: Any, destination: Path) -> str:
    for attempt in range(2):
        try:
            result = await client.download_media(message, file=str(destination))
            if not result:
                raise RuntimeError(f"Telegram returned no output path for message {message.id}")
            return str(result)
        except errors.FloodWaitError as exc:
            if attempt:
                raise
            await asyncio.sleep(exc.seconds + 1)
    raise RuntimeError("unreachable")


async def run(args: argparse.Namespace) -> dict[str, Any]:
    message_ids = parse_message_ids(args.message_ids)
    subdir = validate_subdir(args.output_subdir)
    settings = load_settings(args.account)
    apply_runtime_env(settings)
    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized; refusing interactive login")
        chat = await resolve_entity(client, args.chat)
        messages = _as_list(await client.get_messages(chat, ids=message_ids))
        by_id = {getattr(message, "id", None): message for message in messages if message is not None}
        missing_ids = [message_id for message_id in message_ids if message_id not in by_id]
        if missing_ids:
            raise ValueError(f"Messages were not found in the resolved chat: {missing_ids}")
        media_free = [message_id for message_id in message_ids if not getattr(by_id[message_id], "media", None)]
        if media_free:
            raise ValueError(f"Messages do not contain downloadable media: {media_free}")

        output_dir = Path(settings.data_dir) / "downloads" / subdir
        targets = {
            message_id: output_dir / f"message_{message_id}{media_extension(by_id[message_id])}"
            for message_id in message_ids
        }
        plan = {
            "ok": True,
            "dry_run": not args.execute,
            "chat": entity_payload(chat, input_value=args.chat),
            "output_dir": str(output_dir),
            "overwrite": args.overwrite,
            "media": [media_payload(by_id[message_id], targets[message_id]) for message_id in message_ids],
        }
        if not args.execute:
            return plan

        existing = [str(target) for target in targets.values() if target.exists()]
        if existing and not args.overwrite:
            raise FileExistsError(f"Refusing to overwrite existing files; pass --overwrite after inspection: {existing}")
        output_dir.mkdir(parents=True, exist_ok=True)
        downloaded: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        for message_id in message_ids:
            target = targets[message_id]
            try:
                returned_path = await _download_with_one_flood_retry(client, by_id[message_id], target)
                actual = Path(returned_path)
                verified = actual.is_file() and actual.stat().st_size > 0
                downloaded.append(
                    {
                        "message_id": message_id,
                        "path": str(actual),
                        "bytes": actual.stat().st_size if actual.is_file() else 0,
                        "verified": verified,
                    }
                )
            except Exception as exc:
                failures.append({"message_id": message_id, "error": f"{type(exc).__name__}: {exc}"})
        plan.update(
            {
                "dry_run": False,
                "downloaded": downloaded,
                "failure_count": len(failures),
                "failures": failures,
                "verified": bool(downloaded) and not failures and all(item["verified"] for item in downloaded),
            }
        )
        return plan
    finally:
        await client.disconnect()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Download media from exact Telegram message IDs after a dry-run preview")
    result.add_argument("--account", default="main")
    result.add_argument("--chat", required=True)
    result.add_argument("--message-ids", required=True, help="Comma-separated positive media message IDs")
    result.add_argument("--output-subdir", default="default", help="Safe name under runtime/<account>/data/downloads")
    result.add_argument("--overwrite", action="store_true")
    result.add_argument("--execute", action="store_true", help="Write media files locally after inspection")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        result = asyncio.run(run(args))
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"download_media failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
