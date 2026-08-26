# Module Authoring Contract

This guide is written so a smaller coding model can implement one Telethon userbot operation without making a dangerous mess.

## Before touching code

1. Read the project’s `AGENTS.md`, `MODULES.md`, `core/config.py`, and the closest existing module/test.
2. Run one authoring preflight through the runtime interpreter:

   ```bash
   "$USERBOT_PY" "$USERBOT_SKILL_DIR/scripts/telethon_authoring_context.py" \
     --project-root "$USERBOT_ROOT" --query '<request>' \
     --client-method '<high_level_method>' \
     --raw-request '<namespace.Request>' --json
   ```

3. Use the packet decision: reuse an existing operation, clarify ambiguity, resolve version/API-surface blockers, or author exactly one missing operation. Rerun after selecting the exact API surface if it was initially unknown.
4. Require `telethon.version_match=true`. Treat the installed signatures as the runtime contract, open each returned official URL, and confirm the documentation header matches `installed_version`. Do not mix stable v1 code with v2/development docs.

Prefer a documented high-level `TelegramClient` method over raw TL when both cover the operation. High-level methods have the stronger compatibility guarantee. For history/search work, pass supported filters such as `from_user`, `search`, and `filter` into `iter_messages`; do not download a broad history merely to discard most rows in Python. Telethon uses server-side search where Telegram supports it, but keeps a local sender check in private chats and documents/implements restrictions on some combinations. Keep client-side validation as a safety assertion and inspect the installed method before combining filters or using forum `reply_to`.

## One feature, one file

Create `modules/<snake_case_feature>.py`. Do not combine unrelated profile, story, sticker, payments, or group-admin operations into one generic wrapper.

## Required direct-helper skeleton

Use a direct helper only when no gateway operation fits. Repeated/read-heavy operations should be a small gateway method so agents reuse the local JSON transport. Register direct-helper commands through `scripts/userbotrun.py`; never invoke them bare in the registry.

```python
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from telethon import TelegramClient, errors

ROOT = Path(__file__).resolve().parent.parent
from core.config import apply_runtime_env, load_settings
from core.telegram_targets import entity_payload, resolve_entity


def register(client: TelegramClient) -> None:
    """Direct CLI helper; no event handler is attached."""


async def run(args: argparse.Namespace) -> dict:
    settings = load_settings(args.account)
    apply_runtime_env(settings)
    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized; refusing interactive login")

        target = await resolve_entity(client, args.chat)
        plan = {
            "ok": True,
            "dry_run": not args.execute,
            "target": entity_payload(target, input_value=args.chat),
        }
        # Collect exact IDs/current state.
        if not args.execute:
            return plan
        # Mutate frozen plan, then read exact server state back.
        return plan
    finally:
        await client.disconnect()
```

## Non-negotiable rules

- Use central config; never hard-code credentials, sessions, targets, or access hashes.
- Direct helpers use `connect()` plus `is_user_authorized()`; never `client.start()`.
- Resolve numeric peers through the project target resolver. Never use `abs(id)` or guess `PeerUser`/`PeerChannel`.
- Textual writes fail on ambiguity. Do not choose the first fuzzy match.
- Every Telegram write defaults to dry-run and needs `--execute`.
- Freeze IDs before batch operations. Do not recollect a moving “latest N” set during execution.
- Write only to contained runtime paths for local downloads/exports.
- Keep import time inert: no Telegram connection, background task, external API call, or destructive filesystem operation.

## FloodWait and errors

```python
for attempt in range(2):
    try:
        await client(request)
        break
    except errors.FloodWaitError as exc:
        if attempt:
            raise
        await asyncio.sleep(exc.seconds + 1)
```

Do not broad-catch an error and delete/ban/retry forever. Treat a no-op as successful only after the final state is read back.

## Tests required

Add focused `unittest` cases under `tests/` with a fake client.

At minimum prove:

1. invalid/ambiguous input is rejected;
2. dry-run causes zero mutating calls;
3. batch IDs are positive, unique and frozen;
4. execute passes the critical options (`revoke=True`, preserved reactions, finite restriction deadline, etc.);
5. post-write verification distinguishes success, partial state and failure.

## Definition of done

Before the gate, add the module to `scripts/userbot_module_registry.py` and document it in `MODULES.md`. The gate rejects an inert direct CLI module that is not registered.

```bash
cd "$USERBOT_ROOT"
venv/bin/python scripts/userbot_module_registry.py --validate-catalog --json
venv/bin/python scripts/check_module.py modules/<name>.py --full
```

`check_module.py --full` performs AST safety checks, catalog validation, focused tests, all module `--help` checks, the full suite, and `pip check`. It performs no real Telegram write.

## Prompt for a smaller coding model

```text
Implement exactly one guarded Telethon userbot module.

1. Run the local registry first. Use only status=match; clarify ambiguous; author only after no_match is confirmed.
2. Read AGENTS.md, MODULES.md, core/config.py, the closest module/test, and this guide.
3. Run telethon_authoring_context.py for the original request and exact candidate method/request. Require a matching project pin, inspect the installed signature, and open its official URL.
4. Use central config and existing authorized sessions; never client.start() or hard-code credentials.
5. Default to dry-run. Telegram writes need --execute after a precise target/ID plan.
6. Handle FloodWait once, re-read actual final state, add fake-client tests, update MODULES.md and registry.
7. Run the registry validator and check_module.py --full. Its independent module-help checks are parallelized, but the full suite and dependency checks remain mandatory. Do not use a real Telegram write during verification.
```
