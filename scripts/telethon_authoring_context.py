#!/usr/bin/env python3
"""Build one offline context packet for a focused Telethon module change.

Run this script with the userbot virtualenv. It reads the local registry,
requirements pin, installed Telethon signatures and project documentation. It
does not create a Telegram client, inspect a session, or perform network I/O.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from telethon_api_inventory import client_rows, documentation_metadata, request_rows


def _registry_payload(project_root: Path, query: str) -> dict[str, Any]:
    registry = project_root / "scripts" / "userbot_module_registry.py"
    if not registry.is_file():
        raise ValueError(f"module registry not found: {registry}")
    completed = subprocess.run(
        [sys.executable, str(registry), "--query", query, "--json"],
        cwd=project_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=15,
        check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"registry failed with exit {completed.returncode}: {detail}")
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        raise ValueError("module registry did not return a JSON object")
    return payload


def _exact_rows(
    rows: list[dict[str, str]], values: list[str], *, client: bool
) -> tuple[list[dict[str, str]], list[str]]:
    selected: list[dict[str, str]] = []
    missing: list[str] = []
    for value in values:
        normalized = value.casefold().replace("_", "")
        matches = [
            row
            for row in rows
            if normalized == row["name"].casefold().replace("_", "")
            or row["qualified_name"].casefold().replace("_", "").endswith(normalized)
        ]
        if len(matches) == 1:
            selected.append(matches[0])
        else:
            missing.append(value)
    surface = "client method" if client else "raw request"
    for row in selected:
        row["selection_reason"] = f"exact installed {surface}"
    return selected, missing


def build_context(
    *,
    project_root: Path,
    query: str,
    client_methods: list[str],
    raw_requests: list[str],
) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    route = _registry_payload(root, query)
    telethon = documentation_metadata(root)
    client_items, missing_client = _exact_rows(
        client_rows(), client_methods, client=True
    )
    request_items, missing_requests = _exact_rows(
        request_rows(), raw_requests, client=False
    )
    status = route.get("status")
    if status in {"match", "exact"}:
        decision = "use_existing_operation"
    elif status == "ambiguous":
        decision = "clarify_before_coding"
    elif telethon["version_match"] is not True:
        decision = "resolve_version_alignment_before_coding"
    elif missing_client or missing_requests:
        decision = "resolve_api_surface_before_coding"
    else:
        decision = "author_one_focused_operation"

    read_first = [
        root / "AGENTS.md",
        root / "MODULES.md",
        root / "docs" / "TELETHON_MODULE_AUTHORING.md",
        root / "core" / "config.py",
        root / "core" / "telegram_targets.py",
    ]
    return {
        "schema": "telethon_authoring_context.v1",
        "query": query,
        "decision": decision,
        "route": route,
        "telethon": telethon,
        "api": {
            "client_methods": client_items,
            "raw_requests": request_items,
            "missing": [*missing_client, *missing_requests],
            "preference": (
                "Prefer a documented high-level TelegramClient method when it covers the "
                "operation; use raw TL only for capabilities it does not expose."
            ),
        },
        "read_first": [str(path) for path in read_first if path.is_file()],
        "verification": {
            "catalog": (
                f"{sys.executable} scripts/userbot_module_registry.py "
                "--validate-catalog --json"
            ),
            "focused_then_full": (
                f"{sys.executable} scripts/check_module.py modules/<name>.py --full"
            ),
        },
        "network_io": False,
        "telegram_session_access": False,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Build one offline module-authoring context packet"
    )
    result.add_argument("--project-root", type=Path, required=True)
    result.add_argument("--query", required=True, help="Original natural-language request")
    result.add_argument(
        "--client-method",
        action="append",
        default=[],
        help="Exact high-level method, for example iter_messages",
    )
    result.add_argument(
        "--raw-request",
        action="append",
        default=[],
        help="Exact raw request, for example messages.EditMessageRequest",
    )
    result.add_argument("--json", action="store_true", help="Emit formatted JSON")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        payload = build_context(
            project_root=args.project_root,
            query=args.query,
            client_methods=args.client_method,
            raw_requests=args.raw_request,
        )
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        payload = {"ok": False, "error": type(exc).__name__, "message": str(exc)}
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
