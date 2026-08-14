from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from telethon import TelegramClient, errors
from telethon.tl.types import (
    DocumentAttributeAudio,
    DocumentAttributeVideo,
    MessageMediaPhoto,
    MessageMediaWebPage,
    MessageService,
)

from core.config import apply_runtime_env, load_settings

TARGET_TEXT_DEFAULT = "Бурмулда"


def register(client):
    return None


def message_kind(msg: Any) -> str:
    if isinstance(msg, MessageService):
        return "service"
    if getattr(msg, "photo", None) or isinstance(getattr(msg, "media", None), MessageMediaPhoto):
        return "photo"
    doc = getattr(msg, "document", None)
    if doc:
        mime = (getattr(doc, "mime_type", "") or "").lower()
        for attr in getattr(doc, "attributes", []) or []:
            if isinstance(attr, DocumentAttributeAudio):
                return "voice" if getattr(attr, "voice", False) else "audio"
            if isinstance(attr, DocumentAttributeVideo):
                return "round_video" if getattr(attr, "round_message", False) else "video"
        if mime.startswith("audio/"):
            return "audio"
        if mime.startswith("video/"):
            return "video"
        if mime.startswith("image/"):
            return "image_document"
        return "document"
    media = getattr(msg, "media", None)
    if isinstance(media, MessageMediaWebPage):
        return "webpage"
    if media is not None:
        return type(media).__name__
    if (getattr(msg, "message", None) or "").strip():
        return "text"
    return "empty"


def preview(msg: Any, limit: int = 80) -> str:
    return (getattr(msg, "message", None) or "").replace("\n", " ")[:limit]


async def resolve_entity(client: TelegramClient, chat: str):
    wanted = chat.strip()
    wanted_int: int | None = None
    try:
        wanted_int = int(wanted)
    except ValueError:
        pass

    matches: list[tuple[Any, dict[str, Any]]] = []
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        entity_id = getattr(entity, "id", None)
        metadata = {
            "dialog_id": dialog.id,
            "entity_id": entity_id,
            "title": dialog.name,
            "username": getattr(entity, "username", None),
            "type": type(entity).__name__,
        }
        if wanted_int is not None:
            bot_api_id = int(f"-100{entity_id}") if entity_id is not None else None
            if (
                dialog.id == wanted_int
                or entity_id == wanted_int
                or bot_api_id == wanted_int
                or (wanted_int < 0 and abs(wanted_int) == entity_id)
            ):
                matches.append((entity, metadata))
        else:
            haystack = " ".join(
                str(x or "").lower()
                for x in [dialog.name, getattr(entity, "username", None), getattr(entity, "title", None)]
            )
            if wanted.lower() in haystack:
                matches.append((entity, metadata))

    if matches:
        exact = [
            match
            for match in matches
            if wanted.lower()
            in {
                str(match[1].get("dialog_id", "")).lower(),
                str(match[1].get("entity_id", "")).lower(),
                str(match[1].get("title") or "").lower(),
                str(match[1].get("username") or "").lower(),
            }
        ]
        if len(exact) == 1:
            return exact[0]
        if len(matches) == 1:
            return matches[0]
        raise RuntimeError(
            json.dumps(
                {"error": "ambiguous_chat", "query": chat, "matches": [meta for _, meta in matches[:20]]},
                ensure_ascii=False,
            )
        )

    raise RuntimeError(
        json.dumps({"error": "chat_not_found_in_authorized_dialogs", "query": chat}, ensure_ascii=False)
    )


async def collect(client: TelegramClient, entity: Any, target_text: str):
    me = await client.get_me()
    counts = Counter()
    text_to_edit = []
    delete_candidates = []
    service_skipped = []
    already_target = []
    sample = []

    async for msg in client.iter_messages(entity, from_user=me, limit=None):
        if not getattr(msg, "out", False):
            continue
        kind = message_kind(msg)
        counts[kind] += 1
        row = {"id": msg.id, "kind": kind, "text_preview": preview(msg)}
        if len(sample) < 15:
            sample.append(row)
        if kind == "text":
            if (getattr(msg, "message", "") or "") == target_text:
                already_target.append(row)
            else:
                text_to_edit.append(row)
        elif kind == "service":
            service_skipped.append(row)
        else:
            delete_candidates.append(row)

    return {
        "total": sum(counts.values()),
        "counts": dict(counts),
        "text_to_edit": text_to_edit,
        "delete_candidates": delete_candidates,
        "service_skipped": service_skipped,
        "already_target": already_target,
        "sample": sample,
    }


