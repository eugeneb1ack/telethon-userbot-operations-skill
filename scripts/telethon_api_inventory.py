#!/usr/bin/env python3
"""Read-only inventory for the currently installed Telethon API.

Run with the userbot virtualenv. This script never creates a Telegram client,
connects to Telegram, reads a session, or performs network I/O.
"""

from __future__ import annotations

import argparse
import inspect
import json
import re
import sys
from collections import defaultdict
from importlib.metadata import version

from telethon import TelegramClient
from telethon.tl import functions


def snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def tl_url(namespace: str, request_name: str) -> str:
    return f"https://tl.telethon.dev/methods/{namespace}/{snake_case(request_name.removesuffix('Request'))}.html"


def request_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for namespace in sorted(name for name in dir(functions) if not name.startswith("_")):
        group = getattr(functions, namespace)
        for name in sorted(item for item in dir(group) if item.endswith("Request") and item != "TLRequest"):
            request = getattr(group, name)
            if inspect.isclass(request):
                rows.append(
                    {
                        "surface": "raw_request",
                        "namespace": namespace,
                        "name": name,
                        "qualified_name": f"functions.{namespace}.{name}",
                        "signature": str(inspect.signature(request)),
                        "official_tl_url": tl_url(namespace, name),
                    }
                )
    return rows


def client_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for name in sorted(item for item in dir(TelegramClient) if not item.startswith("_")):
        method = getattr(TelegramClient, name)
        if not callable(method):
            continue
        try:
            signature = str(inspect.signature(method))
        except (TypeError, ValueError):
            continue
        rows.append(
            {
                "surface": "client_method",
                "namespace": "client",
                "name": name,
                "qualified_name": f"TelegramClient.{name}",
                "signature": signature,
                "official_tl_url": f"https://docs.telethon.dev/en/stable/modules/client.html#telethon.TelegramClient.{name}",
            }
        )
    return rows


def matches(row: dict[str, str], query: str | None) -> bool:
    if not query:
        return True
    needle = query.casefold().replace("_", "")
    haystack = " ".join(row[key] for key in ("name", "qualified_name", "namespace")).casefold().replace("_", "")
    return needle in haystack


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Print exact local Telethon API contracts")
    result.add_argument("--namespace", action="append", help="Raw namespace, for example messages or stickers")
    result.add_argument("--request", action="append", help="Exact/fuzzy request, for example messages.EditMessageRequest")
    result.add_argument("--client", action="store_true", help="Query high-level TelegramClient methods")
    result.add_argument("--query", help="Filter by a method/request substring")
    result.add_argument("--all", action="store_true", help="Print matching rows instead of a family summary")
    result.add_argument("--json", action="store_true", help="Emit JSON")
    return result


def main() -> int:
    args = parser().parse_args()
    rows = client_rows() if args.client else request_rows()
    namespaces = {value.casefold() for value in args.namespace or []}
    requests = {value.casefold() for value in args.request or []}
    if namespaces:
        rows = [row for row in rows if row["namespace"].casefold() in namespaces]
    if requests:
        rows = [
            row
            for row in rows
            if any(
                wanted in {row["name"].casefold(), row["qualified_name"].casefold()}
                or wanted.replace("_", "") in row["qualified_name"].casefold().replace("_", "")
                for wanted in requests
            )
        ]
    rows = [row for row in rows if matches(row, args.query)]

    if args.json:
        print(json.dumps({"telethon_version": version("telethon"), "count": len(rows), "items": rows}, ensure_ascii=False, indent=2))
        return 0
    if not args.all and not args.request and not args.query:
        totals: dict[str, int] = defaultdict(int)
        for row in rows:
            totals[row["namespace"]] += 1
        print(f"Telethon {version('telethon')}; {sum(totals.values())} matching {('client methods' if args.client else 'raw requests')}")
        for namespace, count in sorted(totals.items()):
            print(f"{namespace}: {count}")
        print("Use --namespace, --request, --query, or --all.")
        return 0
    for row in rows:
        print(f"{row['qualified_name']}{row['signature']}")
        print(f"  {row['official_tl_url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
