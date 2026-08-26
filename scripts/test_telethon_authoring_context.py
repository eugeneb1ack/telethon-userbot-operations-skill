#!/usr/bin/env python3
"""Offline regression tests for the Telethon module-authoring preflight."""

from __future__ import annotations

import tempfile
import unittest
from importlib.metadata import version
from pathlib import Path

from telethon_authoring_context import build_context


class TelethonAuthoringContextTests(unittest.TestCase):
    def test_packet_combines_registry_version_pin_and_exact_api(self) -> None:
        with tempfile.TemporaryDirectory(prefix="telethon_authoring_context_") as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            (root / "scripts" / "userbot_module_registry.py").write_text(
                "import json\n"
                "print(json.dumps({'ok': False, 'status': 'no_match', "
                "'operations': [], 'candidates': []}))\n",
                encoding="utf-8",
            )
            (root / "requirements.txt").write_text(
                f"telethon=={version('telethon')}\n", encoding="utf-8"
            )
            context = build_context(
                project_root=root,
                query="новая операция",
                client_methods=["iter_messages"],
                raw_requests=["messages.TranscribeAudioRequest"],
            )

        self.assertEqual(context["decision"], "author_one_focused_operation")
        self.assertTrue(context["telethon"]["version_match"])
        self.assertEqual(
            context["api"]["client_methods"][0]["qualified_name"],
            "TelegramClient.iter_messages",
        )
        self.assertEqual(
            context["api"]["raw_requests"][0]["qualified_name"],
            "functions.messages.TranscribeAudioRequest",
        )
        self.assertEqual(context["api"]["missing"], [])
        self.assertFalse(context["network_io"])
        self.assertFalse(context["telegram_session_access"])

    def test_existing_route_stops_authoring(self) -> None:
        with tempfile.TemporaryDirectory(prefix="telethon_authoring_context_") as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            (root / "scripts" / "userbot_module_registry.py").write_text(
                "import json\n"
                "print(json.dumps({'ok': True, 'status': 'match', "
                "'operations': [{'slug': 'existing'}], 'candidates': []}))\n",
                encoding="utf-8",
            )
            context = build_context(
                project_root=root,
                query="готовая операция",
                client_methods=[],
                raw_requests=[],
            )
        self.assertEqual(context["decision"], "use_existing_operation")

    def test_missing_runtime_pin_blocks_new_module_authoring(self) -> None:
        with tempfile.TemporaryDirectory(prefix="telethon_authoring_context_") as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            (root / "scripts" / "userbot_module_registry.py").write_text(
                "import json\n"
                "print(json.dumps({'ok': False, 'status': 'no_match', "
                "'operations': [], 'candidates': []}))\n",
                encoding="utf-8",
            )
            context = build_context(
                project_root=root,
                query="новая операция",
                client_methods=[],
                raw_requests=[],
            )
        self.assertEqual(
            context["decision"], "resolve_version_alignment_before_coding"
        )
        self.assertEqual(context["telethon"]["pin_status"], "missing")
        self.assertIsNone(context["telethon"]["version_match"])

    def test_unknown_api_surface_blocks_authoring(self) -> None:
        with tempfile.TemporaryDirectory(prefix="telethon_authoring_context_") as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            (root / "scripts" / "userbot_module_registry.py").write_text(
                "import json\n"
                "print(json.dumps({'ok': False, 'status': 'no_match', "
                "'operations': [], 'candidates': []}))\n",
                encoding="utf-8",
            )
            (root / "requirements.txt").write_text(
                f"telethon=={version('telethon')}\n", encoding="utf-8"
            )
            context = build_context(
                project_root=root,
                query="новая операция",
                client_methods=["invented_method"],
                raw_requests=[],
            )
        self.assertEqual(
            context["decision"], "resolve_api_surface_before_coding"
        )
        self.assertEqual(context["api"]["missing"], ["invented_method"])


if __name__ == "__main__":
    unittest.main()
