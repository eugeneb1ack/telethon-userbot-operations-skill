#!/usr/bin/env python3
"""Run one direct module without competing for the account session."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import FrameType

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.runtime_lock import AccountRuntimeLock
from scripts.userbotd import safe_account, status_payload, stop


class ShutdownRequested(Exception):
    """Cooperatively unwind the runner after an external shutdown signal."""

    def __init__(self, signum: int) -> None:
        self.signum = signum
        super().__init__(signal.Signals(signum).name)


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
    if process.poll() is not None:
        return "none"

    stages = ((signal.SIGINT, 5), (signal.SIGTERM, 3), (signal.SIGKILL, 2))
    last = "none"
    for sig, timeout in stages:
        last = signal.Signals(sig).name
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            try:
                process.wait(timeout=1)
                return last
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    f"child process group disappeared while pid={process.pid} is still running"
                ) from exc
        try:
            process.wait(timeout=timeout)
            return last
        except subprocess.TimeoutExpired:
            continue
    raise RuntimeError(
        f"child process group did not exit after {last}: pid={process.pid}"
    )


def install_shutdown_handlers() -> dict[
    int, Callable[[int, FrameType | None], None] | int | None
]:
    previous: dict[int, Callable[[int, FrameType | None], None] | int | None] = {}
    shutdown_requested = False

    def request_shutdown(signum: int, _frame: FrameType | None) -> None:
        nonlocal shutdown_requested
        if shutdown_requested:
            return
        shutdown_requested = True
        raise ShutdownRequested(signum)

    for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.getsignal(signum)
        signal.signal(signum, request_shutdown)
    return previous


def restore_shutdown_handlers(
    previous: dict[int, Callable[[int, FrameType | None], None] | int | None],
) -> None:
    for signum, handler in previous.items():
        signal.signal(signum, handler)


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
    previous_handlers = install_shutdown_handlers()
    result_code = 1
    result_payload: dict[str, object] | None = None
    cleanup_signal = "none"
    cleanup_error: Exception | None = None
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
            result_code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            cleanup_signal = stop_process_group(process)
            result_payload = {
                "ok": False,
                "error": "ModuleTimeout",
                "timeout_seconds": timeout,
                "last_signal": cleanup_signal,
            }
            result_code = 124
    except ShutdownRequested as exc:
        result_payload = {
            "ok": False,
            "error": "RunnerInterrupted",
            "signal": signal.Signals(exc.signum).name,
        }
        result_code = 128 + exc.signum
    except (OSError, RuntimeError, ValueError) as exc:
        result_payload = {
            "ok": False,
            "error": type(exc).__name__,
            "message": str(exc),
        }
        result_code = 1
    finally:
        try:
            if process is not None and process.poll() is None:
                cleanup_signal = stop_process_group(process)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            cleanup_error = exc
        finally:
            try:
                if runtime_lock is not None:
                    runtime_lock.release()
            except OSError as exc:
                if cleanup_error is None:
                    cleanup_error = exc
            finally:
                restore_shutdown_handlers(previous_handlers)

    if result_payload is not None:
        if cleanup_signal != "none":
            result_payload["last_signal"] = cleanup_signal
        print(json.dumps(result_payload, ensure_ascii=False), file=sys.stderr)
    if cleanup_error is not None:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "RunnerCleanupError",
                    "message": str(cleanup_error),
                    "child_pid": process.pid if process is not None else None,
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 125
    return result_code


if __name__ == "__main__":
    raise SystemExit(main())
