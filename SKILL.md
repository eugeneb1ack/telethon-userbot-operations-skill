---
name: userbot
description: Install, operate, and extend a local agent-neutral Telethon userbot through its on-demand gateway, bounded semantic memory, and guarded modules. Use to guide an owner through safe setup, inspect Telegram data, reuse and revalidate compact local knowledge, receive direct-message/mention/reply events, send or modify messages, manage groups or profiles, configure signed webhooks, bootstrap a userbot, verify a session, or add and validate one guarded Telethon module.
---

# Telethon Userbot

Use this skill as the canonical operational contract for the local Telethon runtime.

```bash
export USERBOT_ROOT="${USERBOT_ROOT:-$HOME/Documents/telethon-userbot}"
export USERBOT_PY="${USERBOT_PY:-$USERBOT_ROOT/venv/bin/python}"
: "${USERBOT_SKILL_DIR:?Set USERBOT_SKILL_DIR to this installed userbot skill folder}"
```

One process owns each authorized account session. Prefer the short-lived local gateway for supported reads and `scripts/userbotrun.py` for direct modules. Never open the same `.session` from a second process.

## Resolve runtime access before the first command

Read `references/runtime-access.md` when the runtime is outside the current workspace, the harness permissions are unknown, or an earlier task saw `PermissionError`/`Operation not permitted`.

- Resolve `USERBOT_ROOT` and compare its runtime state paths with the harness's current filesystem and local-socket permissions before executing a runtime command.
- A Telegram read can still mutate local state: SQLite recall/cache validation uses WAL/SHM, expiry/LRU metadata and validation timestamps; gateway routes also use logs, locks, PID/socket files, and bounded archives. Do not describe the local operation as filesystem read-only.
- If the required runtime path is outside the active boundary and the harness supports scoped elevation, send the exact canonical command through that native narrow path on the first attempt. In Codex, use the exact command with `require_escalated` instead of first running a sandboxed command that is expected to fail.
- If the harness supports no scoped elevation, request the smallest runtime root/profile change before execution. Do not search for an allegedly “unblocked” copy, move/copy the SQLite database into the workspace or `/tmp`, grant a generic Python interpreter prefix, or weaken the whole sandbox.
- Treat host access as transport only. It never replaces Telegram write preview/approval, target resolution, or read-back.

## Route every request

First query the registry; it is local and performs no Telegram I/O:

```bash
cd "$USERBOT_ROOT"
"$USERBOT_PY" scripts/userbot_module_registry.py --query '<request>' --json
```

- `status=match`: use the one returned command.
- `status=ambiguous`: ask for or derive the missing target/action detail; never pick a candidate silently.
- `status=no_match`: inspect the advisory candidates, then follow “Add one missing operation” below if none actually fits.
- For common reads, the selected command uses `scripts/userbotctl.py`, which reuses or starts a `gateway-only` process and stops after idle time.
- A direct module command must use `scripts/userbotrun.py`; never invoke it bare.

Routine reads and local event acknowledgement do not need conversational approval. Telegram writes do.

## Bounded reusable memory

Read `references/semantic-memory.md` when a request may reuse an earlier verified fact, preference, decision, procedure, entity context, or task result. The CLI is local and does not connect to Telegram:

```bash
"$USERBOT_PY" scripts/userbot_memory.py --account main recall \
  --query '<compact query>' --scope '<narrow stable scope>'
```

Before repeating expensive collection or reasoning, recall narrowly. Revalidate `temporal` items at their source and treat `historical` task results only as evidence of what happened then. After a result is verified, save it only if it is compact, reusable, source-bounded, and likely to avoid meaningful repeated work. Valid kinds are `fact`, `preference`, `decision`, `procedure`, `entity_context`, or `task_result`.

Never store raw messages, transcripts, media, exports, credentials, auth/session material, phone numbers, speculative profiles, unverified inference, or routine one-off answers. Use stable keys and expected revisions for updates. Identical records deduplicate; expiry and per-account/per-scope caps bound growth.

A memory hit never authorizes an external action and never replaces current target resolution, dry-run, explicit approval, frozen IDs, or read-back.

## Runtime safety

- Read-only Telegram work and contained local output may proceed when inputs are known.
- Sending, editing, deleting, forwarding, reacting, changing permissions/profile, or any other Telegram mutation requires an exact preview and explicit owner approval before `--execute`.
- Resolve exact entities and IDs. Fail closed on fuzzy or multiple matches.
- Freeze batch IDs before execution. Retry a `FloodWaitError` at most once per unit. Read exact final server state back.
- Never report an attempted or partially verified request as success.
- Never expose credentials, account env files, session files, private histories, transcripts, or unnecessary identifiers.

For gateway lifecycle, the bounded event inbox, webhook HMAC, or the optional current-login supervisor, read `references/gateway-webhooks.md`.

