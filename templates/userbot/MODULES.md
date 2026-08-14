# Telethon Userbot Modules

Canonical project root: `/Users/johndoe/Documents/telethon-userbot`.

`core/module_loader.py` imports every `modules/*.py`, but only files exposing `register(client)` attach an in-process handler. A direct CLI helper does nothing until it is invoked explicitly.

For agents and natural-language requests, first run the read-only router:

```bash
venv/bin/python scripts/userbot_module_registry.py --query '<request>'
```

It selects an existing module and prints an exact command template; only use the canonical skill’s Telethon API inventory and playbook when it returns no match.

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
| `transcribe_audio_native.py` | Native Telegram audio transcription via `messages.TranscribeAudioRequest`. |
| `summarize_chat_native.py` | Moscow-time bounded chat collection, native STT and local archive/context generation. |
| `count_user_messages.py` | Count one user’s messages for a time window; only `--send` publishes its report. |
| `personal_chats.py`, `group_chats.py`, `channel_chats.py`, `bot_chats.py` | JSON/text dialog inventory CLIs. |
| `profile_settings.py` | Inspect own profile and prepare changes to name, bio, username, or custom-emoji status. Custom emoji documents are resolved before a status change. |
| `group_member.py` | Inspect one group/channel member’s current permissions. |
| `search_messages.py` | Bounded text/sender/date search with compact previews only; optional exports are contained in `runtime/<account>/data/searches/`. |
| `list_group_members.py` | Read-only bounded participant list. Bots/deleted accounts are excluded unless explicitly included; optional exports are contained in runtime data. |

### Telegram-writing helpers

| Module | Write guard |
|---|---|
| `send_message.py` | Dry-run by default; `--execute` sends and then reads the message back. |
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
- Any Telegram write is dry-run/preview first unless an outgoing command is intentionally typed by the owner in Telegram.
- New module authors must follow `docs/TELETHON_MODULE_AUTHORING.md`; it is the implementation contract for weaker coding models too.

## Validation

```bash
cd /Users/johndoe/Documents/telethon-userbot
venv/bin/python -m compileall -q . -x '/(venv|\.git|__pycache__)/'
venv/bin/python -m unittest discover -s tests -v
for f in modules/*.py; do venv/bin/python "$f" --help >/dev/null; done
```
