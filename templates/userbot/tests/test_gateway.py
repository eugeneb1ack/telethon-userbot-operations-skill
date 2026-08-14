from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.event_store import EventStore, event_id
from core import gateway as gateway_module
from core.gateway import GatewayOptions, UserbotGateway, validate_webhook_url, webhook_signature
from scripts.userbotctl import rpc_for, socket_path
from scripts.install_gateway_service import plist_payload, service_label
from scripts.setup_gateway import parse_env, update_env
from scripts.userbotd import process_command, safe_account as safe_daemon_account


class EventStoreTests(unittest.TestCase):
    def test_event_id_and_insert_are_deterministic_and_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.sqlite3")
            payload = {
                "account": "main",
                "kind": "mention",
                "chat_id": -10042,
                "message_id": 7,
                "sender_id": 9,
                "chat_title": "Team",
                "sender_name": "Alice",
                "preview": "hello",
                "occurred_at": "2026-08-15T10:00:00+00:00",
            }
            first_inserted, first = store.add_event(payload)
            second_inserted, second = store.add_event(payload)
            self.assertTrue(first_inserted)
            self.assertFalse(second_inserted)
            self.assertEqual(first["id"], second["id"])
            self.assertEqual(first["id"], event_id("main", -10042, 7, "mention"))
            self.assertEqual(len(store.list_events(limit=20)), 1)
            self.assertEqual(store.acknowledge(first["id"])["status"], "acknowledged")
            store.close()


class WebhookContractTests(unittest.TestCase):
    def test_signature_covers_timestamp_and_exact_body(self) -> None:
        secret = "s" * 32
        timestamp = "1786788000"
        body = b'{"version":1}'
        expected = "sha256=" + hmac.new(
            secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
        ).hexdigest()
        self.assertEqual(webhook_signature(secret, timestamp, body), expected)

    def test_webhook_requires_https_except_for_loopback(self) -> None:
        self.assertEqual(validate_webhook_url("https://example.com/hook"), "https://example.com/hook")
        self.assertEqual(validate_webhook_url("http://127.0.0.1:8080/hook"), "http://127.0.0.1:8080/hook")
        with self.assertRaises(ValueError):
            validate_webhook_url("http://example.com/hook")

    def test_worker_delivers_signed_json(self) -> None:
        async def scenario() -> None:
            received: dict[str, object] = {}
            delivered = asyncio.Event()

            class FakeResponse:
                status = 204

                async def __aenter__(self):
                    delivered.set()
                    return self

                async def __aexit__(self, *_args):
                    return None

            class FakeSession:
                def __init__(self, **_kwargs):
                    pass

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *_args):
                    return None

                def post(self, url, *, data, headers):
                    received.update(url=url, body=data, headers={k.casefold(): v for k, v in headers.items()})
                    return FakeResponse()

            with tempfile.TemporaryDirectory() as tmp:
                secret = "s" * 32
                options = GatewayOptions(
                    enabled=True,
                    socket_path=Path(tmp) / "userbot.sock",
                    database_path=Path(tmp) / "events.sqlite3",
                    preview_chars=0,
                    webhook_url="https://example.com/events",
                    webhook_secret=secret,
                )
                gateway = UserbotGateway(SimpleNamespace(), SimpleNamespace(account="main"), options)
                gateway.store.add_event(
                    {
                        "account": "main",
                        "kind": "mention",
                        "chat_id": -10042,
                        "message_id": 7,
                        "sender_id": 9,
                        "chat_title": "Team",
                        "sender_name": "Alice",
                        "preview": "",
                        "occurred_at": "2026-08-15T10:00:00+00:00",
                        "webhook_status": "pending",
                    }
                )
                with patch.object(gateway_module.aiohttp, "ClientSession", FakeSession):
                    task = asyncio.create_task(gateway._webhook_worker())
                    await asyncio.wait_for(delivered.wait(), timeout=3)
                    gateway._stopping = True
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                headers = received["headers"]
                body = received["body"]
                timestamp = headers["x-userbot-timestamp"]
                self.assertEqual(
                    headers["x-userbot-signature"],
                    webhook_signature(secret, timestamp, body),
                )
                self.assertEqual(json.loads(body)["type"], "telegram.event")
                gateway.store.close()

        asyncio.run(scenario())


class GatewayEventTests(unittest.TestCase):
    def test_private_event_is_stored_as_direct_message_with_bounded_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = SimpleNamespace(account="main", runtime_dir=tmp)
            options = GatewayOptions(
                enabled=True,
                socket_path=Path(tmp) / "userbot.sock",
                database_path=Path(tmp) / "events.sqlite3",
                preview_chars=5,
                webhook_url=None,
                webhook_secret=None,
            )
            gateway = UserbotGateway(SimpleNamespace(), settings, options)
            sender = SimpleNamespace(
                id=9, first_name="Alice", last_name=None, username="alice", bot=False, is_self=False
            )
            chat = SimpleNamespace(first_name="Alice", last_name=None, username="alice")

            class FakeEvent:
                is_private = True
                is_reply = False
                chat_id = 9
                sender_id = 9
                id = 7
                raw_text = "hello world"
                message = SimpleNamespace(
                    mentioned=False,
                    media=None,
                    date=datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc),
                )

                async def get_sender(self):
                    return sender

                async def get_chat(self):
                    return chat

            payload = asyncio.run(gateway._event_payload(FakeEvent()))
            self.assertEqual(payload["kind"], "direct_message")
            self.assertEqual(payload["preview"], "hello")
            gateway.store.close()


class UserbotCtlTests(unittest.TestCase):
    def test_default_socket_is_account_isolated(self) -> None:
        self.assertEqual(
            socket_path(Path("/project"), "main"),
            Path("/project/runtime/main/userbot.sock"),
        )

    def test_event_list_maps_to_read_only_rpc(self) -> None:
        args = SimpleNamespace(command="events", event_command="list", limit=3, unread=True)
        self.assertEqual(
            rpc_for(args),
            ("events.list", {"limit": 3, "unread_only": True}),
        )

    def test_on_demand_command_is_non_interactive_and_has_no_autostart(self) -> None:
        command = process_command(Path("/project"), "main")
        self.assertEqual(command, ["/project/run.sh", "--account", "main", "--non-interactive"])
        self.assertEqual(safe_daemon_account("second_2"), "second_2")
        with self.assertRaises(ValueError):
            safe_daemon_account("../main")


class LaunchdInstallerTests(unittest.TestCase):
    def test_plist_keeps_secrets_out_and_restarts_failed_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            payload = plist_payload(root, "main")
            self.assertEqual(payload["Label"], service_label("main"))
            self.assertTrue(payload["KeepAlive"])
            serialized = str(payload).casefold()
            self.assertNotIn("api_hash", serialized)
            self.assertNotIn("webhook_secret", serialized)


class GatewaySetupTests(unittest.TestCase):
    def test_env_update_preserves_unmanaged_values(self) -> None:
        original = "OPEN_ROUTER_KEY=keep\nUSERBOT_GATEWAY_ENABLED=false\n"
        updated = update_env(
            original,
            {
                "USERBOT_GATEWAY_ENABLED": "true",
                "USERBOT_WEBHOOK_URL": "https://example.com/hook",
            },
        )
        values = parse_env(updated)
        self.assertEqual(values["OPEN_ROUTER_KEY"], "keep")
        self.assertEqual(values["USERBOT_GATEWAY_ENABLED"], "true")
        self.assertEqual(values["USERBOT_WEBHOOK_URL"], "https://example.com/hook")


if __name__ == "__main__":
    unittest.main()
