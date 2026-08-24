from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts import userbot_memory


class UserbotMemoryCliTests(unittest.TestCase):
    def _args(self, database: Path, *values: str):
        return userbot_memory.parser().parse_args(
            ["--account", "main", "--memory-db", str(database), *values]
        )

    def test_shorthand_remember_recall_and_forget_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "memory.sqlite3"
            remembered = userbot_memory.run(
                self._args(
                    database,
                    "remember",
                    "--kind",
                    "procedure",
                    "--scope",
                    "operation:deploy",
                    "--subject",
                    "Проверка деплоя",
                    "--summary",
                    "После деплоя проверить health endpoint.",
                    "--source",
                    "verified repository procedure",
                    "--tags",
                    "деплой,health",
                )
            )
            recalled = userbot_memory.run(
                self._args(
                    database,
                    "recall",
                    "--query",
                    "деплой health",
                    "--scope",
                    "operation:deploy",
                )
            )
            identifier = remembered["item"]["id"]
            preview = userbot_memory.run(
                self._args(database, "forget", "--id", identifier)
            )
            deleted = userbot_memory.run(
                self._args(database, "forget", "--id", identifier, "--execute")
            )

        self.assertEqual(remembered["status"], "created")
        self.assertEqual(recalled["cache_status"], "hit")
        self.assertEqual(recalled["count"], 1)
        self.assertEqual(preview["status"], "preview")
        self.assertEqual(deleted["status"], "deleted")

    def test_shorthand_requires_provenance_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = self._args(
                Path(tmp) / "memory.sqlite3",
                "remember",
                "--kind",
                "fact",
                "--scope",
                "global",
                "--subject",
                "Тема",
                "--summary",
                "Факт",
            )
            with self.assertRaisesRegex(ValueError, "missing source"):
                userbot_memory.run(args)


if __name__ == "__main__":
    unittest.main()