async def delete_ids(client: TelegramClient, entity: Any, ids: list[int], batch_size: int = 80):
    deleted = []
    errors_list = []
    for start in range(0, len(ids), batch_size):
        chunk = ids[start : start + batch_size]
        try:
            await client.delete_messages(entity, chunk, revoke=True)
            deleted.extend(chunk)
            print(f"DELETED {len(deleted)}/{len(ids)}", flush=True)
            await asyncio.sleep(1.0)
        except errors.FloodWaitError as exc:
            print(f"FloodWait delete {exc.seconds}s", flush=True)
            await asyncio.sleep(exc.seconds + 1)
            try:
                await client.delete_messages(entity, chunk, revoke=True)
                deleted.extend(chunk)
            except Exception as retry_exc:
                errors_list.append({"ids": chunk, "error": repr(retry_exc)})
        except Exception as exc:
            errors_list.append({"ids": chunk, "error": repr(exc)})
    return deleted, errors_list


async def run(args):
    settings = load_settings(args.account)
    apply_runtime_env(settings)
    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized; refusing interactive login")
        entity, resolved = await resolve_entity(client, args.chat)
        print("RESOLVED " + json.dumps(resolved, ensure_ascii=False), flush=True)
        before = await collect(client, entity, args.text)
        print(
            "DRY_RUN "
            + json.dumps(
                {
                    "total_own_outgoing": before["total"],
                    "counts": before["counts"],
                    "text_needs_edit": len(before["text_to_edit"]),
                    "already_target": len(before["already_target"]),
                    "delete_candidates": len(before["delete_candidates"]),
                    "service_skipped": len(before["service_skipped"]),
                    "sample_latest": before["sample"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if not args.execute:
            return

        deleted, delete_errors = await delete_ids(
            client, entity, [row["id"] for row in before["delete_candidates"]]
        )

        edited = []
        edit_deleted = []
        edit_errors = []
        text_ids = [row["id"] for row in before["text_to_edit"]]
        for idx, msg_id in enumerate(text_ids, 1):
            try:
                await client.edit_message(entity, msg_id, args.text)
                edited.append(msg_id)
            except errors.FloodWaitError as exc:
                print(f"FloodWait edit {exc.seconds}s {idx}/{len(text_ids)}", flush=True)
                await asyncio.sleep(exc.seconds + 1)
                try:
                    await client.edit_message(entity, msg_id, args.text)
                    edited.append(msg_id)
                except Exception as edit_exc:
                    edit_errors.append({"id": msg_id, "edit_error": repr(edit_exc)})
            except errors.RPCError as edit_exc:
                edit_errors.append({"id": msg_id, "edit_error": repr(edit_exc)})
            except Exception as edit_exc:
                try:
                    await client.delete_messages(entity, [msg_id], revoke=True)
                    edit_deleted.append(msg_id)
                except Exception as delete_exc:
                    edit_errors.append(
                        {"id": msg_id, "edit_error": repr(edit_exc), "delete_error": repr(delete_exc)}
                    )
            if idx % 25 == 0 or idx == len(text_ids):
                print(
                    f"EDIT_PROGRESS {idx}/{len(text_ids)} edited={len(edited)} edit_deleted={len(edit_deleted)} errors={len(edit_errors)}",
                    flush=True,
                )
            await asyncio.sleep(args.delay)

        after = await collect(client, entity, args.text)
        print(
            "RESULT "
            + json.dumps(
                {
                    "resolved": resolved,
                    "deleted_non_text_attempted": len(before["delete_candidates"]),
                    "deleted_non_text_ok": len(deleted),
                    "delete_errors": delete_errors[:20],
                    "text_edit_attempted": len(text_ids),
                    "text_edited_ok": len(edited),
                    "text_deleted_after_edit_fail": len(edit_deleted),
                    "edit_errors": edit_errors[:20],
                    "after_total_own_outgoing": after["total"],
                    "after_counts": after["counts"],
                    "after_text_needs_edit": len(after["text_to_edit"]),
                    "after_delete_candidates": len(after["delete_candidates"]),
                    "after_service_skipped": len(after["service_skipped"]),
                    "after_already_target": len(after["already_target"]),
                    "remaining_bad_text_sample": after["text_to_edit"][:20],
                    "remaining_delete_sample": after["delete_candidates"][:20],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    finally:
        await client.disconnect()


def main():
    parser = argparse.ArgumentParser(description="Replace own text messages and delete own media/non-text messages in a Telegram chat.")
    parser.add_argument("--account", default="main")
    parser.add_argument("--chat", required=True)
    parser.add_argument("--text", default=TARGET_TEXT_DEFAULT)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--delay", type=float, default=0.9)
    args = parser.parse_args()
    if args.delay < 0:
        parser.error("--delay must be >= 0")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
