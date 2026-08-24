from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import transcribe_audio_native


class NativeTranscribeFocusedTests(unittest.TestCase):
    def test_numeric_dialog_fallback_resolves_uncached_chat(self) -> None:
        entity = SimpleNamespace(id=42)

        class FakeClient:
            async def get_input_entity(self, value):
                if isinstance(value, int):
                    raise ValueError("not in cache")
                return f"input:{value.id}"

            async def iter_dialogs(self):
                yield SimpleNamespace(id=-10042, entity=entity)

        resolved = asyncio.run(transcribe_audio_native._resolve_input_entity(FakeClient(), -10042))
        self.assertEqual(resolved, "input:42")

    def test_invalid_timeout_is_rejected_before_network_call(self) -> None:
        with self.assertRaisesRegex(ValueError, "timeout must be positive"):
            asyncio.run(
                transcribe_audio_native.transcribe_message(
                    object(), chat=-10042, message_id=1, timeout=0
                )
            )

    def test_latest_voice_rechecks_and_follows_one_newer_voice(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.scan = 0

            async def get_input_entity(self, _value):
                return "peer"

            async def iter_messages(self, _entity, *, limit):
                self.scan += 1
                voice_id = 10 if self.scan == 1 else 11
                yield SimpleNamespace(id=99, sender_id=7, voice=False)
                yield SimpleNamespace(id=voice_id, sender_id=7, voice=True)

        calls: list[int] = []

        async def fake_transcribe(_client, **kwargs):
            calls.append(kwargs["message_id"])
            return transcribe_audio_native.Result(
                status="ok",
                complete=True,
                message_id=kwargs["message_id"],
                sender_id=7,
                text=f"voice-{kwargs['message_id']}",
            )

        original = transcribe_audio_native.transcribe_message
        transcribe_audio_native.transcribe_message = fake_transcribe
        try:
            result = asyncio.run(
                transcribe_audio_native.transcribe_latest_voice(
                    FakeClient(),
                    chat=-10042,
                    expected_sender_id=7,
                    timeout=1,
                    freshness_retries=1,
                )
            )
        finally:
            transcribe_audio_native.transcribe_message = original

        self.assertEqual(calls, [10, 11])
        self.assertTrue(result.complete)
        self.assertEqual(result.message_id, 11)
        self.assertEqual(result.freshness_status, "current")
        self.assertEqual(result.superseded_message_ids, [10])

    def test_latest_voice_fails_closed_when_tail_keeps_moving(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.scan = 0

            async def get_input_entity(self, _value):
                return "peer"

            async def iter_messages(self, _entity, *, limit):
                self.scan += 1
                yield SimpleNamespace(id=9 + self.scan, sender_id=7, voice=True)

        async def fake_transcribe(_client, **kwargs):
            return transcribe_audio_native.Result(
                status="ok",
                complete=True,
                message_id=kwargs["message_id"],
                sender_id=7,
                text=f"voice-{kwargs['message_id']}",
            )

        original = transcribe_audio_native.transcribe_message
        transcribe_audio_native.transcribe_message = fake_transcribe
        try:
            result = asyncio.run(
                transcribe_audio_native.transcribe_latest_voice(
                    FakeClient(),
                    chat=-10042,
                    expected_sender_id=7,
                    timeout=1,
                    freshness_retries=1,
                )
            )
        finally:
            transcribe_audio_native.transcribe_message = original

        self.assertFalse(result.complete)
        self.assertEqual(result.status, "superseded")
        self.assertEqual(result.message_id, 11)
        self.assertEqual(result.newer_message_id, 12)
        self.assertEqual(result.freshness_status, "newer_voice_found")


if __name__ == "__main__":
    unittest.main()
