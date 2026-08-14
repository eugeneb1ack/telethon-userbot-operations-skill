from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core import telegram_targets
from modules import forward_messages, group_member, message_edit, profile_settings


SETTINGS = SimpleNamespace(
    account="main",
    session_name="/tmp/test-session",
    api_id=1,
    api_hash="test-hash",
)


class TargetParsingTests(unittest.TestCase):
    def test_message_ids_are_positive_unique(self) -> None:
        self.assertEqual(telegram_targets.parse_message_ids("3, 2,1"), [3, 2, 1])
        for invalid in ("", "0", "1,1", "one"):
            with self.assertRaises(ValueError):
                telegram_targets.parse_message_ids(invalid)


class ProfileSettingsTests(unittest.TestCase):
    def test_profile_changes_reject_naive_status_deadline(self) -> None:
        args = profile_settings.parser().parse_args(
            ["--emoji-status-document-id", "123", "--emoji-status-until", "2026-08-15T18:00:00"]
        )
        with self.assertRaisesRegex(ValueError, "timezone"):
            profile_settings.requested_changes(args)

    def test_profile_dry_run_does_not_mutate(self) -> None:
        class FakeClient:
            mutations: list[object]

            def __init__(self, *_args) -> None:
                self.mutations = []

            async def connect(self) -> None:
                return None

            async def disconnect(self) -> None:
                return None

            async def is_user_authorized(self) -> bool:
                return True

            async def get_me(self):
                return SimpleNamespace(id=1, first_name="Old", last_name=None, username="old", emoji_status=None)

            async def __call__(self, request):
                if type(request).__name__ == "GetFullUserRequest":
                    return SimpleNamespace(full_user=SimpleNamespace(about="old bio"))
                self.mutations.append(request)
                raise AssertionError("dry-run attempted a mutation")

        args = profile_settings.parser().parse_args(["--first-name", "New"])
        fake = FakeClient()
        with (
            patch.object(profile_settings, "load_settings", return_value=SETTINGS),
            patch.object(profile_settings, "apply_runtime_env"),
            patch.object(profile_settings, "TelegramClient", return_value=fake),
        ):
            result = asyncio.run(profile_settings.run(args))
        self.assertTrue(result["dry_run"])
        self.assertEqual(fake.mutations, [])


class MessageEditTests(unittest.TestCase):
    def test_edit_dry_run_rejects_write(self) -> None:
        entity = SimpleNamespace(id=10, title="Chat", username=None)
        message = SimpleNamespace(id=4, out=True, media=None, message="old")

        class FakeClient:
            def __init__(self, *_args) -> None:
                self.edit_calls = 0

            async def connect(self) -> None:
                return None

            async def disconnect(self) -> None:
                return None

            async def is_user_authorized(self) -> bool:
                return True

            async def get_entity(self, _value):
                return entity

            async def get_messages(self, _entity, *, ids):
                self.assert_id = ids
                return message

            async def edit_message(self, *_args, **_kwargs):
                self.edit_calls += 1
                raise AssertionError("dry-run attempted edit")

        args = message_edit.parser().parse_args(
            ["--chat", "@chat", "--message-id", "4", "--text", "<b>new</b>", "--parse-mode", "html"]
        )
        fake = FakeClient()
        with (
            patch.object(message_edit, "load_settings", return_value=SETTINGS),
            patch.object(message_edit, "apply_runtime_env"),
            patch.object(message_edit, "TelegramClient", return_value=fake),
        ):
            result = asyncio.run(message_edit.run(args))
        self.assertTrue(result["dry_run"])
        self.assertEqual(fake.edit_calls, 0)


class ForwardMessagesTests(unittest.TestCase):
    def test_forward_dry_run_freezes_and_inspects_source_without_forwarding(self) -> None:
        source = SimpleNamespace(id=10, title="Source", username=None)
        destination = SimpleNamespace(id=20, title="Destination", username=None)
        source_message = SimpleNamespace(id=7, sender_id=3, media=None, message="source text")

        class FakeClient:
            def __init__(self, *_args) -> None:
                self.forward_calls = 0

            async def connect(self) -> None:
                return None

            async def disconnect(self) -> None:
                return None

            async def is_user_authorized(self) -> bool:
                return True

            async def get_entity(self, value):
                return source if value == "@source" else destination

            async def get_messages(self, _entity, *, ids):
                return [source_message] if ids == [7] else []

            async def forward_messages(self, *_args, **_kwargs):
                self.forward_calls += 1
                raise AssertionError("dry-run attempted forward")

        args = forward_messages.parser().parse_args(
            ["--source-chat", "@source", "--destination-chat", "@destination", "--message-ids", "7"]
        )
        fake = FakeClient()
        with (
            patch.object(forward_messages, "load_settings", return_value=SETTINGS),
            patch.object(forward_messages, "apply_runtime_env"),
            patch.object(forward_messages, "TelegramClient", return_value=fake),
        ):
            result = asyncio.run(forward_messages.run(args))
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["message_ids"], [7])
        self.assertEqual(fake.forward_calls, 0)


class GroupMemberTests(unittest.TestCase):
    def test_restriction_requires_a_finite_duration(self) -> None:
        args = group_member.parser().parse_args(
            ["--group", "@group", "--user", "@user", "--action", "restrict", "--deny", "send_messages"]
        )
        with self.assertRaisesRegex(ValueError, "until-hours"):
            group_member.validate_args(args)

    def test_admin_kwargs_are_exact_allowlist(self) -> None:
        rights = group_member.admin_kwargs(("delete_messages", "ban_users"))
        self.assertTrue(rights["delete_messages"])
        self.assertTrue(rights["ban_users"])
        self.assertFalse(rights["add_admins"])

    def test_rank_cannot_be_silently_ignored(self) -> None:
        args = group_member.parser().parse_args(
            ["--group", "@group", "--user", "@user", "--action", "kick", "--rank", "moderator"]
        )
        with self.assertRaisesRegex(ValueError, "rank"):
            group_member.validate_args(args)


if __name__ == "__main__":
    unittest.main()
