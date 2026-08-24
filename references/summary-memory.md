# Incremental dialog-summary memory

Use this protocol for every owner-requested dialog summary. It keeps repeated work local and bounded without treating an old answer as current.

## Storage contract

`summarize_chat_native.py --do-summary` automatically uses `runtime/<account>/data/userbot_memory.sqlite3`. Dialog summaries and general semantic memory use separate tables in this unified account-local database. If an older `summary_memory.sqlite3` exists and no unified database exists yet, the runtime reuses the legacy file in place so existing summaries remain available. The summary tables store:

- one structured summary per account/chat/topic/sender/window scope;
- source count, first/last message cursor, revision, and validation timestamps;
- at most 128 recent message fingerprints, never raw message text or transcript text;
- a normalized participant index derived from the structured summary.

Each summary document is limited to 64 KiB and 64 participants. Retention is limited to 24 scopes per chat and 512 scopes globally. SQLite removes older least-recently-validated scopes. Raw messages, media, audio, and photo bytes remain outside this database.

Telegram collection and tail validation are read-only with respect to Telegram, but the local cache is intentionally mutable. Opening it may create/update SQLite WAL/SHM files, and a validated cache hit updates `last_validated_at`. Before the first summary command, follow `runtime-access.md`: if the private runtime is outside the harness workspace, use its narrow native access route immediately instead of first provoking `PermissionError`. Never copy the database to an “allowed” workspace or `/tmp`.

Reuse is exact-scope by design: account, chat, topic/sender filter, and requested window are part of the key. Repeating the same annual range can hit its saved snapshot after bounded validation. Asking for one month after an annual summary does not pretend that the coarse annual text is a complete monthly answer; it scans only that month once, saves a separate scope, and reuses that scope on later repeats. This preserves accuracy while still avoiding another year-wide scan.

Validation is deliberately bounded: the module checks the most recent 128 in-scope message fingerprints. It detects new messages and recent edits/deletions, but it cannot prove that an older message outside that tail was never edited. Use `--force-refresh` when exact historical revalidation matters.

## Collection states

- `cache_hit`: no full collection, STT, photo download, or commit. Return the stored structured summary and disclose that recent-tail validation was used.
- `miss`: no saved scope exists. Summarize the complete collected window and commit it.
- `delta`: the saved tail anchor is intact and newer messages exist. Merge `previous_summary` with only the new archive records, inspect only their new photos/media, then commit the merged document.
- `refresh`: the bounded anchor changed, too much tail was displaced, or a rolling window dropped old content. Ignore the previous semantic result, summarize the complete refreshed window, and commit it.

For repeatable periods such as “за год”, prefer a fixed half-open range over `--last-hours`:

```bash
"$USERBOT_PY" scripts/userbotrun.py --account main --timeout 3600 \
  modules/summarize_chat_native.py --chat '<chat>' \
  --since YYYY-MM-DD --until YYYY-MM-DD --do-summary
```

`--since` is inclusive and `--until` is exclusive in Europe/Moscow unless the ISO values include another timezone. `--last-hours` and `--last-messages` are supported, but they may require a full refresh when content falls out of the moving window.

## Structured summary document

For `miss`, `delta`, or `refresh`, create a local UTF-8 JSON file with this shape. Keep statements factual and source-bounded; for `delta`, the result must describe the whole merged scope, not just new messages.

```json
{
  "schema": "telegram_dialog_memory.v1",
  "summary": "Compact factual summary of the covered dialog.",
  "participants": [
    {
      "id": 123,
      "name": "Visible Telegram name",
      "username": "optional_username",
      "role": "Role in this dialog",
      "notes": "Stable, relevant context only"
    }
  ],
  "topics": [],
  "facts": [],
  "decisions": [],
  "open_questions": [],
  "chronology": [],
  "coverage_notes": []
}
```

Do not put inferred personality profiles, secrets, unrelated private facts, raw transcripts, or copied message dumps into this document. `coverage_notes` must preserve incomplete STT, uninspected video, and other material limitations.

## Required commit

Commit the structured document before delivering the new summary:

```bash
"$USERBOT_PY" scripts/userbotrun.py --account main \
  modules/summarize_chat_native.py \
  --summary-from '<collector-archive.json>' \
  --commit-summary '<structured-summary.json>'
```

Treat the operation as complete only after it prints `"status": "committed"`. The archive carries an optimistic `base_revision`; a stale preparation cannot overwrite a newer summary. If the commit reports a stale preparation, recollect and merge again instead of forcing the write.

`--no-memory` is only for explicit diagnostics or privacy-driven one-off collection. Do not use it for a normal owner-requested summary. `--memory-db` is a test/advanced path override and must not point into the public skill checkout.
