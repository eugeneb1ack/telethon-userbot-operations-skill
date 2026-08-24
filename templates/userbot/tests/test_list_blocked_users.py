from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from telethon.tl.types import PeerBlocked, PeerChannel, PeerUser

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import list_blocked_users


class ListBlockedUsersTests(unittest.TestCase):
    def test_bounds_are_validated(self) -> None:
        for value in ("0", str(list_blocked_users.MAX_LIMIT + 1)):
            args = list_blocked_users.parser().parse_args(["--limit", value])
            with self.assertRaisesRegex(ValueError, "--limit"):
                list_blocked_users.validate_args(args)

    def test_collection_paginates_filters_non_users_and_preserves_safe_fields(self) -> None:
        responses = {
            0: SimpleNamespace(
                count=3,
                blocked=[PeerBlocked(PeerUser(1), None), PeerBlocked(PeerChannel(2), None)],
                users=[
                    SimpleNamespace(id=1, first_name="Ада", last_name="Лавлейс", username="ada"),
                ],
            ),
            2: SimpleNamespace(
                count=3,
                blocked=[PeerBlocked(PeerUser(3), None)],
                users=[SimpleNamespace(id=3, first_name=None, last_name=None, username=None)],
            ),
        }

        class FakeClient:
            def __init__(self) -> None:
                self.calls: list[tuple[int, int]] = []

            async def __call__(self, request):
                self.calls.append((request.offset, request.limit))
                return responses[request.offset]

        client = FakeClient()
        users, scanned, total, truncated = asyncio.run(
            list_blocked_users.collect_blocked_users(client, limit=3)
        )

        self.assertEqual(client.calls, [(0, 3), (2, 1)])
        self.assertEqual(scanned, 3)
        self.assertEqual(total, 3)
        self.assertFalse(truncated)
        self.assertEqual(
            users,
            [
                list_blocked_users.BlockedUser(1, "Ада Лавлейс", "@ada"),
                list_blocked_users.BlockedUser(3, None, None),
            ],
        )

    def test_payload_marks_a_limit_truncated_result(self) -> None:
        users = [list_blocked_users.BlockedUser(10, "Имя", "@name")]
        payload = list_blocked_users.result_payload(
            users, scanned=1, reported_total=2, may_be_truncated=True
        )

        self.assertTrue(payload["read_only"])
        self.assertTrue(payload["may_be_truncated"])
        self.assertEqual(
            payload["blocked_users"],
            [{"id": 10, "name": "Имя", "username": "@name"}],
        )


if __name__ == "__main__":
    unittest.main()
