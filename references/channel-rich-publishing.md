# Channel rich-media publication through the Telethon userbot

Use this only after the owner explicitly authorizes publication and the final copy is already approved. It is a Telegram external write, not a formatting preview.

If the local registry does not return a purpose-built publishing module, do **not** improvise a one-off sender. Add one narrow CLI module through `module-authoring.md`, with dry-run, `--execute`, duplicate protection and read-back.

## Required preflight

1. Confirm the local media path exists, is a regular non-empty file, and is the intended asset.
2. Resolve the exact target. Require a channel entity, verified immutable ID, expected title, and `broadcast=True`.
3. A private channel may have `username=None`. That is valid only after exact ID/title/type verification; never fabricate a public `@username` URL.
4. Inspect a small bounded recent window and block an exact duplicate of the approved visible text.
5. Render the final HTML only with the project’s allowlisted Telegram formatting. Preserve paragraph breaks. If a requested photo caption exceeds Telegram’s limit, stop and obtain an explicitly approved multi-message layout.

## Send once and prove it

A module may use Telethon `send_file(..., caption=..., parse_mode="html", force_document=False)` for an approved local image/video. It must send once, then fetch the returned message by ID through the same target and verify:

- returned message ID is positive;
- `raw_text` exactly equals the approved visible text;
- requested media type is present;
- each important labelled source link is a `MessageEntityTextUrl` (and requested styles use their expected entity class);
- the message belongs to the verified target.

A successful RPC return is not evidence that Telegram rendered the caption, media or source link correctly.

For a verified private channel, the member-accessible link is:

```text
https://t.me/c/<raw_channel_id>/<message_id>
```

`<raw_channel_id>` omits Telegram’s `-100` prefix. Do not report a guessed public `https://t.me/<username>/...` link when the channel has no username.

## Failure behavior

- No blind retry after an ambiguous timeout: read the recent target window first.
- No replacement post after an ambiguous edit/send failure: it can duplicate a post Telegram accepted.
- Do not fall back from an edit failure to deletion without an explicit owner instruction.
- No silent split into multiple messages.
- Record only safe verification metadata: target title, message ID, media state, entity types and safe link.
