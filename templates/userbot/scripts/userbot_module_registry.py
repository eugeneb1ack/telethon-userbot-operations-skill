#!/usr/bin/env python3
"""Native, read-only router for the local Telethon userbot CLI modules.

This does not connect to Telegram or execute a module. It maps a natural-language
request to a compact command template so smaller models do not need a giant
skill body to rediscover the project surface on every request.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class Operation:
    slug: str
    module: str
    mode: str
    summary: str
    triggers: tuple[str, ...]
    command: str
    safety: str


OPERATIONS = (
    Operation("list_members", "list_group_members.py", "read_only", "List current group/channel members.", ("список участников", "участники", "участников", "состав чата", "members", "participants"), "venv/bin/python scripts/userbotrun.py --account main modules/list_group_members.py --chat '<chat>'", "Bounded read-only listing; bots/deleted accounts are excluded by default."),
    Operation("search_messages", "search_messages.py", "read_only", "Search a bounded chat window using the persistent gateway.", ("поиск сообщений", "найди сообщение", "найти сообщения", "история чата", "search messages", "search chat"), "venv/bin/python scripts/userbotctl.py --account main search --chat '<chat>' --query '<text>' --limit 50", "Fast local-socket read. Use modules/search_messages.py only for sender/date/export filters or gateway fallback."),
    Operation("download_media", "download_media.py", "local_write", "Preview and locally download media from exact message IDs.", ("скачай медиа", "скачай файл", "скачай фото", "скачай видео", "выгрузи медиа", "download media", "download file"), "venv/bin/python scripts/userbotrun.py --account main modules/download_media.py --chat '<chat>' --message-ids <id,id>", "Local file write needs a preview, then --execute; data stays under runtime/<account>/data/downloads."),
    Operation("transcribe_audio", "transcribe_audio_native.py", "read_only", "Native Telegram transcription of one voice/audio/video-note message.", ("расшифруй голосовое", "транскрибируй", "расшифруй аудио", "voice transcription", "transcribe audio"), "venv/bin/python scripts/userbotrun.py --account main modules/transcribe_audio_native.py --chat '<chat>' --message-id <id> --sender-id <expected_sender_id> --json", "For another person's media, always pass and verify --sender-id; do not summarize incomplete transcription."),
    Operation("summarize_chat", "summarize_chat_native.py", "local_write", "Reuse or incrementally update a bounded local dialog summary with native-STT context.", ("саммари чата", "сводка чата", "подведи итоги чата", "summarize chat", "chat recap"), "venv/bin/python scripts/userbotrun.py --account main modules/summarize_chat_native.py --chat '<chat>' --date YYYY-MM-DD --do-summary", "Writes only bounded summary memory and local recovery artifacts; use this route, not legacy summarize.py."),
    Operation("recall_memory", "scripts/userbot_memory.py", "local_read", "Recall compact local knowledge before repeating collection or reasoning.", ("что мы уже знаем", "вспомни сохраненное", "вспомни сохранённое", "достань из памяти", "локальная память", "recall memory"), "venv/bin/python scripts/userbot_memory.py --account main recall --query '<query>' --scope '<scope>'", "Advisory only: revalidate temporal facts and historical task results before current claims or actions."),
    Operation("remember_memory", "scripts/userbot_memory.py", "local_write", "Save one verified, reusable, bounded semantic-memory item.", ("сохрани в память", "запомни это", "занеси в локальную память", "remember this", "save to memory"), "venv/bin/python scripts/userbot_memory.py --account main remember --kind '<kind>' --scope '<scope>' --subject '<subject>' --summary '<summary>' --source '<provenance>'", "Never stores raw messages, transcripts, credentials, unverified inference, or one-off chatter."),
    Operation("count_messages", "count_user_messages.py", "read_only", "Count a participant's messages by type in a time window.", ("посчитай сообщения", "сколько сообщений", "count messages"), "venv/bin/python scripts/userbotrun.py --account main modules/count_user_messages.py --chat '<chat>' --user '<user>' --hours 48", "--send publishes a report and is a Telegram write."),
    Operation("comment_channels", "comment_channels.py", "read_only", "List broadcast channels where the account wrote comments.", ("где я писал комментарии", "каналы с моими комментариями", "мои комментарии в каналах", "comment channels", "where I commented"), "venv/bin/python scripts/userbotrun.py --account main modules/comment_channels.py", "Read-only scan of accessible megagroup dialogs; output contains channel metadata, not message text."),
    Operation("recent_personal_incoming", "recent_personal_incoming.py", "read_only", "List the people who sent the latest incoming messages in personal dialogs, without message text.", ("кто мне написал", "последние личные сообщения", "последние лс", "входящие личные", "recent incoming dms"), "venv/bin/python scripts/userbotctl.py --account main recent-dms --limit 3", "Fast local-socket read. Fall back to the module only when the gateway is unavailable."),
    Operation("event_inbox", "core/gateway.py", "read_only", "List queued direct-message, mention and reply events.", ("уведомления телеграм", "события телеграм", "кто меня отметил", "кто ответил мне", "непрочитанные события", "telegram events", "mentions inbox"), "venv/bin/python scripts/userbotctl.py --account main events list --unread --limit 20", "Local SQLite read through the gateway; acknowledging an event does not write to Telegram."),
    Operation("list_personal_chats", "personal_chats.py", "read_only", "List personal dialogs.", ("личные чаты", "лички", "диалоги", "personal chats", "dms"), "venv/bin/python scripts/userbotctl.py --account main dialogs --kind personal", "Fast local-socket read."),
    Operation("list_groups", "group_chats.py", "read_only", "List group dialogs.", ("список групп", "мои группы", "group chats"), "venv/bin/python scripts/userbotctl.py --account main dialogs --kind groups", "Fast local-socket read."),
    Operation("list_channels", "channel_chats.py", "read_only", "List channel dialogs.", ("список каналов", "мои каналы", "channel chats"), "venv/bin/python scripts/userbotctl.py --account main dialogs --kind channels", "Fast local-socket read."),
    Operation("send_message", "send_message.py", "telegram_write", "Send one message to an exact chat/user.", ("отправь сообщение", "напиши в телеграм", "send message"), "venv/bin/python scripts/userbotrun.py --account main modules/send_message.py --chat '<chat>' --text '<text>'", "Preview first. Telegram send needs explicit user approval and --execute."),
    Operation("edit_message", "message_edit.py", "telegram_write", "Edit one outgoing message, including inline custom emoji in HTML mode.", ("отредактируй сообщение", "измени сообщение", "edit message", "custom emoji в сообщении"), "venv/bin/python scripts/userbotrun.py --account main modules/message_edit.py --chat '<chat>' --message-id <id> --text '<text>'", "Only the account's outgoing message may be edited; execute requires exact read-back."),
    Operation("forward_messages", "forward_messages.py", "telegram_write", "Forward exact frozen message IDs between two chats.", ("перешли сообщение", "форвардни", "перешли в чат", "forward message"), "venv/bin/python scripts/userbotrun.py --account main modules/forward_messages.py --source-chat '<source>' --destination-chat '<destination>' --message-ids <id,id>", "Preview source and destination; forwarding is external disclosure and needs --execute."),
    Operation("pin_message", "pin_message.py", "telegram_write", "Inspect, pin, or unpin one exact message.", ("закрепи сообщение", "открепи сообщение", "пин", "pin message", "unpin"), "venv/bin/python scripts/userbotrun.py --account main modules/pin_message.py --chat '<chat>' --message-id <id> --action pin", "Preview exact message and pin state; change needs --execute."),
    Operation("create_emoji_pack", "create_emoji_pack.py", "telegram_write", "Create one custom emoji pack with a readable text emoji.", ("создай пак эмоджи", "создай пак эмодзи", "emoji pack", "custom emoji pack", "текст пак"), "venv/bin/python scripts/userbotrun.py --account main modules/create_emoji_pack.py --title 'текст пак' --text 'ЖИРНЫЙ' --emoji '💪'", "Dry-run resolves a unique short name and renders locally; Telegram creation needs --execute and exact read-back."),
    Operation("react_custom_emoji", "react_custom_emoji_user_messages.py", "telegram_write", "React to one user's recent messages with a resolved custom emoji document.", ("поставь реакцию кастомным эмодзи", "реакция этим эмодзи", "custom emoji reaction", "react with custom emoji"), "venv/bin/python scripts/userbotrun.py --account main modules/react_custom_emoji_user_messages.py --chat '<chat>' --user-name '<name>' --pack-short-name '<pack>' --limit 100", "Dry-run freezes the exact user ID, pack document ID, and message IDs; --execute preserves existing reactions and verifies every target."),
    Operation("profile", "profile_settings.py", "telegram_write", "Inspect/change profile fields or custom emoji status.", ("измени профиль", "измени био", "смени юзернейм", "emoji status", "статус эмодзи", "profile settings"), "venv/bin/python scripts/userbotrun.py --account main modules/profile_settings.py --about '<bio>'", "Public identity change: preview exact values, resolve custom emoji document ID, then --execute."),
    Operation("group_member", "group_member.py", "telegram_write", "Inspect or change one member's group permissions.", ("выдай админа", "убери админа", "забань", "ограничи участника", "права участника", "group admin", "restrict member", "kick member"), "venv/bin/python scripts/userbotrun.py --account main modules/group_member.py --group '<group>' --user '<user>' --action inspect", "Show current rights first. Admin/restrict/kick is high-impact and needs --execute plus read-back."),
    Operation("react_messages", "react_recent_user_messages.py", "telegram_write", "React to a user's frozen/recent messages without replacing existing account reactions.", ("поставь реакцию", "реакции на сообщения", "react to messages"), "venv/bin/python scripts/userbotrun.py --account main modules/react_recent_user_messages.py --chat '<chat>' --username '<user>' --limit <n> --emoji '<emoji>'", "Dry-run freezes IDs; execute preserves existing reactions unless explicitly overridden."),
    Operation("mention_members", "mention_group_members.py", "telegram_write", "Prepare a mention of all current non-bot group members.", ("отметь всех", "тегни всех", "упомяни всех", "mention everyone"), "venv/bin/python scripts/userbotrun.py --account main modules/mention_group_members.py --chat '<chat>' --text '<text>'", "Dry-run refreshes membership; sending chunks needs --execute."),
    Operation("add_contact", "add_contact.py", "telegram_write", "Add one user to Telegram contacts.", ("добавь в контакты", "add contact"), "venv/bin/python scripts/userbotrun.py --account main modules/add_contact.py --user '<user>'", "Dry-run first; never share own phone by default; write needs --execute."),
    Operation("purge_one_chat", "purge_me.py", "telegram_write", "Plan/delete only the account's outgoing messages in one chat.", ("удали мои сообщения", "почисти мои сообщения", "purge my messages"), "venv/bin/python scripts/userbotrun.py --account main modules/purge_me.py --chat '<chat>'", "Dry-run first. Deletion requires explicit approval and --execute."),
    Operation("replace_own_messages", "mass_replace_own_messages.py", "telegram_write", "Replace own text messages and optionally delete own media in one exact chat.", ("замени мои сообщения", "перепиши мои сообщения", "mass replace"), "venv/bin/python scripts/userbotrun.py --account main modules/mass_replace_own_messages.py --chat '<chat>' --text '<text>'", "Dry-run validates one unambiguous chat; write needs --execute."),
    Operation("purge_all_groups", "purge_all_group_messages.py", "telegram_write", "Plan/delete own messages across eligible groups with exclusions.", ("удали мои сообщения во всех группах", "почисти все группы", "purge all groups"), "venv/bin/python scripts/userbotrun.py --account main modules/purge_all_group_messages.py --exclude '<keep_chat>'", "High-impact bulk deletion. Review every target/exclusion and use --execute only after confirmation."),
)


def normalized_tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[\w@.-]+", value.casefold()) if len(token) > 1}


def score(operation: Operation, query: str) -> int:
    haystack = " ".join((operation.slug, operation.module, operation.summary, *operation.triggers)).casefold()
    raw = query.casefold().strip()
    tokens = normalized_tokens(query)
    points = sum(8 for trigger in operation.triggers if trigger in raw)
    points += sum(1 for token in tokens if token in haystack)
    return points


def operation_payload(operation: Operation, points: int = 0) -> dict[str, object]:
    payload = asdict(operation)
    payload["score"] = points
    return payload


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Find the smallest existing local userbot module for a natural-language request")
    result.add_argument("--query", help="Natural-language request to match")
    result.add_argument("--operation", help="Exact operation slug")
    result.add_argument("--list", action="store_true", help="List all registered operations")
    result.add_argument("--json", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    if sum(bool(value) for value in (args.query, args.operation, args.list)) != 1:
        parser.error("choose exactly one of --query, --operation, or --list")

    if args.list:
        matches = [operation_payload(item) for item in OPERATIONS]
    elif args.operation:
        item = next((item for item in OPERATIONS if item.slug == args.operation), None)
        if item is None:
            print(json.dumps({"ok": False, "error": "unknown_operation", "operation": args.operation}, ensure_ascii=False))
            return 2
        matches = [operation_payload(item)]
    else:
        ranked = sorted(((score(item, args.query), item) for item in OPERATIONS), key=lambda value: (-value[0], value[1].slug))
        matches = [operation_payload(item, points) for points, item in ranked if points > 0][:1]

    payload = {"ok": bool(matches), "count": len(matches), "operations": matches}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif not matches:
        print("No existing module matched. Load userbot and inspect the local API inventory before adding one.")
    else:
        for item in matches:
            print(f"[{item['slug']}] {item['module']} — {item['summary']}")
            print(f"  mode: {item['mode']}")
            print(f"  command: {item['command']}")
            print(f"  safety: {item['safety']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
