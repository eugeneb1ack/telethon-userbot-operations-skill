from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import summarize_chat_native, transcribe_audio_native


class NativeSummaryFocusedTests(unittest.TestCase):
    def test_sender_filter_is_pushed_to_telethon_for_regular_chat(self) -> None:
        class FakeMessage:
            id = 10
            date = summarize_chat_native.datetime(
                2026, 1, 1, tzinfo=summarize_chat_native.timezone.utc
            )
            sender_id = 7
            action = None
            out = False
            reply_to_msg_id = None
            message = "hello"
            voice = video_note = audio = photo = video = False
            document = None

            async def get_sender(self):
                return SimpleNamespace(
                    id=7, first_name="User", last_name=None, username="user"
                )

        class FakeClient:
            def __init__(self) -> None:
                self.options = None

            async def get_input_entity(self, value):
                return value

            async def iter_messages(self, _entity, **options):
                self.options = options
                yield FakeMessage()

        client = FakeClient()
        records = asyncio.run(
            summarize_chat_native._collect_messages(
                client,
                object(),
                start_utc=None,
                end_utc=None,
                sender_id=7,
            )
        )
        self.assertEqual(client.options["from_user"], 7)
        self.assertEqual([item["id"] for item in records], [10])

    def test_topic_collection_keeps_sender_filter_client_side(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.options = None

            async def get_input_entity(self, value):
                return value

            async def iter_messages(self, _entity, **options):
                self.options = options
                if False:
                    yield None

        client = FakeClient()
        asyncio.run(
            summarize_chat_native._collect_recent_markers(
                client,
                object(),
                start_utc=None,
                end_utc=None,
                sender_id=7,
                topic_id=42,
            )
        )
        self.assertNotIn("from_user", client.options)

    def test_queue_is_fifo_and_writes_one_completion_event_per_record(self) -> None:
        records = [
            {"id": index, "kind": "voice", "author": {"id": 7}, "transcription": None}
            for index in range(3)
        ]
        calls: list[int] = []

        async def fake_transcribe(*_args, **kwargs):
            calls.append(kwargs["message_id"])
            return transcribe_audio_native.Result(
                status="ok", complete=True, sender_id=7, text="ok"
            )

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            summarize_chat_native, "transcribe_message", fake_transcribe
        ):
            progress = Path(tmp) / "progress.jsonl"
            asyncio.run(
                summarize_chat_native._transcribe_records(
                    object(),
                    -10042,
                    records,
                    timeout=1,
                    concurrency=1,
                    request_timeout=1,
                    retries=0,
                    progress_path=progress,
                )
            )
            events = [json.loads(line) for line in progress.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(calls, [0, 1, 2])
        self.assertEqual([event["message_id"] for event in events], [0, 1, 2])
        self.assertTrue(all(item["transcription"]["complete"] for item in records))

    @staticmethod
    def _snapshot() -> dict:
        return {
            "source_message_count": 2,
            "first_message_time": "2026-01-01 10:00:00",
            "last_message_id": 11,
            "tail_markers": [
                {"id": 10, "fingerprint": "hash-10"},
                {"id": 11, "fingerprint": "hash-11"},
            ],
        }

    def test_memory_state_uses_hit_for_unchanged_tail(self) -> None:
        snapshot = self._snapshot()
        state = summarize_chat_native._memory_state(
            snapshot,
            list(snapshot["tail_markers"]),
            request={"mode": "date", "date": "2026-01-01"},
            window_start="2026-01-01T00:00:00+03:00",
            force_refresh=False,
        )
        self.assertEqual(state, "hit")

    def test_memory_state_uses_delta_only_with_valid_anchor(self) -> None:
        state = summarize_chat_native._memory_state(
            self._snapshot(),
            [
                {"id": 10, "fingerprint": "hash-10"},
                {"id": 11, "fingerprint": "hash-11"},
                {"id": 12, "fingerprint": "hash-12"},
            ],
            request={"mode": "range", "since": "a", "until": "b"},
            window_start="2026-01-01T00:00:00+03:00",
            force_refresh=False,
        )
        self.assertEqual(state, "delta")

    def test_memory_state_refreshes_changed_tail(self) -> None:
        state = summarize_chat_native._memory_state(
            self._snapshot(),
            [
                {"id": 10, "fingerprint": "changed"},
                {"id": 11, "fingerprint": "hash-11"},
            ],
            request={"mode": "date", "date": "2026-01-01"},
            window_start="2026-01-01T00:00:00+03:00",
            force_refresh=False,
        )
        self.assertEqual(state, "refresh")

    def test_memory_state_refreshes_deleted_marker_inside_overlap(self) -> None:
        snapshot = self._snapshot()
        snapshot["last_message_id"] = 12
        snapshot["source_message_count"] = 3
        snapshot["tail_markers"].append({"id": 12, "fingerprint": "hash-12"})
        state = summarize_chat_native._memory_state(
            snapshot,
            [
                {"id": 10, "fingerprint": "hash-10"},
                {"id": 12, "fingerprint": "hash-12"},
                {"id": 13, "fingerprint": "hash-13"},
            ],
            request={"mode": "range", "since": "a", "until": "b"},
            window_start="2026-01-01T00:00:00+03:00",
            force_refresh=False,
        )
        self.assertEqual(state, "refresh")

    def test_full_last_messages_window_refreshes_when_new_message_arrives(self) -> None:
        snapshot = self._snapshot()
        state = summarize_chat_native._memory_state(
            snapshot,
            [
                *snapshot["tail_markers"],
                {"id": 12, "fingerprint": "hash-12"},
            ],
            request={"mode": "last_messages", "count": 2},
            window_start=None,
            force_refresh=False,
        )
        self.assertEqual(state, "refresh")


if __name__ == "__main__":
    unittest.main()