## Session setup and verification

Read `references/session-bootstrap.md` for bootstrap, login, import, repair, or authorization checks.

- For a fresh installation, follow `INSTALL.ru.md` for Russian or `INSTALL.md` for English. Lead the owner one step at a time; do not merely tell them to “get Telegram credentials.”
- If the owner has no API ID/API hash, direct them to [my.telegram.org/apps](https://my.telegram.org/apps), explain the `API development tools` flow and distinguish these application credentials from a BotFather bot token.
- Perform repository, skill, runtime, dependency, and offline setup steps when authorized. Pause only for the owner-only browser and terminal steps, say exactly what the owner should do next, and continue after they confirm completion.
- Only trusted local `./run.sh --account <name>` may start interactive login.
- The owner enters Telegram code and 2FA directly in that terminal.
- Never ask for, read, type, copy, upload, or commit codes, passwords, API hash, phone number, account env, or `.session` files.
- Verification may inspect metadata and authorization state, never secret contents.

## Owner-requested media-aware summaries

Read `references/summary-memory.md` before summarizing a dialog, group, or channel. Run `summarize_chat_native.py` through `userbotrun.py` with `--do-summary`; do not use legacy `summarize.py`, `--metadata-only`, or `--no-memory` for a delivered owner summary.

The structured summary cache uses `telegram_dialog_memory.v1`. It is keyed by the exact account, chat, and requested window. Reuse `cache_hit`; use `delta` only after its anchor/tail validation succeeds; otherwise refresh. For `miss`, `delta`, or `refresh`, merge the complete context and commit through the same module before answering. Never finish while `commit_required=true` remains uncommitted.

The collector invokes Telegram `messages.TranscribeAudioRequest` for newly collected voice, audio, and video-note records. Use only complete native transcriptions; report incomplete coverage and never substitute external STT. For photos, preview exact IDs with `download_media.py`, execute into a unique runtime subdirectory, inspect every verified image with `view_image`, and add only conversation-relevant visual context. Do not download, play, transcribe, or visually analyze video files.

The STT queue is bounded and resumable. Keep one worker unless current Telegram behaviour has been verified with more. A stable output plus `--resume` preserves completed items; the progress JSONL contains status only, not transcript text. Do not run another direct helper for that account while collection is active.

## Add one missing operation

Read `references/module-authoring.md` before changing code.

1. Inspect `AGENTS.md`, `MODULES.md`, the closest module/test, and central config/target helpers.
2. Inspect the installed Telethon signature and official URL:

   ```bash
   "$USERBOT_PY" "$USERBOT_SKILL_DIR/scripts/telethon_api_inventory.py" \
     --request messages.EditMessageRequest
   ```

3. Add or minimally extend one focused module. Do not create a generic raw-request runner.
4. Add focused fake-client tests, a registry entry, and `MODULES.md` documentation.
5. Run the deterministic gate; it validates AST safety, registration, CLI help, focused tests, the full suite, and dependencies:

   ```bash
   "$USERBOT_PY" scripts/check_module.py modules/<name>.py --full
   ```

Never automate auth/recovery/passkeys, phone changes, account deletion, payments/Stars/gifts/refunds, SMS jobs, or secret-chat internals without a separate explicit owner decision.

## Portable bootstrap

The bundled template contains code and tests only. It has no credentials, account files, sessions, runtime databases, or media. Bootstrap is only for a new destination and refuses to merge into an existing runtime:

```bash
python3 "$USERBOT_SKILL_DIR/scripts/bootstrap_userbot_project.py" \
  --destination "$USERBOT_ROOT"
# After explicit approval to create that exact new folder:
python3 "$USERBOT_SKILL_DIR/scripts/bootstrap_userbot_project.py" \
  --destination "$USERBOT_ROOT" --execute
```

Do not use bootstrap as an updater. For an existing runtime, apply reviewed source changes narrowly and preserve account/runtime data.

## Reference routing

- `references/operation-playbook.md`: exact Telegram operation families and verification.
- `references/runtime-access.md`: preflight and narrow sandbox/harness access without failed probes.
- `references/semantic-memory.md`: bounded cross-task memory and freshness rules.
- `references/summary-memory.md`: incremental dialog summaries and coverage.
- `references/gateway-webhooks.md`: gateway, event retention, webhook delivery.
- `references/session-bootstrap.md`: safe account/session onboarding.
- `references/channel-rich-publishing.md`: approved rich channel publishing.
- `references/module-authoring.md`: focused self-extension and quality gate.

For custom emoji, distinguish inline entities, reactions, profile/channel status, and packs before choosing an API. A sandbox `PermissionError` is an environment policy failure, not evidence that Telegram failed; follow the runtime-access preflight and retry the same canonical route at most once through the narrow native access path. Docker does not bypass the host sandbox or justify copying secret-bearing runtime data.
