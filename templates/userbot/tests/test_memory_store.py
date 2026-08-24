from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from core.memory_store import MemoryStore, memory_database_path
from core.summary_store import SummaryStore


def memory_document(
    subject: str = "Предпочтение владельца",
    summary: str = "Отвечать по-русски и кратко.",
    **overrides: object,
) -> dict[str, object]:
    value: dict[str, object] = {
        "kind": "preference",
        "scope": "global",
        "subject": subject,
        "summary": summary,
        "details": {"style": "concise"},
        "tags": ["ответ", "русский"],
        "provenance": {"source": "explicit owner instruction"},
        "validity": "stable",
        "confidence": 1.0,
    }
    value.update(overrides)
    return value


class MemoryStoreTests(unittest.TestCase):
    def test_remember_and_recall_returns_compact_result_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, MemoryStore(
            Path(tmp) / "memory.sqlite3"
        ) as store:
            status, saved = store.remember("main", memory_document())
            recalled = store.recall("main", "русский ответ")

        self.assertEqual(status, "created")
        self.assertEqual(saved["schema"], "userbot_memory_item.v1")
        self.assertEqual(recalled[0]["id"], saved["id"])
        self.assertNotIn("details", recalled[0])
        self.assertNotIn("provenance", recalled[0])

    def test_include_details_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, MemoryStore(
            Path(tmp) / "memory.sqlite3"
        ) as store:
            store.remember("main", memory_document())
            recalled = store.recall("main", "русский", include_details=True)

        self.assertEqual(recalled[0]["details"], {"style": "concise"})
        self.assertEqual(recalled[0]["provenance"]["source"], "explicit owner instruction")

    def test_unchanged_content_deduplicates_and_update_increments_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, MemoryStore(
            Path(tmp) / "memory.sqlite3"
        ) as store:
            created_status, created = store.remember("main", memory_document())
            unchanged_status, unchanged = store.remember("main", memory_document())
            updated_status, updated = store.remember(
                "main",
                memory_document(summary="Отвечать по-русски, кратко и технически."),
                expected_revision=1,
            )

        self.assertEqual(created_status, "created")
        self.assertEqual(unchanged_status, "unchanged")
        self.assertEqual(unchanged["revision"], created["revision"])
        self.assertEqual(updated_status, "updated")
        self.assertEqual(updated["revision"], 2)

    def test_stale_revision_cannot_overwrite_newer_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, MemoryStore(
            Path(tmp) / "memory.sqlite3"
        ) as store:
            store.remember("main", memory_document())
            store.remember(
                "main",
                memory_document(summary="Вторая версия"),
                expected_revision=1,
            )
            with self.assertRaisesRegex(RuntimeError, "revision is stale"):
                store.remember(
                    "main",
                    memory_document(summary="Устаревшая запись"),
                    expected_revision=1,
                )

    def test_temporal_memory_requires_future_expiry(self) -> None:
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        with tempfile.TemporaryDirectory() as tmp, MemoryStore(
            Path(tmp) / "memory.sqlite3"
        ) as store:
            with self.assertRaisesRegex(ValueError, "requires expires_at"):
                store.remember("main", memory_document(validity="temporal"))
            status, item = store.remember(
                "main",
                memory_document(validity="temporal", expires_at=future),
            )

        self.assertEqual(status, "created")
        self.assertEqual(item["expires_at"], future)

    def test_rejects_raw_content_secrets_and_missing_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, MemoryStore(
            Path(tmp) / "memory.sqlite3"
        ) as store:
            with self.assertRaisesRegex(ValueError, "raw_messages.*forbidden"):
                store.remember(
                    "main",
                    memory_document(details={"raw_messages": ["private text"]}),
                )
            with self.assertRaisesRegex(ValueError, "provenance.source"):
                store.remember("main", memory_document(provenance={}))

    def test_scope_storage_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, MemoryStore(
            Path(tmp) / "memory.sqlite3"
        ) as store, patch("core.memory_store.MAX_ITEMS_PER_SCOPE", 3):
            for index in range(5):
                store.remember("main", memory_document(subject=f"Предмет {index}"))
            items = store.list_recent("main", scope="global", limit=20)

        self.assertEqual(len(items), 3)
        self.assertEqual({item["subject"] for item in items}, {"Предмет 2", "Предмет 3", "Предмет 4"})

    def test_account_storage_is_bounded_across_scopes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, MemoryStore(
            Path(tmp) / "memory.sqlite3"
        ) as store, patch("core.memory_store.MAX_ITEMS_PER_ACCOUNT", 3):
            for index in range(5):
                store.remember(
                    "main",
                    memory_document(subject=f"Предмет {index}", scope=f"project:item-{index}"),
                )
            items = store.list_recent("main", limit=20)

        self.assertEqual(len(items), 3)
        self.assertEqual({item["subject"] for item in items}, {"Предмет 2", "Предмет 3", "Предмет 4"})

    def test_expired_memory_is_purged_before_recall(self) -> None:
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        with tempfile.TemporaryDirectory() as tmp, MemoryStore(
            Path(tmp) / "memory.sqlite3"
        ) as store:
            store.remember(
                "main",
                memory_document(validity="temporal", expires_at=future),
            )
            store.connection.execute(
                "UPDATE memory_items SET expires_at = ?",
                ("2000-01-01T00:00:00+00:00",),
            )
            store.connection.commit()
            recalled = store.recall("main", "русский")
            count = store.stats("main")["item_count"]

        self.assertEqual(recalled, [])
        self.assertEqual(count, 0)

    def test_database_path_reuses_legacy_summary_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            legacy = data_dir / "summary_memory.sqlite3"
            legacy.touch()
            self.assertEqual(memory_database_path(data_dir), legacy)
            (data_dir / "userbot_memory.sqlite3").touch()
            self.assertEqual(
                memory_database_path(data_dir),
                data_dir / "userbot_memory.sqlite3",
            )

    def test_summary_and_general_memory_share_one_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "userbot_memory.sqlite3"
            with SummaryStore(path):
                pass
            with MemoryStore(path) as store:
                store.remember("main", memory_document())
                tables = {
                    row[0]
                    for row in store.connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }

        self.assertIn("summary_snapshots", tables)
        self.assertIn("memory_items", tables)


if __name__ == "__main__":
    unittest.main()
