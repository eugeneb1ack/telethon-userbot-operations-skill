from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import list_forum_topics


class ListForumTopicsTests(unittest.TestCase):
    def test_bounds_and_empty_query_are_rejected(self) -> None:
        for value in ("0", str(list_forum_topics.MAX_LIMIT + 1)):
            args = list_forum_topics.parser().parse_args(["--chat", "1", "--limit", value])
            with self.assertRaisesRegex(ValueError, "--limit"):
                list_forum_topics.validate_args(args)

        args = list_forum_topics.parser().parse_args(["--chat", "1", "--query", "  "])
        with self.assertRaisesRegex(ValueError, "--query"):
            list_forum_topics.validate_args(args)

    def test_collection_uses_topic_search_and_returns_safe_fields(self) -> None:
        topic = SimpleNamespace(
            id=42,
            title="Генерал",
            date=None,
            top_message=900,
            unread_count=3,
            unread_mentions_count=1,
            closed=False,
            pinned=True,
            hidden=False,
            icon_emoji_id=None,
        )

        class FakeClient:
            def __init__(self) -> None:
                self.request = None

            async def __call__(self, request):
                self.request = request
                return SimpleNamespace(count=1, topics=[topic])

        client = FakeClient()
        topics, reported_count = asyncio.run(
            list_forum_topics.collect_topics(client, "chat", query=" Генерал ", limit=10)
        )

        self.assertEqual(client.request.q, "Генерал")
        self.assertEqual(client.request.offset_id, 0)
        self.assertEqual(client.request.offset_topic, 0)
        self.assertEqual(client.request.limit, 10)
        self.assertEqual(reported_count, 1)
        self.assertEqual(topics[0]["id"], 42)
        self.assertEqual(topics[0]["title"], "Генерал")
        self.assertEqual(topics[0]["top_message_id"], 900)
        self.assertTrue(topics[0]["pinned"])


if __name__ == "__main__":
    unittest.main()
