from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import rich_article


ARTICLE = "<h1>ChatGPT и WebMCP</h1><p>Короткий анонс.</p><blockquote>Проверка.</blockquote>"


class RichArticleTests(unittest.TestCase):
    def _source(self) -> tempfile.TemporaryDirectory[str]:
        directory = tempfile.TemporaryDirectory()
        Path(directory.name, "article.html").write_text(ARTICLE, encoding="utf-8")
        return directory

    def _args(self, path: Path, *, execute: bool = False) -> argparse.Namespace:
        return argparse.Namespace(
            account="main",
            chat="-100123",
            title="ChatGPT и WebMCP",
            file=str(path),
            format="html",
            skip_entity_detection=False,
            duplicate_window=25,
            allow_duplicate=False,
            execute=execute,
        )

    def test_html_validation_rejects_regular_web_markup(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported Rich HTML tag <script>"):
            rich_article.validate_source("<h1>Title</h1><script>x</script>", "html", "Title")

    def test_builds_document_html_constructor(self) -> None:
        message = rich_article.build_rich_message(ARTICLE, "html", skip_entity_detection=True)
        self.assertEqual(type(message).__name__, "InputRichMessageHTML")
        self.assertTrue(message.noautolink)
        self.assertEqual(message.html, ARTICLE)

    def test_dry_run_does_not_send(self) -> None:
        class Client:
            async def connect(self):
                return None

            async def disconnect(self):
                return None

            async def is_user_authorized(self):
                return True

            async def get_entity(self, _chat):
                return SimpleNamespace(id=123, title="Articles", broadcast=True)

            async def iter_messages(self, _entity, *, limit):
                if False:
                    yield limit

            async def __call__(self, _request):
                raise AssertionError("dry-run attempted a Telegram send")

        with self._source() as directory:
            args = self._args(Path(directory, "article.html"))
            settings = SimpleNamespace(account="main", session_name="session", api_id=1, api_hash="hash")
            with (
                patch.object(rich_article, "load_settings", return_value=settings),
                patch.object(rich_article, "apply_runtime_env"),
                patch.object(rich_article, "TelegramClient", return_value=Client()),
            ):
                result = asyncio.run(rich_article.run(args))
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["requested"]["rich_message_type"], "InputRichMessageHTML")
        self.assertTrue(result["target"]["broadcast"])

    def test_rejects_non_broadcast_target(self) -> None:
        class Client:
            async def connect(self):
                return None

            async def disconnect(self):
                return None

            async def is_user_authorized(self):
                return True

            async def get_entity(self, _chat):
                return SimpleNamespace(id=123, title="Group", broadcast=False)

        with self._source() as directory:
            args = self._args(Path(directory, "article.html"))
            settings = SimpleNamespace(account="main", session_name="session", api_id=1, api_hash="hash")
            with (
                patch.object(rich_article, "load_settings", return_value=settings),
                patch.object(rich_article, "apply_runtime_env"),
                patch.object(rich_article, "TelegramClient", return_value=Client()),
            ):
                with self.assertRaisesRegex(ValueError, "broadcast channel"):
                    asyncio.run(rich_article.run(args))

    def test_execute_sends_raw_rich_message_and_reads_it_back(self) -> None:
        class Client:
            def __init__(self):
                self.request = None

            async def connect(self):
                return None

            async def disconnect(self):
                return None

            async def is_user_authorized(self):
                return True

            async def get_entity(self, _chat):
                return SimpleNamespace(id=123, title="Articles", broadcast=True)

            async def iter_messages(self, _entity, *, limit):
                if False:
                    yield limit

            async def __call__(self, request):
                self.request = request
                return SimpleNamespace(updates=[SimpleNamespace(message=SimpleNamespace(id=77))])

            async def get_messages(self, _entity, *, ids):
                if ids != 77:
                    raise AssertionError(f"expected message id 77, got {ids}")
                return SimpleNamespace(
                    id=77,
                    out=True,
                    message="",
                    rich_message=SimpleNamespace(blocks=[{"text": "ChatGPT и WebMCP"}]),
                )

        with self._source() as directory:
            args = self._args(Path(directory, "article.html"), execute=True)
            client = Client()
            settings = SimpleNamespace(account="main", session_name="session", api_id=1, api_hash="hash")
            with (
                patch.object(rich_article, "load_settings", return_value=settings),
                patch.object(rich_article, "apply_runtime_env"),
                patch.object(rich_article, "TelegramClient", return_value=client),
            ):
                result = asyncio.run(rich_article.run(args))
        self.assertEqual(client.request.message, "")
        self.assertEqual(type(client.request.rich_message).__name__, "InputRichMessageHTML")
        self.assertTrue(result["verification"]["verified"])


if __name__ == "__main__":
    unittest.main()
