from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from telethon import TelegramClient, errors, functions, types

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import apply_runtime_env, load_settings


def register(client: TelegramClient) -> None:
    """Direct CLI helper; retained for the dynamic module loader."""


def _emoji_status_payload(status: Any) -> dict[str, Any] | None:
    if status is None:
        return None
    return {
        "type": type(status).__name__,
        "document_id": getattr(status, "document_id", None),
        "until": getattr(status, "until", None).isoformat() if getattr(status, "until", None) else None,
    }


def profile_payload(user: Any, *, about: str | None = None) -> dict[str, Any]:
    return {
        "id": getattr(user, "id", None),
        "first_name": getattr(user, "first_name", None),
        "last_name": getattr(user, "last_name", None),
        "username": getattr(user, "username", None),
        "about": about,
        "emoji_status": _emoji_status_payload(getattr(user, "emoji_status", None)),
    }


def parse_status_until(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("--emoji-status-until must be ISO-8601, e.g. 2026-08-15T18:00:00+03:00") from exc
    if parsed.tzinfo is None:
        raise ValueError("--emoji-status-until must include an explicit timezone offset")
    if parsed <= datetime.now(parsed.tzinfo):
        raise ValueError("--emoji-status-until must be in the future")
    return parsed


def normalized_username(value: str | None) -> str | None:
    if value is None:
        return None
    username = value.strip().lstrip("@")
    if not username:
        raise ValueError("--username cannot be empty")
    return username


def requested_changes(args: argparse.Namespace) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for field in ("first_name", "last_name", "about"):
        value = getattr(args, field)
        if value is not None:
            changes[field] = value
    username = normalized_username(args.username)
    if username is not None:
        changes["username"] = username
    if args.clear_emoji_status:
        changes["emoji_status"] = {"clear": True}
    elif args.emoji_status_document_id is not None:
        if args.emoji_status_document_id <= 0:
            raise ValueError("--emoji-status-document-id must be positive")
        changes["emoji_status"] = {
            "document_id": args.emoji_status_document_id,
            "until": parse_status_until(args.emoji_status_until).isoformat() if args.emoji_status_until else None,
        }
    elif args.emoji_status_until:
        raise ValueError("--emoji-status-until requires --emoji-status-document-id")
    return changes


async def _call_with_one_flood_retry(client: TelegramClient, request: Any) -> Any:
    for attempt in range(2):
        try:
            return await client(request)
        except errors.FloodWaitError as exc:
            if attempt:
                raise
            await asyncio.sleep(exc.seconds + 1)
    raise RuntimeError("unreachable")


async def get_profile(client: TelegramClient) -> tuple[Any, str | None]:
    me = await client.get_me()
    full = await client(functions.users.GetFullUserRequest(types.InputUserSelf()))
    return me, getattr(getattr(full, "full_user", None), "about", None)


async def resolve_custom_emoji_document(client: TelegramClient, document_id: int) -> dict[str, Any]:
    documents = await client(functions.messages.GetCustomEmojiDocumentsRequest(document_id=[document_id]))
    document = next((item for item in documents if getattr(item, "id", None) == document_id), None)
    if document is None:
        raise ValueError(f"Custom emoji document {document_id} is unavailable to this account")
    return {"id": document.id, "mime_type": getattr(document, "mime_type", None)}


async def run(args: argparse.Namespace) -> dict[str, Any]:
    changes = requested_changes(args)
    settings = load_settings(args.account)
    apply_runtime_env(settings)
    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized; refusing interactive login")
        before, before_about = await get_profile(client)
        emoji_document = None
        if changes.get("emoji_status", {}).get("document_id"):
            emoji_document = await resolve_custom_emoji_document(
                client,
                changes["emoji_status"]["document_id"],
            )
        plan = {
            "ok": True,
            "account": settings.account,
            "dry_run": not args.execute,
            "current": profile_payload(before, about=before_about),
            "requested_changes": changes,
            "custom_emoji_document": emoji_document,
        }
        if not args.execute:
            return plan
        if not changes:
            raise ValueError("No profile change was requested; omit --execute to inspect the profile")

        applied: list[str] = []
        profile_fields = {key: changes[key] for key in ("first_name", "last_name", "about") if key in changes}
        if profile_fields:
            await _call_with_one_flood_retry(client, functions.account.UpdateProfileRequest(**profile_fields))
            applied.append("profile")
        if "username" in changes:
            await _call_with_one_flood_retry(
                client,
                functions.account.UpdateUsernameRequest(username=changes["username"]),
            )
            applied.append("username")
        if "emoji_status" in changes:
            requested_status = changes["emoji_status"]
            if requested_status.get("clear"):
                status: Any = types.EmojiStatusEmpty()
            else:
                status = types.EmojiStatus(
                    document_id=requested_status["document_id"],
                    until=parse_status_until(args.emoji_status_until),
                )
            await _call_with_one_flood_retry(client, functions.account.UpdateEmojiStatusRequest(emoji_status=status))
            applied.append("emoji_status")

        after, after_about = await get_profile(client)
        verification = {
            key: (after_about if key == "about" else getattr(after, key, None)) == value
            for key, value in profile_fields.items()
        }
        if "username" in changes:
            verification["username"] = getattr(after, "username", None) == changes["username"]
        if "emoji_status" in changes:
            current_status = getattr(after, "emoji_status", None)
            expected_id = changes["emoji_status"].get("document_id")
            if changes["emoji_status"].get("clear"):
                verification["emoji_status"] = current_status is None or isinstance(current_status, types.EmojiStatusEmpty)
            else:
                verification["emoji_status"] = getattr(current_status, "document_id", None) == expected_id
        plan.update(
            {
                "dry_run": False,
                "applied": applied,
                "current_after": profile_payload(after, about=after_about),
                "verified": verification,
            }
        )
        return plan
    finally:
        await client.disconnect()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Inspect or update an authorized Telegram account profile")
    result.add_argument("--account", default="main")
    result.add_argument("--first-name")
    result.add_argument("--last-name")
    result.add_argument("--about", help="Profile bio; pass an empty string only when intentionally clearing it")
    result.add_argument("--username", help="Public username without @")
    emoji_group = result.add_mutually_exclusive_group()
    emoji_group.add_argument("--emoji-status-document-id", type=int)
    emoji_group.add_argument("--clear-emoji-status", action="store_true")
    result.add_argument("--emoji-status-until", help="ISO-8601 datetime with timezone offset")
    result.add_argument("--execute", action="store_true", help="Apply the requested profile changes")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        result = asyncio.run(run(args))
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"profile_settings failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
