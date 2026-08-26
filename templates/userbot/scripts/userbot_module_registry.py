#!/usr/bin/env python3
"""Read-only natural-language router for guarded local userbot operations.

The router never connects to Telegram. It either returns one high-confidence
operation, reports ambiguity, or returns no_match with advisory candidates.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIN_MATCH_SCORE = 8
AMBIGUITY_MARGIN = 3
STOP_TOKENS = {
    "and",
    "for",
    "the",
    "with",
    "без",
    "где",
    "дай",
    "для",
    "его",
    "или",
    "как",
    "мне",
    "мой",
    "мои",
    "на",
    "найди",
    "покажи",
    "список",
    "что",
    "это",
}


@dataclass(frozen=True)
class Operation:
    slug: str
    module: str
    mode: str
    summary: str
    triggers: tuple[str, ...]
    command: str
    safety: str
    negative_triggers: tuple[str, ...] = field(default_factory=tuple)


OPERATIONS = (
    Operation(
        "list_members",
        "list_group_members.py",
        "read_only",
        "List current group/channel members.",
        ("список участников", "участники чата", "участники группы", "состав чата", "members", "participants"),
        "venv/bin/python scripts/userbotrun.py --account main modules/list_group_members.py --chat '<chat>'",
        "Bounded read-only listing; bots/deleted accounts are excluded by default.",
    ),
    Operation(
        "search_messages",
        "search_messages.py",
        "read_only",
        "Search a bounded chat window using the persistent gateway.",
        ("поиск сообщений", "найди сообщение", "найти сообщения", "поиск по истории", "search messages", "search chat"),
        "venv/bin/python scripts/userbotctl.py --account main search --chat '<chat>' --query '<text>' --limit 50",
        "Fast local-socket read. Use the direct module only for sender/date/export filters or gateway fallback.",
        ("найди ветку", "темы форума", "forum topics"),
    ),
    Operation(
        "download_media",
        "download_media.py",
        "local_write",
        "Preview and locally download media from exact message IDs.",
        ("скачай медиа", "скачай файл", "скачай фото", "скачай видео", "выгрузи медиа", "download media", "download file"),
        "venv/bin/python scripts/userbotrun.py --account main modules/download_media.py --chat '<chat>' --message-ids <id,id>",
        "Local file write needs a preview, then --execute; data stays under runtime/<account>/data/downloads.",
    ),
    Operation(
        "transcribe_latest_voice",
        "dialog_updates_native.py",
        "local_write",
        "Collect freshness-checked latest or unseen dialog content from one exact sender.",
        (
            "последнее голосовое",
            "последний войс",
            "последнее гс",
            "еще одно голосовое",
            "глянь последнее голосовое",
            "посмотри последнее голосовое",
            "latest voice",
            "transcribe latest voice",
        ),
        "venv/bin/python scripts/userbotrun.py --account main modules/dialog_updates_native.py --chat '<chat>' --sender-id <expected_sender_id> --latest 1 --content voice --json",
        "Bypasses summary memory, tracks a content-scoped cursor, rechecks the mixed live tail, and fails closed while it keeps moving.",
    ),
    Operation(
        "dialog_updates",
        "dialog_updates_native.py",
        "local_write",
        "Collect current mixed dialog updates after an anchor or delivery cursor.",
        (
            "что еще она написала",
            "что ещё она написала",
            "следующее сообщение",
            "еще одно сообщение",
            "ещё одно сообщение",
            "там еще одно",
            "там ещё одно",
            "последнее сообщение",
            "глянь последнее сообщение",
            "посмотри последнее сообщение",
            "новые сообщения",
            "после моего сообщения",
            "все после моего сообщения",
            "dialog updates",
        ),
        "venv/bin/python scripts/userbotrun.py --account main modules/dialog_updates_native.py --chat '<chat>' --sender-id <expected_sender_id> --unseen --content all --json",
        "Stores only bounded message-ID cursors locally; use --after-latest-outgoing for 'after my message'.",
    ),
    Operation(
        "transcribe_audio",
        "transcribe_audio_native.py",
        "read_only",
        "Native Telegram transcription of one voice/audio/video-note message.",
        ("расшифруй голосовое", "транскрибируй", "расшифруй аудио", "расшифруй кружок", "voice transcription", "transcribe audio"),
        "venv/bin/python scripts/userbotrun.py --account main modules/transcribe_audio_native.py --chat '<chat>' --message-id <id> --sender-id <expected_sender_id> --json",
        "For another person's media, pass and verify --sender-id; do not summarize incomplete transcription.",
        ("последнее голосовое", "последний войс", "последнее гс", "latest voice"),
    ),
    Operation(
        "summarize_chat",
        "summarize_chat_native.py",
        "local_write",
        "Reuse or incrementally update a bounded local dialog summary with native-STT context.",
        (
            "саммари чата",
            "сводка чата",
            "подведи итоги чата",
            "суммируй диалог",
            "суммируй чат",
            "о чем мы общались",
            "о чем мы говорили",
            "summarize chat",
            "chat recap",
        ),
        "venv/bin/python scripts/userbotrun.py --account main modules/summarize_chat_native.py --chat '<chat>' --since YYYY-MM-DD --until YYYY-MM-DD --do-summary",
        "Writes bounded summary memory and local recovery artifacts; use this route, not legacy summarize.py.",
    ),
    Operation(
        "recall_memory",
        "scripts/userbot_memory.py",
        "local_read",
        "Recall compact local knowledge before repeating collection or reasoning.",
        ("что мы уже знаем", "вспомни сохраненное", "вспомни сохранённое", "достань из памяти", "локальная память", "recall memory"),
        "venv/bin/python scripts/userbot_memory.py --account main recall --query '<query>' --scope '<scope>'",
        "Advisory only: revalidate temporal facts and historical task results before current claims or actions.",
    ),
    Operation(
        "remember_memory",
        "scripts/userbot_memory.py",
        "local_write",
        "Save one verified, reusable, bounded semantic-memory item.",
        ("сохрани в память", "запомни это", "занеси в локальную память", "remember this", "save to memory"),
        "venv/bin/python scripts/userbot_memory.py --account main remember --kind '<kind>' --scope '<scope>' --subject '<subject>' --summary '<summary>' --source '<provenance>'",
        "Never stores raw messages, transcripts, credentials, unverified inference, or one-off chatter.",
    ),
    Operation(
        "count_messages",
        "count_user_messages.py",
        "read_only",
        "Count a participant's messages by type in a time window.",
        ("посчитай сообщения", "сколько сообщений", "статистика сообщений", "count messages"),
        "venv/bin/python scripts/userbotrun.py --account main modules/count_user_messages.py --chat '<chat>' --user '<user>' --hours 48",
        "--send publishes a report and is a Telegram write.",
    ),
    Operation(
        "owned_channels",
        "owned_channels.py",
        "read_only",
        "List broadcast channels created and owned by the current account.",
        ("каналы где я владелец", "каналы которыми я владею", "мои собственные каналы", "owned channels", "channels i own"),
        "venv/bin/python scripts/userbotrun.py --account main modules/owned_channels.py",
        "Read-only creator=True scan; returns only title and public username.",
        ("писал комментарии", "мои комментарии"),
    ),
    Operation(
        "comment_channels",
        "comment_channels.py",
        "read_only",
        "List broadcast channels where the account wrote comments.",
        (
            "где я писал комментарии",
            "писал комментарии",
            "каналы с моими комментариями",
            "мои комментарии в каналах",
            "comment channels",
            "where i commented",
        ),
        "venv/bin/python scripts/userbotrun.py --account main modules/comment_channels.py",
        "Read-only scan; output contains channel metadata, not message text.",
        ("каналы где я владелец", "owned channels"),
    ),
    Operation(
        "list_forum_topics",
        "list_forum_topics.py",
        "read_only",
        "List or search forum topics in one group/channel.",
        ("найди ветку", "список веток", "темы форума", "ветки форума", "forum topics", "find topic"),
        "venv/bin/python scripts/userbotrun.py --account main modules/list_forum_topics.py --chat '<chat>' --query '<optional-title>'",
        "Read-only bounded topic listing; verifies the target is a group/channel.",
    ),
    Operation(
        "list_blocked_users",
        "list_blocked_users.py",
        "read_only",
        "List users currently blocked by the account.",
        ("черном списке", "чёрном списке", "заблокированные пользователи", "кого я заблокировал", "blocked users", "block list"),
        "venv/bin/python scripts/userbotrun.py --account main modules/list_blocked_users.py",
        "Read-only paginated snapshot; returns minimal user identity fields and truncation status.",
        ("события телеграм", "непрочитанные"),
    ),
    Operation(
        "recent_personal_incoming",
        "recent_personal_incoming.py",
        "read_only",
        "List people who sent the latest incoming direct messages, without message text.",
        ("кто мне написал", "последние личные сообщения", "последние лс", "входящие личные", "recent incoming dms"),
        "venv/bin/python scripts/userbotctl.py --account main recent-dms --limit 3",
        "Fast local-socket read; no message text is returned.",
    ),
    Operation(
        "event_inbox",
        "core/gateway.py",
        "read_only",
        "List queued direct-message, mention and reply events.",
        ("уведомления телеграм", "события телеграм", "кто меня отметил", "кто ответил мне", "непрочитанные события", "telegram events", "mentions inbox"),
        "venv/bin/python scripts/userbotctl.py --account main events list --unread --limit 20",
        "Local bounded SQLite read; acknowledging an event does not write to Telegram.",
        ("черном списке", "чёрном списке", "blocked users"),
    ),
    Operation(
        "list_personal_chats",
        "personal_chats.py",
        "read_only",
        "List personal dialogs.",
        ("личные чаты", "личные диалоги", "лички", "personal chats", "dms"),
        "venv/bin/python scripts/userbotctl.py --account main dialogs --kind personal",
        "Fast local-socket read.",
    ),
    Operation(
        "list_groups",
        "group_chats.py",
        "read_only",
        "List group dialogs.",
        ("список групп", "мои группы", "групповые чаты", "group chats"),
        "venv/bin/python scripts/userbotctl.py --account main dialogs --kind groups",
        "Fast local-socket read.",
    ),
    Operation(
        "list_channels",
        "channel_chats.py",
        "read_only",
        "List channel dialogs.",
        ("список каналов", "все мои каналы", "каналы аккаунта", "channel chats"),
        "venv/bin/python scripts/userbotctl.py --account main dialogs --kind channels",
        "Fast local-socket read; ownership requires the dedicated owned_channels route.",
        ("владелец", "комментарии"),
    ),
    Operation(
        "list_bots",
        "bot_chats.py",
        "read_only",
        "List bot dialogs.",
        ("список ботов", "мои боты в диалогах", "bot chats"),
        "venv/bin/python scripts/userbotctl.py --account main dialogs --kind bots",
        "Fast local-socket read.",
    ),
    Operation(
        "send_message",
        "send_message.py",
        "telegram_write",
        "Send one message or exact reply to one resolved chat/user.",
        ("отправь сообщение", "напиши в телеграм", "ответь реплаем", "отправь как реплай", "send message", "reply to message"),
        "venv/bin/python scripts/userbotrun.py --account main modules/send_message.py --chat '<chat>' --text '<text>'",
        "Preview first. Sending needs explicit approval and --execute; replies verify the exact parent message.",
    ),
    Operation(
        "send_photo",
        "send_photo.py",
        "telegram_write",
        "Send one verified local image to one resolved Telegram chat.",
        ("отправь фото", "отправь фотографию", "пришли картинку", "send photo", "send image"),
        "venv/bin/python scripts/userbotrun.py --account main modules/send_photo.py --chat '<chat>' --photo '<local-path>' --caption '<caption>'",
        "Preview target, local file metadata and caption; sending needs explicit approval and --execute.",
    ),
    Operation(
        "edit_message",
        "message_edit.py",
        "telegram_write",
        "Edit one outgoing message, including inline custom emoji in HTML mode.",
        ("отредактируй сообщение", "измени сообщение", "edit message", "custom emoji в сообщении"),
        "venv/bin/python scripts/userbotrun.py --account main modules/message_edit.py --chat '<chat>' --message-id <id> --text '<text>'",
        "Only an outgoing message may be edited; execute requires exact read-back.",
    ),
    Operation(
        "richtext",
        "richtext.py",
        "telegram_write",
        "Edit one outgoing message with allowlisted Telegram HTML entities.",
        ("красивое форматирование", "форматированный текст", "rich text", "richtext", "blockquote expandable", "tg-emoji"),
        "venv/bin/python scripts/userbotrun.py --account main modules/richtext.py --chat '<chat>' --message-id <id> --file '<html-file>' --no-link-preview",
        "Dry-run validates markup and ownership; --execute verifies plain text and explicit entities.",
    ),
    Operation(
        "rich_article",
        "rich_article.py",
        "telegram_write",
        "Publish one structured Telegram Rich Message article with optional embedded local media to one broadcast channel.",
        (
            "rich article",
            "rich message article",
            "статья в телеграм",
            "richtext статья",
            "telegram статья с заголовками",
            "опубликуй статью",
            "встроить фото в статью",
            "статья с аудио видео документом",
        ),
        "venv/bin/python scripts/userbotrun.py --account main modules/rich_article.py --chat '<channel>' --title '<visible-title>' --file '<article.html>' --format html --media 'cover:photo:/absolute/path/cover.png'",
        "Dry-run resolves a broadcast channel, checks title duplicates and records local photo/video/audio/document bindings; --execute uploads those files without posts and verifies them inside returned rich blocks.",
    ),
    Operation(
        "forward_messages",
        "forward_messages.py",
        "telegram_write",
        "Forward exact frozen message IDs between two chats.",
        ("перешли сообщение", "форвардни", "перешли в чат", "forward message"),
        "venv/bin/python scripts/userbotrun.py --account main modules/forward_messages.py --source-chat '<source>' --destination-chat '<destination>' --message-ids <id,id>",
        "Preview source and destination; forwarding is external disclosure and needs --execute.",
    ),
    Operation(
        "pin_message",
        "pin_message.py",
        "telegram_write",
        "Inspect, pin, or unpin one exact message.",
        ("закрепи сообщение", "открепи сообщение", "пин сообщения", "pin message", "unpin"),
        "venv/bin/python scripts/userbotrun.py --account main modules/pin_message.py --chat '<chat>' --message-id <id> --action pin",
        "Preview exact message and pin state; change needs --execute.",
    ),
    Operation(
        "create_emoji_pack",
        "create_emoji_pack.py",
        "telegram_write",
        "Create one custom emoji pack with a readable text emoji.",
        ("создай пак эмоджи", "создай пак эмодзи", "emoji pack", "custom emoji pack", "текст пак"),
        "venv/bin/python scripts/userbotrun.py --account main modules/create_emoji_pack.py --title 'текст пак' --text 'ЖИРНЫЙ' --emoji '💪'",
        "Dry-run resolves a unique short name and renders locally; Telegram creation needs --execute and read-back.",
    ),
    Operation(
        "react_custom_emoji",
        "react_custom_emoji_user_messages.py",
        "telegram_write",
        "React to one user's recent messages with a resolved custom emoji document.",
        ("поставь реакцию кастомным эмодзи", "реакция этим эмодзи", "custom emoji reaction", "react with custom emoji"),
        "venv/bin/python scripts/userbotrun.py --account main modules/react_custom_emoji_user_messages.py --chat '<chat>' --user-name '<name>' --pack-short-name '<pack>' --limit 100",
        "Dry-run freezes the user, document and message IDs; --execute preserves existing reactions.",
    ),
    Operation(
        "profile",
        "profile_settings.py",
        "telegram_write",
        "Inspect/change profile fields or custom emoji status.",
        ("измени профиль", "измени био", "смени юзернейм", "emoji status", "статус эмодзи", "статус профиля", "profile settings"),
        "venv/bin/python scripts/userbotrun.py --account main modules/profile_settings.py --about '<bio>'",
        "Public identity change: preview exact values, then --execute and re-read.",
    ),
    Operation(
        "group_member",
        "group_member.py",
        "telegram_write",
        "Inspect or change one member's group permissions.",
        ("выдай админа", "убери админа", "забань", "ограничи участника", "права участника", "group admin", "restrict member", "kick member"),
        "venv/bin/python scripts/userbotrun.py --account main modules/group_member.py --group '<group>' --user '<user>' --action inspect",
        "Show current rights first. Admin/restrict/kick is high-impact and needs --execute plus read-back.",
    ),
    Operation(
        "react_messages",
        "react_recent_user_messages.py",
        "telegram_write",
        "React to a user's frozen/recent messages without replacing existing reactions.",
        ("поставь реакцию", "реакции на сообщения", "react to messages"),
        "venv/bin/python scripts/userbotrun.py --account main modules/react_recent_user_messages.py --chat '<chat>' --username '<user>' --limit <n> --emoji '<emoji>'",
        "Dry-run freezes IDs; execute preserves existing reactions unless explicitly overridden.",
        ("кастомным эмодзи", "custom emoji"),
    ),
    Operation(
        "mention_members",
        "mention_group_members.py",
        "telegram_write",
        "Prepare a mention of all current non-bot group members.",
        ("отметь всех", "тегни всех", "упомяни всех", "mention everyone"),
        "venv/bin/python scripts/userbotrun.py --account main modules/mention_group_members.py --chat '<chat>' --text '<text>'",
        "Dry-run refreshes membership; sending chunks needs --execute.",
    ),
    Operation(
        "add_contact",
        "add_contact.py",
        "telegram_write",
        "Add one user to Telegram contacts.",
        ("добавь в контакты", "add contact"),
        "venv/bin/python scripts/userbotrun.py --account main modules/add_contact.py --user '<user>'",
        "Dry-run first; never share own phone by default; write needs --execute.",
    ),
    Operation(
        "purge_one_chat",
        "purge_me.py",
        "telegram_write",
        "Plan/delete only the account's outgoing messages in one chat.",
        ("удали мои сообщения", "почисти мои сообщения", "purge my messages"),
        "venv/bin/python scripts/userbotrun.py --account main modules/purge_me.py --chat '<chat>'",
        "Dry-run first. Deletion requires explicit approval and --execute.",
        ("во всех группах", "все группы"),
    ),
    Operation(
        "replace_own_messages",
        "mass_replace_own_messages.py",
        "telegram_write",
        "Replace own text messages and optionally delete own media in one exact chat.",
        ("замени мои сообщения", "перепиши мои сообщения", "mass replace"),
        "venv/bin/python scripts/userbotrun.py --account main modules/mass_replace_own_messages.py --chat '<chat>' --text '<text>'",
        "Dry-run validates one unambiguous chat; write needs --execute.",
    ),
    Operation(
        "purge_all_groups",
        "purge_all_group_messages.py",
        "telegram_write",
        "Plan/delete own messages across eligible groups with exclusions.",
        ("удали мои сообщения во всех группах", "почисти все группы", "purge all groups"),
        "venv/bin/python scripts/userbotrun.py --account main modules/purge_all_group_messages.py --exclude '<keep_chat>'",
        "High-impact bulk deletion. Review every target/exclusion and use --execute only after confirmation.",
    ),
)


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value.casefold()).replace("ё", "е")
    normalized = re.sub(r"[^\w@.+-]+", " ", normalized)
    return " ".join(normalized.split())


def normalized_tokens(value: str) -> set[str]:
    return {
        token
        for token in normalize_text(value).split()
        if len(token) > 1 and token not in STOP_TOKENS
    }


def score(operation: Operation, query: str) -> int:
    raw = normalize_text(query)
    tokens = normalized_tokens(query)
    if not raw:
        return 0

    negative = {
        normalize_text(trigger)
        for trigger in operation.negative_triggers
        if normalize_text(trigger) in raw
    }
    trigger_scores: list[int] = []
    for trigger in operation.triggers:
        normalized = normalize_text(trigger)
        trigger_tokens = normalized_tokens(trigger)
        if normalized in raw:
            trigger_scores.append(12 + min(4, len(trigger_tokens)))
        elif len(trigger_tokens) >= 2 and trigger_tokens <= tokens:
            trigger_scores.append(8 + min(3, len(trigger_tokens)))

    operation_tokens = normalized_tokens(
        " ".join((operation.slug, operation.module, operation.summary, *operation.triggers))
    )
    advisory_overlap = min(5, len(tokens & operation_tokens))
    points = max(trigger_scores, default=0) + advisory_overlap
    if negative and not trigger_scores:
        points -= 20
    return max(0, points)


def operation_payload(operation: Operation, points: int = 0) -> dict[str, Any]:
    payload = asdict(operation)
    payload.pop("negative_triggers", None)
    payload["score"] = points
    payload["confidence"] = round(min(1.0, points / 16), 3) if points else 0.0
    return payload


def module_path(operation: Operation, root: Path = PROJECT_ROOT) -> Path:
    if "/" in operation.module:
        return root / operation.module
    return root / "modules" / operation.module


def validate_catalog(root: Path = PROJECT_ROOT) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    valid_modes = {"read_only", "local_read", "local_write", "telegram_write"}
    for operation in OPERATIONS:
        if operation.slug in seen:
            errors.append(f"duplicate slug: {operation.slug}")
        seen.add(operation.slug)
        if operation.mode not in valid_modes:
            errors.append(f"{operation.slug}: invalid mode {operation.mode}")
        if not operation.triggers:
            errors.append(f"{operation.slug}: no triggers")
        if not module_path(operation, root).is_file():
            errors.append(
                f"{operation.slug}: module not found: {module_path(operation, root).relative_to(root)}"
            )
        if "venv/bin/python " not in operation.command:
            errors.append(f"{operation.slug}: command does not use project venv")
        if "modules/" in operation.command and "scripts/userbotrun.py" not in operation.command:
            errors.append(f"{operation.slug}: direct module command bypasses userbotrun.py")
    return errors


def rank(query: str) -> dict[str, Any]:
    ranked = sorted(
        ((score(item, query), item) for item in OPERATIONS),
        key=lambda value: (-value[0], value[1].slug),
    )
    candidates = [
        operation_payload(operation, points)
        for points, operation in ranked
        if points > 0
    ][:3]
    if not candidates or candidates[0]["score"] < MIN_MATCH_SCORE:
        return {
            "ok": False,
            "status": "no_match",
            "count": 0,
            "operations": [],
            "candidates": candidates,
        }

    if (
        len(candidates) > 1
        and candidates[1]["score"] >= MIN_MATCH_SCORE
        and candidates[0]["score"] - candidates[1]["score"] < AMBIGUITY_MARGIN
    ):
        return {
            "ok": False,
            "status": "ambiguous",
            "count": 0,
            "operations": [],
            "candidates": candidates,
        }

    return {
        "ok": True,
        "status": "match",
        "count": 1,
        "operations": [candidates[0]],
        "candidates": candidates,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Find one high-confidence existing local userbot operation"
    )
    result.add_argument("--query", help="Natural-language request to match")
    result.add_argument("--operation", help="Exact operation slug")
    result.add_argument("--list", action="store_true", help="List registered operations")
    result.add_argument(
        "--validate-catalog",
        action="store_true",
        help="Validate registry files, commands, modes and unique slugs",
    )
    result.add_argument("--json", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    selected = (args.query, args.operation, args.list, args.validate_catalog)
    if sum(bool(value) for value in selected) != 1:
        parser().error(
            "choose exactly one of --query, --operation, --list, or --validate-catalog"
        )

    if args.validate_catalog:
        errors = validate_catalog()
        payload: dict[str, Any] = {
            "ok": not errors,
            "status": "valid" if not errors else "invalid",
            "operation_count": len(OPERATIONS),
            "errors": errors,
        }
    elif args.list:
        matches = [operation_payload(item) for item in OPERATIONS]
        payload = {
            "ok": True,
            "status": "list",
            "count": len(matches),
            "operations": matches,
        }
    elif args.operation:
        item = next((item for item in OPERATIONS if item.slug == args.operation), None)
        if item is None:
            payload = {
                "ok": False,
                "status": "unknown_operation",
                "error": "unknown_operation",
                "operation": args.operation,
            }
        else:
            payload = {
                "ok": True,
                "status": "exact",
                "count": 1,
                "operations": [operation_payload(item)],
            }
    else:
        payload = rank(args.query)

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif payload["status"] in {"valid", "list", "exact", "match"}:
        if payload["status"] == "valid":
            print(f"Catalog valid: {payload['operation_count']} operations")
        else:
            for item in payload.get("operations", []):
                print(f"[{item['slug']}] {item['module']} — {item['summary']}")
                print(f"  mode: {item['mode']}")
                print(f"  command: {item['command']}")
                print(f"  safety: {item['safety']}")
    elif payload["status"] == "ambiguous":
        print("Ambiguous request. Inspect candidates and choose an exact operation:")
        for item in payload["candidates"]:
            print(f"  [{item['slug']}] score={item['score']} — {item['summary']}")
    elif payload["status"] == "no_match":
        print("No high-confidence module matched. Inspect candidates, then author one guarded module if needed.")
    else:
        print(f"Unknown operation: {args.operation}")

    if payload["status"] in {"unknown_operation", "invalid"}:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
