#!/usr/bin/env python3
"""Offline quality gate for one newly written Telethon module."""

from __future__ import annotations

import argparse
import ast
import json
import py_compile
import re
import subprocess
import sys
import tempfile
from pathlib import Path


NAME_RE = re.compile(r"[a-z][a-z0-9_]*\.py\Z")


def analyze_source(source: str) -> list[str]:
    errors: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"syntax error: {exc.msg} at line {exc.lineno}"]

    functions = {
        node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if "register" not in functions:
        errors.append("missing register(client)")

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

    if "TelegramClient" in source:
        required = (
            "load_settings",
            "apply_runtime_env",
            "is_user_authorized",
            "disconnect",
        )
        missing = [name for name in required if name not in source]
        if missing:
            errors.append("direct helper is missing: " + ", ".join(missing))

    if "--execute" in source:
        if "action=\"store_true\"" not in source and "action='store_true'" not in source:
            errors.append("--execute must be a boolean store_true flag")
        if "dry_run" not in source:
            errors.append("Telegram write CLI must return an explicit dry_run plan")

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
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    return {
        "command": command,
        "returncode": completed.returncode,
        "output": completed.stdout[-4000:],
    }


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
        for candidate in sorted(modules_dir.glob("*.py")):
            if candidate.name.startswith("__"):
                continue
            checks.append(
                run_check([sys.executable, str(candidate), "--help"], cwd=root, timeout=10)
            )
            if checks[-1]["returncode"] != 0:
                break
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
