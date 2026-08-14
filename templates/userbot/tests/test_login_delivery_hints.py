from __future__ import annotations

import sys
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main import describe_code_delivery, restrict_session_permissions


class LoginDeliveryHintTests(unittest.TestCase):
    def test_app_delivery_explains_where_to_look(self) -> None:
        code_type = type("SentCodeTypeApp", (), {"length": 5})()

        hint = describe_code_delivery(SimpleNamespace(type=code_type))

        self.assertIn("чат «Telegram»", hint)
        self.assertIn("5-значный", hint)

    def test_sms_delivery_is_distinguished(self) -> None:
        code_type = type("SentCodeTypeSms", (), {"length": 6})()

        hint = describe_code_delivery(SimpleNamespace(type=code_type))

        self.assertIn("SMS", hint)
        self.assertIn("6-значный", hint)

    def test_restricts_existing_session_file_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_path = Path(tmp) / "main.session"
            session_path.write_bytes(b"not inspected by this test")
            session_path.chmod(0o644)

            restrict_session_permissions(
                SimpleNamespace(session=SimpleNamespace(filename=str(session_path)))
            )

            self.assertEqual(stat.S_IMODE(session_path.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
