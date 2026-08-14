from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont
from telethon import TelegramClient, errors, functions, types

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import apply_runtime_env, load_settings

FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Helvetica Neue Bold.ttf",
)
CYRILLIC = str.maketrans(
    "абвгдеёзийклмнопрстуфхцчшщъыьэюяАБВГДЕЁЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ",
    "abvgdeezijklmnoprstufhzcss_y_euaABVGDEEZIJKLMNOPRSTUFHZCSS_Y_EUA",
)


def register(client: TelegramClient) -> None:
    """Direct CLI helper; no event handler is attached."""


def validate_inputs(title: str, text: str, emoji: str) -> None:
    if not title.strip() or len(title) > 64:
        raise ValueError("title must be 1..64 characters")
    if not text.strip() or "\n" in text or len(text) > 32:
        raise ValueError("text must be 1..32 characters on one line")
    if not emoji.strip() or len(emoji) > 16:
        raise ValueError("emoji must be a non-empty Unicode string")


def validate_short_name(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,63}", value):
        raise ValueError("short name must be 5..64 ASCII letters/digits/underscores and start with a letter")
    return value


def _slug(value: str) -> str:
    value = value.translate(CYRILLIC)
    value = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return value or "emoji_pack"


def render_emoji(text: str, output_path: Path) -> dict[str, Any]:
    font_path = next((Path(item) for item in FONT_CANDIDATES if Path(item).exists()), None)
    if font_path is None:
        raise RuntimeError("No readable bold Cyrillic font was found")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    chosen_size = 12
    chosen_box = None
    chosen_stroke = 1
    for size in range(60, 7, -1):
        font = ImageFont.truetype(str(font_path), size=size)
        stroke = max(1, size // 22)
        box = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
        if box[2] - box[0] <= 94 and box[3] - box[1] <= 62:
            chosen_size, chosen_box, chosen_stroke = size, box, stroke
            break
    if chosen_box is None:
        raise ValueError("text does not fit inside the emoji canvas")

    font = ImageFont.truetype(str(font_path), size=chosen_size)
    width = chosen_box[2] - chosen_box[0]
    height = chosen_box[3] - chosen_box[1]
    x = (100 - width) // 2 - chosen_box[0]
    y = (100 - height) // 2 - chosen_box[1]
    draw.text((x, y + 2), text, font=font, fill=(0, 0, 0, 190), stroke_width=chosen_stroke + 1, stroke_fill=(0, 0, 0, 190))
    draw.text((x, y), text, font=font, fill=(255, 225, 0, 255), stroke_width=chosen_stroke, stroke_fill=(18, 18, 18, 255))
    image.save(output_path, format="PNG", optimize=True)
    return {
        "path": str(output_path),
        "width": image.width,
        "height": image.height,
        "font": str(font_path),
        "font_size": chosen_size,
        "text": text,
    }


async def _call_with_one_flood_retry(client: TelegramClient, request: Any) -> Any:
    for attempt in range(2):
        try:
            return await client(request)
        except errors.FloodWaitError as exc:
            if attempt:
                raise
            await asyncio.sleep(exc.seconds + 1)
    raise RuntimeError("unreachable")


def _check_result_is_available(result: Any) -> bool:
    if isinstance(result, bool):
        return result
    return type(result).__name__.lower() in {"booltrue", "true"}


async def choose_short_name(client: TelegramClient, title: str, requested: str | None) -> str:
    if requested:
        short_name = validate_short_name(requested)
        available = await _call_with_one_flood_retry(client, functions.stickers.CheckShortNameRequest(short_name))
        if not _check_result_is_available(available):
            raise ValueError(f"short name is not available: {short_name}")
        return short_name

    suggested = await _call_with_one_flood_retry(client, functions.stickers.SuggestShortNameRequest(title))
    suggested_name = getattr(suggested, "short_name", suggested)
    base = validate_short_name(str(suggested_name))
    for attempt in range(6):
        candidate = base if attempt == 0 else f"{base[:54]}_{int(time.time()) % 1000000:06d}{attempt}"
        candidate = validate_short_name(candidate[:64].rstrip("_"))
        available = await _call_with_one_flood_retry(client, functions.stickers.CheckShortNameRequest(candidate))
        if _check_result_is_available(available):
            return candidate
    raise RuntimeError("Telegram did not offer an available short name")


async def prepare_plan(
    client: TelegramClient,
    *,
    title: str,
    text: str,
    emoji: str,
    requested_short_name: str | None,
    data_dir: str,
) -> dict[str, Any]:
    validate_inputs(title, text, emoji)
    me = await client.get_me()
    short_name = await choose_short_name(client, title, requested_short_name)
    image_path = Path(data_dir) / "emoji_packs" / f"{short_name}.png"
    render_info = render_emoji(text, image_path)
    return {
        "title": title,
        "short_name": short_name,
        "emoji": emoji,
        "text": text,
        "owner_id": getattr(me, "id", None),
        "owner_username": getattr(me, "username", None),
        "image": render_info,
        "emojis": True,
    }


async def create_pack(client: TelegramClient, plan: dict[str, Any]) -> dict[str, Any]:
    uploaded_file = await client.upload_file(plan["image"]["path"])
    uploaded = await _call_with_one_flood_retry(
        client,
        functions.messages.UploadMediaRequest(
            peer=types.InputPeerSelf(),
            media=types.InputMediaUploadedDocument(
                file=uploaded_file,
                mime_type="image/png",
                attributes=[types.DocumentAttributeFilename(Path(plan["image"]["path"]).name)],
            ),
        ),
    )
    document = getattr(uploaded, "document", None)
    if not isinstance(document, types.Document):
        raise RuntimeError("Telegram did not return an uploaded document")
    input_document = types.InputDocument(
        id=document.id,
        access_hash=document.access_hash,
        file_reference=document.file_reference,
    )
    sticker = types.InputStickerSetItem(document=input_document, emoji=plan["emoji"])
    created = await _call_with_one_flood_retry(
        client,
        functions.stickers.CreateStickerSetRequest(
            user_id=types.InputUserSelf(),
            title=plan["title"],
            short_name=plan["short_name"],
            stickers=[sticker],
            emojis=True,
        ),
    )

    final = await _call_with_one_flood_retry(
        client,
        functions.messages.GetStickerSetRequest(
            stickerset=types.InputStickerSetShortName(plan["short_name"]),
            hash=0,
        ),
    )
    sticker_set = getattr(final, "set", None)
    packs = getattr(final, "packs", []) or []
    document_ids = {getattr(item, "id", None) for item in getattr(final, "documents", []) or []}
    mapped_ids = {
        document_id
        for item in packs
        if getattr(item, "emoticon", None) == plan["emoji"]
        for document_id in (getattr(item, "documents", []) or [])
    }
    readback_document_id = next(iter(mapped_ids & document_ids), None)
    pack_match = readback_document_id is not None
    verification = {
        "title": getattr(sticker_set, "title", None) == plan["title"],
        "short_name": getattr(sticker_set, "short_name", None) == plan["short_name"],
        "count": getattr(sticker_set, "count", None) == 1,
        "emojis": getattr(sticker_set, "emojis", None) is True,
        "document_present": readback_document_id is not None,
        "emoji_mapping": pack_match,
    }
    if not all(verification.values()):
        raise RuntimeError(f"Emoji pack read-back mismatch: {verification}")
    return {
        "created": True,
        "verified": True,
        "pack": {
            "title": plan["title"],
            "short_name": plan["short_name"],
            "count": getattr(sticker_set, "count", None),
            "emoji": plan["emoji"],
            "text": plan["text"],
            "document_id": readback_document_id,
            "link": f"https://t.me/addemoji/{plan['short_name']}",
        },
        "verification": verification,
        "created_type": type(created).__name__,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    settings = load_settings(args.account)
    apply_runtime_env(settings)
    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized; refusing interactive login")
        plan = await prepare_plan(
            client,
            title=args.title,
            text=args.text,
            emoji=args.emoji,
            requested_short_name=args.short_name,
            data_dir=settings.data_dir,
        )
        result: dict[str, Any] = {"ok": True, "dry_run": not args.execute, "plan": plan}
        if not args.execute:
            return result
        result.update(await create_pack(client, plan))
        result["dry_run"] = False
        return result
    finally:
        await client.disconnect()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Create one Telegram custom emoji pack with a readable text emoji")
    result.add_argument("--account", default="main")
    result.add_argument("--title", default="текст пак")
    result.add_argument("--text", default="ЖИРНЫЙ")
    result.add_argument("--emoji", default="💪")
    result.add_argument("--short-name", help="Frozen Telegram short name from a reviewed dry-run")
    result.add_argument("--execute", action="store_true", help="Create the pack; default is dry-run")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        result = asyncio.run(run(args))
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"create_emoji_pack failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
