from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.summary_store import (
    MAX_SCOPES_PER_CHAT,
    MAX_SUMMARY_BYTES,
    SUMMARY_DOCUMENT_SCHEMA,
    SummaryStore,
    scope_key,
)


def preparation(*, request: dict, chat_id: int = 42, base_revision: int = 0) -> dict:
    identifier = scope_key(
        account="main",
        chat_id=chat_id,
        topic_id=None,
        sender_id=None,
        request=request,
    )
    return {
        "scope_key": identifier,
        "scope": {
            "account": "main",
            "chat_id": chat_id,
            "topic_id": None,
            "sender_id": None,
            "request": request,
        },
        "base_revision": base_revision,
        "source": {
            "message_count": 2,
            "first_message_id": 10,
            "last_message_id": 11,
            "first_message_time": "2026-01-01 10:00:00",
            "last_message_time": "2026-01-01 10:05:00",
            "window_start": "2026-01-01T00:00:00+03:00",
            "window_end": "2026-01-02T00:00:00+03:00",
            "tail_markers": [
                {"id": 10, "fingerprint": "hash-10"},
                {"id": 11, "fingerprint": "hash-11"},
            ],
        },
    }


def document(summary: str = "Короткая сводка") -> dict:
    return {
        "schema": SUMMARY_DOCUMENT_SCHEMA,
        "summary": summary,
        "participants": [
            {
                "id": 777,
                "name": "Участник",
                "username": "participant",
                "role": "собеседник",
                "notes": "обсуждает проект",
            }
        ],
        "topics": ["проект"],
        "facts": [],
        "decisions": [],
        "open_questions": [],
        "chronology": [],
        "coverage_notes": [],
    }


class SummaryStoreTests(unittest.TestCase):
    def test_commit_round_trip_and_participant_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "summary.sqlite3"
            with SummaryStore(path) as store:
                saved = store.commit(preparation(request={"mode": "date", "date": "2026-01-01"}), document())
                participant = store.connection.execute(
                    "SELECT participant_id, name FROM summary_participants"
                ).fetchone()

            self.assertEqual(saved["summary"]["summary"], "Короткая сводка")
            self.assertEqual(saved["revision"], 1)
            self.assertEqual(tuple(participant), (777, "Участник"))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_stale_preparation_cannot_overwrite_newer_revision(self) -> None:
        request = {"mode": "date", "date": "2026-01-01"}
        pending = preparation(request=request)
        with tempfile.TemporaryDirectory() as tmp, SummaryStore(
            Path(tmp) / "summary.sqlite3"
        ) as store:
            store.commit(pending, document("Первая версия"))
            with self.assertRaisesRegex(RuntimeError, "preparation is stale"):
                store.commit(pending, document("Устаревшая версия"))

    def test_summary_size_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, SummaryStore(
            Path(tmp) / "summary.sqlite3"
        ) as store:
            with self.assertRaisesRegex(ValueError, "exceeds"):
                store.commit(
                    preparation(request={"mode": "date", "date": "2026-01-01"}),
                    document("x" * (MAX_SUMMARY_BYTES + 1)),
                )

    def test_old_scopes_are_pruned_per_chat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, SummaryStore(
            Path(tmp) / "summary.sqlite3"
        ) as store:
            for index in range(MAX_SCOPES_PER_CHAT + 3):
                store.commit(
                    preparation(request={"mode": "date", "date": f"2026-01-{index + 1:02d}"}),
                    document(f"Сводка {index}"),
                )
            count = store.connection.execute(
                "SELECT COUNT(*) FROM summary_snapshots WHERE chat_id = 42"
            ).fetchone()[0]

        self.assertEqual(count, MAX_SCOPES_PER_CHAT)


if __name__ == "__main__":
    unittest.main()
