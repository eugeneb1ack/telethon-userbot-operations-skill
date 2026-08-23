from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from telethon.tl.types import Channel, MessageReplyHeader, PeerChannel

from modules.comment_channels import (
    DiscussionCandidate,
    add_channel_hit,
    is_broadcast_channel,
    is_comment_message,
    remember_candidate,
    serialize_channel,
)


def channel(identifier: int, title: str, *, broadcast: bool, megagroup: bool) -> Channel:
    return Channel(
        id=identifier,
        title=title,
        photo=None,
        date=None,
        broadcast=broadcast,
        megagroup=megagroup,
        access_hash=identifier * 10,
        username=None,
    )


class CommentChannelsTests(unittest.TestCase):
    def test_only_reply_headers_are_comments(self) -> None:
        self.assertTrue(is_comment_message(SimpleNamespace(reply_to=MessageReplyHeader(reply_to_msg_id=10))))
        self.assertFalse(is_comment_message(SimpleNamespace(reply_to=None)))

    def test_broadcast_channel_predicate_excludes_megagroup(self) -> None:
        self.assertTrue(is_broadcast_channel(channel(1, "News", broadcast=True, megagroup=False)))
        self.assertFalse(is_broadcast_channel(channel(2, "Discussion", broadcast=False, megagroup=True)))

    def test_candidate_tracks_reply_channel_and_dates(self) -> None:
        group = channel(100, "Discussion", broadcast=False, megagroup=True)
        message = SimpleNamespace(
            date=datetime(2026, 8, 15, 10, tzinfo=timezone.utc),
            reply_to=MessageReplyHeader(reply_to_msg_id=10, reply_to_peer_id=PeerChannel(200)),
            reply_to_chat=None,
        )
        candidates: dict[int, DiscussionCandidate] = {}
        candidate = remember_candidate(candidates, message, group)
        self.assertEqual(candidate.comments_count, 1)
        self.assertEqual(candidate.reply_channel_ids, {200})

    def test_channel_hits_are_serialized_without_text(self) -> None:
        group = channel(100, "Discussion", broadcast=False, megagroup=True)
        broadcast = channel(200, "News", broadcast=True, megagroup=False)
        candidate = DiscussionCandidate(
            entity=group,
            comments_count=2,
            first_comment=datetime(2026, 8, 1, tzinfo=timezone.utc),
            last_comment=datetime(2026, 8, 2, tzinfo=timezone.utc),
        )
        channels: dict[int, dict[str, object]] = {}
        add_channel_hit(channels, broadcast, candidate)
        payload = serialize_channel(channels[200])
        self.assertEqual(payload["comment_count"], 2)
        self.assertNotIn("text", payload)


if __name__ == "__main__":
    unittest.main()
