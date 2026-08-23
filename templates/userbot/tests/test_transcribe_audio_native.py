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


if __name__ == "__main__":
    unittest.main()
