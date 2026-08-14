#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path


def safe_account(value: str) -> str:
    if not value or any(not (char.isalnum() or char in "_-") for char in value):
        raise ValueError("account may contain only letters, digits, _ and -")
    return value


def paths(project_root: Path, account: str) -> dict[str, Path]:
    runtime = project_root / "runtime" / account
    return {
        "runtime": runtime,
        "pid": runtime / "userbot.pid",
        "socket": runtime / "userbot.sock",
        "stdout": runtime / "logs" / "gateway.stdout.log",
        "stderr": runtime / "logs" / "gateway.stderr.log",
    }


def process_command(project_root: Path, account: str) -> list[str]:
    return [str(project_root / "run.sh"), "--account", account, "--non-interactive"]


def read_pid(path: Path) -> int | None:
    try:
        value = int(path.read_text(encoding="ascii").strip())
    except (FileNotFoundError, ValueError, OSError):
        return None
    return value if value > 1 else None


def process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def socket_is_ready(path: Path) -> bool:
    if not path.is_socket():
        return False
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(0.5)
    try:
        probe.connect(str(path))
    except OSError:
        return False
    finally:
        probe.close()
    return True


def status_payload(project_root: Path, account: str) -> dict[str, object]:
    state = paths(project_root, account)
    pid = read_pid(state["pid"])
    alive = bool(pid and process_is_alive(pid))
    return {
        "ok": True,
        "account": account,
        "running": alive,
        "pid": pid if alive else None,
        "socket_ready": socket_is_ready(state["socket"]),
        "autostart": False,
    }


def start(project_root: Path, account: str) -> dict[str, object]:
    state = paths(project_root, account)
    current = status_payload(project_root, account)
    if current["running"] and current["socket_ready"]:
        return current
    if socket_is_ready(state["socket"]):
        return {
            "ok": True,
            "account": account,
            "running": True,
            "pid": None,
            "socket_ready": True,
            "autostart": False,
            "note": "gateway is already provided by another foreground process",
        }

    state["stdout"].parent.mkdir(parents=True, exist_ok=True)
    stdout_handle = state["stdout"].open("ab", buffering=0)
    stderr_handle = state["stderr"].open("ab", buffering=0)
    try:
        process = subprocess.Popen(
            process_command(project_root, account),
            cwd=project_root,
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        stdout_handle.close()
        stderr_handle.close()
    state["pid"].write_text(f"{process.pid}\n", encoding="ascii")
    os.chmod(state["pid"], 0o600)

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            state["pid"].unlink(missing_ok=True)
            raise RuntimeError(
                f"userbot exited during startup; inspect {state['stderr']}"
            )
        if socket_is_ready(state["socket"]):
            return status_payload(project_root, account)
        time.sleep(0.1)
    process.terminate()
    state["pid"].unlink(missing_ok=True)
    raise RuntimeError("gateway socket did not become ready within 15 seconds")


def stop(project_root: Path, account: str) -> dict[str, object]:
    state = paths(project_root, account)
    pid = read_pid(state["pid"])
    if pid is None or not process_is_alive(pid):
        state["pid"].unlink(missing_ok=True)
        return {"ok": True, "account": account, "stopped": True, "was_running": False}
    if not socket_is_ready(state["socket"]):
        raise RuntimeError("refusing to signal a process without a live userbot socket")
    # SIGINT lets asyncio unwind main(), close the gateway and remove the socket.
    os.kill(pid, signal.SIGINT)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and process_is_alive(pid):
        time.sleep(0.1)
    state["pid"].unlink(missing_ok=True)
    return {
        "ok": True,
        "account": account,
        "stopped": not process_is_alive(pid),
        "was_running": True,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Start or stop the persistent gateway on demand without autostart"
    )
    result.add_argument(
        "--project-root", type=Path, default=Path(__file__).resolve().parent.parent
    )
    result.add_argument("--account", default="main")
    result.add_argument("command", choices=("start", "status", "stop"))
    return result


def main() -> int:
    args = parser().parse_args()
    root = args.project_root.expanduser().resolve()
    account = safe_account(args.account)
    if not (root / "run.sh").is_file():
        raise ValueError(f"run.sh not found under {root}")
    if args.command == "start":
        payload = start(root, account)
    elif args.command == "stop":
        payload = stop(root, account)
    else:
        payload = status_payload(root, account)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
