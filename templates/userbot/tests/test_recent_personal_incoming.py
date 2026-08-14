from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import recent_personal_incoming


class RecentPersonalIncomingTests(unittest.TestCase):
    def test_filters_to_non_bot_non_self_direct_dialogs(self) -> None:
        direct_user = SimpleNamespace(bot=False, is_self=False)
        self.assertTrue(
            recent_personal_incoming.is_personal_dialog(
                SimpleNamespace(is_user=True, entity=direct_user)
            )
        )
        self.assertFalse(
            recent_personal_incoming.is_personal_dialog(
                SimpleNamespace(is_user=True, entity=SimpleNamespace(bot=True, is_self=False))
            )
        )
        self.assertFalse(
            recent_personal_incoming.is_personal_dialog(
                SimpleNamespace(
                    is_user=True,
                    entity=SimpleNamespace(
                        id=recent_personal_incoming.TELEGRAM_SERVICE_USER_ID,
                        bot=False,
                        is_self=False,
                    ),
                )
            )
        )
        self.assertFalse(
            recent_personal_incoming.is_personal_dialog(
                SimpleNamespace(is_user=False, entity=direct_user)
            )
        )

    def test_collects_latest_incoming_per_dialog_and_orders_globally(self) -> None:
        now = datetime(2026, 8, 15, 9, 0, tzinfo=timezone.utc)
        alice = SimpleNamespace(first_name="Alice", last_name=None, username="alice", bot=False, is_self=False)
        bob = SimpleNamespace(first_name="Bob", last_name=None, username=None, bot=False, is_self=False)
        carol = SimpleNamespace(first_name="Carol", last_name=None, username="carol", bot=False, is_self=False)
        dialogs = [
            SimpleNamespace(is_user=True, entity=alice),
            SimpleNamespace(is_user=True, entity=bob),
            SimpleNamespace(is_user=True, entity=carol),
            SimpleNamespace(is_user=False, entity=SimpleNamespace(bot=False, is_self=False)),
        ]
        messages = {
            id(alice): [SimpleNamespace(out=True, date=now), SimpleNamespace(out=False, date=now - timedelta(minutes=4))],
            id(bob): [SimpleNamespace(out=False, date=now - timedelta(minutes=1))],
            id(carol): [SimpleNamespace(out=False, date=now - timedelta(minutes=2))],
        }

        class FakeClient:
            async def iter_dialogs(self, *, limit):
                for dialog in dialogs[:limit]:
                    yield dialog

            async def iter_messages(self, entity, *, limit):
                for message in messages[id(entity)][:limit]:
                    yield message

        records, scanned = asyncio.run(
            recent_personal_incoming.collect_recent_incoming(
                FakeClient(),
                limit=3,
                dialogs_limit=10,
                messages_per_dialog=10,
            )
        )

        self.assertEqual([record.display_name for record in records], ["Bob", "Carol", "Alice"])
        self.assertEqual(scanned, 3)

    def test_bounds_are_validated(self) -> None:
        args = recent_personal_incoming.parser().parse_args(["--limit", "0"])
        with self.assertRaisesRegex(ValueError, "--limit"):
            recent_personal_incoming.validate_args(args)

        args = recent_personal_incoming.parser().parse_args(["--dialogs-limit", "501"])
        with self.assertRaisesRegex(ValueError, "--dialogs-limit"):
            recent_personal_incoming.validate_args(args)


if __name__ == "__main__":
    unittest.main()
