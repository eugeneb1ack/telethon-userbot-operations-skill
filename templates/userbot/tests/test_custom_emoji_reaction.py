from __future__ import annotations

import unittest
from types import SimpleNamespace

from modules.react_custom_emoji_user_messages import has_my_custom_reaction, normalize_name


class CustomEmojiReactionTests(unittest.TestCase):
    def test_name_matching_collapses_case_and_whitespace(self) -> None:
        self.assertEqual(normalize_name("  Авель   Адамович "), "авель адамович")

    def test_custom_reaction_matches_only_my_document(self) -> None:
        message = SimpleNamespace(
            reactions=SimpleNamespace(
                recent_reactions=[
                    SimpleNamespace(
                        peer_id=SimpleNamespace(user_id=42),
                        reaction=SimpleNamespace(document_id=123),
                    )
                ]
            )
        )
        self.assertTrue(has_my_custom_reaction(message, 42, 123))
        self.assertFalse(has_my_custom_reaction(message, 42, 999))
        self.assertFalse(has_my_custom_reaction(message, 77, 123))


if __name__ == "__main__":
    unittest.main()
