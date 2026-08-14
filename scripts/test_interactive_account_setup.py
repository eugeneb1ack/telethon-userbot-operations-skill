#!/usr/bin/env python3
"""Offline regression test for the generated interactive account setup script."""

from __future__ import annotations

import importlib.util
import stat
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "templates" / "userbot" / "scripts" / "setup_account.py"
SPEC = importlib.util.spec_from_file_location("setup_account", SCRIPT)
assert SPEC and SPEC.loader
setup_account = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = setup_account
SPEC.loader.exec_module(setup_account)


class InteractiveAccountSetupTests(unittest.TestCase):
    def test_collects_valid_values_after_invalid_attempts_without_echoing_secrets(self) -> None:
        visible = iter(["not-a-number", "12345", ""])
        hidden = iter(["invalid", "a" * 32, "79991234567", "+79991234567"])
        feedback: list[str] = []

        values = setup_account.collect_account_values(
            "main",
            input_func=lambda _prompt: next(visible),
            secret_input=lambda _prompt: next(hidden),
            output=feedback.append,
        )

        self.assertEqual(values.api_id, 12345)
        self.assertEqual(values.session_name, "main")
        self.assertEqual(values.phone_number, "+79991234567")
        self.assertEqual(values.api_hash, "a" * 32)
        self.assertEqual(len(feedback), 3)
        self.assertTrue(all("a" * 32 not in message for message in feedback))

    def test_writes_mode_600_and_refuses_accidental_overwrite(self) -> None:
        values = setup_account.AccountValues(12345, "b" * 32, "+79991234567", "main")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "accounts" / "main.env"
            setup_account.write_account_file(target, values, replace=False)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            self.assertIn("API_ID=12345", target.read_text(encoding="utf-8"))
            with self.assertRaises(FileExistsError):
                setup_account.write_account_file(target, values, replace=False)
            setup_account.write_account_file(target, values, replace=True)


if __name__ == "__main__":
    unittest.main()
