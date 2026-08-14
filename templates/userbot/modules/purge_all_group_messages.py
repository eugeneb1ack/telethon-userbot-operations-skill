from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from telethon import TelegramClient, errors
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.types import Channel, Chat

from core.config import apply_runtime_env, load_settings

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


@dataclass
class DialogPlan:
    dialog_id: int
    entity_id: int | None
    title: str
    type: str
    username: str | None
    is_megagroup: bool
    is_gigagroup: bool
    linked_chat_id: int | None
    exclude_reason: str | None
    own_count: int = 0
    sample_ids: list[int] | None = None


def register(client):
    return None


async def get_linked_chat_id(client: TelegramClient, entity: Any) -> tuple[int | None, str | None]:
    if not isinstance(entity, Channel):
        return None, None
    try:
        full = await client(GetFullChannelRequest(entity))
        return getattr(full.full_chat, "linked_chat_id", None), None
    except errors.FloodWaitError:
        raise
    except Exception as exc:
        return None, f"linked_chat_lookup_error:{type(exc).__name__}"


async def count_own_messages(client: TelegramClient, entity: Any, limit_sample: int = 5) -> tuple[int, list[int]]:
    count = 0
    sample: list[int] = []
    async for msg in client.iter_messages(entity, from_user="me", limit=None):
        if not getattr(msg, "out", False):
            continue
        count += 1
        if len(sample) < limit_sample:
            sample.append(msg.id)
    return count, sample


async def delete_own_messages(
    client: TelegramClient,
    entity: Any,
    *,
    batch_size: int = 100,
    pause: float = 1.0,
) -> dict[str, Any]:
    ids: list[int] = []
    async for msg in client.iter_messages(entity, from_user="me", limit=None):
        if getattr(msg, "out", False) and getattr(msg, "id", None):
            ids.append(msg.id)

    deleted = 0
    errors_list = []
    for start in range(0, len(ids), batch_size):
        chunk = ids[start : start + batch_size]
        try:
            await client.delete_messages(entity, chunk, revoke=True)
            deleted += len(chunk)
            logger.info("Deleted %s/%s from %s", deleted, len(ids), getattr(entity, "title", entity))
        except errors.FloodWaitError as exc:
            logger.warning("FloodWait delete %ss in %s", exc.seconds, getattr(entity, "title", entity))
            await asyncio.sleep(exc.seconds + 1)
            try:
                await client.delete_messages(entity, chunk, revoke=True)
                deleted += len(chunk)
            except Exception as retry_exc:
                errors_list.append({"ids": chunk, "error": repr(retry_exc)})
        except Exception as exc:
            errors_list.append({"ids": chunk, "error": repr(exc)})
        await asyncio.sleep(pause)
    return {"found": len(ids), "deleted": deleted, "errors": errors_list[:20]}


def matches_exclude(dialog_id: int, entity_id: int | None, title: str, username: str | None, excludes: list[str]) -> str | None:
    hay = " ".join(str(x or "").lower() for x in [dialog_id, entity_id, title, username])
    for item in excludes:
        needle = item.strip().lower()
        if needle and needle in hay:
            return f"manual_exclude:{item}"
    return None


async def build_plan(client: TelegramClient, *, include_linked_discussions: bool, count: bool, excludes: list[str]) -> list[DialogPlan]:
    plans: list[DialogPlan] = []
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        is_group_like = bool(getattr(dialog, "is_group", False))
        if not is_group_like:
            continue
        if not isinstance(entity, (Channel, Chat)):
            continue

        is_megagroup = bool(getattr(entity, "megagroup", False))
        is_gigagroup = bool(getattr(entity, "gigagroup", False))
        linked_chat_id, linked_lookup_error = await get_linked_chat_id(client, entity)
        exclude_reason = matches_exclude(
            int(dialog.id),
            getattr(entity, "id", None),
            dialog.name or getattr(entity, "title", ""),
            getattr(entity, "username", None),
            excludes,
        )
        # Telegram linked discussion groups expose linked_chat_id on full channel in many cases.
        # User explicitly said: exclude comments to channels.
        if linked_chat_id and not include_linked_discussions:
            exclude_reason = (exclude_reason + ";" if exclude_reason else "") + f"linked_discussion_or_channel_comments:{linked_chat_id}"
        if linked_lookup_error and not include_linked_discussions:
            # A destructive operation must fail closed when we cannot determine
            # whether a group is a channel's discussion/comment chat.
            exclude_reason = (exclude_reason + ";" if exclude_reason else "") + linked_lookup_error

        plan = DialogPlan(
            dialog_id=int(dialog.id),
            entity_id=getattr(entity, "id", None),
            title=dialog.name or getattr(entity, "title", ""),
            type=type(entity).__name__,
            username=getattr(entity, "username", None),
            is_megagroup=is_megagroup,
            is_gigagroup=is_gigagroup,
            linked_chat_id=linked_chat_id,
            exclude_reason=exclude_reason,
            sample_ids=[],
        )
        if count:
            try:
                own_count, sample = await count_own_messages(client, entity)
                plan.own_count = own_count
                plan.sample_ids = sample
            except errors.FloodWaitError as exc:
                logger.warning("FloodWait count %ss in %s", exc.seconds, dialog.name)
                await asyncio.sleep(exc.seconds + 1)
                own_count, sample = await count_own_messages(client, entity)
                plan.own_count = own_count
                plan.sample_ids = sample
            except Exception as exc:
                plan.exclude_reason = (plan.exclude_reason + ";" if plan.exclude_reason else "") + f"count_error:{repr(exc)}"
        plans.append(plan)
    return plans


