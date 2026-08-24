from __future__ import annotations

import argparse
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import richtext


class RichTextParserTests(unittest.TestCase):
    def test_parses_supported_entities_and_code_language(self) -> None:
        source = (
            "<strong>Заголовок</strong>\n"
            "<blockquote expandable>Важно</blockquote>\n"
            "<pre><code class=\"language-json\">{&quot;ok&quot;: true}</code></pre>"
        )
        text, entities = richtext.parse_richtext(source)
        self.assertEqual(text, 'Заголовок\nВажно\n{"ok": true}')
        self.assertEqual(
            [type(entity).__name__ for entity in entities],
            ["MessageEntityBold", "MessageEntityBlockquote", "MessageEntityPre"],
        )
        self.assertEqual(entities[-1].language, "json")
        self.assertTrue(entities[1].collapsed)

    def test_rejects_unsupported_or_unclosed_markup(self) -> None:
        for source in ("<h1>Title</h1>", "<strong>unclosed", "<br>"):
            with self.assertRaises(ValueError):
                richtext.parse_richtext(source)


class RichTextRuntimeTests(unittest.TestCase):
    def _args(self, *, execute: bool) -> argparse.Namespace:
        return argparse.Namespace(
            account="main",
            chat="99",
            message_id=10,
            text="<strong>new</strong> <code>x</code>",
            file=None,
            no_link_preview=True,
            execute=execute,
        )

    def test_dry_run_never_edits(self) -> None:
        class Client:
            async def connect(self):
                return None

            async def disconnect(self):
                return None

            async def is_user_authorized(self):
                return True

            async def get_entity(self, _chat):
                return SimpleNamespace(id=99)

            async def get_messages(self, _entity, *, ids):
                return SimpleNamespace(id=ids, out=True, message="old", entities=[])

            async def edit_message(self, *_args, **_kwargs):
                raise AssertionError("dry-run attempted a Telegram edit")

        settings = SimpleNamespace(account="main", session_name="session", api_id=1, api_hash="hash")
        with (
            patch.object(richtext, "load_settings", return_value=settings),
            patch.object(richtext, "apply_runtime_env"),
            patch.object(richtext, "TelegramClient", return_value=Client()),
        ):
            result = asyncio.run(richtext.run(self._args(execute=False)))
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["requested"]["parse_mode"], "html")
        self.assertEqual(result["requested"]["entities"][0]["type"], "MessageEntityBold")

    def test_execute_checks_plain_text_and_entities(self) -> None:
        source = "<strong>new</strong> <code>x</code>"
        plain, entities = richtext.parse_richtext(source)

        class Client:
            def __init__(self):
                self.edited = False

            async def connect(self):
                return None

            async def disconnect(self):
                return None

            async def is_user_authorized(self):
                return True

            async def get_entity(self, _chat):
                return SimpleNamespace(id=99)

            async def get_messages(self, _entity, *, ids):
                if self.edited:
                    return SimpleNamespace(id=ids, out=True, message=plain, entities=entities)
                return SimpleNamespace(id=ids, out=True, message="old", entities=[])

            async def edit_message(self, _entity, _message_id, text, **kwargs):
                self.assert_html_kwargs(kwargs, text)
                self.edited = True
                return SimpleNamespace(id=10)

            def assert_html_kwargs(self, kwargs, text):
                if kwargs.get("parse_mode") != "html" or text != source:
                    raise AssertionError("richtext did not use Telethon HTML parse mode")

        client = Client()
        settings = SimpleNamespace(account="main", session_name="session", api_id=1, api_hash="hash")
        with (
            patch.object(richtext, "load_settings", return_value=settings),
            patch.object(richtext, "apply_runtime_env"),
            patch.object(richtext, "TelegramClient", return_value=client),
        ):
            result = asyncio.run(richtext.run(self._args(execute=True)))
        self.assertTrue(result["verification"]["verified"])
        self.assertTrue(result["verification"]["entities"])

    def test_entity_verifier_allows_telegram_auto_links(self) -> None:
        _, expected = richtext.parse_richtext("<strong>new</strong>")
        from telethon.tl.types import MessageEntityUrl

        actual = [*expected, MessageEntityUrl(offset=5, length=14)]
        result = richtext.verify_entities(expected, actual)
        self.assertTrue(result["verified"])
        self.assertEqual(len(result["allowed_auto"]), 1)

    def test_entity_verifier_rejects_unexpected_formatting(self) -> None:
        _, expected = richtext.parse_richtext("<strong>new</strong>")
        from telethon.tl.types import MessageEntityItalic

        result = richtext.verify_entities(expected, [*expected, MessageEntityItalic(offset=0, length=3)])
        self.assertFalse(result["verified"])
        self.assertEqual(len(result["unexpected"]), 1)


if __name__ == "__main__":
    unittest.main()
