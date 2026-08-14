---
name: userbot
description: Use for any local Telethon userbot request.
version: 2.1.0
---

# Telethon Userbot

This is the **single canonical skill** for the local Telethon userbot.

- Project runtime: `$USERBOT_ROOT`, default `$HOME/Documents/telethon-userbot`.
- Python: `$USERBOT_PY`, default `$USERBOT_ROOT/venv/bin/python`.
- Existing userbot modules are the execution layer.
- This skill routes a request, enforces safety, and says when a new module is justified.

```bash
export USERBOT_ROOT="${USERBOT_ROOT:-$HOME/Documents/telethon-userbot}"
export USERBOT_PY="$USERBOT_ROOT/venv/bin/python"
export USERBOT_SKILL_DIR="${USERBOT_SKILL_DIR:-$HOME/.hermes/skills/openclaw-imports/userbot}"
```

For a named Hermes profile, set `USERBOT_SKILL_DIR` to that profile’s installed `userbot` directory explicitly.

## Portable bootstrap

This skill bundles a secret-free source template with the current module catalog. If `$USERBOT_ROOT` does not exist, plan a new project first:

```bash
python3 "$USERBOT_SKILL_DIR/scripts/bootstrap_userbot_project.py" \
  --destination "$USERBOT_ROOT"
```

Only after the owner explicitly approves creation of that **new** folder:

```bash
python3 "$USERBOT_SKILL_DIR/scripts/bootstrap_userbot_project.py" \
  --destination "$USERBOT_ROOT" --execute
```

The bootstrap refuses an existing destination. Never use it to overwrite, merge into, or “upgrade” a working userbot. The template contains source code and 23 registry routes, but no `.env`, session, runtime data, or account material.

## One route for every request

Start with the project’s read-only registry. It performs no Telegram I/O and returns an existing module whenever possible:

```bash
cd "$USERBOT_ROOT"
"$USERBOT_PY" scripts/userbot_module_registry.py --query '<natural-language request>'
```

Use the returned CLI, not an ad-hoc script. The registry covers members, history search, downloads, transcription, summaries, profile/custom emoji status, messages, forwarding, pins, group permissions, reactions, contacts, and cleanup.

## Session onboarding

Load `references/session-bootstrap.md` for setup, login, importing a `.session`, repair, or authorization verification.

- Only trusted local `./run.sh --account <name>` may start interactive login.
- The owner types Telegram code and 2FA directly into that terminal.
- Agents never ask for, read, type, copy, upload, or commit codes, passwords, `.session`, API hash, phone numbers, or account env files.
- Check readiness without exposing contents:

```bash
"$USERBOT_PY" "$USERBOT_SKILL_DIR/scripts/verify_userbot_session.py" \
  --project-root "$USERBOT_ROOT" --account main

# Explicit read-only authorization probe; it never starts login.
"$USERBOT_PY" "$USERBOT_SKILL_DIR/scripts/verify_userbot_session.py" \
  --project-root "$USERBOT_ROOT" --account main --online
```

## Runtime safety contract

- Direct helpers use `load_settings()` + `apply_runtime_env()`, `connect()`, `is_user_authorized()`, and `disconnect()` in `finally`. Never `client.start()`.
- Read-only work may proceed after exact source/target resolution.
- Telegram writes and local downloads plan first. Add `--execute` only after explicit approval for the exact external action.
- Freeze IDs before batches. Fail closed on ambiguity. Respect `FloodWaitError` once per unit. Read back final state.
- Never report an attempted request as success.

## When no module exists

1. Inspect installed API with this bundled inventory:
   ```bash
   "$USERBOT_PY" "$USERBOT_SKILL_DIR/scripts/telethon_api_inventory.py" \
     --request messages.EditMessageRequest
   ```
2. Open the official URL returned by inventory: Telethon client docs, RPC errors, and matching TL schema.
3. Read `references/module-authoring.md`.
4. Add one focused module under `modules/`, then update the registry and `MODULES.md`.

This is the bounded self-improvement loop: reuse first; otherwise inspect the installed API and official documentation; add one tested module; update its route and docs. Do not silently rewrite unrelated modules, turn it into a generic Telegram API wrapper, or publish a new package revision without an explicit owner request.

For custom emoji, distinguish inline entities, reactions, profile/channel status, and emoji packs. Use `references/operation-playbook.md` before choosing an API.

For an explicitly approved channel post with local media, use `references/channel-rich-publishing.md`. If the registry has no purpose-built module, add one through the authoring contract; do not bypass the dry-run/read-back boundary with an ad-hoc send.

Never build a generic raw-request runner or automate auth, recovery, passkeys, phone changes, account deletion, payments/Stars/gifts/refunds, SMS jobs, or secret-chat internals.

## Verification after code changes

```bash
cd "$USERBOT_ROOT"
venv/bin/python -m compileall -q . -x '/(venv|\.git|__pycache__)'
venv/bin/python -m unittest discover -s tests -v
venv/bin/python -m pip check
for f in modules/*.py; do venv/bin/python "$f" --help >/dev/null; done
```

`userbot` is the only installed skill name. Do not create aliases or duplicate operational guides.