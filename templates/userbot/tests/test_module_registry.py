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

    def test_owned_channel_request_does_not_route_to_comments(self) -> None:
        result = self.query("перечисли каналы где я владелец")
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "match")
        self.assertEqual(result["operations"][0]["slug"], "owned_channels")

    def test_forum_topic_request_routes_to_topic_module(self) -> None:
        result = self.query("найди ветку в форуме")
        self.assertTrue(result["ok"])
        self.assertEqual(result["operations"][0]["slug"], "list_forum_topics")

    def test_blocked_user_request_routes_to_blocked_list(self) -> None:
        result = self.query("кто у меня в черном списке")
        self.assertTrue(result["ok"])
        self.assertEqual(result["operations"][0]["slug"], "list_blocked_users")

    def test_recall_memory_request_routes_to_local_cli(self) -> None:
        result = self.query("что мы уже знаем про процедуру деплоя")
        self.assertTrue(result["ok"])
        operation = result["operations"][0]
        self.assertEqual(operation["slug"], "recall_memory")
        self.assertEqual(operation["mode"], "local_read")
        self.assertEqual(operation["module"], "scripts/userbot_memory.py")

    def test_remember_request_routes_to_local_cli(self) -> None:
        result = self.query("сохрани в память проверенную процедуру")
        self.assertTrue(result["ok"])
        operation = result["operations"][0]
        self.assertEqual(operation["slug"], "remember_memory")
        self.assertEqual(operation["mode"], "local_write")

    def test_unknown_operation_returns_nonzero_json_error(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(REGISTRY), "--operation", "not_real", "--json"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(json.loads(completed.stdout)["error"], "unknown_operation")

    def test_low_confidence_query_returns_no_match(self) -> None:
        result = self.query("что-нибудь сделай")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "no_match")
        self.assertEqual(result["operations"], [])

    def test_ambiguous_query_does_not_select_an_operation(self) -> None:
        result = self.query("покажи каналы где я владелец и писал комментарии")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(result["operations"], [])
        self.assertGreaterEqual(len(result["candidates"]), 2)

    def test_catalog_is_valid_and_direct_commands_use_runner(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(REGISTRY), "--validate-catalog", "--json"],
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout)
        self.assertTrue(result["ok"], result["errors"])

        listed = subprocess.run(
            [sys.executable, str(REGISTRY), "--list", "--json"],
            check=True,
            capture_output=True,
            text=True,
        )
        for operation in json.loads(listed.stdout)["operations"]:
            if "modules/" in operation["command"]:
                self.assertIn("scripts/userbotrun.py", operation["command"])


if __name__ == "__main__":
    unittest.main()
