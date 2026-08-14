from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace

from modules.count_user_messages import resolve_chat


class FakeClient:
    def __init__(self, dialogs):
        self.dialogs = dialogs

    async def iter_dialogs(self):
        for dialog in self.dialogs:
            yield dialog


def group_dialog(identifier: int, name: str):
    entity = SimpleNamespace(id=identifier, title=name, username=None)
    return SimpleNamespace(id=identifier, name=name, entity=entity, is_group=True)


class CountUserMessagesResolutionTests(unittest.TestCase):
    def test_ambiguous_group_name_requires_explicit_target(self) -> None:
        client = FakeClient(
            [
                group_dialog(101, "Team chat"),
                group_dialog(202, "Team chat"),
            ]
        )

        with self.assertRaises(RuntimeError) as caught:
            asyncio.run(resolve_chat(client, "team chat"))

        payload = json.loads(str(caught.exception))
        self.assertEqual(payload["error"], "ambiguous_chat")
        self.assertEqual(len(payload["matches"]), 2)


if __name__ == "__main__":
    unittest.main()
