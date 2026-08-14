---
name: userbot
description: Operate and extend a local agent-neutral Telethon userbot through its persistent gateway and existing modules. Use to inspect Telegram data, receive direct-message/mention/reply events, send or modify messages, manage groups or profiles, configure signed webhooks, bootstrap a userbot, verify a session, or add one guarded Telethon module.
---

# Telethon Userbot

This is the single canonical, agent-neutral skill for the local Telethon userbot.

- Project runtime: `$USERBOT_ROOT`, default `$HOME/Documents/telethon-userbot`.
- Python: `$USERBOT_PY`, default `$USERBOT_ROOT/venv/bin/python`.
- The persistent gateway owns the Telegram connection and serves local JSON over a Unix socket.
- Existing modules remain the compatibility and extension layer.

```bash
export USERBOT_ROOT="${USERBOT_ROOT:-$HOME/Documents/telethon-userbot}"
export USERBOT_PY="$USERBOT_ROOT/venv/bin/python"
# Other agents override this with their own installed skill directory.
export USERBOT_SKILL_DIR="${USERBOT_SKILL_DIR:-$HOME/.codex/skills/userbot}"
```

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

The bootstrap refuses an existing destination. Never use it to overwrite, merge into, or “upgrade” a working userbot. The template contains source code and a generated registry catalog, but no `.env`, session, runtime data, or account material.

## Fast path

Prefer the already-running local gateway. These commands use no new Telegram connection:

```bash
cd "$USERBOT_ROOT"
"$USERBOT_PY" scripts/userbotctl.py --account main status
"$USERBOT_PY" scripts/userbotctl.py --account main recent-dms --limit 3
"$USERBOT_PY" scripts/userbotctl.py --account main events list --unread
```

If the socket is unavailable, start it on demand without login prompts or autostart, then retry:

```bash
"$USERBOT_PY" scripts/userbotd.py --account main start
```

Routine read-only gateway calls and local event acknowledgements may proceed immediately. If on-demand startup fails, use the registry's read-only fallback; do not request a separate conversational confirmation merely to connect or read. Stop the detached gateway with `userbotd.py --account main stop`. Never install autostart unless the owner explicitly requests it.

## One route for every request

Start with the project’s read-only registry. It performs no Telegram I/O and returns an existing module whenever possible:

```bash
cd "$USERBOT_ROOT"
"$USERBOT_PY" scripts/userbot_module_registry.py --query '<natural-language request>'
```

Use the single returned command, not an ad-hoc script. The registry prefers gateway routes for common reads and existing guarded modules for the rest.

## Session onboarding

Load `references/session-bootstrap.md` for setup, login, importing a `.session`, repair, or authorization verification.

- Only trusted local `./run.sh --account <name>` may start interactive login.
- The owner types Telegram code and 2FA directly into that terminal.
- For a new or placeholder account profile, have the owner run `python3 scripts/setup_account.py --account <name>` locally. It validates the values, hides the API hash, and keeps the phone number visible for the owner to verify.
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

- The gateway is the preferred session owner. Direct helpers are fallback and use `connect()` + `is_user_authorized()` without interactive login.
- Read-only Telegram work and contained local outputs may proceed once required inputs are known.
- Sending, editing, deleting, forwarding, reacting, changing permissions/profile, or otherwise writing to Telegram requires an exact preview and explicit approval before `--execute`.
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

For the persistent gateway, on-demand process, event inbox, webhook payload, HMAC verification, and explicitly optional autostart, load `references/gateway-webhooks.md`.

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
