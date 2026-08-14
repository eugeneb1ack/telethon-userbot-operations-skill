# Security and Write Boundaries

## Never commit

The repository must never include:

- `.env` or `accounts/*.env`;
- Telegram `.session`, `.session-journal`, or any runtime database;
- API IDs/hashes, bot tokens, phone numbers, OAuth data, cookies, or private keys;
- chat dumps, downloaded media, contact exports, or user identifiers;
- real custom emoji document IDs, access hashes, or file references.

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
