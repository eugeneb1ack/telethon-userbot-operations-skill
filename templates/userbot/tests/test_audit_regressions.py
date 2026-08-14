from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import config, module_loader
from modules import purge_me, transcribe_audio_native


class ConfigSelectionTests(unittest.TestCase):
    def test_environment_selected_account_uses_its_own_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            accounts = base / "accounts"
            accounts.mkdir()
            (accounts / "main.env").write_text(
                "API_ID=12345\nAPI_HASH=hash\nPHONE_NUMBER=+10000000000\nSESSION_NAME=main-session\n",
                encoding="utf-8",
            )
            with (
                patch.object(config, "BASE_DIR", base),
                patch.object(config, "ACCOUNTS_DIR", accounts),
                patch.object(config, "SHARED_ENV_FILE", accounts / "_shared.env"),
                patch.dict(os.environ, {"USERBOT_ACCOUNT": "main"}, clear=True),
            ):
                settings = config.load_settings()
            self.assertEqual(settings.account, "main")
            self.assertEqual(Path(settings.runtime_dir), base / "runtime" / "main")
            self.assertEqual(Path(settings.session_name), base / "runtime" / "main" / "sessions" / "main-session")

    def test_isolated_profile_rejects_session_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            accounts = base / "accounts"
            accounts.mkdir()
            (accounts / "main.env").write_text(
                "API_ID=12345\nAPI_HASH=hash\nPHONE_NUMBER=+10000000000\nSESSION_NAME=../escape\n",
                encoding="utf-8",
            )
            with (
                patch.object(config, "BASE_DIR", base),
                patch.object(config, "ACCOUNTS_DIR", accounts),
                patch.object(config, "SHARED_ENV_FILE", accounts / "_shared.env"),
                patch.dict(os.environ, {}, clear=True),
            ):
                with self.assertRaisesRegex(ValueError, "SESSION_NAME"):
                    config.load_settings("main")


class ModuleLoaderTests(unittest.TestCase):
    def test_failed_registration_rolls_back_handlers_and_module(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.handlers: list[tuple[object, object]] = []

            def on(self, builder: object):
                def decorator(callback: object) -> object:
                    self.handlers.append((callback, builder))
                    return callback

                return decorator

            def list_event_handlers(self):
                return list(self.handlers)

            def remove_event_handler(self, callback: object, builder: object) -> None:
                self.handlers.remove((callback, builder))

        with tempfile.TemporaryDirectory() as tmp:
            modules_dir = Path(tmp)
            (modules_dir / "half_registered.py").write_text(
                "def register(client):\n"
                "    @client.on(object())\n"
                "    async def handler(event):\n"
                "        return None\n"
                "    raise RuntimeError('intentional test failure')\n",
                encoding="utf-8",
            )
            client = FakeClient()
            loaded = asyncio.run(module_loader.load_modules(client, modules_dir))
        self.assertEqual(loaded, [])
        self.assertEqual(client.handlers, [])
        self.assertNotIn("modules.half_registered", sys.modules)


class NativeTranscriptionTests(unittest.TestCase):
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


class PurgeSafetyTests(unittest.TestCase):
    def test_execute_deletes_only_outgoing_messages_and_verifies_remaining(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.deleted = False
                self.delete_calls: list[tuple[list[int], bool]] = []

            async def iter_messages(self, _entity, *, offset_id=0, limit=None, **_kwargs):
                if limit is None:
                    if not self.deleted:
                        yield SimpleNamespace(id=10, out=True)
                    return
                if not offset_id:
                    yield SimpleNamespace(id=10, out=True)
                    yield SimpleNamespace(id=9, out=False)

            async def delete_messages(self, _entity, ids, *, revoke):
                self.delete_calls.append((ids, revoke))
                self.deleted = True

        client = FakeClient()
        stats = asyncio.run(
            purge_me.purge_my_messages(
                client,
                object(),
                execute=True,
                search_pause_seconds=0,
                delete_pause_seconds=0,
            )
        )
        self.assertEqual((stats.checked, stats.deleted, stats.remaining), (1, 1, 0))
        self.assertEqual(client.delete_calls, [([10], True)])


if __name__ == "__main__":
    unittest.main()
