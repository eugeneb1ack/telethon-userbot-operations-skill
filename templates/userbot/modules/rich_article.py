from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from telethon import TelegramClient, errors, functions, types

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import apply_runtime_env, load_settings
from core.telegram_targets import entity_payload, resolve_entity


MAX_SOURCE_CHARACTERS = 32_768
MAX_DUPLICATE_WINDOW = 100

# This is the documented Rich HTML surface, rather than the much smaller
# Telegram HTML entity surface used by richtext.py. Attribute validation stays
# with Telegram because several tags have context-dependent attributes.
RICH_HTML_TAGS = {
    "a",
    "aside",
    "audio",
    "b",
    "blockquote",
    "br",
    "caption",
    "cite",
    "code",
    "del",
    "details",
    "em",
    "figure",
    "figcaption",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "img",
    "input",
    "ins",
    "li",
    "mark",
    "ol",
    "p",
    "pre",
    "s",
    "strike",
    "strong",
    "sub",
    "summary",
    "sup",
    "table",
    "tbody",
    "td",
    "tfoot",
    "tg-button",
    "tg-button-row",
    "tg-collage",
    "tg-document",
    "tg-emoji",
    "tg-map",
    "tg-math",
    "tg-math-block",
    "tg-reference",
    "tg-slideshow",
    "tg-spoiler",
    "tg-time",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
    "video",
}
VOID_RICH_HTML_TAGS = {"br", "hr", "img", "input", "tg-map"}
TEXT_FIELDS = {"caption", "credit", "text", "title"}


