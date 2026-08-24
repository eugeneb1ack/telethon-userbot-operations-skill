from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from telethon.tl.types import Channel

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import owned_channels


def channel(identifier: int, title: str, *, creator: bool, broadcast: bool, megagroup: bool) -> Channel:
    return Channel(
        id=identifier,
        title=title,
        photo=None,
        date=None,
        creator=creator,
        broadcast=broadcast,
        megagroup=megagroup,
        access_hash=1,
    )


class OwnedChannelsTests(unittest.TestCase):
    def test_only_self_created_broadcast_channels_are_selected(self) -> None:
        owned = channel(1, "Owned", creator=True, broadcast=True, megagroup=False)
        admin = channel(2, "Admin", creator=False, broadcast=True, megagroup=False)
        group = channel(3, "Group", creator=True, broadcast=False, megagroup=True)

        self.assertTrue(owned_channels.is_owned_broadcast_channel(SimpleNamespace(is_channel=True, entity=owned)))
        self.assertFalse(owned_channels.is_owned_broadcast_channel(SimpleNamespace(is_channel=True, entity=admin)))
        self.assertFalse(owned_channels.is_owned_broadcast_channel(SimpleNamespace(is_channel=True, entity=group)))
        self.assertFalse(owned_channels.is_owned_broadcast_channel(SimpleNamespace(is_channel=False, entity=owned)))

    def test_collection_sorts_and_preserves_public_username_only(self) -> None:
        alpha = channel(1, "Альфа", creator=True, broadcast=True, megagroup=False)
        beta = channel(2, "Бета", creator=True, broadcast=True, megagroup=False)
        alpha.username = "alpha"
        beta.username = None
        dialogs = [
            SimpleNamespace(is_channel=True, entity=beta),
            SimpleNamespace(is_channel=True, entity=alpha),
        ]

        class FakeClient:
            async def iter_dialogs(self):
                for dialog in dialogs:
                    yield dialog

        channels = asyncio.run(owned_channels.collect_owned_channels(FakeClient()))
        payload = owned_channels.result_payload(channels)

        self.assertEqual([item.title for item in channels], ["Альфа", "Бета"])
        self.assertEqual(payload["owned_channel_count"], 2)
        self.assertEqual(payload["channels"][0]["username"], "@alpha")
        self.assertIsNone(payload["channels"][1]["username"])


if __name__ == "__main__":
    unittest.main()
