# Security and Write Boundaries

## Package contents

This repository may contain only:

- the canonical `userbot` skill documentation and helper scripts;
- a source-only `templates/userbot/` snapshot;
- tests and documentation required to run it safely.

It must never contain `.env`, account profiles, Telegram `.session` / `.session-journal`, runtime databases, media, chat archives, API IDs/hashes, bot tokens, phone numbers, OAuth data, cookies, private keys, real document IDs/access hashes/file references, or copied project runtime directories.

`templates/userbot/` is intentionally allowlisted source code. It is not a backup of a live userbot directory.

## Installation and bootstrap boundary

- Package installation changes only a Hermes skill directory.
- `bootstrap_userbot_project.py` creates a project only when its destination does not exist. It refuses existing paths and must never be modified into an in-place upgrade tool.
- A working userbot project is maintained separately. Do not copy runtime state or account material back into this package.

## Session registration boundary

- Only the trusted local project launcher may run interactive `client.start()` for first login.
- Agents and direct modules use `connect()` plus `is_user_authorized()` and refuse interactive login.
- The owner types Telegram login codes and 2FA in their own local terminal. The agent must not request, display, transmit, store, or enter them.
- A pre-existing `.session` may be moved only by the owner from a trusted local source to the expected isolated runtime path, then checked with `verify_userbot_session.py`.

## Telegram external actions

A Telethon API surface existing does not make unattended automation safe.

Every new mutating helper must:

1. resolve and display exact targets;
2. dry-run by default;
3. require a separate `--execute` flag and explicit owner approval;
4. freeze candidate IDs before a batch;
5. handle `FloodWaitError` once per unit;
6. read back actual final state;
7. report partial/failed verification honestly.

Do not add generic automation for auth, passwords/recovery/passkeys, phone changes, account deletion, payments, gifts, Stars, refunds, SMS jobs, or secret-chat internals.

## Bounded maintenance

The canonical skill can extend a missing capability through the documented module-authoring loop. It may not silently refactor unrelated modules, publish a revision, merge project changes into the package template, or change a live project without explicit owner direction.

## Logging

Log only operation counts, safe labels/titles, bounded previews, IDs when the owner requested them, and error classes. Do not log credentials, account config, session paths/content, full private histories, exported media, or raw third-party model output.
