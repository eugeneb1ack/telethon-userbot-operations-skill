# Security and Write Boundaries

## Never commit

The repository must never include:

- `.env` or `accounts/*.env`;
- Telegram `.session`, `.session-journal`, or any runtime database;
- API IDs/hashes, bot tokens, phone numbers, OAuth data, cookies, or private keys;
- chat dumps, downloaded media, contact exports, or user identifiers;
- real custom emoji document IDs, access hashes, or file references.

## Session registration boundary

- Only the trusted local userbot launcher may run an interactive `client.start()` for first login.
- Agents and direct modules use `connect()` plus `is_user_authorized()`; they must refuse interactive login.
- Telegram login codes and 2FA passwords are typed by the owner directly into their local terminal. They are never requested, displayed, transmitted, or stored by the agent.
- A pre-existing `.session` may be copied only by the owner from a trusted local source into the expected isolated runtime path, then verified with `verify_userbot_session.py`.

## External actions

Telegram mutation is an external action. A feature is not safe merely because Telethon exposes it.

Every new mutating helper must:

1. resolve and display exact targets;
2. dry-run by default;
3. use a separate `--execute` flag;
4. freeze candidate IDs before a batch;
5. handle `FloodWaitError` once per unit;
6. read back final state;
7. report partial or failed verification honestly.

## Explicitly excluded generic automation

This package does not support generic handlers for authentication, recovery/passkeys, phone-number changes, deleting accounts, payments, gifts, Stars, refunds, SMS jobs, or secret-chat internals.

Those APIs may appear in the Telethon inventory. Their presence is documentation, not permission to automate them.

## Logging

Log only operation IDs, counts, titles, type names, bounded previews, and error classes. Never log full private chat history, credentials, session paths, or raw third-party model output.
