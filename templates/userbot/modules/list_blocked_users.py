from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from telethon import TelegramClient, errors, functions
from telethon.tl.types import PeerUser

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import apply_runtime_env, load_settings

PAGE_SIZE = 100
MAX_LIMIT = 5_000


@dataclass(frozen=True)
class BlockedUser:
    id: int
    name: str | None
    username: str | None


def register(client: TelegramClient) -> None:
    """Direct CLI helper; no event handler is attached."""


def validate_args(args: argparse.Namespace) -> None:
    if not 1 <= args.limit <= MAX_LIMIT:
        raise ValueError(f"--limit must be between 1 and {MAX_LIMIT}")


def user_payload(user: Any) -> BlockedUser:
    name = " ".join(
        value.strip()
        for value in (getattr(user, "first_name", None), getattr(user, "last_name", None))
        if isinstance(value, str) and value.strip()
    )
    username = getattr(user, "username", None)
    return BlockedUser(
        id=int(user.id),
        name=name or None,
        username=f"@{username}" if isinstance(username, str) and username else None,
    )


async def get_blocked_page(client: TelegramClient, *, offset: int, limit: int) -> Any:
    for attempt in range(2):
        try:
            return await client(
                functions.contacts.GetBlockedRequest(offset=offset, limit=limit)
            )
        except errors.FloodWaitError as exc:
            if attempt:
                raise
            await asyncio.sleep(exc.seconds + 1)
    raise AssertionError("unreachable")


async def collect_blocked_users(
    client: TelegramClient, *, limit: int
) -> tuple[list[BlockedUser], int, int | None, bool]:
    """List PeerUser entries from Telegram's current block list, preserving server order."""
    users: list[BlockedUser] = []
    seen_user_ids: set[int] = set()
    offset = 0
    scanned = 0
    reported_total: int | None = None
    last_page_was_full = False

    while scanned < limit:
        page_limit = min(PAGE_SIZE, limit - scanned)
        response = await get_blocked_page(client, offset=offset, limit=page_limit)
        blocked_peers = list(getattr(response, "blocked", ()) or ())
        response_count = getattr(response, "count", None)
        if isinstance(response_count, int):
            reported_total = response_count
        resolved_users = {
            int(user.id): user
            for user in (getattr(response, "users", ()) or ())
            if getattr(user, "id", None) is not None
        }

        for blocked_entry in blocked_peers:
            peer = getattr(blocked_entry, "peer_id", blocked_entry)
            if not isinstance(peer, PeerUser):
                continue
            user_id = int(peer.user_id)
            if user_id in seen_user_ids:
                continue
            seen_user_ids.add(user_id)
            user = resolved_users.get(user_id)
            users.append(
                user_payload(user)
                if user is not None
                else BlockedUser(id=user_id, name=None, username=None)
            )

        scanned += len(blocked_peers)
        offset += len(blocked_peers)
        last_page_was_full = bool(blocked_peers) and len(blocked_peers) == page_limit

        if not blocked_peers:
            break
        if reported_total is not None and offset >= reported_total:
            break
        if reported_total is None and len(blocked_peers) < page_limit:
            break

    may_be_truncated = (
        scanned < reported_total
        if reported_total is not None
        else scanned >= limit and last_page_was_full
    )
    return users, scanned, reported_total, may_be_truncated


def result_payload(
    users: list[BlockedUser],
    *,
    scanned: int,
    reported_total: int | None,
    may_be_truncated: bool,
) -> dict[str, Any]:
    return {
        "ok": True,
        "read_only": True,
        "matching_rule": "contacts.GetBlockedRequest PeerBlocked entries whose peer_id is PeerUser",
        "blocked_user_count": len(users),
        "scanned_blocked_peer_count": scanned,
        "reported_blocked_peer_count": reported_total,
        "may_be_truncated": may_be_truncated,
        "blocked_users": [
            {"id": user.id, "name": user.name, "username": user.username}
            for user in users
        ],
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    validate_args(args)
    settings = load_settings(args.account)
    apply_runtime_env(settings)
    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized; refusing interactive login")
        users, scanned, reported_total, may_be_truncated = await collect_blocked_users(
            client, limit=args.limit
        )
        return result_payload(
            users,
            scanned=scanned,
            reported_total=reported_total,
            may_be_truncated=may_be_truncated,
        )
    finally:
        await client.disconnect()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="List users blocked by the current Telegram account without writing to Telegram"
    )
    result.add_argument("--account", default="main")
    result.add_argument(
        "--limit",
        type=int,
        default=MAX_LIMIT,
        help=f"Maximum block-list entries to scan; default: {MAX_LIMIT}",
    )
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        print(json.dumps(asyncio.run(run(args)), ensure_ascii=False, indent=2))
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"list_blocked_users failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
