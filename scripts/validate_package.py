#!/usr/bin/env python3
"""Validate the portable userbot skill package without network or Telegram I/O."""

from __future__ import annotations

import py_compile
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "userbot"
REQUIRED = (
    "SKILL.md",
    "README.md",
    "README.ru.md",
    "INSTALL.md",
    "INSTALL.ru.md",
    "SECURITY.md",
    ".gitignore",
    "assets/telegram-userbot-skill-manga.png",
    "references/operation-playbook.md",
    "references/gateway-webhooks.md",
    "references/channel-rich-publishing.md",
    "references/module-authoring.md",
    "references/session-bootstrap.md",
    "scripts/bootstrap_userbot_project.py",
    "scripts/telethon_api_inventory.py",
    "scripts/verify_userbot_session.py",
    "scripts/test_bootstrap_userbot_project.py",
    "scripts/test_interactive_account_setup.py",
    "scripts/test_session_checker.py",
    "templates/userbot/main.py",
    "templates/userbot/run.sh",
    "templates/userbot/requirements.txt",
    "templates/userbot/AGENTS.md",
    "templates/userbot/MODULES.md",
    "templates/userbot/ACCOUNT.env.example",
    "templates/userbot/core/config.py",
    "templates/userbot/core/event_store.py",
    "templates/userbot/core/gateway.py",
    "templates/userbot/core/runtime_lock.py",
    "templates/userbot/scripts/setup_gateway.py",
    "templates/userbot/scripts/install_gateway_service.py",
    "templates/userbot/scripts/setup_account.py",
    "templates/userbot/scripts/userbotctl.py",
    "templates/userbot/scripts/userbotd.py",
    "templates/userbot/scripts/userbotrun.py",
    "templates/userbot/scripts/check_module.py",
    "templates/userbot/scripts/userbot_module_registry.py",
)
FORBIDDEN_NAMES = {".env", "accounts", "runtime", "data", "venv", ".venv", "__pycache__"}
FORBIDDEN_SUFFIXES = {".session", ".session-journal", ".sqlite3", ".db"}
TEXT_SUFFIXES = {".py", ".md", ".sh", ".txt", ".toml", ".json", ".yaml", ".yml", ".example"}
SECRET_PATTERNS = (
    re.compile(r"\b\d{7,12}:[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"(?i)(?:api_hash|bot_token|password|phone_number)\s*=\s*[\"'][^\"']{12,}[\"']"),
)
LEGACY_RUNTIME_PATTERN = re.compile(rf"\b{''.join(('h', 'ermes'))}\b", re.IGNORECASE)
AI_CREDIT_FILES = {"README.md", "README.ru.md"}
AI_CREDIT_PHRASE = f"{''.join(('Her', 'mes'))} Agent"
SUMMARY_MEDIA_CONTRACT = (
    "## Owner-requested media-aware summaries",
    "`summarize_chat_native.py`",
    "`--metadata-only`",
    "`messages.TranscribeAudioRequest`",
    "`download_media.py`",
    "`view_image`",
    "Do not download, play, transcribe, or visually analyze video files.",
)


def source_files() -> list[Path]:
    return [
        path
        for path in sorted(ROOT.rglob("*.py"))
        if ".git" not in path.parts and "__pycache__" not in path.parts
    ]


def main() -> int:
    missing = [relative for relative in REQUIRED if not (ROOT / relative).is_file()]
    if missing:
        print(f"Missing required files: {', '.join(missing)}", file=sys.stderr)
        return 1

    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    frontmatter, separator, _ = skill.partition("\n---\n")
    frontmatter_lines = frontmatter.splitlines()
    expected_frontmatter_keys = ["name", "description"]
    actual_frontmatter_keys = [line.partition(":")[0] for line in frontmatter_lines[1:] if line]
    if (
        frontmatter_lines[:1] != ["---"]
        or not separator
        or actual_frontmatter_keys != expected_frontmatter_keys
        or frontmatter_lines[1] != "name: userbot"
        or not frontmatter_lines[2].partition(":")[2].strip()
    ):
        print("SKILL.md frontmatter must contain name=userbot and a description only", file=sys.stderr)
        return 1

    missing_summary_contract = [
        required for required in SUMMARY_MEDIA_CONTRACT if required not in skill
    ]
    if missing_summary_contract:
        print(
            "SKILL.md is missing the owner-summary media contract: "
            + ", ".join(missing_summary_contract),
            file=sys.stderr,
        )
        return 1

    blocked = []
    secret_hits = []
    legacy_runtime_hits = []
    for path in ROOT.rglob("*"):
        if ".git" in path.parts:
            continue
        if path.name in FORBIDDEN_NAMES or path.suffix in FORBIDDEN_SUFFIXES:
            blocked.append(str(path.relative_to(ROOT)))
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            secret_hits.append(str(path.relative_to(ROOT)))
            continue
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            secret_hits.append(str(path.relative_to(ROOT)))
        legacy_scan_text = text
        if str(path.relative_to(ROOT)) in AI_CREDIT_FILES:
            legacy_scan_text = legacy_scan_text.replace(AI_CREDIT_PHRASE, "")
        if LEGACY_RUNTIME_PATTERN.search(legacy_scan_text):
            legacy_runtime_hits.append(str(path.relative_to(ROOT)))
    if blocked:
        print(f"Forbidden runtime or secret-bearing paths: {', '.join(sorted(blocked))}", file=sys.stderr)
        return 1
    if secret_hits:
        print(f"Potential credential values in package: {', '.join(sorted(secret_hits))}", file=sys.stderr)
        return 1
    if legacy_runtime_hits:
        print(f"Legacy runtime references in package: {', '.join(sorted(legacy_runtime_hits))}", file=sys.stderr)
        return 1

    template_modules = list((TEMPLATE / "modules").glob("*.py"))
    if len(template_modules) < 20:
        print("Template module catalog is unexpectedly incomplete", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="userbot_skill_validate_") as tmp:
        compile_dir = Path(tmp)
        for script in source_files():
            relative = script.relative_to(ROOT)
            output = compile_dir / f"{relative}.c"
            output.parent.mkdir(parents=True, exist_ok=True)
            py_compile.compile(str(script), cfile=str(output), doraise=True)

    print("package_validation=ok")
    print(f"required_files={len(REQUIRED)}")
    print(f"template_modules={len(template_modules)}")
    print(f"python_sources={len(source_files())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
