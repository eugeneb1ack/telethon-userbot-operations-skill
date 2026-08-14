# Telethon Userbot Operations Skill

A private Hermes skill for safely extending a local [Telethon](https://docs.telethon.dev/) userbot.

It is **not** a Telegram client or a generic API cannon. It gives an agent a disciplined route from a request to either:

1. an existing local userbot module; or
2. one small, tested, dry-run-first module built against the exact installed Telethon API.

## What it contains

```text
telethon-userbot-operations-skill/
├── SKILL.md
├── INSTALL.md
├── SECURITY.md
├── references/
│   ├── operation-playbook.md
│   └── module-authoring.md
└── scripts/
    ├── telethon_api_inventory.py
    └── validate_package.py
```

- **`SKILL.md`** — compact agent routing and safety contract.
- **`telethon_api_inventory.py`** — read-only local introspection of installed raw Telethon requests and `TelegramClient` methods.
- **`operation-playbook.md`** — maps everyday tasks to the right Telethon surface.
- **`module-authoring.md`** — deterministic implementation checklist and skeleton for a smaller coding model.
- **`validate_package.py`** — no-network package sanity check.

## Prerequisites

This package assumes a separate local userbot project with:

- Python virtual environment containing Telethon;
- a `core.config` module which loads account profiles;
- an already-authorized Telegram user session;
- a module registry at `scripts/userbot_module_registry.py` when available.

Default paths are configurable:

```bash
export USERBOT_ROOT="$HOME/Documents/telethon-userbot"
export USERBOT_PY="$USERBOT_ROOT/venv/bin/python"
```

The skill never stores API IDs, hashes, phone numbers, bot tokens, session files, chat IDs, or emoji document IDs.

## Fast path for an agent

```bash
cd "$USERBOT_ROOT"
"$USERBOT_PY" scripts/userbot_module_registry.py \
  --query 'find messages from Alice in our group yesterday'
```

If a local module is returned, use it. For a new capability, inspect an exact installed request before coding:

```bash
"$USERBOT_PY" "$SKILL_DIR/scripts/telethon_api_inventory.py" \
  --request messages.EditMessageRequest
```

## Custom emoji: four separate operations

“Custom emoji” is not one API. The skill separates:

| Intent | API surface |
|---|---|
| Emoji inline in a message/caption | `send_message` / `edit_message` + HTML `<tg-emoji>` entity |
| Custom emoji reaction | `types.ReactionCustomEmoji` + `messages.SendReactionRequest` |
| Profile/channel emoji status | `account.UpdateEmojiStatusRequest` / `channels.UpdateEmojiStatusRequest` |
| Emoji pack lifecycle | `stickers.CreateStickerSetRequest`, add/replace/change/remove requests |

Read the playbook before choosing one. Do not fabricate Telegram document access hashes or file references.

## Safety model

Every write follows:

```text
resolve exact target → dry-run plan → explicit approval → --execute → read-back
```

The module authoring guide requires bounded batches, one FloodWait retry, immutable candidate IDs, fake-client tests, and actual server-state verification.

## Validate this package

No Telegram connection or network request is made:

```bash
python3 scripts/validate_package.py
python3 -m py_compile scripts/telethon_api_inventory.py
```

## Updating the package

```bash
git pull --ff-only
python3 scripts/validate_package.py
```

Then replace or sync the installed skill directory according to [INSTALL.md](INSTALL.md), and start a new Hermes session or use `/reset`.

## Private-use notice

This repository is designed for private deployment. Do not publish userbot sessions, account configuration, runtime exports, media downloads, private chat archives, or secrets alongside it.
