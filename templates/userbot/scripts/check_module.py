#!/usr/bin/env python3
"""Offline quality gate for one newly written Telethon module."""

from __future__ import annotations

import argparse
import ast
import json
import os
import py_compile
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


NAME_RE = re.compile(r"[a-z][a-z0-9_]*\.py\Z")
TELEGRAM_WRITE_METHODS = {
    "delete_messages",
    "edit_admin",
    "edit_message",
    "edit_permissions",
    "forward_messages",
    "kick_participant",
    "pin_message",
    "send_file",
    "send_message",
    "unpin_message",
}


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _execute_flag_is_guarded(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node) != "add_argument":
            continue
        flags = {
            argument.value
            for argument in node.args
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
        }
        if "--execute" not in flags:
            continue
        return any(
            keyword.arg == "action"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value == "store_true"
            for keyword in node.keywords
        )
    return False


def _register_function(tree: ast.Module) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    return next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "register"
        ),
        None,
    )


def _register_is_inert(tree: ast.Module) -> bool:
    function = _register_function(tree)
    if function is None:
        return False
    meaningful = [
        statement
        for statement in function.body
        if not (
            isinstance(statement, ast.Pass)
            or (
                isinstance(statement, ast.Return)
                and (
                    statement.value is None
                    or isinstance(statement.value, ast.Constant)
                    and statement.value.value is None
                )
            )
            or (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            )
        )
    ]
    return not meaningful


def _registered_modules(registry_path: Path) -> set[str]:
    tree = ast.parse(registry_path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node) != "Operation":
            continue
        if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
            value = node.args[1].value
            if isinstance(value, str) and "/" not in value:
                modules.add(value)
    return modules


def analyze_source(source: str) -> list[str]:
    errors: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"syntax error: {exc.msg} at line {exc.lineno}"]

    register = _register_function(tree)
    if register is None:
        errors.append("missing register(client)")
    elif (
        len(register.args.posonlyargs) + len(register.args.args) != 1
        or register.args.vararg is not None
        or register.args.kwarg is not None
    ):
        errors.append("register must accept exactly one client argument")

    forbidden_start = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "start"
    ]
    if forbidden_start:
        errors.append(
            "client.start() is forbidden; use connect() + is_user_authorized() "
            f"(line {forbidden_start[0]})"
        )

    constructs_client = any(
        isinstance(node, ast.Call) and _call_name(node) == "TelegramClient"
        for node in ast.walk(tree)
    )
    if constructs_client:
        required = (
            "load_settings",
            "apply_runtime_env",
            "is_user_authorized",
            "disconnect",
        )
        missing = [name for name in required if name not in source]
        if missing:
            errors.append("direct helper is missing: " + ", ".join(missing))

    write_lines = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _call_name(node) in TELEGRAM_WRITE_METHODS
    ]
    has_execute = "--execute" in source
    if has_execute:
        if not _execute_flag_is_guarded(tree):
            errors.append("--execute must be an argparse store_true flag")
        if "dry_run" not in source:
            errors.append("Telegram write CLI must return an explicit dry_run plan")
    if write_lines and not has_execute:
        errors.append(
            "Telegram write calls require a guarded --execute CLI "
            f"(first write at line {write_lines[0]})"
        )

    top_level_await = [
        node.lineno
        for statement in tree.body
        if not isinstance(
            statement,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.ClassDef,
                ast.Import,
                ast.ImportFrom,
                ast.Assign,
                ast.AnnAssign,
                ast.If,
                ast.Expr,
            ),
        )
        for node in ast.walk(statement)
        if isinstance(node, ast.Await)
    ]
    if top_level_await:
        errors.append(f"top-level await is forbidden (line {top_level_await[0]})")
    return errors


def run_check(command: list[str], *, cwd: Path, timeout: int) -> dict[str, object]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
        env=environment,
    )
    return {
        "command": command,
        "returncode": completed.returncode,
        "output": completed.stdout[-4000:],
    }


