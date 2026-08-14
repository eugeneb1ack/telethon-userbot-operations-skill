from __future__ import annotations

import ast
import asyncio
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import config
from modules import purge_me, react_recent_user_messages as reactions


class ConfigIsolationTests(unittest.TestCase):
    def test_normalizes_russian_phone_number_without_plus(self) -> None:
        self.assertEqual(config._normalize_phone_number("79991234567"), "+79991234567")
        self.assertEqual(config._normalize_phone_number("+79991234567"), "+79991234567")
        with self.assertRaises(ValueError):
            config._normalize_phone_number("89991234567")

    def test_rejects_path_like_account_names(self) -> None:
        for value in ("../other", "main/name", "has space", ".hidden"):
            with self.assertRaises(ValueError):
                config._normalize_account_name(value)

    def test_incomplete_second_profile_cannot_reuse_first_profile_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            accounts = Path(tmp)
            (accounts / "first.env").write_text(
                "API_ID=12345\nAPI_HASH=first-hash\nPHONE_NUMBER=+10000000000\n",
                encoding="utf-8",
            )
            (accounts / "second.env").write_text(
                "API_ID=54321\nPHONE_NUMBER=+10000000001\n",
                encoding="utf-8",
            )
            with (
                patch.object(config, "ACCOUNTS_DIR", accounts),
                patch.object(config, "SHARED_ENV_FILE", accounts / "_shared.env"),
                patch.dict(os.environ, {}, clear=True),
            ):
                first = config.load_settings("first")
                self.assertEqual(first.api_hash, "first-hash")
                with self.assertRaises(ValueError):
                    config.load_settings("second")


class CommandHandlerTests(unittest.TestCase):
    EXPECTED = {
        "add_contact.py": ".addcontact @somebody",
        "bot_chats.py": ".bots",
        "channel_chats.py": ".channels",
        "group_chats.py": ".groups",
        "personal_chats.py": ".dms",
        "ping.py": ".ping",
        "purge_me.py": ".purgeme",
        "summarize.py": ".summarize @example",
    }

    def test_commands_match_their_text_and_are_outgoing_only(self) -> None:
        modules_dir = ROOT / "modules"
        for filename, command in self.EXPECTED.items():
            tree = ast.parse((modules_dir / filename).read_text(encoding="utf-8"))
            calls = [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "NewMessage"
                and any(keyword.arg == "pattern" for keyword in node.keywords)
            ]
            self.assertEqual(len(calls), 1, filename)
            call = calls[0]
            pattern = next(keyword.value.value for keyword in call.keywords if keyword.arg == "pattern")
            outgoing = next(keyword.value.value for keyword in call.keywords if keyword.arg == "outgoing")
            self.assertTrue(outgoing, filename)
            self.assertIsNotNone(re.match(pattern, command), filename)


class PurgeAndReactionTests(unittest.TestCase):
    def test_purge_dry_run_never_calls_delete(self) -> None:
        class Client:
            async def iter_messages(self, _entity, *, offset_id=0, **_kwargs):
                if offset_id:
                    return
                yield SimpleNamespace(id=10, out=True)
                yield SimpleNamespace(id=9, out=True)

            async def delete_messages(self, *_args, **_kwargs):
                raise AssertionError("dry-run attempted deletion")

        stats = asyncio.run(
            purge_me.purge_my_messages(
                Client(),
                object(),
                execute=False,
                search_pause_seconds=0,
                delete_pause_seconds=0,
            )
        )
        self.assertEqual((stats.checked, stats.deleted, stats.batches), (2, 0, 1))

    def test_reaction_helpers_keep_only_the_current_users_reactions(self) -> None:
        mine = SimpleNamespace(
            peer_id=SimpleNamespace(user_id=1),
            reaction=SimpleNamespace(emoticon="✅"),
        )
        someone_else = SimpleNamespace(
            peer_id=SimpleNamespace(user_id=2),
            reaction=SimpleNamespace(emoticon="🔥"),
        )
        message = SimpleNamespace(reactions=SimpleNamespace(recent_reactions=[mine, someone_else]))
        self.assertTrue(reactions.has_my_recent_reaction(message, 1, "✅"))
        self.assertEqual(reactions.my_recent_reactions(message, 1), [mine.reaction])


class SecretRegressionTests(unittest.TestCase):
    def test_direct_helpers_do_not_embed_telegram_app_credentials(self) -> None:
        for filename in ("send_message.py", "smart_assistant.py"):
            source = (ROOT / "modules" / filename).read_text(encoding="utf-8")
            self.assertIsNone(
                re.search(r"TelegramClient\([^\n]*,\s*\d{5,}\s*,\s*['\"][^'\"]+['\"]\)", source),
                filename,
            )


if __name__ == "__main__":
    unittest.main()
