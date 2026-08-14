from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.check_module import analyze_source


class ModuleQualityTests(unittest.TestCase):
    def test_rejects_interactive_client_start(self) -> None:
        source = """
def register(client):
    return None

async def run(client):
    await client.start()
"""
        self.assertTrue(any("client.start" in error for error in analyze_source(source)))

    def test_direct_helper_requires_central_lifecycle(self) -> None:
        source = """
from telethon import TelegramClient

def register(client):
    return None
"""
        errors = analyze_source(source)
        self.assertTrue(any("load_settings" in error for error in errors))

    def test_guarded_direct_helper_passes_static_contract(self) -> None:
        source = '''
from telethon import TelegramClient
from core.config import apply_runtime_env, load_settings

def register(client):
    return None

async def run(client):
    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError("not authorized")
    await client.disconnect()

def parser():
    parser.add_argument("--execute", action="store_true")
    dry_run = True
'''
        self.assertEqual(analyze_source(source), [])


if __name__ == "__main__":
    unittest.main()
