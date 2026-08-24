from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.memory_store import connect_memory_database, secure_memory_files


SUMMARY_DOCUMENT_SCHEMA = "telegram_dialog_memory.v1"
MAX_SUMMARY_BYTES = 64 * 1024
MAX_PARTICIPANTS = 64
MAX_TAIL_MARKERS = 128
MAX_SCOPES_PER_CHAT = 24
MAX_SCOPES_TOTAL = 512


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def scope_key(
    *,
    account: str,
    chat_id: int,
    topic_id: int | None,
    sender_id: int | None,
    request: dict[str, Any],
) -> str:
    raw = _canonical_json(
        {
            "account": account,
            "chat_id": chat_id,
            "topic_id": topic_id,
            "sender_id": sender_id,
            "request": request,
        }
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def normalize_summary_document(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("summary document must be a JSON object")

    schema = value.get("schema") or SUMMARY_DOCUMENT_SCHEMA
    if schema != SUMMARY_DOCUMENT_SCHEMA:
        raise ValueError(f"summary schema must be {SUMMARY_DOCUMENT_SCHEMA}")

    summary = value.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("summary document requires a non-empty 'summary' string")

    normalized: dict[str, Any] = {
        "schema": SUMMARY_DOCUMENT_SCHEMA,
        "summary": summary.strip(),
    }
    list_fields = (
        "participants",
        "topics",
        "facts",
        "decisions",
        "open_questions",
        "chronology",
        "coverage_notes",
    )
    for field in list_fields:
        items = value.get(field, [])
        if not isinstance(items, list):
            raise ValueError(f"summary field '{field}' must be a list")
        normalized[field] = items

    if len(normalized["participants"]) > MAX_PARTICIPANTS:
        raise ValueError(f"summary supports at most {MAX_PARTICIPANTS} participants")
    for participant in normalized["participants"]:
        if not isinstance(participant, dict):
            raise ValueError("every summary participant must be a JSON object")
        identifier = participant.get("id")
        if identifier is not None and (not isinstance(identifier, int) or isinstance(identifier, bool)):
            raise ValueError("participant id must be an integer or null")
        for field in ("name", "username", "role", "notes"):
            field_value = participant.get(field)
            if field_value is not None and not isinstance(field_value, str):
                raise ValueError(f"participant {field} must be a string or null")

    serialized = _canonical_json(normalized).encode("utf-8")
    if len(serialized) > MAX_SUMMARY_BYTES:
        raise ValueError(f"summary document exceeds {MAX_SUMMARY_BYTES} bytes")
    return normalized


class SummaryStore:
    """Bounded semantic cache for dialog summaries; raw messages never enter it."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.connection = connect_memory_database(path)
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS summary_snapshots (
                scope_key TEXT PRIMARY KEY,
                account TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                topic_id INTEGER,
                sender_id INTEGER,
                request_json TEXT NOT NULL,
                window_mode TEXT NOT NULL,
                window_start TEXT,
                window_end TEXT,
                summary_json TEXT NOT NULL,
                source_message_count INTEGER NOT NULL,
                first_message_id INTEGER,
                last_message_id INTEGER,
                first_message_time TEXT,
                last_message_time TEXT,
                tail_markers_json TEXT NOT NULL,
                revision INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_validated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS summary_snapshots_chat_idx
                ON summary_snapshots(account, chat_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS summary_snapshots_lru_idx
                ON summary_snapshots(last_validated_at DESC);

            CREATE TABLE IF NOT EXISTS summary_participants (
                scope_key TEXT NOT NULL REFERENCES summary_snapshots(scope_key) ON DELETE CASCADE,
                participant_id INTEGER NOT NULL,
                name TEXT,
                username TEXT,
                role TEXT,
                notes TEXT,
                data_json TEXT NOT NULL,
                PRIMARY KEY (scope_key, participant_id)
            );
            CREATE INDEX IF NOT EXISTS summary_participants_user_idx
                ON summary_participants(participant_id);
            """
        )
        self.connection.commit()
        secure_memory_files(path)

    def close(self) -> None:
        self.connection.close()
        secure_memory_files(self.path)

    def __enter__(self) -> SummaryStore:
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    @staticmethod
    def _public(row: Any) -> dict[str, Any]:
        return {
            "scope_key": row["scope_key"],
            "account": row["account"],
            "chat_id": row["chat_id"],
            "topic_id": row["topic_id"],
            "sender_id": row["sender_id"],
            "request": json.loads(row["request_json"]),
            "window_mode": row["window_mode"],
            "window_start": row["window_start"],
            "window_end": row["window_end"],
            "summary": json.loads(row["summary_json"]),
            "source_message_count": row["source_message_count"],
            "first_message_id": row["first_message_id"],
            "last_message_id": row["last_message_id"],
            "first_message_time": row["first_message_time"],
            "last_message_time": row["last_message_time"],
            "tail_markers": json.loads(row["tail_markers_json"]),
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_validated_at": row["last_validated_at"],
        }

    def get(self, identifier: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM summary_snapshots WHERE scope_key = ?", (identifier,)
        ).fetchone()
        return self._public(row) if row else None

    def mark_validated(self, identifier: str) -> None:
        self.connection.execute(
            "UPDATE summary_snapshots SET last_validated_at = ? WHERE scope_key = ?",
            (utc_now(), identifier),
        )
        self.connection.commit()

    def commit(self, preparation: dict[str, Any], document: Any) -> dict[str, Any]:
        normalized = normalize_summary_document(document)
        identifier = str(preparation.get("scope_key") or "")
        scope = preparation.get("scope")
        source = preparation.get("source")
        if not identifier or not isinstance(scope, dict) or not isinstance(source, dict):
            raise ValueError("archive has no valid summary-memory preparation")

        request = scope.get("request")
        account = str(scope.get("account") or "")
        chat_id = scope.get("chat_id")
        topic_id = scope.get("topic_id")
        sender_id = scope.get("sender_id")
        if not account or not isinstance(chat_id, int) or not isinstance(request, dict):
            raise ValueError("summary-memory scope is incomplete")
        if topic_id is not None and (not isinstance(topic_id, int) or isinstance(topic_id, bool)):
            raise ValueError("summary-memory topic id must be an integer or null")
        if sender_id is not None and (not isinstance(sender_id, int) or isinstance(sender_id, bool)):
            raise ValueError("summary-memory sender id must be an integer or null")
        expected_identifier = scope_key(
            account=account,
            chat_id=chat_id,
            topic_id=topic_id,
            sender_id=sender_id,
            request=request,
        )
        if identifier != expected_identifier:
            raise ValueError("summary-memory scope key does not match its scope")

        markers = source.get("tail_markers") or []
        if not isinstance(markers, list) or len(markers) > MAX_TAIL_MARKERS:
            raise ValueError(f"tail marker count must be between 0 and {MAX_TAIL_MARKERS}")
        for marker in markers:
            if (
                not isinstance(marker, dict)
                or not isinstance(marker.get("id"), int)
                or not isinstance(marker.get("fingerprint"), str)
            ):
                raise ValueError("invalid tail marker in summary-memory preparation")

        base_revision = int(preparation.get("base_revision") or 0)
        existing = self.get(identifier)
        current_revision = int(existing["revision"]) if existing else 0
        if current_revision != base_revision:
            raise RuntimeError(
                "summary-memory preparation is stale; collect the dialog again before committing"
            )
        revision = current_revision + 1
        now = utc_now()
        created_at = existing["created_at"] if existing else now
        values = {
            "scope_key": identifier,
            "account": account,
            "chat_id": chat_id,
            "topic_id": topic_id,
            "sender_id": sender_id,
            "request_json": _canonical_json(request),
            "window_mode": str(request.get("mode") or "unknown"),
            "window_start": source.get("window_start"),
            "window_end": source.get("window_end"),
            "summary_json": _canonical_json(normalized),
            "source_message_count": int(source.get("message_count") or 0),
            "first_message_id": source.get("first_message_id"),
            "last_message_id": source.get("last_message_id"),
            "first_message_time": source.get("first_message_time"),
            "last_message_time": source.get("last_message_time"),
            "tail_markers_json": _canonical_json(markers),
            "revision": revision,
            "created_at": created_at,
            "updated_at": now,
            "last_validated_at": now,
            "base_revision": base_revision,
        }
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO summary_snapshots (
                    scope_key, account, chat_id, topic_id, sender_id, request_json,
                    window_mode, window_start, window_end, summary_json,
                    source_message_count, first_message_id, last_message_id,
                    first_message_time, last_message_time, tail_markers_json,
                    revision, created_at, updated_at, last_validated_at
                ) VALUES (
                    :scope_key, :account, :chat_id, :topic_id, :sender_id, :request_json,
                    :window_mode, :window_start, :window_end, :summary_json,
                    :source_message_count, :first_message_id, :last_message_id,
                    :first_message_time, :last_message_time, :tail_markers_json,
                    :revision, :created_at, :updated_at, :last_validated_at
                )
                ON CONFLICT(scope_key) DO UPDATE SET
                    request_json = excluded.request_json,
                    window_mode = excluded.window_mode,
                    window_start = excluded.window_start,
                    window_end = excluded.window_end,
                    summary_json = excluded.summary_json,
                    source_message_count = excluded.source_message_count,
                    first_message_id = excluded.first_message_id,
                    last_message_id = excluded.last_message_id,
                    first_message_time = excluded.first_message_time,
                    last_message_time = excluded.last_message_time,
                    tail_markers_json = excluded.tail_markers_json,
                    revision = excluded.revision,
                    updated_at = excluded.updated_at,
                    last_validated_at = excluded.last_validated_at
                WHERE summary_snapshots.revision = :base_revision
                """,
                values,
            )
            if cursor.rowcount != 1:
                raise RuntimeError(
                    "summary-memory preparation is stale; collect the dialog again before committing"
                )
            self.connection.execute(
                "DELETE FROM summary_participants WHERE scope_key = ?", (identifier,)
            )
            for participant in normalized["participants"]:
                participant_id = participant.get("id")
                if not isinstance(participant_id, int) or isinstance(participant_id, bool):
                    continue
                self.connection.execute(
                    """
                    INSERT INTO summary_participants (
                        scope_key, participant_id, name, username, role, notes, data_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        identifier,
                        participant_id,
                        participant.get("name"),
                        participant.get("username"),
                        participant.get("role"),
                        participant.get("notes"),
                        _canonical_json(participant),
                    ),
                )
            self._prune(account=account, chat_id=chat_id)
        self.connection.execute("PRAGMA incremental_vacuum(200)")
        result = self.get(identifier)
        if result is None:
            raise RuntimeError("summary commit did not produce a readable snapshot")
        return result

    def _prune(self, *, account: str, chat_id: int) -> None:
        chat_rows = self.connection.execute(
            """
            SELECT scope_key FROM summary_snapshots
            WHERE account = ? AND chat_id = ?
            ORDER BY last_validated_at DESC, updated_at DESC
            LIMIT -1 OFFSET ?
            """,
            (account, chat_id, MAX_SCOPES_PER_CHAT),
        ).fetchall()
        global_rows = self.connection.execute(
            """
            SELECT scope_key FROM summary_snapshots
            ORDER BY last_validated_at DESC, updated_at DESC
            LIMIT -1 OFFSET ?
            """,
            (MAX_SCOPES_TOTAL,),
        ).fetchall()
        stale = {row["scope_key"] for row in (*chat_rows, *global_rows)}
        self.connection.executemany(
            "DELETE FROM summary_snapshots WHERE scope_key = ?",
            ((identifier,) for identifier in stale),
        )
