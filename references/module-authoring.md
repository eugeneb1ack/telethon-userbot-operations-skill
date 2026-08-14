# Module Authoring Contract

This guide is written so a smaller coding model can implement one Telethon userbot operation without making a dangerous mess.

## Before touching code

1. Read the project’s `AGENTS.md`, `MODULES.md`, `core/config.py`, and the closest existing module/test.
2. Run the local module registry. Use an existing gateway route or module if it matches.
3. Query the installed API inventory for the exact request/method signature.
4. Read the official URL returned by inventory.

## One feature, one file

Create `modules/<snake_case_feature>.py`. Do not combine unrelated profile, story, sticker, payments, or group-admin operations into one generic wrapper.

## Required direct-helper skeleton

Use a direct helper only when no gateway operation fits. Repeated/read-heavy operations should be a small gateway method so agents reuse the persistent client and local JSON transport.

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

```bash
cd "$USERBOT_ROOT"
venv/bin/python -m compileall -q . -x '/(venv|\.git|__pycache__)'
venv/bin/python -m unittest discover -s tests -v
venv/bin/python -m pip check
for f in modules/*.py; do venv/bin/python "$f" --help >/dev/null; done
```

Then update the local module registry and `MODULES.md`.

## Prompt for a smaller coding model

```text
Implement exactly one guarded Telethon userbot module.

1. Run the local module registry first. If it returns a module, use it rather than writing code.
2. Read AGENTS.md, MODULES.md, core/config.py, the closest module/test, and this guide.
3. Inspect the exact installed Telethon API using telethon_api_inventory.py and open its official URL.
4. Use central config and existing authorized sessions; never client.start() or hard-code credentials.
5. Default to dry-run. Telegram writes need --execute after a precise target/ID plan.
6. Handle FloodWait once, re-read actual final state, add fake-client tests, update MODULES.md and registry.
7. Run compileall, unittest, pip check and all module --help commands. Do not use a real Telegram write during verification.
```
