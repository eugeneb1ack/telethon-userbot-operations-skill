from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "scripts" / "userbot_module_registry.py"


class UserbotModuleRegistryTests(unittest.TestCase):
    def query(self, text: str) -> dict:
        completed = subprocess.run(
            [sys.executable, str(REGISTRY), "--query", text, "--json"],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout)

    def test_member_request_routes_to_read_only_participant_module(self) -> None:
        result = self.query("дай список участников чата цыгане")
        self.assertTrue(result["ok"])
        operation = result["operations"][0]
        self.assertEqual(operation["slug"], "list_members")
        self.assertEqual(operation["mode"], "read_only")
        self.assertEqual(operation["module"], "list_group_members.py")
        self.assertIn("scripts/userbotrun.py", operation["command"])

    def test_custom_emoji_status_routes_to_profile_module(self) -> None:
        result = self.query("поставь кастомный эмодзи в статус профиля")
        self.assertTrue(result["ok"])
        self.assertEqual(result["operations"][0]["slug"], "profile")
        self.assertEqual(result["operations"][0]["module"], "profile_settings.py")

    def test_comment_channel_request_routes_to_comment_module(self) -> None:
        result = self.query("перечисли каналы где я писал комментарии")
        self.assertTrue(result["ok"])
        operation = result["operations"][0]
        self.assertEqual(operation["slug"], "comment_channels")
        self.assertEqual(operation["module"], "comment_channels.py")
        self.assertEqual(operation["mode"], "read_only")

    def test_unknown_operation_returns_nonzero_json_error(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(REGISTRY), "--operation", "not_real", "--json"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(json.loads(completed.stdout)["error"], "unknown_operation")


if __name__ == "__main__":
    unittest.main()
