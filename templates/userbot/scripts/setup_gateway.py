#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.gateway import validate_webhook_url

MANAGED_KEYS = (
    "USERBOT_GATEWAY_ENABLED",
    "USERBOT_EVENT_PREVIEW_CHARS",
    "USERBOT_WEBHOOK_URL",
    "USERBOT_WEBHOOK_SECRET",
)


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if match:
            values[match.group(1)] = match.group(2)
    return values


def update_env(text: str, updates: dict[str, str]) -> str:
    remaining = dict(updates)
    lines: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", line)
        key = match.group(1) if match else None
        if key in remaining:
            lines.append(f"{key}={remaining.pop(key)}")
        else:
            lines.append(line)
    if remaining:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("# Persistent local gateway and optional signed event webhook")
        for key in MANAGED_KEYS:
            if key in remaining:
                lines.append(f"{key}={remaining.pop(key)}")
    return "\n".join(lines).rstrip() + "\n"


def prompt_preview(current: str | None) -> int:
    default = current or "160"
    raw = input(f"Символов текста в событии [{default}; 0 = без текста]: ").strip()
    value = int(raw or default)
    if not 0 <= value <= 500:
        raise ValueError("preview length must be between 0 and 500")
    return value


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Configure the persistent gateway and signed outbound webhook locally"
    )
    result.add_argument(
        "--shared-env",
        type=Path,
        default=ROOT / "accounts" / "_shared.env",
        help="Shared integration env file",
    )
    result.add_argument(
        "--disable-webhook",
        action="store_true",
        help="Keep the gateway enabled but remove webhook URL and secret",
    )
    return result


def main() -> int:
    args = parser().parse_args()
    path = args.shared_env.expanduser().resolve()
    current_text = path.read_text(encoding="utf-8") if path.exists() else ""
    current = parse_env(current_text)

    if args.disable_webhook:
        updates = {
            "USERBOT_GATEWAY_ENABLED": "true",
            "USERBOT_EVENT_PREVIEW_CHARS": current.get("USERBOT_EVENT_PREVIEW_CHARS", "160"),
            "USERBOT_WEBHOOK_URL": "",
            "USERBOT_WEBHOOK_SECRET": "",
        }
    else:
        print("Gateway использует Telegram-соединение постоянно; агенты обращаются к нему локально.")
        print("Webhook получает только новые события direct_message, mention и reply.")
        url = input("Webhook URL (HTTPS или http://localhost): ").strip()
        validated_url = validate_webhook_url(url)
        if not validated_url:
            raise ValueError("webhook URL is required; use --disable-webhook to disable it")
        secret = getpass.getpass("HMAC secret (минимум 32 символа, ввод скрыт): ")
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,}", secret):
            raise ValueError("HMAC secret must be 32+ ASCII letters, digits, _ or -")
        preview_chars = prompt_preview(current.get("USERBOT_EVENT_PREVIEW_CHARS"))
        updates = {
            "USERBOT_GATEWAY_ENABLED": "true",
            "USERBOT_EVENT_PREVIEW_CHARS": str(preview_chars),
            "USERBOT_WEBHOOK_URL": validated_url,
            "USERBOT_WEBHOOK_SECRET": secret,
        }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(update_env(current_text, updates), encoding="utf-8")
    os.chmod(path, 0o600)
    print(f"Настройка сохранена: {path}")
    print("Перезапусти ./run.sh --account main и проверь scripts/userbotctl.py status.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (EOFError, KeyboardInterrupt):
        print("\nНастройка отменена.", file=sys.stderr)
        raise SystemExit(130)
    except ValueError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        raise SystemExit(2)
