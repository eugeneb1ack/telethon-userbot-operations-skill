---
name: telethon-userbot-operations
description: "Use for any local Telethon userbot operation."
version: 1.2.0
---

# Telethon Userbot Operations

Use this skill when the local Telethon userbot needs an operation that is not already routed by its module registry: custom emoji packs, stickers, stories, invite links, folders, privacy, forums, or unusual group/channel settings.

## Runtime contract

Set these once for the current machine/profile:

```bash
export USERBOT_ROOT="${USERBOT_ROOT:-$HOME/Documents/telethon-userbot}"
export USERBOT_PY="$USERBOT_ROOT/venv/bin/python"
```

The project must have an existing authorized session. Every direct helper must use `core.config.load_settings(account)` and `apply_runtime_env(settings)`, call `connect()`, require `is_user_authorized()`, and disconnect in `finally`. Never hard-code credentials or invoke interactive `client.start()`.

## Choose the smallest existing route first

Before designing a new helper, ask the project’s read-only registry:

```bash
cd "$USERBOT_ROOT"
"$USERBOT_PY" scripts/userbot_module_registry.py --query '<natural-language request>'
```

If it returns a module, use that CLI. Do not create a one-off or a duplicate module.

## Verify exact Telethon API contracts

If the registry has no match, query the installed Telethon schema before writing code:

```bash
SKILL_DIR="${SKILL_DIR:-$HOME/.hermes/skills/social-media/telethon-userbot-operations}"
"$USERBOT_PY" "$SKILL_DIR/scripts/telethon_api_inventory.py" --request messages.EditMessageRequest
"$USERBOT_PY" "$SKILL_DIR/scripts/telethon_api_inventory.py" --namespace stickers --json
"$USERBOT_PY" "$SKILL_DIR/scripts/telethon_api_inventory.py" --client --query edit_message
```

Then consult the official page printed by the inventory:

- <https://docs.telethon.dev/en/stable/modules/client.html>
- <https://docs.telethon.dev/en/stable/concepts/errors.html>
- `https://tl.telethon.dev/methods/<namespace>/<request>.html`

## Universal write boundary

1. Resolve the exact peer/object; fail closed on ambiguity.
2. Output a dry-run plan by default, including frozen IDs/current state.
3. Add `--execute` only after explicit user approval for the external action.
4. On `FloodWaitError`, wait `seconds + 1` and retry the same unit once.
5. Read back exact server state. An attempted request is not success.
6. Add fake-client tests and update the userbot registry plus `MODULES.md`.

Use `references/module-authoring.md` for the required module shape. Use `references/operation-playbook.md` for API routing and custom-emoji distinctions.

## Never generic-automate

Do not build an unrestricted raw-request runner. Keep `auth`, passwords/recovery/passkeys, phone changes, account deletion, payments/Stars/gifts/refunds, SMS jobs, and secret-chat internals manual unless a narrowly reviewed owner task explicitly calls for them.
