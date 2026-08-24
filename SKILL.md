---
name: userbot
description: Operate and extend a local agent-neutral Telethon userbot through its on-demand gateway, bounded semantic memory, and guarded modules. Use to inspect Telegram data, reuse and revalidate compact local knowledge, receive direct-message/mention/reply events, send or modify messages, manage groups or profiles, configure signed webhooks, bootstrap a userbot, verify a session, or add and validate one guarded Telethon module.
---

# Telethon Userbot

This is the single canonical, agent-neutral skill for the local Telethon userbot.

- Project runtime: `$USERBOT_ROOT`, default `$HOME/Documents/telethon-userbot`.
- Python: `$USERBOT_PY`, default `$USERBOT_ROOT/venv/bin/python`.
- One account process owns the Telegram session. The local JSON gateway normally starts on demand and stops after idle time.
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

Call `userbotctl.py` directly. It reuses a live gateway or starts a lightweight
`gateway-only` process itself; the default process exits 60 seconds after the last local RPC:

```bash
cd "$USERBOT_ROOT"
"$USERBOT_PY" scripts/userbotctl.py --account main status
"$USERBOT_PY" scripts/userbotctl.py --account main recent-dms --limit 3
"$USERBOT_PY" scripts/userbotctl.py --account main events list --unread
```

Manual lifecycle commands are available for diagnosis; normal requests do not need them:

```bash
"$USERBOT_PY" scripts/userbotd.py --account main start
"$USERBOT_PY" scripts/userbotd.py --account main status
"$USERBOT_PY" scripts/userbotd.py --account main stop
```

Routine read-only gateway calls and local event acknowledgements may proceed immediately. Do not request a separate conversational confirmation merely to connect or read. `USERBOT_IDLE_SECONDS` may tune the 10–3600 second idle window. Never disable idle shutdown for agent requests.

## One route for every request

Start with the project’s read-only registry. It performs no Telegram I/O and returns an existing module whenever possible:

```bash
cd "$USERBOT_ROOT"
"$USERBOT_PY" scripts/userbot_module_registry.py --query '<natural-language request>'
```

Use the single returned command, not an ad-hoc script. The registry prefers gateway routes for common reads and routes direct modules through `userbotrun.py`, which serializes session ownership and enforces a hard process timeout.

## Bounded local semantic memory

Read `references/semantic-memory.md` whenever a request could reuse an earlier preference, decision, procedure, verified fact, entity context, or task result.

1. Before repeating expensive collection or reasoning, run `scripts/userbot_memory.py --account <name> recall` with a compact query and the narrowest known scope. This local CLI does not connect to Telegram or acquire its session lock. Use the default summary-only results first; request details only when needed.
2. Treat recalled entries according to their `validity`. `stable` preferences and procedures remain usable until contradicted. Re-check `temporal` facts at their source before a current answer or action depends on them. Treat every `historical` task result only as evidence of what happened then; inspect live state again.
3. After a result is verified, save it only when it is compact, reusable, source-bounded, and likely to prevent meaningful repeated work. Choose one of `fact`, `preference`, `decision`, `procedure`, `entity_context`, or `task_result`; include compact provenance. Use a stable key and `--expected-revision` when updating recalled knowledge.
4. Never store raw messages, transcripts, exports, media, credentials, auth/session material, phone numbers, speculative profiles, unverified inference, or one-off chatter. Do not use memory as an audit log. Database files remain under `runtime/<account>/data/` and never enter this skill checkout.
5. A memory hit never authorizes an external action and never replaces current target resolution, Telegram dry-run, explicit write approval, frozen IDs, or final read-back. If freshness cannot be established, say so and verify live or ignore the item.

The agent decides whether an item is worth keeping; it must not write a memory record for every request. Identical documents deduplicate, revisions prevent stale overwrite, temporal records expire automatically, and per-scope/account limits prevent unbounded growth.

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

- The gateway is the preferred session owner. An account lock prevents two processes from opening one `.session`.
- Run direct helpers only through the registry-provided `userbotrun.py` command. It stops only an idle gateway, refuses to stop a foreground owner, and kills a timed-out module process group.
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
5. Run the deterministic module gate before the full suite:
   ```bash
   "$USERBOT_PY" scripts/check_module.py modules/<name>.py --full
   ```

This is the bounded self-improvement loop: reuse first; otherwise inspect the installed API and official documentation; add one tested module; update its route and docs. Do not silently rewrite unrelated modules, turn it into a generic Telegram API wrapper, or publish a new package revision without an explicit owner request.

## Owner-requested media-aware summaries

When the account owner asks to summarize a chat, direct dialog, group, or channel, make the summary media-aware. This is a read-only Telegram workflow with contained local files; do not send, forward, or upload the source media.

