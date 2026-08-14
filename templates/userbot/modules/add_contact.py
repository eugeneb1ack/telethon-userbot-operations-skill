from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

from telethon import TelegramClient, events, functions
from telethon.tl.types import User

logger = logging.getLogger(__name__)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _ensure_project_on_path() -> None:
    root = str(_project_root())
    if root not in sys.path:
        sys.path.insert(0, root)


def _display_name(user: User) -> str:
    return " ".join(part for part in [user.first_name, user.last_name] if part).strip() or (
        f"@{user.username}" if user.username else str(user.id)
    )


def _split_name(entity: User, first_name: str | None, last_name: str | None) -> tuple[str, str]:
    first = (first_name or entity.first_name or entity.username or str(entity.id) or "Contact").strip()
    last = (last_name if last_name is not None else (entity.last_name or "")).strip()
    if not first:
        first = entity.username or str(entity.id) or "Contact"
    return first, last


def _mask_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) <= 4:
        return "***"
    return f"***{digits[-4:]}"


def _safe_user_payload(entity: User, *, first_name: str, last_name: str, phone: str, share_my_phone: bool) -> dict[str, Any]:
    return {
        "user_id": entity.id,
        "display_name": _display_name(entity),
        "username": entity.username,
        "contact_first_name": first_name,
        "contact_last_name": last_name,
        "target_phone_masked": _mask_phone(phone),
        "share_my_phone": bool(share_my_phone),
    }


async def add_contact(
    client: TelegramClient,
    *,
    user: str,
    first_name: str | None = None,
    last_name: str | None = None,
    phone: str = "",
    share_my_phone: bool = False,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Add a Telegram user to contacts via contacts.AddContactRequest.

    Telethon TL docs: https://tl.telethon.dev/methods/contacts/add_contact.html

    `phone` is the target contact phone, not our own account phone. By default
    `share_my_phone` is false, so `add_phone_privacy_exception` is false and the
    added user is not granted a phone-privacy exception for our account.
    """

    entity = await client.get_entity(user)
    if not isinstance(entity, User):
        raise ValueError(f"Entity is not a user: {user!r}")
    if entity.bot:
        raise ValueError(f"Refusing to add bot account as contact: {user!r}")
    if entity.is_self:
        raise ValueError("Refusing to add self as contact")

    contact_first, contact_last = _split_name(entity, first_name, last_name)
    payload = _safe_user_payload(
        entity,
        first_name=contact_first,
        last_name=contact_last,
        phone=phone,
        share_my_phone=share_my_phone,
    )

    if dry_run:
        return {"ok": True, "dry_run": True, "contact": payload}

    result = await client(
        functions.contacts.AddContactRequest(
            id=entity,
            first_name=contact_first,
            last_name=contact_last,
            phone=phone or "",
            add_phone_privacy_exception=bool(share_my_phone),
        )
    )
    return {
        "ok": True,
        "dry_run": False,
        "contact": payload,
        "result_type": type(result).__name__,
    }


def register(client: TelegramClient) -> None:
    @client.on(events.NewMessage(outgoing=True, pattern=r"^\.addcontact(?:\s+(.+))?$"))
    async def add_contact_handler(event: events.NewMessage.Event) -> None:
        raw = (event.pattern_match.group(1) or "").strip()
        if not raw:
            await event.reply("Usage: .addcontact @username")
            return
        try:
            result = await add_contact(client, user=raw, share_my_phone=False, dry_run=False)
            contact = result["contact"]
            username = f"@{contact['username']}" if contact.get("username") else "no username"
            await event.reply(
                f"Added contact: {contact['display_name']} ({username}). "
                "Phone privacy exception: off."
            )
        except Exception as exc:
            logger.exception("Failed to add contact")
            await event.reply(f"Failed to add contact: {type(exc).__name__}: {exc}")


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Add a Telegram user to contacts without exposing own phone by default")
    parser.add_argument("--account", default="main", help="Account profile from accounts/<name>.env")
    parser.add_argument("--user", required=True, help="Username, user id, phone, or other Telethon entity-like value")
    parser.add_argument("--first-name", help="Contact first name override")
    parser.add_argument("--last-name", help="Contact last name override")
    parser.add_argument("--phone", default="", help="Target contact phone if known; masked in output")
    parser.add_argument("--share-my-phone", action="store_true", help="Grant phone privacy exception to the added user")
    parser.add_argument("--dry-run", action="store_true", help="Resolve entity but do not modify contacts (the default)")
    parser.add_argument("--execute", action="store_true", help="Actually modify Telegram contacts")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Output JSON")
    args = parser.parse_args()
    if args.dry_run and args.execute:
        parser.error("--dry-run and --execute are mutually exclusive")

    _ensure_project_on_path()
    from core.config import apply_runtime_env, load_settings

    settings = load_settings(args.account)
    apply_runtime_env(settings)

    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise SystemExit("Account is not authorized; start the userbot once to login")
        result = await add_contact(
            client,
            user=args.user,
            first_name=args.first_name,
            last_name=args.last_name,
            phone=args.phone,
            share_my_phone=args.share_my_phone,
            dry_run=not args.execute,
        )
        if args.json_output:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            contact = result["contact"]
            username = f"@{contact['username']}" if contact.get("username") else "no username"
            action = "Would add" if result.get("dry_run") else "Added"
            print(f"{action}: {contact['display_name']} ({username}) id={contact['user_id']}")
            print(f"share_my_phone={contact['share_my_phone']}")
            if contact.get("target_phone_masked"):
                print(f"target_phone={contact['target_phone_masked']}")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(_main())
