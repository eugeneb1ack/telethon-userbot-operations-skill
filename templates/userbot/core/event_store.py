from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MAX_EVENTS = 10_000
MAX_ACKNOWLEDGED_EVENTS = 2_000
MAX_WEBHOOK_ATTEMPTS = 12
SQLITE_JOURNAL_LIMIT_BYTES = 8 * 1024 * 1024


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def event_id(account: str, chat_id: int, message_id: int, kind: str) -> str:
    raw = f"{account}:{chat_id}:{message_id}:{kind}".encode("utf-8")
    return f"TG-{hashlib.sha256(raw).hexdigest()[:12].upper()}"


class EventStore:
    """Bounded durable inbox for Telegram events and webhook delivery state."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute("PRAGMA busy_timeout=10000")
        self.connection.execute("PRAGMA wal_autocheckpoint=1000")
        self.connection.execute(f"PRAGMA journal_size_limit={SQLITE_JOURNAL_LIMIT_BYTES}")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                account TEXT NOT NULL,
                kind TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                sender_id INTEGER,
                chat_title TEXT NOT NULL,
                sender_name TEXT NOT NULL,
                preview TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'unread',
                webhook_status TEXT NOT NULL DEFAULT 'pending',
                webhook_attempts INTEGER NOT NULL DEFAULT 0,
                webhook_next_attempt_at TEXT,
                webhook_last_error TEXT,
                inserted_at TEXT NOT NULL,
                UNIQUE(account, chat_id, message_id, kind)
            );
            CREATE INDEX IF NOT EXISTS events_status_idx
                ON events(status, occurred_at DESC);
            CREATE INDEX IF NOT EXISTS events_webhook_idx
                ON events(webhook_status, webhook_next_attempt_at, occurred_at);
            """
        )
        self.connection.commit()
        self._prune()
        self._secure_files()

    def _secure_files(self) -> None:
        for candidate in (
            self.path,
            Path(f"{self.path}-wal"),
            Path(f"{self.path}-shm"),
        ):
            try:
                if candidate.exists():
                    os.chmod(candidate, 0o600)
            except OSError:
                pass

    def _prune(self) -> None:
        self.connection.execute(
            """
            DELETE FROM events
            WHERE rowid IN (
                SELECT rowid FROM events
                WHERE status = 'acknowledged'
                ORDER BY rowid DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (MAX_ACKNOWLEDGED_EVENTS,),
        )
        total = int(self.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        excess = max(0, total - MAX_EVENTS)
        if excess:
            self.connection.execute(
                """
                DELETE FROM events
                WHERE rowid IN (
                    SELECT rowid FROM events
                    ORDER BY rowid ASC
                    LIMIT ?
                )
                """,
                (excess,),
            )
        self.connection.commit()
        self._secure_files()

    def close(self) -> None:
        self.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self._secure_files()
        self.connection.close()
        self._secure_files()

    def add_event(self, payload: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        identifier = event_id(
            str(payload["account"]),
            int(payload["chat_id"]),
            int(payload["message_id"]),
            str(payload["kind"]),
        )
        values = {
            "id": identifier,
            "account": str(payload["account"]),
            "kind": str(payload["kind"]),
            "chat_id": int(payload["chat_id"]),
            "message_id": int(payload["message_id"]),
            "sender_id": payload.get("sender_id"),
            "chat_title": str(payload.get("chat_title") or "Неизвестный чат"),
            "sender_name": str(payload.get("sender_name") or "Неизвестный отправитель"),
            "preview": str(payload.get("preview") or ""),
            "occurred_at": str(payload["occurred_at"]),
            "webhook_status": str(payload.get("webhook_status") or "disabled"),
            "inserted_at": utc_now(),
        }
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO events (
                id, account, kind, chat_id, message_id, sender_id,
                chat_title, sender_name, preview, occurred_at, webhook_status, inserted_at
            ) VALUES (
                :id, :account, :kind, :chat_id, :message_id, :sender_id,
                :chat_title, :sender_name, :preview, :occurred_at, :webhook_status, :inserted_at
            )
            """,
            values,
        )
        self.connection.commit()
        record = self.get_event(identifier)
        if record is None:
            raise RuntimeError("event insert did not produce a readable record")
        self._prune()
        return cursor.rowcount == 1, record

    @staticmethod
    def _public(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "account": row["account"],
            "kind": row["kind"],
            "chat_id": row["chat_id"],
            "message_id": row["message_id"],
            "sender_id": row["sender_id"],
            "chat_title": row["chat_title"],
            "sender_name": row["sender_name"],
            "preview": row["preview"],
            "occurred_at": row["occurred_at"],
            "status": row["status"],
            "webhook_status": row["webhook_status"],
        }

    def get_event(self, identifier: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM events WHERE id = ?", (identifier,)
        ).fetchone()
        return self._public(row) if row else None

    def list_events(self, *, limit: int, unread_only: bool = False) -> list[dict[str, Any]]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if unread_only:
            rows = self.connection.execute(
                "SELECT * FROM events WHERE status = 'unread' ORDER BY occurred_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM events ORDER BY occurred_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._public(row) for row in rows]

    def acknowledge(self, identifier: str) -> dict[str, Any] | None:
        self.connection.execute(
            "UPDATE events SET status = 'acknowledged' WHERE id = ?", (identifier,)
        )
        self.connection.commit()
        record = self.get_event(identifier)
        self._prune()
        return record

    def next_webhook_event(self) -> dict[str, Any] | None:
        now = utc_now()
        row = self.connection.execute(
            """
            SELECT * FROM events
            WHERE webhook_status = 'pending'
              AND (webhook_next_attempt_at IS NULL OR webhook_next_attempt_at <= ?)
            ORDER BY occurred_at ASC
            LIMIT 1
            """,
            (now,),
        ).fetchone()
        return self._public(row) if row else None

    def mark_webhook_sent(self, identifier: str) -> None:
        self.connection.execute(
            """
            UPDATE events
            SET webhook_status = 'sent', webhook_last_error = NULL,
                webhook_next_attempt_at = NULL
            WHERE id = ?
            """,
            (identifier,),
        )
        self.connection.commit()
        self._secure_files()

    def mark_webhook_failed(
        self, identifier: str, *, error: str, next_attempt_at: str
    ) -> bool:
        self.connection.execute(
            """
            UPDATE events
            SET webhook_attempts = webhook_attempts + 1,
                webhook_last_error = ?,
                webhook_status = CASE
                    WHEN webhook_attempts + 1 >= ? THEN 'failed'
                    ELSE 'pending'
                END,
                webhook_next_attempt_at = CASE
                    WHEN webhook_attempts + 1 >= ? THEN NULL
                    ELSE ?
                END
            WHERE id = ?
            """,
            (
                error[:240],
                MAX_WEBHOOK_ATTEMPTS,
                MAX_WEBHOOK_ATTEMPTS,
                next_attempt_at,
                identifier,
            ),
        )
        self.connection.commit()
        self._secure_files()
        row = self.connection.execute(
            "SELECT webhook_status FROM events WHERE id = ?", (identifier,)
        ).fetchone()
        return bool(row and row[0] == "failed")

    def webhook_attempts(self, identifier: str) -> int:
        row = self.connection.execute(
            "SELECT webhook_attempts FROM events WHERE id = ?", (identifier,)
        ).fetchone()
        return int(row[0]) if row else 0
