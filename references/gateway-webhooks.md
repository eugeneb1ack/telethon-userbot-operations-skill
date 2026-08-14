# Persistent gateway and webhook

Load this reference for fast reads, background operation, event notifications, webhooks, or integration with a non-Codex agent.

## Runtime

`main.py` owns one authorized `TelegramClient`. It registers the durable event inbox before `catch_up()`, exposes a mode-`0600` Unix socket, then loads regular modules. Agents call `scripts/userbotctl.py`; they do not create another Telegram connection for supported reads.

Default per-account paths:

```text
runtime/<account>/userbot.sock
runtime/<account>/events.sqlite3
runtime/<account>/logs/
```

Fast read examples:

```bash
venv/bin/python scripts/userbotctl.py --account main status
venv/bin/python scripts/userbotctl.py --account main recent-dms --limit 3
venv/bin/python scripts/userbotctl.py --account main dialogs --kind groups
venv/bin/python scripts/userbotctl.py --account main search --chat '@chat' --query 'text'
venv/bin/python scripts/userbotctl.py --account main recent --chat '@chat' --limit 20
venv/bin/python scripts/userbotctl.py --account main events list --unread
```

These are read-only Telegram operations. `events ack` changes only the local inbox state.

## Default: foreground or on-demand

Normal foreground mode starts the gateway only while the userbot is running:

```bash
./run.sh --account main
```

For agent requests, start a detached process on demand. This does not create a LaunchAgent and does not survive a reboot:

```bash
venv/bin/python scripts/userbotd.py --account main start
venv/bin/python scripts/userbotd.py --account main status
venv/bin/python scripts/userbotd.py --account main stop
```

On-demand mode is non-interactive and refuses to initiate Telegram login. Authorize the session once through the foreground launcher first.

## Optional macOS autostart

Autostart is not installed by default and is not required. Only when the owner explicitly asks for login-time background operation, inspect and execute:

```bash
venv/bin/python scripts/install_gateway_service.py --project-root "$PWD" --account main

venv/bin/python scripts/install_gateway_service.py \
  --project-root "$PWD" --account main --execute
```

The optional launchd plist contains paths and the account label, never credentials. Stop or replace it through `launchctl bootout` before a foreground or on-demand launch to avoid two processes opening the same session.

## Events

The inbox records only new incoming:

- `direct_message` for a non-bot private message;
- `mention` when Telegram marks the account as mentioned;
- `reply` when a group message replies to an outgoing account message.

The unique key is `(account, chat_id, message_id, kind)`. SQLite deduplicates replayed updates. `USERBOT_EVENT_PREVIEW_CHARS=0` disables message text in stored/webhook events; the allowed range is 0–500 and the default is 160.

## Configure an outbound webhook

Run the local interactive setup. The URL is visible; the HMAC secret is hidden and never printed:

```bash
venv/bin/python scripts/setup_gateway.py
```

It writes these keys to `accounts/_shared.env` with mode `0600`:

```dotenv
USERBOT_GATEWAY_ENABLED=true
USERBOT_EVENT_PREVIEW_CHARS=160
USERBOT_WEBHOOK_URL=https://receiver.example/telegram-events
USERBOT_WEBHOOK_SECRET=<at-least-32-character-shared-secret>
```

Only HTTPS is accepted, except `http://localhost`, `http://127.0.0.1`, and `http://[::1]` for local development. Disable delivery without disabling the gateway:

```bash
venv/bin/python scripts/setup_gateway.py --disable-webhook
```

## Webhook contract

The gateway sends `POST` with compact JSON:

```json
{
  "version": 1,
  "type": "telegram.event",
  "event": {
    "id": "TG-...",
    "account": "main",
    "kind": "mention",
    "chat_id": -1000000000000,
    "message_id": 42,
    "sender_id": 123,
    "chat_title": "Team",
    "sender_name": "Alice",
    "preview": "bounded preview",
    "occurred_at": "2026-08-15T10:00:00+00:00",
    "status": "unread",
    "webhook_status": "pending"
  }
}
```

Headers:

```text
X-Userbot-Event: TG-...
X-Userbot-Timestamp: <unix-seconds>
X-Userbot-Signature: sha256=<hex-hmac>
```

Signature input is the ASCII timestamp, one dot, then the exact request body:

```python
expected = hmac.new(
    secret.encode("utf-8"),
    timestamp.encode("ascii") + b"." + raw_body,
    hashlib.sha256,
).hexdigest()
```

Reject stale timestamps and compare with `hmac.compare_digest`. Return any `2xx` only after accepting the event. Non-`2xx` responses are retried with bounded exponential backoff; the SQLite inbox preserves pending deliveries across restarts. The gateway never logs the webhook URL, secret, or payload.

## Agent integration

Any agent with shell execution can use `userbotctl.py` and parse its JSON. A tool-native adapter (MCP, HTTP, plugin) should wrap the same CLI/RPC contract rather than open `userbot.session`. The outbound webhook may wake an external workflow; it is a notification transport, not authorization to send Telegram messages.

Telegram writes keep the normal boundary: resolve exact target/message, show a preview, obtain owner approval, execute through a guarded module, and read back the final state.
