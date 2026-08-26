from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.dialog_cursor_store import DialogCursorStore
from modules import dialog_updates_native


def record(message_id: int, kind: str = "text") -> dict:
    return {
        "id": message_id,
        "kind": kind,
        "text": f"text-{message_id}",
        "transcription": None,
    }


class DialogUpdatesTests(unittest.TestCase):
    def test_latest_collects_only_requested_sender_count_before_tail_check(self) -> None:
        calls: list[dict] = []

        async def fake_collect(*_args, **kwargs):
            calls.append(kwargs)
            return ([record(10)], False) if len(calls) == 1 else ([], False)

        async def fake_transcribe(*_args, **_kwargs):
            return None

        with tempfile.TemporaryDirectory() as tmp, DialogCursorStore(
            Path(tmp) / "memory.sqlite3"
        ) as store, patch.object(
            dialog_updates_native, "_resolve_entity", return_value=SimpleNamespace(id=42)
        ), patch.object(
            dialog_updates_native, "_chat_id", return_value=42
        ), patch.object(
            dialog_updates_native, "_collect_bounded", side_effect=fake_collect
        ), patch.object(
            dialog_updates_native, "_transcribe_records", side_effect=fake_transcribe
        ):
            result = asyncio.run(
                dialog_updates_native.collect_dialog_updates(
                    object(),
                    account="main",
                    chat=-10042,
                    sender_id=7,
                    mode="latest",
                    content="all",
                    latest_count=1,
                    after_message_id=None,
                    scan_limit=200,
                    max_rounds=3,
                    transcription_timeout=1,
                    request_timeout=1,
                    cursor_store=store,
                )
            )

        self.assertTrue(result["complete"])
        self.assertEqual(calls[0]["scan_limit"], 1)
        self.assertFalse(calls[0]["detect_overflow"])
        self.assertEqual(calls[1]["scan_limit"], 200)

    def test_latest_voice_keeps_bounded_scan_before_content_filter(self) -> None:
        calls: list[dict] = []

        async def fake_collect(*_args, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return [record(9, "voice"), record(10, "text")], False
            return [], False

        async def fake_transcribe(_client, _chat, records, **_kwargs):
            for item in records:
                if item["kind"] == "voice":
                    item["transcription"] = {"complete": True, "text": "voice-9"}

        with tempfile.TemporaryDirectory() as tmp, DialogCursorStore(
            Path(tmp) / "memory.sqlite3"
        ) as store, patch.object(
            dialog_updates_native, "_resolve_entity", return_value=SimpleNamespace(id=42)
        ), patch.object(
            dialog_updates_native, "_chat_id", return_value=42
        ), patch.object(
            dialog_updates_native, "_collect_bounded", side_effect=fake_collect
        ), patch.object(
            dialog_updates_native, "_transcribe_records", side_effect=fake_transcribe
        ):
            result = asyncio.run(
                dialog_updates_native.collect_dialog_updates(
                    object(),
                    account="main",
                    chat=-10042,
                    sender_id=7,
                    mode="latest",
                    content="voice",
                    latest_count=1,
                    after_message_id=None,
                    scan_limit=200,
                    max_rounds=3,
                    transcription_timeout=1,
                    request_timeout=1,
                    cursor_store=store,
                )
            )

        self.assertTrue(result["complete"])
        self.assertEqual([item["id"] for item in result["records"]], [9])
        self.assertEqual(calls[0]["scan_limit"], 200)
        self.assertFalse(calls[0]["detect_overflow"])

    def test_mixed_latest_follows_new_text_and_voice_then_advances_cursor(self) -> None:
        scans = [
            ([record(10)], False),
            ([record(11, "voice")], False),
            ([], False),
        ]

        async def fake_collect(*_args, **_kwargs):
            return scans.pop(0)

        async def fake_transcribe(_client, _chat, records, **_kwargs):
            for item in records:
                if item["kind"] == "voice" and item["transcription"] is None:
                    item["transcription"] = {"complete": True, "text": f"voice-{item['id']}"}

        with tempfile.TemporaryDirectory() as tmp, DialogCursorStore(
            Path(tmp) / "memory.sqlite3"
        ) as store, patch.object(
            dialog_updates_native, "_resolve_entity", return_value=SimpleNamespace(id=42)
        ), patch.object(
            dialog_updates_native, "_chat_id", return_value=42
        ), patch.object(
            dialog_updates_native, "_collect_bounded", side_effect=fake_collect
        ), patch.object(
            dialog_updates_native, "_transcribe_records", side_effect=fake_transcribe
        ):
            result = asyncio.run(
                dialog_updates_native.collect_dialog_updates(
                    object(),
                    account="main",
                    chat=-10042,
                    sender_id=7,
                    mode="latest",
                    content="all",
                    latest_count=1,
                    after_message_id=None,
                    scan_limit=200,
                    max_rounds=3,
                    transcription_timeout=1,
                    request_timeout=1,
                    cursor_store=store,
                )
            )
            cursor = store.get(account="main", chat_id=42, sender_id=7, content_scope="all")

        self.assertTrue(result["complete"])
        self.assertEqual([item["id"] for item in result["records"]], [10, 11])
        self.assertEqual(result["records"][1]["transcription"]["text"], "voice-11")
        self.assertEqual(cursor, 11)

    def test_moving_tail_does_not_advance_cursor(self) -> None:
        scans = [
            ([record(10)], False),
            ([record(11)], False),
            ([record(12)], False),
        ]

        async def fake_collect(*_args, **_kwargs):
            return scans.pop(0)

        async def fake_transcribe(*_args, **_kwargs):
            return None

        with tempfile.TemporaryDirectory() as tmp, DialogCursorStore(
            Path(tmp) / "memory.sqlite3"
        ) as store, patch.object(
            dialog_updates_native, "_resolve_entity", return_value=SimpleNamespace(id=42)
        ), patch.object(
            dialog_updates_native, "_chat_id", return_value=42
        ), patch.object(
            dialog_updates_native, "_collect_bounded", side_effect=fake_collect
        ), patch.object(
            dialog_updates_native, "_transcribe_records", side_effect=fake_transcribe
        ):
            result = asyncio.run(
                dialog_updates_native.collect_dialog_updates(
                    object(),
                    account="main",
                    chat=-10042,
                    sender_id=7,
                    mode="latest",
                    content="all",
                    latest_count=1,
                    after_message_id=None,
                    scan_limit=200,
                    max_rounds=2,
                    transcription_timeout=1,
                    request_timeout=1,
                    cursor_store=store,
                )
            )
            cursor = store.get(account="main", chat_id=42, sender_id=7, content_scope="all")

        self.assertFalse(result["complete"])
        self.assertEqual(result["status"], "moving_tail")
        self.assertIsNone(cursor)

    def test_unseen_uses_content_scoped_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, DialogCursorStore(
            Path(tmp) / "memory.sqlite3"
        ) as store:
            store.advance(
                account="main", chat_id=42, sender_id=7, content_scope="voice", last_message_id=20
            )
            self.assertEqual(
                store.get(account="main", chat_id=42, sender_id=7, content_scope="voice"), 20
            )
            self.assertIsNone(
                store.get(account="main", chat_id=42, sender_id=7, content_scope="text")
            )

    def test_empty_after_anchor_initializes_cursor_for_later_unseen(self) -> None:
        async def fake_collect(*_args, **_kwargs):
            return [], False

        async def fake_transcribe(*_args, **_kwargs):
            return None

        with tempfile.TemporaryDirectory() as tmp, DialogCursorStore(
            Path(tmp) / "memory.sqlite3"
        ) as store, patch.object(
            dialog_updates_native, "_resolve_entity", return_value=SimpleNamespace(id=42)
        ), patch.object(
            dialog_updates_native, "_chat_id", return_value=42
        ), patch.object(
            dialog_updates_native, "_collect_bounded", side_effect=fake_collect
        ), patch.object(
            dialog_updates_native, "_transcribe_records", side_effect=fake_transcribe
        ):
            result = asyncio.run(
                dialog_updates_native.collect_dialog_updates(
                    object(),
                    account="main",
                    chat=-10042,
                    sender_id=7,
                    mode="after_message",
                    content="all",
                    latest_count=None,
                    after_message_id=50,
                    scan_limit=200,
                    max_rounds=3,
                    transcription_timeout=1,
                    request_timeout=1,
                    cursor_store=store,
                )
            )
            cursor = store.get(account="main", chat_id=42, sender_id=7, content_scope="all")

        self.assertEqual(result["status"], "empty")
        self.assertTrue(result["cursor_advanced"])
        self.assertEqual(cursor, 50)


if __name__ == "__main__":
    unittest.main()
