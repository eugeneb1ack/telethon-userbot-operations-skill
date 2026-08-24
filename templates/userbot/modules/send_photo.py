from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError
from telethon import TelegramClient, errors

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config import apply_runtime_env, load_settings
from core.telegram_targets import entity_payload, resolve_entity


def register(client: TelegramClient) -> None:
    """Direct CLI helper; no event handler is attached."""


def photo_path(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError("--photo must be a non-empty regular file")
    if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise ValueError("--photo must be a JPG, PNG, or WebP image")
    try:
        with Image.open(path) as image:
            if image.format not in {"JPEG", "PNG", "WEBP"}:
                raise ValueError("--photo content must be JPEG, PNG, or WebP")
            image.verify()
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError("--photo is not a valid readable image") from exc
    return path


def photo_metadata(path: Path) -> dict[str, Any]:
    with Image.open(path) as image:
        return {
            "name": path.name,
            "bytes": path.stat().st_size,
            "format": image.format,
            "width": image.width,
            "height": image.height,
        }


async def _send_once(client: Any, target: Any, photo: Path, caption: str) -> Any:
    for attempt in range(2):
        try:
            return await client.send_file(target, str(photo), caption=caption or None, force_document=False)
        except errors.FloodWaitError as exc:
            if attempt:
                raise
            await asyncio.sleep(exc.seconds + 1)
    raise RuntimeError("unreachable")


async def run(args: argparse.Namespace) -> dict[str, Any]:
    photo = photo_path(args.photo)
    settings = load_settings(args.account)
    apply_runtime_env(settings)
    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized; refusing interactive login")
        target = await resolve_entity(client, args.chat)
        plan: dict[str, Any] = {
            "ok": True,
            "dry_run": not args.execute,
            "status": "dry_run" if not args.execute else "pending",
            "target": entity_payload(target, input_value=args.chat),
            "photo": photo_metadata(photo),
            "caption": args.caption,
        }
        if not args.execute:
            return plan
        sent = await _send_once(client, target, photo, args.caption)
        verified = await client.get_messages(target, ids=sent.id)
        if getattr(verified, "id", None) != sent.id:
            raise RuntimeError("Telegram did not return the sent photo during read-back")
        if not getattr(verified, "photo", None):
            raise RuntimeError("Telegram did not render the sent media as a photo")
        if (getattr(verified, "message", None) or "") != args.caption:
            raise RuntimeError("Telegram rendered a different photo caption")
        plan.update(
            {
                "dry_run": False,
                "status": "sent",
                "message_id": int(sent.id),
                "verified": True,
            }
        )
        return plan
    finally:
        await client.disconnect()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Send one local photo to an exact Telegram chat")
    result.add_argument("--account", required=True)
    result.add_argument("--chat", required=True)
    result.add_argument("--photo", required=True)
    result.add_argument("--caption", default="")
    result.add_argument("--execute", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        print(json.dumps(asyncio.run(run(args)), ensure_ascii=False, indent=2))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"send_photo failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
