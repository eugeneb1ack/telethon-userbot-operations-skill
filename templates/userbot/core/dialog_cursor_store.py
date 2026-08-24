from __future__ import annotations

from pathlib import Path
from typing import Any

from core.memory_store import connect_memory_database, normalize_memory_account, secure_memory_files, utc_now


MAX_DIALOG_CURSORS = 512
ALLOWED_CONTENT_SCOPES = {"all", "voice", "text"}


class DialogCursorStore:
    """Small delivery cursor store; it never stores message or transcript text."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.connection = connect_memory_database(path)
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS dialog_delivery_cursors (
                account TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                sender_id INTEGER NOT NULL,
                content_scope TEXT NOT NULL,
                last_message_id INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (account, chat_id, sender_id, content_scope)
            );
            CREATE INDEX IF NOT EXISTS dialog_delivery_cursors_lru_idx
                ON dialog_delivery_cursors(updated_at DESC);
            """
        )
        self.connection.commit()
        secure_memory_files(path)

    def close(self) -> None:
        self.connection.close()
        secure_memory_files(self.path)

    def __enter__(self) -> DialogCursorStore:
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    @staticmethod
    def _validate_scope(account: str, content_scope: str) -> str:
        normalized = normalize_memory_account(account)
        if content_scope not in ALLOWED_CONTENT_SCOPES:
            raise ValueError(f"content_scope must be one of {sorted(ALLOWED_CONTENT_SCOPES)}")
        return normalized

    def get(
        self,
        *,
        account: str,
        chat_id: int,
        sender_id: int,
        content_scope: str,
    ) -> int | None:
        normalized = self._validate_scope(account, content_scope)
        row = self.connection.execute(
            """
            SELECT last_message_id
            FROM dialog_delivery_cursors
            WHERE account = ? AND chat_id = ? AND sender_id = ? AND content_scope = ?
            """,
            (normalized, chat_id, sender_id, content_scope),
        ).fetchone()
        return int(row["last_message_id"]) if row else None

    def advance(
        self,
        *,
        account: str,
        chat_id: int,
        sender_id: int,
        content_scope: str,
        last_message_id: int,
    ) -> int:
        normalized = self._validate_scope(account, content_scope)
        if last_message_id <= 0:
            raise ValueError("last_message_id must be positive")
        now = utc_now()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO dialog_delivery_cursors (
                    account, chat_id, sender_id, content_scope, last_message_id, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(account, chat_id, sender_id, content_scope) DO UPDATE SET
                    last_message_id = MAX(dialog_delivery_cursors.last_message_id, excluded.last_message_id),
                    updated_at = excluded.updated_at
                """,
                (normalized, chat_id, sender_id, content_scope, last_message_id, now),
            )
            self.connection.execute(
                """
                DELETE FROM dialog_delivery_cursors
                WHERE rowid IN (
                    SELECT rowid FROM dialog_delivery_cursors
                    ORDER BY updated_at DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (MAX_DIALOG_CURSORS,),
            )
        secure_memory_files(self.path)
        return self.get(
            account=normalized,
            chat_id=chat_id,
            sender_id=sender_id,
            content_scope=content_scope,
        ) or last_message_id
