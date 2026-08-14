# Telethon Userbot Skill

Private, portable Codex skill **`userbot`** for a local [Telethon](https://docs.telethon.dev/) userbot.

This repository is the single distribution point. It contains exactly one active skill name, its safe operating rules, current module source template, session onboarding, API inventory, and offline checks.

It is not a generic Telegram API cannon. It routes a request to an existing module first; only then does it permit one small, tested extension built against the installed Telethon API and official Telethon/TL documentation.

## What is inside

```text
telethon-userbot-operations-skill/
├── SKILL.md                         # canonical skill: name=userbot
├── INSTALL.md
├── SECURITY.md
├── references/
│   ├── session-bootstrap.md
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
    ├── scripts/userbot_module_registry.py
    ├── tests/
    └── docs/
```

The template has **28 reusable modules** and **23 registry routes**. A context-specific local publisher is deliberately excluded. The package has no account configuration, Telegram session, runtime files, chat exports, media, phone number, API credentials, bot token, or third-party keys.

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

## Install

Use [INSTALL.md](INSTALL.md). On a new Mac it installs one `userbot` skill and can bootstrap a new project from `templates/userbot/`. On a machine that already has a working project, it installs only the skill and leaves the project untouched.

## Private-use notice

Keep this repository private. Never add `.env`, account files, session databases, runtime data, downloaded media, archives, phone numbers, tokens, API hashes, access hashes, or document file references.
