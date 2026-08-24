# Bounded local semantic memory

Use this protocol when a request may benefit from compact knowledge produced by an earlier task. The goal is to avoid rereading large histories or rediscovering the same verified procedure while never treating cached state as current without evidence.

## Storage and isolation

`scripts/userbot_memory.py` uses `runtime/<account>/data/userbot_memory.sqlite3`. It does not load Telegram credentials, acquire the Telegram session lock, or connect to Telegram. Dialog summaries and general semantic items use separate tables in the same account-local database.

For backward compatibility, if an older `summary_memory.sqlite3` exists and the unified database does not, the CLI and dialog summarizer reuse that file in place. They do not duplicate or migrate private data automatically.

The database is plaintext runtime data. Its SQLite database/WAL files are restricted to the local OS account with mode `0600`, but the application does not encrypt their contents. Protect the machine and runtime directory. Never put the database in the public skill checkout, commit it, upload it, or share it between accounts.

Recall is read-only with respect to Telegram, not to the local database: it purges expired rows, updates access/LRU metadata, and opens SQLite in WAL mode. Follow `runtime-access.md` before the first command. If the runtime is outside the active harness boundary, choose the narrow native access/elevation route up front; do not use a failing probe or copy the database elsewhere.

## Recall before repeating work

Recall memory when a new request could depend on an earlier preference, decision, procedure, verified fact, entity context, or completed task result:

```bash
cd "$USERBOT_ROOT"
"$USERBOT_PY" scripts/userbot_memory.py --account main recall \
  --query '<compact natural-language query>' --scope '<scope>' --limit 5
```

Omit `--scope` only when the relevant scope is unknown. A scoped query searches that scope plus `global`. Recall returns `subject`, `summary`, freshness metadata, and provenance-free retrieval fields by default. Add `--include-details` only when the compact summaries are insufficient. A miss is normal; continue with live work.

Recall is a hint, not proof. Interpret `validity` as follows:

- `stable`: an owner preference, durable decision, or procedure that remains usable until contradicted. Do not label external status or mutable Telegram state as stable.
- `temporal`: a time-sensitive fact with a mandatory future `expires_at`. If the answer or action depends on current truth, re-check the source before use even when the item has not expired. Expired items are deleted automatically.
- `historical`: a verified past result or event. It proves only what was observed then. Always inspect current state before reporting that it is still true or using it to target an action.

No memory hit may bypass fresh target resolution, Telegram dry-run, explicit write approval, frozen IDs, authorization checks, or final read-back.

## What deserves memory

Save one compact item only when all of these are true:

1. The result is verified or explicitly stated by the owner, not a guess.
2. It is likely to reduce repeated collection, reasoning, or token use later.
3. It can be represented as a short reusable statement with provenance and freshness semantics.
4. Keeping it locally is proportionate to the privacy of the task.

Useful kinds are:

- `preference`: explicit owner preference or output convention;
- `decision`: a durable choice and its relevant constraint;
- `procedure`: a verified workflow, command shape, or recovery sequence;
- `fact`: a compact verified fact, with `temporal` validity when it can change;
- `entity_context`: stable context about a chat, project, service, or person that is necessary for later work;
- `task_result`: a verified completed result, always interpreted as historical evidence.

Do not save raw messages, transcripts, exports, copied conversation history, media, secrets, credentials, session material, phone numbers, auth data, speculative personality profiles, unverified inferences, or one-off chatter. Do not create a memory item merely because a task occurred.

Recommended scopes use safe compact identifiers:

- `global`
- `user:<telegram_id>`
- `chat:<telegram_id>`
- `operation:<registry_slug>`
- `project:<short_name>`

## Remember or revision-update

For a compact item, use CLI fields directly:

```bash
"$USERBOT_PY" scripts/userbot_memory.py --account main remember \
  --kind procedure \
  --scope operation:deploy \
  --subject 'Post-deploy verification' \
  --summary 'Check the health endpoint and fresh service logs after deployment.' \
  --source 'verified repository procedure' \
  --tags 'deploy,health,verification' \
  --validity stable
```

For structured details, provide a local UTF-8 JSON document:

```json
{
  "schema": "userbot_memory_item.v1",
  "key": "project:example:release-check",
  "kind": "procedure",
  "scope": "project:example",
  "subject": "Release verification",
  "summary": "Run the focused tests, deploy, then check the health endpoint and fresh logs.",
  "details": {
    "checks": ["focused tests", "health endpoint", "fresh logs"]
  },
  "tags": ["release", "verification"],
  "provenance": {
    "source": "verified repository workflow"
  },
  "validity": "stable",
  "confidence": 1.0,
  "observed_at": "2026-08-24T12:00:00+03:00",
  "expires_at": null
}
```

```bash
"$USERBOT_PY" scripts/userbot_memory.py --account main remember \
  --file '<memory-item.json>'
```

The stable `key` identifies the concept. Reusing it updates that item instead of appending duplicates. If the item was recalled before modification, pass `--expected-revision <n>` so stale work cannot overwrite a newer update. Identical content returns `status=unchanged`.

For `temporal` memory, pass an explicit future ISO timestamp with timezone through `--expires-at`. For a completed operation, save only after verification and use `kind=task_result`, `validity=historical`; never save a dry-run as a completed result.

## Inspect, measure, and forget

```bash
"$USERBOT_PY" scripts/userbot_memory.py --account main list --scope project:example
"$USERBOT_PY" scripts/userbot_memory.py --account main get --id MEM-0123456789ABCDEF
"$USERBOT_PY" scripts/userbot_memory.py --account main stats
"$USERBOT_PY" scripts/userbot_memory.py --account main forget --id MEM-0123456789ABCDEF
# Review the exact preview, then repeat with --execute to delete it.
```

General memory is bounded to 16 KiB per item, 128 items per scope, and 1,024 items per account; least-recently-used items are pruned. That limits the generic semantic payload to roughly 16 MiB per account before SQLite overhead. Dialog summaries retain their separate 64 KiB/512-scope limits. The defaults intentionally favor compact reusable structure over exhaustive history.
