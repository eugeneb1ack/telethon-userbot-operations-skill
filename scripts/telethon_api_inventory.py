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
from pathlib import Path

from telethon import TelegramClient
from telethon.tl import functions


TELETHON_PIN_RE = re.compile(r"^\s*telethon\s*==\s*([^\s;#]+)", re.IGNORECASE)


def snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def tl_url(namespace: str, request_name: str) -> str:
    return f"https://tl.telethon.dev/methods/{namespace}/{snake_case(request_name.removesuffix('Request'))}.html"


def pinned_telethon_version(project_root: Path | None) -> str | None:
    if project_root is None:
        return None
    requirements = project_root.expanduser().resolve() / "requirements.txt"
    try:
        lines = requirements.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        match = TELETHON_PIN_RE.match(line)
        if match:
            return match.group(1)
    return None


def documentation_metadata(project_root: Path | None = None) -> dict[str, object]:
    installed = version("telethon")
    pinned = pinned_telethon_version(project_root)
    pin_status = "missing" if pinned is None else "match" if pinned == installed else "mismatch"
    return {
        "installed_version": installed,
        "pinned_version": pinned,
        "version_match": None if pinned is None else pinned == installed,
        "pin_status": pin_status,
        "stable_docs_url": "https://docs.telethon.dev/en/stable/",
        "client_reference_url": "https://docs.telethon.dev/en/stable/modules/client.html",
        "tl_reference_url": "https://tl.telethon.dev/",
        "changelog_url": "https://docs.telethon.dev/en/stable/misc/changelog.html",
        "policy": (
            "Require a matching project pin. Treat installed signatures as the runtime "
            "contract. Open the official URL and confirm the documentation header matches "
            "installed_version before coding."
        ),
    }


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
    result.add_argument(
        "--project-root",
        type=Path,
        help="Optional runtime root whose requirements.txt pin should match the installed package",
    )
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

    documentation = documentation_metadata(args.project_root)
    if args.json:
        print(
            json.dumps(
                {
                    "telethon_version": version("telethon"),
                    "documentation": documentation,
                    "count": len(rows),
                    "items": rows,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if not args.all and not args.request and not args.query:
        totals: dict[str, int] = defaultdict(int)
        for row in rows:
            totals[row["namespace"]] += 1
        pin = documentation["pinned_version"]
        pin_note = f"; project pin {pin}; match={documentation['version_match']}" if pin else ""
        print(
            f"Telethon {version('telethon')}{pin_note}; {sum(totals.values())} matching "
            f"{('client methods' if args.client else 'raw requests')}"
        )
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
