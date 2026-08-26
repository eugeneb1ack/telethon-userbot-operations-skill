from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import rich_article


ARTICLE = "<h1>ChatGPT и WebMCP</h1><p>Короткий анонс.</p><blockquote>Проверка.</blockquote>"
ARTICLE_WITH_PHOTO = ARTICLE + '<figure><img src="tg://photo?id=cover"><figcaption>Обложка</figcaption></figure>'


class RichArticleTests(unittest.TestCase):
    def _source(self, source: str = ARTICLE) -> tempfile.TemporaryDirectory[str]:
        directory = tempfile.TemporaryDirectory()
        Path(directory.name, "article.html").write_text(source, encoding="utf-8")
        return directory

    def _args(
        self,
        path: Path,
        *,
        execute: bool = False,
        media: list[str] | None = None,
    ) -> argparse.Namespace:
        return argparse.Namespace(
            account="main",
            chat="-100123",
            title="ChatGPT и WebMCP",
            file=str(path),
            format="html",
            media=media or [],
            skip_entity_detection=False,
            duplicate_window=25,
            allow_duplicate=False,
            execute=execute,
        )

    def test_html_validation_rejects_regular_web_markup(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported Rich HTML tag <script>"):
            rich_article.validate_source("<h1>Title</h1><script>x</script>", "html", "Title")

    def test_builds_document_html_constructor(self) -> None:
        media_file = object()
        message = rich_article.build_rich_message(
            ARTICLE,
            "html",
            skip_entity_detection=True,
            files=[media_file],
        )
        self.assertEqual(type(message).__name__, "InputRichMessageHTML")
        self.assertTrue(message.noautolink)
        self.assertEqual(message.html, ARTICLE)
        self.assertEqual(message.files, [media_file])

    def test_media_reference_requires_one_matching_local_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cover = Path(directory, "cover.png")
            cover.write_bytes(b"image bytes")
            spec = rich_article.parse_media_spec(f"cover:photo:{cover}")
            references = rich_article.validate_source(
                ARTICLE_WITH_PHOTO,
                "html",
                "ChatGPT и WebMCP",
                media_specs=[spec],
            )
        self.assertEqual(references, [rich_article.MediaReference(id="cover", kind="photo")])

        self_closing_references = rich_article.validate_source(
            ARTICLE_WITH_PHOTO.replace('id=cover">', 'id=cover"/>'),
            "html",
            "ChatGPT и WebMCP",
            media_specs=[spec],
        )
        self.assertEqual(
            self_closing_references,
            [rich_article.MediaReference(id="cover", kind="photo")],
        )

        with self.assertRaisesRegex(ValueError, "no matching --media file"):
            rich_article.validate_source(
                ARTICLE_WITH_PHOTO,
                "html",
                "ChatGPT и WebMCP",
                media_specs=[],
            )

    def test_media_spec_rejects_mismatched_kind_and_duplicate_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cover = Path(directory, "cover.png")
            cover.write_bytes(b"image bytes")
            with self.assertRaisesRegex(ValueError, "does not match file MIME"):
                rich_article.parse_media_spec(f"cover:video:{cover}")
            with self.assertRaisesRegex(ValueError, "ids must be unique"):
                rich_article.freeze_media_specs([f"cover:photo:{cover}", f"cover:photo:{cover}"])

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

    def test_dry_run_freezes_embedded_photo_without_upload(self) -> None:
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

            async def upload_file(self, _path):
                raise AssertionError("dry-run attempted a media upload")

            async def __call__(self, _request):
                raise AssertionError("dry-run attempted a Telegram write")

        with self._source(ARTICLE_WITH_PHOTO) as directory:
            cover = Path(directory, "cover.png")
            cover.write_bytes(b"image bytes")
            args = self._args(
                Path(directory, "article.html"),
                media=[f"cover:photo:{cover}"],
            )
            settings = SimpleNamespace(account="main", session_name="session", api_id=1, api_hash="hash")
            with (
                patch.object(rich_article, "load_settings", return_value=settings),
                patch.object(rich_article, "apply_runtime_env"),
                patch.object(rich_article, "TelegramClient", return_value=Client()),
            ):
                result = asyncio.run(rich_article.run(args))
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["requested"]["media"][0]["id"], "cover")
        self.assertEqual(result["requested"]["media_references"], [{"id": "cover", "kind": "photo"}])

    def test_upload_photo_creates_rich_file_binding(self) -> None:
        class Client:
            def __init__(self):
                self.uploaded_paths: list[str] = []
                self.requests: list[object] = []

            async def upload_file(self, path: str):
                self.uploaded_paths.append(path)
                return "uploaded-file"

            async def __call__(self, request):
                self.requests.append(request)
                return SimpleNamespace()

        with tempfile.TemporaryDirectory() as directory:
            cover = Path(directory, "cover.png")
            cover.write_bytes(b"image bytes")
            spec = rich_article.parse_media_spec(f"cover:photo:{cover}")
            client = Client()
            with patch.object(rich_article.utils, "get_input_photo", return_value=SimpleNamespace(id=901)):
                files, uploaded = asyncio.run(
                    rich_article.upload_rich_media(client, SimpleNamespace(id=123), [spec])
                )
        self.assertEqual(client.uploaded_paths, [str(cover.resolve())])
        self.assertEqual(type(client.requests[0]).__name__, "UploadMediaRequest")
        self.assertEqual(type(client.requests[0].media).__name__, "InputMediaUploadedPhoto")
        self.assertEqual(type(files[0]).__name__, "InputRichFilePhoto")
        self.assertEqual(uploaded[0].telegram_media_id, 901)

    def test_upload_audio_video_and_document_create_document_bindings(self) -> None:
        class Client:
            def __init__(self):
                self.requests: list[object] = []

            async def upload_file(self, path: str):
                return f"uploaded:{Path(path).name}"

            async def __call__(self, request):
                self.requests.append(request)
                return SimpleNamespace()

        with tempfile.TemporaryDirectory() as directory:
            paths = {
                "audio": Path(directory, "interview.m4a"),
                "video": Path(directory, "demo.mp4"),
                "document": Path(directory, "report.pdf"),
            }
            for path in paths.values():
                path.write_bytes(b"media bytes")
            specs = [
                rich_article.parse_media_spec(f"interview:audio:{paths['audio']}"),
                rich_article.parse_media_spec(f"demo:video:{paths['video']}"),
                rich_article.parse_media_spec(f"report:document:{paths['document']}"),
            ]
            client = Client()
            with patch.object(
                rich_article.utils,
                "get_input_document",
                side_effect=[SimpleNamespace(id=902), SimpleNamespace(id=903), SimpleNamespace(id=904)],
            ):
                files, uploaded = asyncio.run(
                    rich_article.upload_rich_media(client, SimpleNamespace(id=123), specs)
                )
        self.assertEqual([type(request.media).__name__ for request in client.requests], [
            "InputMediaUploadedDocument",
            "InputMediaUploadedDocument",
            "InputMediaUploadedDocument",
        ])
        self.assertEqual([request.media.force_file for request in client.requests], [False, False, True])
        self.assertEqual([type(file).__name__ for file in files], [
            "InputRichFileDocument",
            "InputRichFileDocument",
            "InputRichFileDocument",
        ])
        self.assertEqual([media.attachment_type for media in uploaded], ["document", "document", "document"])

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

    def test_execute_requires_readback_of_embedded_photo(self) -> None:
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
                return SimpleNamespace(updates=[SimpleNamespace(message=SimpleNamespace(id=88))])

            async def get_messages(self, _entity, *, ids):
                if ids != 88:
                    raise AssertionError(f"expected message id 88, got {ids}")
                return SimpleNamespace(
                    id=88,
                    out=True,
                    message="",
                    rich_message=SimpleNamespace(
                        blocks=[{"text": "ChatGPT и WebMCP"}],
                        photos=[SimpleNamespace(id=902)],
                        documents=[],
                    ),
                )

        with self._source(ARTICLE_WITH_PHOTO) as directory:
            cover = Path(directory, "cover.png")
            cover.write_bytes(b"image bytes")
            args = self._args(
                Path(directory, "article.html"),
                execute=True,
                media=[f"cover:photo:{cover}"],
            )
            rich_file = rich_article.types.InputRichFilePhoto(
                id="cover",
                photo=SimpleNamespace(id=902),
            )
            uploaded = rich_article.UploadedMedia(
                id="cover",
                kind="photo",
                attachment_type="photo",
                telegram_media_id=902,
            )
            client = Client()
            settings = SimpleNamespace(account="main", session_name="session", api_id=1, api_hash="hash")
            with (
                patch.object(rich_article, "load_settings", return_value=settings),
                patch.object(rich_article, "apply_runtime_env"),
                patch.object(rich_article, "TelegramClient", return_value=client),
                patch.object(
                    rich_article,
                    "upload_rich_media",
                    new=AsyncMock(return_value=([rich_file], [uploaded])),
                ),
            ):
                result = asyncio.run(rich_article.run(args))
        self.assertEqual(client.request.rich_message.files, [rich_file])
        self.assertTrue(result["verification"]["embedded_media"]["all_present"])
        self.assertTrue(result["verification"]["verified"])


if __name__ == "__main__":
    unittest.main()
