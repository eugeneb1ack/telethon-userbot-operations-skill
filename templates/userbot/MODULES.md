# Telethon Userbot Modules

Canonical project root: `$USERBOT_ROOT` (the current userbot checkout).

`core/module_loader.py` imports every `modules/*.py`, but only files exposing `register(client)` attach an in-process handler. A direct CLI helper does nothing until it is invoked explicitly.

For agents and natural-language requests, first run the read-only router:

```bash
venv/bin/python scripts/userbot_module_registry.py --query '<request>' --json
```

Use `status=match` only. Clarify `ambiguous`; on `no_match`, inspect candidates and author a guarded module only if none fits. Validate catalog/file/runner consistency with `--validate-catalog --json`.

## Local semantic memory

`scripts/userbot_memory.py` recalls and revision-updates compact reusable knowledge in `runtime/<account>/data/userbot_memory.sqlite3`. It is a local CLI, not a Telethon module: it never loads account credentials, connects to Telegram, or acquires the session lock.

```bash
venv/bin/python scripts/userbot_memory.py --account main recall \
  --query '<compact query>' --scope '<scope>'
venv/bin/python scripts/userbot_memory.py --account main remember \
  --kind procedure --scope operation:<slug> --subject '<subject>' \
  --summary '<verified reusable result>' --source '<compact provenance>'
venv/bin/python scripts/userbot_memory.py --account main stats
```

Recall is advisory: temporal facts and historical task results require live revalidation before they support a current claim or Telegram action. Memory never bypasses target resolution, dry-run, approval, or final read-back. See the installed skill's `references/semantic-memory.md` for the schema, retention, privacy, and freshness contract.

## Persistent gateway

`main.py` starts `core/gateway.py` before ordinary modules. It owns the authorized client, stores incoming direct-message/mention/reply events in `runtime/<account>/events.sqlite3`, exposes `runtime/<account>/userbot.sock`, and optionally delivers HMAC-signed webhooks. The inbox is capped at 10,000 events, retains at most 2,000 acknowledged events, stops a webhook after 12 failed attempts, and secures the database/WAL/SHM as owner-only files.

Use `scripts/userbotctl.py` for gateway-backed reads:

```bash
venv/bin/python scripts/userbotctl.py --account main status
venv/bin/python scripts/userbotctl.py --account main recent-dms --limit 3
venv/bin/python scripts/userbotctl.py --account main dialogs --kind groups
venv/bin/python scripts/userbotctl.py --account main search --chat '@chat' --query 'text'
venv/bin/python scripts/userbotctl.py --account main events list --unread
```

`userbotctl.py` starts an idle-bounded gateway automatically. Direct helpers are
a compatibility fallback and must use the command returned by the registry:
`scripts/userbotrun.py` serializes account ownership and applies a hard timeout.
Do not invoke a direct helper bare while any gateway owns the session.

## In-process userbot handlers

| Module | Role | Safety boundary |
|---|---|---|
| `ping.py` | Outgoing `.ping` → `pong`. | Commands are outgoing-only. |
| `personal_chats.py` | Outgoing `.dms`; lists personal dialogs. | Never responds to incoming commands. |
| `group_chats.py` | Outgoing `.groups`; lists group dialogs. | Never responds to incoming commands. |
| `channel_chats.py` | Outgoing `.channels`; lists channels. | Never responds to incoming commands. |
| `bot_chats.py` | Outgoing `.bots`; lists bot dialogs. | Never responds to incoming commands. |
| `add_contact.py` | Outgoing `.addcontact @username`. | Direct CLI is dry-run by default; write requires `--execute`. |
| `purge_me.py` | Outgoing `.purgeme [chat]`; plans deletion of only the owner’s outgoing messages. | Handler and CLI dry-run by default; deletion requires `.purgeme --execute [chat]` or CLI `--execute`, then verifies what remains. |
| `summarize.py` | Legacy `.summarize` and legacy external STT/OpenRouter pipeline. | Outgoing-only; do not use for new summaries. |
| `smart_assistant.py` | Optional bot-assisted responder. | Experimental: may send chat material to external services when enabled. Bot controls are owner-only. |

## Direct helpers

### Read or local-output first

