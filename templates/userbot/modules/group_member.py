from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from telethon import TelegramClient, errors
from telethon.tl.types import Channel, Chat, User

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import apply_runtime_env, load_settings
from core.telegram_targets import entity_payload, parse_peer, resolve_entity

ADMIN_RIGHTS = (
    "change_info",
    "post_messages",
    "edit_messages",
    "delete_messages",
    "ban_users",
    "invite_users",
    "pin_messages",
    "add_admins",
    "manage_call",
    "anonymous",
)
PERMISSION_RIGHTS = (
    "view_messages",
    "send_messages",
    "send_media",
    "send_stickers",
    "send_gifs",
    "send_games",
    "send_inline",
    "embed_link_previews",
    "send_polls",
    "change_info",
    "invite_users",
    "pin_messages",
)
ACTIONS = ("inspect", "grant-admin", "revoke-admin", "restrict", "unrestrict", "kick")


def register(client: TelegramClient) -> None:
    """Direct CLI helper; retained for the dynamic module loader."""


def parse_right_names(value: str | None, allowed: tuple[str, ...], label: str) -> tuple[str, ...]:
    if not value:
        return ()
    names = tuple(item.strip() for item in value.split(",") if item.strip())
    if len(names) != len(set(names)):
        raise ValueError(f"--{label} contains duplicate names")
    unknown = sorted(set(names).difference(allowed))
    if unknown:
        raise ValueError(f"Unknown --{label} values: {', '.join(unknown)}; allowed: {', '.join(allowed)}")
    return names


def admin_kwargs(rights: tuple[str, ...]) -> dict[str, bool]:
    return {name: name in rights for name in ADMIN_RIGHTS}


def restriction_kwargs(denied: tuple[str, ...]) -> dict[str, bool]:
    return {name: name not in denied for name in PERMISSION_RIGHTS}


def validate_args(args: argparse.Namespace) -> tuple[tuple[str, ...], tuple[str, ...]]:
    rights = parse_right_names(args.rights, ADMIN_RIGHTS, "rights")
    denied = parse_right_names(args.deny, PERMISSION_RIGHTS, "deny")
    if args.action == "grant-admin" and not rights:
        raise ValueError("grant-admin requires --rights")
    if args.action != "grant-admin" and rights:
        raise ValueError("--rights is only valid with grant-admin")
    if args.rank and args.action != "grant-admin":
        raise ValueError("--rank is only valid with grant-admin")
    if args.action == "restrict":
        if not denied:
            raise ValueError("restrict requires --deny")
        if args.until_hours is None:
            raise ValueError("restrict requires bounded --until-hours")
    elif denied:
        raise ValueError("--deny is only valid with restrict")
    if args.until_hours is not None and args.action != "restrict":
        raise ValueError("--until-hours is only valid with restrict")
    if args.until_hours is not None and not 1 <= args.until_hours <= 8760:
        raise ValueError("--until-hours must be between 1 and 8760")
    if args.action == "inspect" and args.execute:
        raise ValueError("inspect is read-only; remove --execute")
    if args.action != "inspect" and not args.execute:
        # This is intentional: output still is the dry-run plan.
        pass
    return rights, denied


def permissions_payload(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    fields = (
        "is_admin",
        "is_creator",
        "is_banned",
        "has_default_permissions",
        "until_date",
        *ADMIN_RIGHTS,
        *PERMISSION_RIGHTS,
    )
    result = {"type": type(value).__name__}
    for field in fields:
        if hasattr(value, field):
            raw = getattr(value, field)
            result[field] = raw.isoformat() if isinstance(raw, datetime) else raw
    return result


async def resolve_member(client: TelegramClient, group: Any, value: str) -> User:
    try:
        member = await resolve_entity(client, value)
    except ValueError:
        peer = parse_peer(value)
        if not isinstance(peer, int):
            raise
        async for participant in client.iter_participants(group):
            if getattr(participant, "id", None) == peer:
                member = participant
                break
        else:
            raise ValueError(f"User {value!r} was not found in group participants")
    if not isinstance(member, User):
        raise ValueError(f"Target is not a user: {type(member).__name__}")
    return member


async def _call_with_one_flood_retry(operation):
    for attempt in range(2):
        try:
            return await operation()
        except errors.FloodWaitError as exc:
            if attempt:
                raise
            await asyncio.sleep(exc.seconds + 1)
    raise RuntimeError("unreachable")


async def run(args: argparse.Namespace) -> dict[str, Any]:
    rights, denied = validate_args(args)
    settings = load_settings(args.account)
    apply_runtime_env(settings)
    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized; refusing interactive login")
        me = await client.get_me()
        group = await resolve_entity(client, args.group)
        if not isinstance(group, (Channel, Chat)):
            raise ValueError(f"Target is not a group/channel: {type(group).__name__}")
        member = await resolve_member(client, group, args.user)
        if member.id == getattr(me, "id", None) or getattr(member, "is_self", False):
            raise ValueError("Refusing to change this account's own group permissions")

        before = await client.get_permissions(group, member)
        plan = {
            "ok": True,
            "dry_run": not args.execute,
            "action": args.action,
            "group": entity_payload(group, input_value=args.group),
            "member": entity_payload(member, input_value=args.user),
            "before": permissions_payload(before),
            "requested": {
                "rights": list(rights),
                "deny": list(denied),
                "rank": args.rank,
                "until_hours": args.until_hours,
            },
        }
        if not args.execute:
            return plan

        until = (
            datetime.now(timezone.utc) + timedelta(hours=args.until_hours)
            if args.action == "restrict"
            else None
        )
        if args.action == "grant-admin":
            await _call_with_one_flood_retry(
                lambda: client.edit_admin(group, member, is_admin=True, title=args.rank, **admin_kwargs(rights))
            )
        elif args.action == "revoke-admin":
            await _call_with_one_flood_retry(lambda: client.edit_admin(group, member, is_admin=False))
        elif args.action == "restrict":
            await _call_with_one_flood_retry(
                lambda: client.edit_permissions(group, member, until_date=until, **restriction_kwargs(denied))
            )
        elif args.action == "unrestrict":
            await _call_with_one_flood_retry(lambda: client.edit_permissions(group, member))
        elif args.action == "kick":
            await _call_with_one_flood_retry(lambda: client.kick_participant(group, member))
        else:
            raise RuntimeError(f"Unsupported action: {args.action}")

        try:
            after = await client.get_permissions(group, member)
            verification = {"state": "read_back", "permissions": permissions_payload(after)}
        except errors.UserNotParticipantError:
            verification = {"state": "member_absent_after_write", "permissions": None}
        plan.update({"dry_run": False, "verification": verification})
        return plan
    finally:
        await client.disconnect()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Inspect or change one group/channel member after a dry-run plan"
    )
    result.add_argument("--account", default="main")
    result.add_argument("--group", required=True, help="Exact username/link or numeric dialog/entity ID")
    result.add_argument("--user", required=True, help="@username or cached/numeric participant ID")
    result.add_argument("--action", choices=ACTIONS, default="inspect")
    result.add_argument("--rights", help=f"Comma-separated admin rights: {', '.join(ADMIN_RIGHTS)}")
    result.add_argument("--rank", help="Custom admin rank; only used by grant-admin")
    result.add_argument("--deny", help=f"Comma-separated permissions to deny: {', '.join(PERMISSION_RIGHTS)}")
    result.add_argument("--until-hours", type=int, help="Required finite restriction duration")
    result.add_argument("--execute", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        result = asyncio.run(run(args))
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"group_member failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
