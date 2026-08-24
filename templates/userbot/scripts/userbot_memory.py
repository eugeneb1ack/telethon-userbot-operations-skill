#!/usr/bin/env python3
"""Inspect and maintain bounded account-local semantic memory.

This CLI never loads Telegram credentials, acquires a session lock, or connects
to Telegram. It only reads or writes the account's local SQLite memory database.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.memory_store import (
    ALLOWED_KINDS,
    ALLOWED_VALIDITY,
    MemoryStore,
    memory_database_path,
    normalize_memory_account,
)


def _database_path(account: str, override: str | None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    return memory_database_path(PROJECT_ROOT / "runtime" / account / "data")


def _load_document(args: argparse.Namespace) -> dict[str, Any]:
    if args.file:
        source = Path(args.file).expanduser()
        if not source.is_file():
            raise ValueError(f"memory JSON file does not exist: {source}")
        try:
            value = json.loads(source.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid memory JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError("memory JSON must contain one object")
        return value

    required = {
        "kind": args.kind,
        "scope": args.scope,
        "subject": args.subject,
        "summary": args.summary,
        "source": args.source,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ValueError(
            "remember requires --file or all of: --kind, --scope, --subject, "
            "--summary, --source; missing " + ", ".join(missing)
        )
    tags = [tag.strip() for tag in (args.tags or "").split(",") if tag.strip()]
    document: dict[str, Any] = {
        "kind": args.kind,
        "scope": args.scope,
        "subject": args.subject,
        "summary": args.summary,
        "tags": tags,
        "provenance": {"source": args.source},
        "validity": args.validity,
        "confidence": args.confidence,
    }
    for field in ("key", "observed_at", "expires_at"):
        value = getattr(args, field)
        if value is not None:
            document[field] = value
    return document


def _emit(payload: dict[str, Any], *, stream: Any = sys.stdout) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2), file=stream)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Recall or update bounded account-local semantic memory without Telegram I/O"
    )
    result.add_argument("--account", default=os.getenv("USERBOT_ACCOUNT", "main"))
    result.add_argument("--memory-db", help="Advanced/test override for the local SQLite path")
    commands = result.add_subparsers(dest="command", required=True)

    recall = commands.add_parser("recall", help="Find compact reusable memory")
    recall.add_argument("--query", required=True)
    recall.add_argument("--scope")
    recall.add_argument("--limit", type=int, default=5)
    recall.add_argument("--include-details", action="store_true")

    remember = commands.add_parser("remember", help="Create or revision-update one memory item")
    remember.add_argument("--file", help="UTF-8 JSON document; overrides shorthand fields")
    remember.add_argument("--key")
    remember.add_argument("--kind", choices=sorted(ALLOWED_KINDS))
    remember.add_argument("--scope")
    remember.add_argument("--subject")
    remember.add_argument("--summary")
    remember.add_argument("--source", help="Compact provenance used for later revalidation")
    remember.add_argument("--tags", help="Comma-separated retrieval tags")
    remember.add_argument("--validity", choices=sorted(ALLOWED_VALIDITY), default="stable")
    remember.add_argument("--confidence", type=float, default=1.0)
    remember.add_argument("--observed-at")
    remember.add_argument("--expires-at")
    remember.add_argument("--expected-revision", type=int)

    get = commands.add_parser("get", help="Read one exact memory item")
    get.add_argument("--id", required=True)
    get.add_argument("--summary-only", action="store_true")

    listing = commands.add_parser("list", help="List recent compact items")
    listing.add_argument("--scope")
    listing.add_argument("--limit", type=int, default=20)

    commands.add_parser("stats", help="Show bounded-memory usage")

    forget = commands.add_parser("forget", help="Preview or delete one exact local item")
    forget.add_argument("--id", required=True)
    forget.add_argument("--execute", action="store_true")
    return result


def run(args: argparse.Namespace) -> dict[str, Any]:
    account = normalize_memory_account(args.account)
    database = _database_path(account, args.memory_db)
    with MemoryStore(database) as store:
        if args.command == "recall":
            items = store.recall(
                account,
                args.query,
                scope=args.scope,
                limit=args.limit,
                include_details=args.include_details,
            )
            return {
                "ok": True,
                "action": "recall",
                "cache_status": "hit" if items else "miss",
                "account": account,
                "database": str(database),
                "count": len(items),
                "items": items,
            }
        if args.command == "remember":
            status, item = store.remember(
                account,
                _load_document(args),
                expected_revision=args.expected_revision,
            )
            return {
                "ok": True,
                "action": "remember",
                "status": status,
                "account": account,
                "database": str(database),
                "item": item,
            }
        if args.command == "get":
            item = store.get(account, args.id, include_details=not args.summary_only)
            return {
                "ok": item is not None,
                "action": "get",
                "account": account,
                "database": str(database),
                "item": item,
            }
        if args.command == "list":
            items = store.list_recent(account, scope=args.scope, limit=args.limit)
            return {
                "ok": True,
                "action": "list",
                "account": account,
                "database": str(database),
                "count": len(items),
                "items": items,
            }
        if args.command == "stats":
            return {
                "ok": True,
                "action": "stats",
                "account": account,
                "database": str(database),
                "stats": store.stats(account),
            }
        if args.command == "forget":
            item = store.get(account, args.id, include_details=False)
            if item is None:
                return {
                    "ok": False,
                    "action": "forget",
                    "status": "not_found",
                    "account": account,
                    "database": str(database),
                    "item": None,
                }
            if not args.execute:
                return {
                    "ok": True,
                    "action": "forget",
                    "status": "preview",
                    "execute_required": True,
                    "account": account,
                    "database": str(database),
                    "item": item,
                }
            deleted = store.forget(account, args.id)
            return {
                "ok": deleted is not None,
                "action": "forget",
                "status": "deleted" if deleted else "not_found",
                "account": account,
                "database": str(database),
                "item": deleted,
            }
    raise ValueError(f"unsupported command: {args.command}")


def main() -> int:
    try:
        payload = run(parser().parse_args())
    except (OSError, RuntimeError, ValueError) as exc:
        _emit({"ok": False, "error": str(exc)}, stream=sys.stderr)
        return 2
    _emit(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
