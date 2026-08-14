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
import uuid
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


DEFAULT_IDLE_SECONDS = 60


def bounded_idle_seconds(value: int) -> int:
    if not 10 <= value <= 3600:
        raise ValueError("idle seconds must be between 10 and 3600")
    return value


def process_command(
    project_root: Path, account: str, idle_seconds: int = DEFAULT_IDLE_SECONDS
) -> list[str]:
    return [
        str(project_root / "run.sh"),
        "--account",
        account,
        "--non-interactive",
        "--gateway-only",
        "--idle-seconds",
        str(bounded_idle_seconds(idle_seconds)),
    ]


def read_pid(path: Path) -> int | None:
    try:
        value = int(path.read_text(encoding="ascii").strip())
    except (FileNotFoundError, ValueError, OSError):
        return None
    return value if value > 1 else None


def process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
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


def gateway_status(path: Path) -> dict[str, object] | None:
    if not path.is_socket():
        return None
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(1)
    try:
        probe.connect(str(path))
        payload = {
            "id": uuid.uuid4().hex,
            "method": "status",
            "params": {},
        }
        probe.sendall(json.dumps(payload).encode("utf-8") + b"\n")
        chunks = bytearray()
        while b"\n" not in chunks and len(chunks) < 64 * 1024:
            part = probe.recv(4096)
            if not part:
                break
            chunks.extend(part)
        response = json.loads(bytes(chunks).split(b"\n", 1)[0])
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    finally:
        probe.close()
    if not response.get("ok") or not isinstance(response.get("result"), dict):
        return None
    return response["result"]


def status_payload(project_root: Path, account: str) -> dict[str, object]:
    state = paths(project_root, account)
    pid = read_pid(state["pid"])
    remote = gateway_status(state["socket"])
    gateway_pid = remote.get("pid") if remote else None
    identity_verified = bool(
        isinstance(gateway_pid, int) and pid == gateway_pid and process_is_alive(pid)
    )
    return {
        "ok": True,
        "account": account,
        "running": remote is not None,
        "pid": gateway_pid if isinstance(gateway_pid, int) else None,
        "socket_ready": remote is not None,
        "identity_verified": identity_verified,
        "idle_timeout_seconds": remote.get("idle_timeout_seconds") if remote else None,
        "autostart": False,
    }


def _terminate_spawned_process(process: subprocess.Popen[bytes]) -> None:
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def start(
    project_root: Path,
    account: str,
    idle_seconds: int = DEFAULT_IDLE_SECONDS,
) -> dict[str, object]:
    state = paths(project_root, account)
    current = status_payload(project_root, account)
    if current["running"] and current["socket_ready"]:
        return current

    state["stdout"].parent.mkdir(parents=True, exist_ok=True)
    stdout_handle = state["stdout"].open("ab", buffering=0)
    stderr_handle = state["stderr"].open("ab", buffering=0)
    try:
        process = subprocess.Popen(
            process_command(project_root, account, idle_seconds),
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
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"userbot exited during startup; inspect {state['stderr']}"
            )
        if gateway_status(state["socket"]) is not None:
            return status_payload(project_root, account)
        time.sleep(0.1)
    _terminate_spawned_process(process)
    raise RuntimeError("gateway socket did not become ready within 15 seconds")


def stop(project_root: Path, account: str) -> dict[str, object]:
    state = paths(project_root, account)
    current = status_payload(project_root, account)
    pid = current["pid"]
    if not current["running"]:
        state["pid"].unlink(missing_ok=True)
        return {"ok": True, "account": account, "stopped": True, "was_running": False}
    if not isinstance(pid, int) or not current["identity_verified"]:
        raise RuntimeError("refusing to signal a gateway whose PID identity is not verified")
    # SIGINT lets asyncio unwind main(), close the gateway and remove the socket.
    os.kill(pid, signal.SIGINT)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and process_is_alive(pid):
        time.sleep(0.1)
    forced = None
    if process_is_alive(pid):
        forced = "SIGTERM"
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and process_is_alive(pid):
            time.sleep(0.1)
    if process_is_alive(pid):
        forced = "SIGKILL"
        os.kill(pid, signal.SIGKILL)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and process_is_alive(pid):
            time.sleep(0.1)
    state["pid"].unlink(missing_ok=True)
    return {
        "ok": True,
        "account": account,
        "stopped": not process_is_alive(pid),
        "was_running": True,
        "forced": forced,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Start or stop the persistent gateway on demand without autostart"
    )
    result.add_argument(
        "--project-root", type=Path, default=Path(__file__).resolve().parent.parent
    )
    result.add_argument("--account", default="main")
    result.add_argument(
        "--idle-seconds",
        type=int,
        default=DEFAULT_IDLE_SECONDS,
        help="Auto-stop detached gateway after local RPC inactivity (10-3600)",
    )
    result.add_argument("command", choices=("start", "status", "stop"))
    return result


def main() -> int:
    args = parser().parse_args()
    root = args.project_root.expanduser().resolve()
    account = safe_account(args.account)
    if not (root / "run.sh").is_file():
        raise ValueError(f"run.sh not found under {root}")
    if args.command == "start":
        payload = start(root, account, bounded_idle_seconds(args.idle_seconds))
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
