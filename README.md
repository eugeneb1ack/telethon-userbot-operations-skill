# Telethon Userbot Skill

Private, portable, agent-neutral skill **`userbot`** for a local [Telethon](https://docs.telethon.dev/) userbot.

This repository is the single distribution point. It contains one active skill name, an auto-starting idle-bounded local JSON gateway, a durable Telegram event inbox, signed outbound webhooks, the current module source template, session onboarding, API inventory, and offline checks.

It is not a generic Telegram API cannon. It routes a request to an existing module first; only then does it permit one small, tested extension built against the installed Telethon API and official Telethon/TL documentation.

## What is inside

```text
telethon-userbot-operations-skill/
├── SKILL.md                         # canonical skill: name=userbot
├── INSTALL.md
├── SECURITY.md
├── references/
│   ├── session-bootstrap.md
│   ├── gateway-webhooks.md
│   ├── module-authoring.md
│   ├── operation-playbook.md
│   └── channel-rich-publishing.md
├── scripts/
│   ├── bootstrap_userbot_project.py  # create a NEW project only
│   ├── verify_userbot_session.py     # offline / explicit online readiness check
│   ├── telethon_api_inventory.py     # zero-network API introspection
│   └── test_*.py / validate_package.py
└── templates/userbot/                # secret-free current project template
    ├── core/
    ├── modules/
    ├── scripts/userbotctl.py           # one-command on-demand gateway RPC
    ├── scripts/userbotrun.py           # locked, timeout-bounded direct modules
    ├── scripts/check_module.py         # deterministic generated-module gate
    ├── scripts/userbot_module_registry.py
    ├── tests/
    └── docs/
```

The registry and module catalog are validated from source instead of documented with a manually maintained count. A context-specific local publisher is deliberately excluded. The package has no account configuration, Telegram session, runtime files, chat exports, media, phone number, API credentials, bot token, webhook secret, or third-party keys.

## Fast on-demand access

Agents call one command. It reuses a live mode-`0600` Unix socket or starts a
`gateway-only` process and retries automatically. The on-demand owner exits 60
seconds after the last local RPC:

```bash
cd "$USERBOT_ROOT"
venv/bin/python scripts/userbotctl.py --account main status
venv/bin/python scripts/userbotctl.py --account main recent-dms --limit 3
venv/bin/python scripts/userbotctl.py --account main events list --unread
```

Incoming direct messages, mentions and replies are deduplicated in a local SQLite inbox. An optional HMAC-signed HTTPS webhook delivers the same compact events to any external workflow or agent. See [references/gateway-webhooks.md](references/gateway-webhooks.md).

One account lock prevents concurrent clients from opening the same Telethon
SQLite session. Registry direct modules run through `scripts/userbotrun.py`,
which stops only an idle gateway and terminates a stuck module after a bounded
timeout.

## Current routed capabilities

The live project registry is authoritative. At the current package revision it routes:

| Area | Existing routes |
|---|---|
| Read-only | members, history search, media preview/download plan, native transcription, native summary, message count, personal/group/channel dialog lists |
| Messages | send, edit own message, forward exact IDs, inspect/pin/unpin exact message |
| Identity and custom emoji | inspect/update profile, custom emoji status, create emoji pack, react using custom emoji |
| Group work | inspect/change one member’s permissions, reactions, mention plan, cleanup own messages across one/all eligible chats |
| Contacts | add one contact |
| Channel publication | technical contract for an explicitly approved rich-media channel post; a new dedicated module is required when the registry has no route |

Run the actual local router before every task; it does no Telegram I/O:

```bash
cd "$USERBOT_ROOT"
"$USERBOT_PY" scripts/userbot_module_registry.py --query '<request>'
```

## Bounded self-improvement

When no route exists, the canonical `userbot` skill tells an agent to:

1. query the installed Telethon API with `telethon_api_inventory.py`;
2. read the matching official Telethon/TL documentation;
3. implement one narrow module using central account config and an existing authorized session;
4. default every Telegram write to dry-run, require explicit `--execute`, and verify actual state afterwards;
5. add fake-client tests, update the registry and `MODULES.md`, then run the full offline suite.
6. pass `scripts/check_module.py modules/<name>.py --full` before the module is used.

That is deliberate, bounded maintenance — not autonomous uncontrolled rewriting. The skill does not overwrite a working project, request secrets, run interactive login, or publish a new GitHub revision without explicit owner direction.

## Session boundary

The owner creates and authorizes a session locally. An agent never receives or operates Telegram login code, 2FA, `.session`, API hash, phone number, or account env contents.

Use [references/session-bootstrap.md](references/session-bootstrap.md) for the exact first-login, import, and verifier procedure.

## Validate the repository

No command below contacts Telegram or makes a network request:

```bash
python3 scripts/validate_package.py
python3 scripts/test_bootstrap_userbot_project.py
python3 scripts/test_session_checker.py
```

## Install in Codex or another agent

Use [INSTALL.md](INSTALL.md). Codex uses `~/.codex/skills/userbot`; another agent can use its documented skill directory or call the JSON CLI directly. All agents share one runtime and must not open separate copies of `userbot.session`.

## Private-use notice

Keep this repository private. Never add `.env`, account files, session databases, runtime data, downloaded media, archives, phone numbers, tokens, API hashes, access hashes, or document file references.
