from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, RPCError

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


@dataclass(frozen=True)
class PurgeStats:
    checked: int
    deleted: int
    batches: int
    failed_batches: int = 0
    remaining: int | None = None


def chunked(items: list[int], size: int) -> Iterable[list[int]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


async def resolve_target_entity(client: TelegramClient, target):
    """Resolve chat from username/link/id with fallback through dialogs.

    Accepts values like:
    - @username
    - t.me/... links
    - 2855053074
    - -1002855053074
    """
    raw = str(target).strip()
    if raw.lstrip("-").isdigit():
        wanted_id = int(raw)
        matches = []
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            if wanted_id in {getattr(dialog, "id", None), getattr(entity, "id", None)}:
                matches.append((dialog, entity))
        if len(matches) == 1:
            dialog, entity = matches[0]
            logger.info("Resolved target via dialog scan: %s (%s)", dialog.name, getattr(entity, "id", None))
            return entity
        if len(matches) > 1:
            raise ValueError(f"Ambiguous numeric chat id: {target!r}")
        raise ValueError(f"Numeric chat id is not present in authorized dialogs: {target!r}")

    entity = await client.get_entity(raw.rstrip("/"))
    logger.info("Resolved target via get_entity(%r): %s", raw, getattr(entity, "title", getattr(entity, "id", entity)))
    return entity


async def purge_my_messages(
    client: TelegramClient,
    entity,
    *,
    execute: bool = False,
    exclude_message_ids: set[int] | None = None,
    search_chunk_size: int = 100,
    delete_chunk_size: int = 100,
    search_pause_seconds: float = 0.0,
    delete_pause_seconds: float = 1.0,
) -> PurgeStats:
    """Plan or delete only this account's outgoing messages in one chat."""
    message_ids: list[int] = []
    checked = deleted = batches = 0
    excluded = exclude_message_ids or set()
    search_flood_retries = 0
    search_offset_id = 0

    while True:
        current_batch: list[int] = []
        scanned_count = 0
        last_scanned_id = search_offset_id
        try:
            async for msg in client.iter_messages(
                entity,
                from_user="me",
                limit=search_chunk_size,
                offset_id=search_offset_id,
            ):
                scanned_count += 1
                if getattr(msg, "id", None):
                    last_scanned_id = int(msg.id)
                if getattr(msg, "id", None) and getattr(msg, "out", False) and msg.id not in excluded:
                    current_batch.append(msg.id)
        except FloodWaitError as exc:
            if search_flood_retries >= 1:
                logger.error("FloodWait repeated while searching; refusing to delete a partial plan")
                return PurgeStats(checked=checked, deleted=0, batches=batches, failed_batches=1)
            search_flood_retries += 1
            wait_seconds = int(exc.seconds) + 1
            logger.warning("FloodWait while searching messages, sleeping %s sec", wait_seconds)
            await asyncio.sleep(wait_seconds)
            continue
        except RPCError:
            logger.exception("RPC error while searching; refusing to delete a partial plan")
            return PurgeStats(checked=checked, deleted=0, batches=batches, failed_batches=1)

        if not scanned_count:
            break
        if last_scanned_id == search_offset_id:
            logger.error("Search page had no usable message IDs; refusing to loop")
            return PurgeStats(
                checked=checked,
                deleted=0,
                batches=batches,
                failed_batches=1,
            )
        search_offset_id = last_scanned_id
        checked += len(current_batch)
        message_ids.extend(current_batch)
        batches += 1
        logger.info("Found %s of your messages so far", len(message_ids))
        if scanned_count < search_chunk_size:
            break
        if search_pause_seconds > 0:
            await asyncio.sleep(search_pause_seconds)

    if not execute:
        return PurgeStats(checked=checked, deleted=0, batches=batches)

    failed_batches = 0
    for chunk in chunked(message_ids, delete_chunk_size):
        for attempt in range(2):
            try:
                await client.delete_messages(entity, chunk, revoke=True)
                deleted += len(chunk)
                logger.info("Deleted %s/%s messages", deleted, len(message_ids))
                break
            except FloodWaitError as exc:
                if attempt:
                    failed_batches += 1
                    logger.error("FloodWait repeated while deleting batch of %s messages", len(chunk))
                    break
                wait_seconds = int(exc.seconds) + 1
                logger.warning("FloodWait while deleting messages, sleeping %s sec", wait_seconds)
                await asyncio.sleep(wait_seconds)
            except RPCError:
                failed_batches += 1
                logger.exception("RPC error while deleting batch of %s messages", len(chunk))
                break
        await asyncio.sleep(delete_pause_seconds)

    remaining: int | None = 0
    try:
        async for msg in client.iter_messages(entity, from_user="me", limit=None):
            if getattr(msg, "out", False) and getattr(msg, "id", None) not in excluded:
                remaining += 1
    except RPCError:
        logger.exception("RPC error while verifying purge")
        remaining = None

    return PurgeStats(
        checked=checked,
        deleted=deleted,
        batches=batches,
        failed_batches=failed_batches,
        remaining=remaining,
    )


def register(client: TelegramClient) -> None:
    @client.on(events.NewMessage(outgoing=True, pattern=r"^\.purgeme(?:\s+(.+))?$"))
    async def purgeme_handler(event: events.NewMessage.Event) -> None:
        target_raw = (event.pattern_match.group(1) or "").strip() if event.pattern_match else ""
        tokens = target_raw.split()
        execute = bool(tokens and tokens[0] == "--execute")
        if execute:
            tokens.pop(0)
        target = " ".join(tokens) or event.chat_id

        status = await event.reply("Считаю твои сообщения для очистки...")
        try:
            entity = await resolve_target_entity(client, target)
            stats = await purge_my_messages(client, entity, execute=execute, exclude_message_ids={status.id})
            if not execute:
                await status.edit(
                    "DRY-RUN: удаления не было.\n"
                    f"Найдено твоих исходящих сообщений: **{stats.checked}**\n"
                    f"Поисковых батчей: **{stats.batches}**\n\n"
                    "Для удаления повтори: `.purgeme --execute [chat_id|@username]`"
                )
                return
            await status.edit(
                "Очистка завершена.\n"
                f"Проверено твоих сообщений: **{stats.checked}**\n"
                f"Удалено: **{stats.deleted}**\n"
                f"Поисковых батчей: **{stats.batches}**\n"
                f"Ошибочных батчей: **{stats.failed_batches}**\n"
                f"Осталось после проверки: **{stats.remaining if stats.remaining is not None else 'не удалось проверить'}**"
            )
        except Exception:
            logger.exception("Ошибка в .purgeme handler")
            await status.edit("Не удалось завершить очистку. Проверь target и логи.")


async def _verification_run(chat: str, account: str | None = None, execute: bool = False) -> None:
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
        entity = await resolve_target_entity(client, chat)
        stats = await purge_my_messages(client, entity, execute=execute)
        heading = "Purge completed:\n" if execute else "DRY-RUN purge plan:\n"
        print(
            heading
            + f"- checked: {stats.checked}\n"
            + f"- deleted: {stats.deleted}\n"
            + f"- batches: {stats.batches}\n"
            + f"- failed batches: {stats.failed_batches}\n"
            + f"- remaining after verify: {stats.remaining}"
        )
    finally:
        await client.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Delete only your own messages from a target chat")
    parser.add_argument("--chat", required=True, help="Chat id or username, e.g. @mychat or -100123")
    parser.add_argument("--account", help="Имя аккаунта из accounts/<name>.env (например: main)")
    parser.add_argument("--execute", action="store_true", help="Actually delete messages (default is dry-run)")
    args = parser.parse_args()

    try:
        asyncio.run(_verification_run(args.chat, account=args.account, execute=args.execute))
    except KeyboardInterrupt:
        print("Остановлено пользователем.")
    except Exception as exc:
        logger.exception("Verification run failed")
        raise SystemExit(f"Ошибка во время проверки модуля purge_me: {exc}")