class RichArticleHTMLValidator(HTMLParser):
    """Validate tag names and nesting before Telegram parses a Rich HTML article."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.errors: list[str] = []
        self.stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.casefold()
        if normalized not in RICH_HTML_TAGS:
            self.errors.append(f"unsupported Rich HTML tag <{tag}>")
            return
        if normalized not in VOID_RICH_HTML_TAGS:
            self.stack.append(normalized)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.casefold()
        if normalized not in RICH_HTML_TAGS:
            self.errors.append(f"unsupported Rich HTML tag <{tag}/>")
        elif normalized not in VOID_RICH_HTML_TAGS:
            self.errors.append(f"Rich HTML tag <{tag}> must have an explicit closing tag")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if normalized in VOID_RICH_HTML_TAGS:
            self.errors.append(f"void Rich HTML tag <{tag}> must not have a closing tag")
        elif not self.stack:
            self.errors.append(f"unexpected closing Rich HTML tag </{tag}>")
        elif self.stack[-1] != normalized:
            self.errors.append(f"closing Rich HTML tag </{tag}> does not match <{self.stack[-1]}>")
        else:
            self.stack.pop()

    def close(self) -> None:
        super().close()
        if self.stack:
            self.errors.append("unclosed Rich HTML tags: " + ", ".join(self.stack))


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def validate_source(source: str, source_format: str, title: str) -> None:
    if not source.strip():
        raise ValueError("rich article source cannot be empty")
    if len(source) > MAX_SOURCE_CHARACTERS:
        raise ValueError(f"rich article source exceeds {MAX_SOURCE_CHARACTERS} characters")
    if not title.strip():
        raise ValueError("--title must not be empty")

    if source_format == "html":
        validator = RichArticleHTMLValidator()
        validator.feed(source)
        validator.close()
        if validator.errors:
            raise ValueError("; ".join(validator.errors))

    visible = normalize(visible_source_text(source, source_format))
    if normalize(title) not in visible:
        raise ValueError("--title must occur in the visible rich article source")


class _VisibleHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def visible_source_text(source: str, source_format: str) -> str:
    if source_format == "html":
        parser = _VisibleHTML()
        parser.feed(source)
        parser.close()
        return " ".join(parser.parts)
    return re.sub(r"[`*_~#\[\]()<>]", " ", source)


def _value_text(value: Any, *, field: str | None = None) -> list[str]:
    if isinstance(value, str):
        return [value] if field in TEXT_FIELDS else []
    if isinstance(value, dict):
        return [part for key, child in value.items() for part in _value_text(child, field=key)]
    if isinstance(value, (list, tuple)):
        return [part for child in value for part in _value_text(child, field=field)]
    if hasattr(value, "to_dict"):
        return _value_text(value.to_dict())
    if hasattr(value, "__dict__"):
        return _value_text(vars(value))
    return []


def rendered_text(message: Any) -> str:
    chunks = [getattr(message, "message", None) or ""]
    rich_message = getattr(message, "rich_message", None)
    if rich_message is not None:
        chunks.extend(_value_text(rich_message))
    return " ".join(chunks)


def build_rich_message(source: str, source_format: str, *, skip_entity_detection: bool) -> Any:
    kwargs = {"noautolink": True} if skip_entity_detection else {}
    if source_format == "html":
        return types.InputRichMessageHTML(html=source, **kwargs)
    return types.InputRichMessageMarkdown(markdown=source, **kwargs)


def extract_message_id(updates: Any) -> int:
    for update in getattr(updates, "updates", ()):
        message = getattr(update, "message", None)
        message_id = getattr(message, "id", None)
        if isinstance(message_id, int) and message_id > 0:
            return message_id
    direct_id = getattr(updates, "id", None)
    if isinstance(direct_id, int) and direct_id > 0:
        return direct_id
    raise RuntimeError("Telegram did not return a rich article message ID")


def article_payload(message: Any, title: str) -> dict[str, Any]:
    rich_message = getattr(message, "rich_message", None)
    text = rendered_text(message)
    return {
        "id": getattr(message, "id", None),
        "outgoing": bool(getattr(message, "out", False)),
        "has_rich_message": rich_message is not None,
        "rich_block_count": len(getattr(rich_message, "blocks", None) or ()),
        "title_present": normalize(title) in normalize(text),
        "text_preview": text.replace("\n", " ")[:160],
    }


async def recent_duplicate(
    client: TelegramClient,
    entity: Any,
    title: str,
    limit: int,
) -> int | None:
    target = normalize(title)
    async for message in client.iter_messages(entity, limit=limit):
        if target in normalize(rendered_text(message)):
            return getattr(message, "id", None)
    return None


async def _send_once(client: TelegramClient, entity: Any, rich_message: Any) -> Any:
    request = functions.messages.SendMessageRequest(
        peer=entity,
        message="",
        rich_message=rich_message,
    )
    for attempt in range(2):
        try:
            return await client(request)
        except errors.FloodWaitError as exc:
            if attempt:
                raise
            await asyncio.sleep(exc.seconds + 1)
    raise RuntimeError("unreachable")


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if not 1 <= args.duplicate_window <= MAX_DUPLICATE_WINDOW:
        raise ValueError(f"--duplicate-window must be between 1 and {MAX_DUPLICATE_WINDOW}")

    source_path = Path(args.file).expanduser().resolve()
    if not source_path.is_file():
        raise ValueError(f"rich article source file not found: {source_path}")
    source = source_path.read_text(encoding="utf-8")
    validate_source(source, args.format, args.title)
    rich_message = build_rich_message(
        source,
        args.format,
        skip_entity_detection=args.skip_entity_detection,
    )

    settings = load_settings(args.account)
    apply_runtime_env(settings)
    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized; refusing interactive login")
        entity = await resolve_entity(client, args.chat)
        if not getattr(entity, "broadcast", False):
            raise ValueError("rich articles may be published only to a broadcast channel")

        duplicate_id = None
        if not args.allow_duplicate:
            duplicate_id = await recent_duplicate(client, entity, args.title, args.duplicate_window)
            if duplicate_id is not None:
                raise FileExistsError(
                    f"a recent rich article already contains this title (message_id={duplicate_id}); "
                    "use a distinct title or explicit --allow-duplicate"
                )

        target = entity_payload(entity, input_value=args.chat)
        target["broadcast"] = True
        plan: dict[str, Any] = {
            "ok": True,
            "dry_run": not args.execute,
            "account": settings.account,
            "target": target,
            "requested": {
                "title": args.title,
                "format": args.format,
                "source_path": str(source_path),
                "source_characters": len(source),
                "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
                "skip_entity_detection": args.skip_entity_detection,
                "duplicate_window": args.duplicate_window,
                "allow_duplicate": args.allow_duplicate,
                "rich_message_type": type(rich_message).__name__,
            },
        }
        if not args.execute:
            return plan

        updates = await _send_once(client, entity, rich_message)
        message_id = extract_message_id(updates)
        after = await client.get_messages(entity, ids=message_id)
        verification = article_payload(after, args.title)
        verification["verified"] = (
            getattr(after, "id", None) == message_id
            and verification["outgoing"]
            and verification["has_rich_message"]
            and verification["rich_block_count"] > 0
            and verification["title_present"]
        )
        plan.update(
            {
                "dry_run": False,
                "status": "sent",
                "message_id": message_id,
                "verification": verification,
            }
        )
        return plan
    finally:
        await client.disconnect()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Publish one structured Telegram Rich Message article to a broadcast channel"
    )
    result.add_argument("--account", default="main")
    result.add_argument("--chat", required=True, help="Exact broadcast-channel username/link or numeric ID")
    result.add_argument("--title", required=True, help="Exact visible title, used for verification and duplicate protection")
    result.add_argument("--file", required=True, help="UTF-8 Rich HTML or Rich Markdown source file")
    result.add_argument("--format", choices=("html", "markdown"), default="html")
    result.add_argument("--skip-entity-detection", action="store_true")
    result.add_argument("--duplicate-window", type=int, default=25)
    result.add_argument("--allow-duplicate", action="store_true")
    result.add_argument("--execute", action="store_true")
    result.add_argument("--json", action="store_true", dest="json_output")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        result = asyncio.run(run(args))
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"rich_article failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json_output else json.dumps(result, ensure_ascii=False))
    verification = result.get("verification")
    return 0 if verification is None or verification.get("verified") else 1


def register(client: TelegramClient) -> None:
    """Direct CLI helper; no event handler is attached."""


if __name__ == "__main__":
    raise SystemExit(main())
