from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import send_message


class SendMessageTests(unittest.TestCase):
    def test_dry_run_validates_reply_target_without_sending(self) -> None:
        class Client:
            async def connect(self): pass
            async def disconnect(self): pass
            async def is_user_authorized(self): return True
            async def get_entity(self, _chat): return SimpleNamespace(id=99)
            async def get_messages(self, _entity, *, ids): return SimpleNamespace(id=ids)
            async def send_message(self, *_args, **_kwargs):
                raise AssertionError("dry-run sent a message")

        settings = SimpleNamespace(account="main", session_name="session", api_id=1, api_hash="hash")
        with (
            patch.object(send_message, "load_settings", return_value=settings),
            patch.object(send_message, "apply_runtime_env"),
            patch.object(send_message, "TelegramClient", return_value=Client()),
        ):
            result = asyncio.run(send_message.send_message(account="main", chat="99", text="reply", reply_to=10))
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["reply_to"], 10)

    def test_rejects_nonpositive_reply_target(self) -> None:
        with self.assertRaises(ValueError):
            asyncio.run(send_message.send_message(account="main", chat="99", text="reply", reply_to=0))

    def test_execute_verifies_text_and_reply_target(self) -> None:
        class Client:
            async def connect(self): pass
            async def disconnect(self): pass
            async def is_user_authorized(self): return True
            async def get_entity(self, _chat): return SimpleNamespace(id=99)

            async def send_message(self, _entity, text, *, reply_to):
                self.sent = (text, reply_to)
                return SimpleNamespace(id=11)

            async def get_messages(self, _entity, *, ids):
                return SimpleNamespace(
                    id=ids,
                    message="reply",
                    reply_to=SimpleNamespace(reply_to_msg_id=10),
                )

        client = Client()
        settings = SimpleNamespace(account="main", session_name="session", api_id=1, api_hash="hash")
        with (
            patch.object(send_message, "load_settings", return_value=settings),
            patch.object(send_message, "apply_runtime_env"),
            patch.object(send_message, "TelegramClient", return_value=client),
        ):
            result = asyncio.run(
                send_message.send_message(
                    account="main",
                    chat="99",
                    text="reply",
                    reply_to=10,
                    execute=True,
                )
            )
        self.assertEqual(client.sent, ("reply", 10))
        self.assertTrue(result["verified"])
        self.assertFalse(result["dry_run"])

    def test_rejects_empty_text_before_connecting(self) -> None:
        with self.assertRaisesRegex(ValueError, "empty"):
            asyncio.run(send_message.send_message(account="main", chat="99", text="  "))


if __name__ == "__main__":
    unittest.main()
