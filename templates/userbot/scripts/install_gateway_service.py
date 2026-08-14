#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path


def safe_account(value: str) -> str:
    if not value or any(not (char.isalnum() or char in "_-") for char in value):
        raise ValueError("account may contain only letters, digits, _ and -")
    return value


def service_label(account: str) -> str:
    return f"com.local.telethon-userbot.{safe_account(account)}"


def plist_payload(project_root: Path, account: str) -> dict:
    root = project_root.resolve()
    run_script = root / "run.sh"
    if not run_script.is_file():
        raise ValueError(f"run.sh not found under {root}")
    logs = root / "runtime" / account / "logs"
    return {
        "Label": service_label(account),
        "ProgramArguments": [
            str(run_script),
            "--account",
            account,
            "--non-interactive",
            "--gateway-only",
        ],
        "WorkingDirectory": str(root),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ProcessType": "Background",
        "ThrottleInterval": 10,
        "StandardOutPath": str(logs / "gateway.stdout.log"),
        "StandardErrorPath": str(logs / "gateway.stderr.log"),
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Run an optional launchd supervisor for the current login session only"
    )
    result.add_argument(
        "--project-root", type=Path, default=Path(__file__).resolve().parent.parent
    )
    result.add_argument("--account", default="main")
    result.add_argument("--execute", action="store_true")
    result.add_argument("--unload", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    account = safe_account(args.account)
    project_root = args.project_root.expanduser().resolve()
    payload = plist_payload(project_root, account)
    label = payload["Label"]
    destination = (
        project_root / "runtime" / account / "launchd" / f"{label}.plist"
    )
    print(f"service={label}")
    print(f"project_root={project_root}")
    print(f"plist={destination}")
    print("autostart=false")
    if args.execute and args.unload:
        raise ValueError("choose --execute or --unload")
    domain = f"gui/{os.getuid()}"
    if args.unload:
        subprocess.run(
            ["launchctl", "bootout", domain, str(destination)],
            check=False,
        )
        destination.unlink(missing_ok=True)
        print("unloaded=true")
        return 0
    if not args.execute:
        print("dry_run=true")
        return 0

    logs = project_root / "runtime" / account / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".plist.tmp")
    temporary.write_bytes(plistlib.dumps(payload, sort_keys=True))
    os.chmod(temporary, 0o600)
    temporary.replace(destination)

    subprocess.run(
        ["launchctl", "bootout", domain, str(destination)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(["launchctl", "bootstrap", domain, str(destination)], check=True)
    subprocess.run(["launchctl", "kickstart", "-k", f"{domain}/{label}"], check=True)
    print("loaded_for_current_login=true")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
