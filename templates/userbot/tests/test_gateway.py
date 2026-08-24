from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import event_store as event_store_module
from core.event_store import EventStore, event_id
from core import gateway as gateway_module
from core.gateway import GatewayOptions, UserbotGateway, validate_webhook_url, webhook_signature
from core.runtime_lock import AccountRuntimeLock
from scripts.userbotctl import rpc_for, socket_path
from scripts.install_gateway_service import plist_payload, service_label
from scripts.setup_gateway import parse_env, update_env
from scripts.userbotd import process_command, safe_account as safe_daemon_account
from scripts.userbotrun import bounded_timeout, module_command, resolve_module, stop_process_group


class EventStoreTests(unittest.TestCase):
    @staticmethod
    def payload(message_id: int, *, webhook_status: str = "disabled") -> dict[str, object]:
        return {
            "account": "main",
            "kind": "mention",
            "chat_id": -10042,
            "message_id": message_id,
            "sender_id": 9,
            "chat_title": "Team",
            "sender_name": "Alice",
            "preview": "hello",
            "occurred_at": f"2026-08-15T10:00:{message_id:02d}+00:00",
            "webhook_status": webhook_status,
        }

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

    def test_store_has_hard_event_cap_and_keeps_newest_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            event_store_module, "MAX_EVENTS", 3
        ):
            store = EventStore(Path(tmp) / "events.sqlite3")
            for message_id in range(1, 6):
                store.add_event(self.payload(message_id))
            records = store.list_events(limit=20)
            self.assertEqual([record["message_id"] for record in records], [5, 4, 3])
            store.close()

    def test_acknowledged_history_has_a_separate_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            event_store_module, "MAX_ACKNOWLEDGED_EVENTS", 1
        ):
            store = EventStore(Path(tmp) / "events.sqlite3")
            identifiers = []
            for message_id in range(1, 4):
                _, record = store.add_event(self.payload(message_id))
                identifiers.append(record["id"])
            store.acknowledge(identifiers[0])
            store.acknowledge(identifiers[1])
            records = store.list_events(limit=20)
            self.assertEqual([record["message_id"] for record in records], [3, 2])
            store.close()

    def test_database_and_wal_sidecars_are_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "events.sqlite3"
            store = EventStore(database)
            store.add_event(self.payload(1))
            for candidate in (database, Path(f"{database}-wal"), Path(f"{database}-shm")):
                if candidate.exists():
                    self.assertEqual(stat.S_IMODE(candidate.stat().st_mode), 0o600)
            store.close()

    def test_webhook_failure_becomes_terminal_after_bounded_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            event_store_module, "MAX_WEBHOOK_ATTEMPTS", 2
        ):
            store = EventStore(Path(tmp) / "events.sqlite3")
            _, record = store.add_event(self.payload(1, webhook_status="pending"))
            self.assertFalse(
                store.mark_webhook_failed(
                    record["id"],
                    error="HTTP 503",
                    next_attempt_at="2026-08-15T10:00:01+00:00",
                )
            )
            self.assertTrue(
                store.mark_webhook_failed(
                    record["id"],
                    error="HTTP 503",
                    next_attempt_at="2026-08-15T10:00:02+00:00",
                )
            )
            self.assertEqual(store.webhook_attempts(record["id"]), 2)
            self.assertEqual(store.get_event(record["id"])["webhook_status"], "failed")
            self.assertIsNone(store.next_webhook_event())
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
        self.assertEqual(
            command,
            [
                "/project/run.sh",
                "--account",
                "main",
                "--non-interactive",
                "--gateway-only",
                "--idle-seconds",
                "60",
            ],
        )
        self.assertEqual(safe_daemon_account("second_2"), "second_2")
        with self.assertRaises(ValueError):
            safe_daemon_account("../main")


class RuntimeLifecycleTests(unittest.TestCase):
    def test_account_lock_rejects_a_second_owner_and_cleans_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = AccountRuntimeLock(tmp)
            second = AccountRuntimeLock(tmp)
            first.acquire()
            self.assertEqual(
                int((Path(tmp) / "userbot.pid").read_text(encoding="ascii")),
                first.pid,
            )
            with self.assertRaisesRegex(RuntimeError, "already owns"):
                second.acquire()
            first.release()
            self.assertFalse((Path(tmp) / "userbot.pid").exists())

    def test_idle_worker_disconnects_only_after_local_requests_finish(self) -> None:
        async def scenario() -> None:
            disconnected = asyncio.Event()

            class FakeClient:
                async def disconnect(self):
                    disconnected.set()

            with tempfile.TemporaryDirectory() as tmp:
                options = GatewayOptions(
                    enabled=True,
                    socket_path=Path(tmp) / "userbot.sock",
                    database_path=Path(tmp) / "events.sqlite3",
                    preview_chars=0,
                    webhook_url=None,
                    webhook_secret=None,
                )
                gateway = UserbotGateway(
                    FakeClient(),
                    SimpleNamespace(account="main"),
                    options,
                    idle_timeout_seconds=1,
                )
                gateway._active_requests = 1
                gateway._last_local_activity = time.monotonic() - 2
                task = asyncio.create_task(gateway._idle_worker())
                await asyncio.sleep(0.15)
                self.assertFalse(disconnected.is_set())
                gateway._active_requests = 0
                await asyncio.wait_for(disconnected.wait(), timeout=2)
                await task
                gateway.store.close()

        asyncio.run(scenario())

    def test_direct_module_runner_builds_one_account_scoped_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module = root / "modules" / "example.py"
            module.parent.mkdir()
            module.write_text("", encoding="utf-8")
            resolved = resolve_module(root, "modules/example.py")
            self.assertEqual(
                module_command(
                    Path("/project/venv/bin/python"),
                    resolved,
                    "main",
                    ["--", "--chat", "@example"],
                ),
                [
                    "/project/venv/bin/python",
                    str(module.resolve()),
                    "--account",
                    "main",
                    "--chat",
                    "@example",
                ],
            )
            self.assertEqual(bounded_timeout(180), 180)
            with self.assertRaises(ValueError):
                bounded_timeout(0)

    def test_direct_module_runner_stops_a_child_process_group(self) -> None:
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            signal_name = stop_process_group(process)
            self.assertEqual(signal_name, "SIGINT")
            self.assertIsNotNone(process.poll())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)


class LaunchdInstallerTests(unittest.TestCase):
    def test_plist_keeps_secrets_out_and_is_gateway_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            payload = plist_payload(root, "main")
            self.assertEqual(payload["Label"], service_label("main"))
            self.assertEqual(payload["KeepAlive"], {"SuccessfulExit": False})
            self.assertIn("--gateway-only", payload["ProgramArguments"])
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