1. Read `references/summary-memory.md`, then run `summarize_chat_native.py` through `userbotrun.py` with `--do-summary`. Do **not** use legacy `summarize.py`, `--metadata-only`, or `--no-memory` for a delivered owner summary. The native collector must invoke Telegram's `messages.TranscribeAudioRequest` for every newly collected `voice`, `audio`, and `video_note` record.
2. If the module returns `cache_hit`, use the stored structured result; do not collect media or commit again. Otherwise inspect the new archive's `transcribable_count`, `transcribed_complete_count`, and per-message transcription status. Include only complete native transcriptions in the summary context. Never fall back to Whisper or another external STT service; report any incomplete Telegram transcription as a coverage limitation.
3. For every newly collected record whose `kind` is `photo`, take the exact message IDs from the archive and call `download_media.py` through `userbotrun.py`. First inspect its dry-run plan; then use `--execute` with a unique safe `--output-subdir`, without `--overwrite`. Open every verified local image with `view_image` (or the host's equivalent local multimodal image inspection) and add a concise visual description relevant to the conversation into the final context, including photos without captions.
4. Do not download, play, transcribe, or visually analyze video files. Retain a video's text/caption in the context and state that its visual content was not analyzed if that can affect the summary.
5. Keep downloaded photos only in the local runtime data directory. Do not expose raw media, transcriptions, or private identifiers beyond what the owner asked to summarize.
6. For `miss`, `delta`, or `refresh`, write the complete structured `telegram_dialog_memory.v1` result and commit it through the same module before delivering the answer. A `delta` document must merge the saved summary with new context. Never finish a new summary while `commit_required=true` remains uncommitted.

### Native STT queue, progress, and recovery

`summarize_chat_native.py` submits voice/audio/video-note records through a bounded FIFO queue. Keep its default single worker unless current Telegram behaviour has been verified with a higher value; parallel `TranscribeAudioRequest` calls can leave a pending item without a completion update.

- The module writes its JSON archive before STT and atomically updates it after every completed item. It also writes a sidecar `<archive>.progress.jsonl` with start, retry, and completion events (no transcript text). Use a stable `--output` path and inspect that progress file during a long run.
- `--transcription-request-timeout` bounds the request itself; `--transcription-timeout` bounds waiting for Telegram's final native update. Transient timeout/network/FloodWait failures retry with backoff. A permanent bad message or sender mismatch is recorded once and the queue continues.
- If a bounded `userbotrun.py` process is stopped or times out, rerun the same window and `--output` with `--resume`. Completed native transcripts are retained and skipped; incomplete records are retried. Report archive coverage (`transcribable_count`, `transcribed_complete_count`) rather than treating a partial archive as complete.
- Do not run a second direct helper against the same account while this queue is active. Use `userbotrun.py` and one caller only.

Example collection command:

```bash
"$USERBOT_PY" scripts/userbotrun.py --account main --timeout 900 \
  modules/summarize_chat_native.py --chat '<chat>' --date YYYY-MM-DD --do-summary
```

For a resumable long window with explicit progress:

```bash
"$USERBOT_PY" scripts/userbotrun.py --account main --timeout 3600 \
  modules/summarize_chat_native.py --chat '<chat>' --date YYYY-MM-DD \
  --output 'runtime/main/data/transcripts/<safe-name>.json' --resume --do-summary
```

The archive records each photo's ID. Preview and then download only those IDs:

```bash
"$USERBOT_PY" scripts/userbotrun.py --account main --timeout 900 \
  modules/download_media.py --chat '<chat>' --message-ids '<photo-id,photo-id>' \
  --output-subdir '<safe-task-name>'
# Inspect the dry-run output, then repeat the exact command with --execute.
```

For gateway lifecycle, event inbox, webhook payload, HMAC verification, and the optional current-login supervisor, load `references/gateway-webhooks.md`.

## Sandbox and diagnostics

- A `PermissionError` on `runtime/<account>/logs/gateway.*` from the Codex sandbox is an environment filesystem policy failure, not proof that Telegram, gateway, session, or STT failed. Keep the canonical userbot process/session ownership model unchanged and request narrow elevated access for the exact read-only runtime command when it is needed.
- Docker does not bypass the host application's filesystem or network sandbox. Do not copy or mount `.env`, session files, API hashes, phone data, or the account runtime into a temporary container merely to inspect logs. It would add another session owner and a broader secret boundary.
- Prefer the summary sidecar progress JSONL for long STT work. For gateway lifecycle diagnosis, use `userbotctl.py --account main status` and, when necessary, a narrowly approved read of the existing runtime logs. A future dedicated diagnostic route should return only bounded, redacted log tails through the gateway; do not expose raw runtime files through a generic RPC.

For custom emoji, distinguish inline entities, reactions, profile/channel status, and emoji packs. Use `references/operation-playbook.md` before choosing an API.

For an explicitly approved channel post with local media, use `references/channel-rich-publishing.md`. If the registry has no purpose-built module, add one through the authoring contract; do not bypass the dry-run/read-back boundary with an ad-hoc send.

Never build a generic raw-request runner or automate auth, recovery, passkeys, phone changes, account deletion, payments/Stars/gifts/refunds, SMS jobs, or secret-chat internals.

## Verified comment-channel snapshot for account `main`

Read-only scan verified on 2026-08-15 across 25 accessible megagroup dialogs and 3,678 inspected messages. It found these broadcast channels linked to discussion chats where the account wrote reply/comment messages:

- `Quantumult X News` — `@QuanXNews` — channel ID `1361573877` — 1 comment.
- `Rose Delete` — `@rosedelete` — channel ID `3527911700` — 25 comments.

The scan excludes ordinary groups that are not linked to a broadcast channel and does not store or print comment text. This is an account- and time-specific snapshot; refresh it with the `comment_channels.py` module when the list needs to be current.

## Verification after code changes

```bash
cd "$USERBOT_ROOT"
venv/bin/python -m compileall -q . -x '/(venv|\.git|__pycache__)'
venv/bin/python -m unittest discover -s tests -v
venv/bin/python -m pip check
for f in modules/*.py; do venv/bin/python "$f" --help >/dev/null; done
```

`userbot` is the only installed skill name. Do not create aliases or duplicate operational guides.
