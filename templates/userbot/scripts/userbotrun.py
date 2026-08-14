#!/usr/bin/env python3
"""Run one direct module without competing for the account session."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.runtime_lock import AccountRuntimeLock
from scripts.userbotd import safe_account, status_payload, stop


def bounded_timeout(value: int) -> int:
    if not 10 <= value <= 3600:
        raise ValueError("timeout must be between 10 and 3600 seconds")
    return value


def resolve_module(project_root: Path, value: str) -> Path:
    candidate = (project_root / value).resolve()
    modules_dir = (project_root / "modules").resolve()
    if candidate.parent != modules_dir or candidate.suffix != ".py" or not candidate.is_file():
        raise ValueError("module must be an existing modules/<name>.py file")
    return candidate


def module_command(
    python: Path, module: Path, account: str, module_args: list[str]
) -> list[str]:
    args = list(module_args)
    if args[:1] == ["--"]:
        args = args[1:]
    return [str(python), str(module), "--account", account, *args]


def stop_process_group(process: subprocess.Popen[bytes]) -> str:
    stages = ((signal.SIGINT, 5), (signal.SIGTERM, 3), (signal.SIGKILL, 2))
    last = "none"
    for sig, timeout in stages:
        last = signal.Signals(sig).name
        os.killpg(process.pid, sig)
        try:
            process.wait(timeout=timeout)
            break
        except subprocess.TimeoutExpired:
            continue
    return last


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Run one direct userbot module with session locking and a hard timeout"
    )
    result.add_argument(
        "--project-root", type=Path, default=Path(__file__).resolve().parent.parent
    )
    result.add_argument("--account", default="main")
    result.add_argument("--timeout", type=int, default=180)
    result.add_argument("module")
    result.add_argument("module_args", nargs=argparse.REMAINDER)
    return result


def main() -> int:
    args = parser().parse_args()
    process: subprocess.Popen[bytes] | None = None
    runtime_lock: AccountRuntimeLock | None = None
    try:
        root = args.project_root.expanduser().resolve()
        account = safe_account(args.account)
        timeout = bounded_timeout(args.timeout)
        module = resolve_module(root, args.module)
        python = root / "venv" / "bin" / "python"
        if not python.is_file():
            raise ValueError(f"project interpreter not found: {python}")

        gateway = status_payload(root, account)
        if gateway["running"]:
            if not gateway.get("idle_timeout_seconds"):
                raise RuntimeError(
                    "a foreground gateway owns this session; use a gateway route or stop it explicitly"
                )
            stop(root, account)

        runtime_lock = AccountRuntimeLock(root / "runtime" / account)
        runtime_lock.acquire()
        command = module_command(python, module, account, args.module_args)
        process = subprocess.Popen(command, cwd=root, start_new_session=True)
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            forced = stop_process_group(process)
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "ModuleTimeout",
                        "timeout_seconds": timeout,
                        "last_signal": forced,
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            return 124
    except (OSError, RuntimeError, ValueError) as exc:
        print(
            json.dumps(
                {"ok": False, "error": type(exc).__name__, "message": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    finally:
        if process is not None and process.poll() is None:
            stop_process_group(process)
        if runtime_lock is not None:
            runtime_lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
