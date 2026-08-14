from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from modules.create_emoji_pack import render_emoji, validate_inputs, validate_short_name


class EmojiPackValidationTests(unittest.TestCase):
    def test_rejects_empty_or_multiline_text(self) -> None:
        with self.assertRaises(ValueError):
            validate_inputs("текст пак", "", "💪")
        with self.assertRaises(ValueError):
            validate_inputs("текст пак", "ЖИРНЫЙ\nещё", "💪")

    def test_rejects_unsafe_short_name(self) -> None:
        with self.assertRaises(ValueError):
            validate_short_name("текст-пак")
        with self.assertRaises(ValueError):
            validate_short_name("x")

    def test_render_is_100_square_rgba_and_nonempty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "emoji.png"
            info = render_emoji("ЖИРНЫЙ", path)
            self.assertEqual(info["text"], "ЖИРНЫЙ")
            with Image.open(path) as image:
                self.assertEqual(image.size, (100, 100))
                self.assertEqual(image.mode, "RGBA")
                self.assertIsNotNone(image.getchannel("A").getbbox())


if __name__ == "__main__":
    unittest.main()
