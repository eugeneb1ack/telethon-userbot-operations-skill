from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import summarize_chat_native, transcribe_audio_native


class NativeSummaryFocusedTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
