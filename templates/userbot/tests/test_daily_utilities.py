from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from modules import download_media, list_group_members, pin_message, search_messages


SETTINGS = SimpleNamespace(
    account="main",
    session_name="/tmp/test-session",
    api_id=1,
    api_hash="test-hash",
    data_dir=tempfile.gettempdir(),
)


class SearchMessagesTests(unittest.TestCase):
    def test_search_requires_a_filter_and_bounds_limit(self) -> None:
        args = search_messages.parser().parse_args(["--chat", "@chat"])
        with self.assertRaisesRegex(ValueError, "filter"):
            search_messages.validate_args(args)
        too_many = search_messages.parser().parse_args(["--chat", "@chat", "--query", "x", "--limit", "501"])
        with self.assertRaisesRegex(ValueError, "limit"):
            search_messages.validate_args(too_many)

    def test_search_output_is_contained_in_runtime_data(self) -> None:
        settings = SimpleNamespace(data_dir="/tmp/runtime/data")
        self.assertEqual(
            search_messages.safe_output_path(settings, "history.json"),
            Path("/tmp/runtime/data/searches/history.json"),
        )


class GroupMemberListTests(unittest.TestCase):
    def test_participant_output_filename_cannot_escape_runtime_data(self) -> None:
        args = list_group_members.parser().parse_args(["--chat", "@chat", "--output", "../escape"])
        with self.assertRaisesRegex(ValueError, "filename"):
            list_group_members.validate_args(args)

    def test_member_record_avoids_numeric_identifier_output(self) -> None:
        user = SimpleNamespace(id=42, first_name="Alice", last_name=None, username="alice")
        self.assertEqual(list_group_members.participant_record(user), {"name": "Alice", "username": "@alice"})


class DownloadMediaTests(unittest.TestCase):
    def test_download_subdir_rejects_path_like_values(self) -> None:
        for value in ("../escape", "nested/path", ".hidden"):
            with self.assertRaises(ValueError):
                download_media.validate_subdir(value)

    def test_download_dry_run_never_calls_download_media(self) -> None:
        chat = SimpleNamespace(id=10, title="Chat", username=None)
        message = SimpleNamespace(
            id=7,
            media=object(),
            voice=False,
            video_note=False,
            photo=object(),
            video=False,
            audio=False,
            document=None,
            file=SimpleNamespace(ext=".jpg", name=None, mime_type="image/jpeg", size=100),
        )

        class FakeClient:
            def __init__(self, *_args) -> None:
                self.download_calls = 0

            async def connect(self) -> None:
                return None

            async def disconnect(self) -> None:
                return None

            async def is_user_authorized(self) -> bool:
                return True

            async def get_entity(self, _value):
                return chat

            async def get_messages(self, _chat, *, ids):
                return [message] if ids == [7] else []

            async def download_media(self, *_args, **_kwargs):
                self.download_calls += 1
                raise AssertionError("dry-run attempted a local download")

        args = download_media.parser().parse_args(["--chat", "@chat", "--message-ids", "7"])
        fake = FakeClient()
        with (
            patch.object(download_media, "load_settings", return_value=SETTINGS),
            patch.object(download_media, "apply_runtime_env"),
            patch.object(download_media, "TelegramClient", return_value=fake),
        ):
            result = asyncio.run(download_media.run(args))
        self.assertTrue(result["dry_run"])
        self.assertEqual(fake.download_calls, 0)


class PinMessageTests(unittest.TestCase):
    def test_pin_only_options_are_not_silently_ignored(self) -> None:
        args = pin_message.parser().parse_args(
            ["--chat", "@chat", "--message-id", "7", "--action", "unpin", "--notify"]
        )
        with self.assertRaisesRegex(ValueError, "only with"):
            pin_message.validate_args(args)

    def test_inspect_cannot_execute(self) -> None:
        args = pin_message.parser().parse_args(["--chat", "@chat", "--message-id", "7", "--execute"])
        with self.assertRaisesRegex(ValueError, "read-only"):
            pin_message.validate_args(args)


if __name__ == "__main__":
    unittest.main()