def run_checks_parallel(
    commands: list[list[str]], *, cwd: Path, timeout: int, workers: int = 4
) -> list[dict[str, object]]:
    """Run independent offline checks concurrently while preserving order."""
    if not commands:
        return []
    worker_count = min(max(1, workers), len(commands))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [
            executor.submit(run_check, command, cwd=cwd, timeout=timeout)
            for command in commands
        ]
        return [future.result() for future in futures]


def validate_module(project_root: Path, module: Path, *, full: bool) -> dict[str, object]:
    root = project_root.resolve()
    modules_dir = root / "modules"
    path = module if module.is_absolute() else root / module
    path = path.resolve()
    if path.parent != modules_dir or not NAME_RE.fullmatch(path.name):
        raise ValueError("module must be modules/<snake_case_name>.py")
    if not path.is_file():
        raise ValueError(f"module not found: {path}")

    source_errors = analyze_source(path.read_text(encoding="utf-8"))
    tree = ast.parse(path.read_text(encoding="utf-8")) if not source_errors else None
    registry_path = root / "scripts" / "userbot_module_registry.py"
    if tree is not None and _register_is_inert(tree):
        registered = _registered_modules(registry_path) if registry_path.is_file() else set()
        if path.name not in registered:
            source_errors.append(
                f"direct CLI module is missing from registry: {path.name}"
            )
    test_path = root / "tests" / f"test_{path.stem}.py"
    if not test_path.is_file():
        source_errors.append(f"missing focused test: tests/{test_path.name}")

    with tempfile.TemporaryDirectory(prefix="userbot_module_check_") as tmp:
        try:
            py_compile.compile(
                str(path),
                cfile=str(Path(tmp) / f"{path.stem}.pyc"),
                doraise=True,
            )
        except py_compile.PyCompileError as exc:
            source_errors.append(str(exc))

    checks: list[dict[str, object]] = []
    if not source_errors:
        checks.append(
            run_check(
                [sys.executable, str(registry_path), "--validate-catalog", "--json"],
                cwd=root,
                timeout=10,
            )
        )
        checks.append(run_check([sys.executable, str(path), "--help"], cwd=root, timeout=10))
        checks.append(
            run_check(
                [
                    sys.executable,
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "tests",
                    "-p",
                    test_path.name,
                    "-v",
                ],
                cwd=root,
                timeout=120,
            )
        )
    if full and not source_errors and all(item["returncode"] == 0 for item in checks):
        checks.append(
            run_check(
                [
                    sys.executable,
                    "-m",
                    "compileall",
                    "-q",
                    ".",
                    "-x",
                    r"/(venv|\.git|__pycache__)/",
                ],
                cwd=root,
                timeout=120,
            )
        )
        help_commands = [
            [sys.executable, str(candidate), "--help"]
            for candidate in sorted(modules_dir.glob("*.py"))
            if not candidate.name.startswith("__")
        ]
        checks.extend(
            run_checks_parallel(help_commands, cwd=root, timeout=10, workers=4)
        )
        if all(item["returncode"] == 0 for item in checks):
            checks.extend(
                [
                    run_check(
                        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                        cwd=root,
                        timeout=300,
                    ),
                    run_check([sys.executable, "-m", "pip", "check"], cwd=root, timeout=60),
                ]
            )

    failed_commands = [item for item in checks if item["returncode"] != 0]
    return {
        "ok": not source_errors and not failed_commands,
        "module": str(path.relative_to(root)),
        "source_errors": source_errors,
        "checks": checks,
        "full": full,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Offline quality gate for one userbot module")
    result.add_argument("module", type=Path, help="modules/<name>.py")
    result.add_argument(
        "--project-root", type=Path, default=Path(__file__).resolve().parent.parent
    )
    result.add_argument("--full", action="store_true", help="Also run the full suite and pip check")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        payload = validate_module(
            args.project_root.expanduser(), args.module.expanduser(), full=args.full
        )
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        payload = {"ok": False, "error": type(exc).__name__, "message": str(exc)}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