| Module | Role |
|---|---|
| `transcribe_audio_native.py` | Native Telegram audio transcription via `messages.TranscribeAudioRequest`. Exact-ID mode stays frozen; `--latest-voice --sender-id ...` performs a live sender-scoped selection, follows one newly arrived voice, and fails closed if the tail moves again. |
| `dialog_updates_native.py` | Freshness-checked live dialog slices for text, voice, or mixed content. Supports latest-N, unseen-after-cursor, exact anchors, and latest-outgoing anchors; advances an ID-only content cursor only after complete STT and a stable tail check. |
| `summarize_chat_native.py` | Moscow-time bounded chat collection, queued native STT, atomic archive/progress recovery, and bounded SQLite dialog-summary tables with recent-tail validation, delta collection, and revision-checked commit. |
| `count_user_messages.py` | Count one user’s messages for a time window; only `--send` publishes its report. |
| `comment_channels.py` | List broadcast channels linked to accessible discussion groups where the account wrote reply/comment messages. | Read-only; message text is not returned. |
| `owned_channels.py` | List broadcast channels created and owned by the current account. |
| `list_forum_topics.py` | List or title-filter forum topics in one exact group/channel. |
| `list_blocked_users.py` | Return a bounded, minimal snapshot of currently blocked users. |
| `recent_personal_incoming.py` | List recent incoming personal-dialog senders with timestamps only; message text and media are not returned. |
| `personal_chats.py`, `group_chats.py`, `channel_chats.py`, `bot_chats.py` | JSON/text dialog inventory CLIs. |
| `profile_settings.py` | Inspect own profile and prepare changes to name, bio, username, or custom-emoji status. Custom emoji documents are resolved before a status change. |
| `group_member.py` | Inspect one group/channel member’s current permissions. |
| `search_messages.py` | Bounded text/sender/date search with compact previews only; optional exports are contained in `runtime/<account>/data/searches/`. |
| `list_group_members.py` | Read-only bounded participant list. Bots/deleted accounts are excluded unless explicitly included; optional exports are contained in runtime data. |

### Telegram-writing helpers

| Module | Write guard |
|---|---|
| `send_message.py` | Dry-run validates the target and optional `--reply-to`; `--execute` sends and verifies text and reply parent. |
| `send_photo.py` | Dry-run verifies one local image and target; `--execute` sends and reads the message back. |
| `richtext.py` | Validates allowlisted Telegram HTML; `--execute` edits one owned message and verifies text/entities. |
| `rich_article.py` | Publishes one structured Rich Message article to a broadcast channel through Telethon's `rich_message` field. Accepts Rich HTML or Rich Markdown, defaults to dry-run, blocks recent same-title duplicates, and verifies the returned rich blocks. |
| `message_edit.py` | Dry-run by default; edits one of the owner’s outgoing messages only, with exact read-back. Supports inline custom emoji through `--parse-mode html`. |
| `forward_messages.py` | Dry-run freezes source message IDs and previews both peers; `--execute` forwards those exact IDs and reads destination messages back. |
| `create_emoji_pack.py` | Dry-run renders one exact text emoji and freezes an available short name; `--execute` creates the custom emoji pack and reads back title, count, emoji mapping, and document. |
| `react_custom_emoji_user_messages.py` | Dry-run freezes exact group/user/pack/message IDs; `--execute` applies a custom emoji reaction while preserving existing reactions and verifies every target. |
| `profile_settings.py` | `--execute` changes only explicitly supplied profile fields or emoji status, then re-reads the profile. These are public identity changes. |
| `group_member.py` | `--execute` can grant/revoke admin rights, add a finite restriction, remove restrictions, or kick one non-self member after showing current permissions. |
| `download_media.py` | Dry-run previews exact media message IDs and contained local destinations. `--execute` writes files only below `runtime/<account>/data/downloads/` and refuses overwrite by default. |
| `pin_message.py` | Inspects exact message pin state. `--execute` pins/unpins exactly that ID and checks it again. |
| `mention_group_members.py` | Dry-run by default; `--execute` sends mention chunks. |
| `react_recent_user_messages.py` | Dry-run by default; `--execute` applies reactions. Existing reactions are preserved unless `--replace-existing` is explicit. |
| `mass_replace_own_messages.py` | Dry-run by default; `--execute` edits/deletes only the owner’s messages after unambiguous chat resolution. Service rows are reported, not repeatedly deleted as media. |
| `purge_all_group_messages.py` | Dry-run by default; `--execute` deletes the owner’s messages in eligible groups. Linked discussion/comment chats fail closed unless explicitly included. |


## Runtime contract

- Runtime dependencies are pinned in `requirements.txt` (Telethon `1.44.0`).
- Account config lives in `accounts/<name>.env`; shared integration config can live in `accounts/_shared.env`.
- Account sessions and generated data are isolated below `runtime/<account>/`.
- Direct helpers connect only to an already-authorized session. They must not start an interactive Telegram login.
- The gateway is the preferred single session owner; common reads use its local socket without another Telegram connection.
- Any Telegram write is dry-run/preview first unless an outgoing command is intentionally typed by the owner in Telegram.
- New module authors must follow `docs/TELETHON_MODULE_AUTHORING.md`; it is the implementation contract for weaker coding models too.

## Validation

```bash
cd "${USERBOT_ROOT:?set USERBOT_ROOT to the userbot project}"
venv/bin/python scripts/userbot_module_registry.py --validate-catalog --json
venv/bin/python scripts/check_module.py modules/<changed_module>.py --full
```