async def run(args):
    settings = load_settings(args.account)
    apply_runtime_env(settings)
    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized; refusing interactive login")
        plans = await build_plan(
            client,
            include_linked_discussions=args.include_linked_discussions,
            count=True,
            excludes=args.exclude,
        )
        targets = [p for p in plans if not p.exclude_reason and p.own_count > 0]
        excluded = [p for p in plans if p.exclude_reason and p.own_count > 0]
        empty = [p for p in plans if not p.exclude_reason and p.own_count == 0]

        print("PLAN " + json.dumps({
            "target_chats": len(targets),
            "target_messages": sum(p.own_count for p in targets),
            "excluded_nonempty_chats": len(excluded),
            "excluded_messages": sum(p.own_count for p in excluded),
            "empty_target_chats": len(empty),
            "targets": [asdict(p) for p in targets],
            "excluded_nonempty": [asdict(p) for p in excluded],
        }, ensure_ascii=False), flush=True)

        if not args.execute:
            return

        results = []
        for idx, plan in enumerate(targets, 1):
            logger.info("Purging %s/%s: %s (%s messages)", idx, len(targets), plan.title, plan.own_count)
            try:
                entity = await client.get_entity(plan.dialog_id)
            except Exception:
                entity = await client.get_entity(plan.entity_id)
            if getattr(entity, "id", None) != plan.entity_id:
                results.append(
                    {
                        "dialog_id": plan.dialog_id,
                        "entity_id": plan.entity_id,
                        "title": plan.title,
                        "found": 0,
                        "deleted": 0,
                        "errors": [
                            {
                                "error": "resolved_entity_mismatch",
                                "actual_entity_id": getattr(entity, "id", None),
                            }
                        ],
                    }
                )
                continue
            result = await delete_own_messages(client, entity, batch_size=args.batch_size, pause=args.pause)
            result.update({"dialog_id": plan.dialog_id, "entity_id": plan.entity_id, "title": plan.title})
            results.append(result)

        verify_plans = await build_plan(
            client,
            include_linked_discussions=args.include_linked_discussions,
            count=True,
            excludes=args.exclude,
        )
        verify_targets = [p for p in verify_plans if not p.exclude_reason and p.own_count > 0]
        verification_unknown = [p for p in verify_plans if "count_error:" in (p.exclude_reason or "")]
        print("RESULT " + json.dumps({
            "attempted_chats": len(targets),
            "attempted_messages": sum(p.own_count for p in targets),
            "results": results,
            "remaining_target_chats": len(verify_targets),
            "remaining_target_messages": sum(p.own_count for p in verify_targets),
            "remaining_targets": [asdict(p) for p in verify_targets],
            "verification_unknown_chats": len(verification_unknown),
            "verification_unknown": [asdict(p) for p in verification_unknown],
        }, ensure_ascii=False), flush=True)
    finally:
        await client.disconnect()


def main():
    parser = argparse.ArgumentParser(description="Delete own messages from all group chats, optionally excluding linked channel discussion/comment groups.")
    parser.add_argument("--account", default="main")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--include-linked-discussions", action="store_true", help="DANGER: also purge groups linked as channel discussions/comments")
    parser.add_argument("--exclude", action="append", default=[], help="Manual exclude substring/id/title/username. Can be repeated.")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--pause", type=float, default=1.0)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 100:
        parser.error("--batch-size must be between 1 and 100")
    if args.pause < 0:
        parser.error("--pause must be >= 0")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
