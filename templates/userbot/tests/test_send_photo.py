from __future__ import annotations

import argparse
import asyncio
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import send_photo


class SendPhotoTests(unittest.TestCase):
    @staticmethod
    def image(directory: str) -> Path:
        path = Path(directory) / "cat.jpg"
        Image.new("RGB", (4, 3), "red").save(path, "JPEG")
        return path

    @staticmethod
    def args(path: Path, *, execute: bool) -> argparse.Namespace:
        return argparse.Namespace(
            account="main",
            chat="99",
            photo=str(path),
            caption="caption",
            execute=execute,
        )

    def test_rejects_missing_file(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-empty"):
            send_photo.photo_path("/definitely/not/a/file.jpg")

    def test_rejects_non_photo_extension_and_fake_image(self) -> None:
        with TemporaryDirectory() as directory:
            item = Path(directory) / "item.txt"
            item.write_text("x")
            with self.assertRaisesRegex(ValueError, "JPG"):
                send_photo.photo_path(str(item))

            fake = Path(directory) / "fake.jpg"
            fake.write_bytes(b"not an image")
            with self.assertRaisesRegex(ValueError, "valid readable image"):
                send_photo.photo_path(str(fake))

    def test_accepts_real_jpg_and_reports_dimensions(self) -> None:
        with TemporaryDirectory() as directory:
            item = self.image(directory)
            self.assertEqual(send_photo.photo_path(str(item)), item.resolve())
            metadata = send_photo.photo_metadata(item)
            self.assertEqual((metadata["width"], metadata["height"]), (4, 3))
            self.assertEqual(metadata["format"], "JPEG")

    def test_dry_run_validates_without_sending(self) -> None:
        class Client:
            async def connect(self): pass
            async def disconnect(self): pass
            async def is_user_authorized(self): return True
            async def get_entity(self, _chat): return SimpleNamespace(id=99)
            async def send_file(self, *_args, **_kwargs):
                raise AssertionError("dry-run sent a photo")

        settings = SimpleNamespace(account="main", session_name="session", api_id=1, api_hash="hash")
        with TemporaryDirectory() as directory:
            with (
                patch.object(send_photo, "load_settings", return_value=settings),
                patch.object(send_photo, "apply_runtime_env"),
                patch.object(send_photo, "TelegramClient", return_value=Client()),
            ):
                result = asyncio.run(
                    send_photo.run(self.args(self.image(directory), execute=False))
                )
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["target"]["id"], 99)

    def test_execute_reads_back_photo_and_caption(self) -> None:
        class Client:
            async def connect(self): pass
            async def disconnect(self): pass
            async def is_user_authorized(self): return True
            async def get_entity(self, _chat): return SimpleNamespace(id=99)

            async def send_file(self, _target, _path, *, caption, force_document):
                self.sent = (caption, force_document)
                return SimpleNamespace(id=11)

            async def get_messages(self, _target, *, ids):
                return SimpleNamespace(id=ids, photo=object(), message="caption")

        client = Client()
        settings = SimpleNamespace(account="main", session_name="session", api_id=1, api_hash="hash")
        with TemporaryDirectory() as directory:
            with (
                patch.object(send_photo, "load_settings", return_value=settings),
                patch.object(send_photo, "apply_runtime_env"),
                patch.object(send_photo, "TelegramClient", return_value=client),
            ):
                result = asyncio.run(
                    send_photo.run(self.args(self.image(directory), execute=True))
                )
        self.assertEqual(client.sent, ("caption", False))
        self.assertEqual(result["status"], "sent")
        self.assertTrue(result["verified"])


if __name__ == "__main__":
    unittest.main()
