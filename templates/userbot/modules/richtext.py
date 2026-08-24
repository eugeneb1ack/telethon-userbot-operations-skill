from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import json
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from telethon import TelegramClient, errors
from telethon.extensions import html as telethon_html

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import apply_runtime_env, load_settings
from core.telegram_targets import entity_payload, resolve_entity


SUPPORTED_TAGS = {
    "a",
    "b",
    "blockquote",
    "code",
    "del",
    "em",
    "i",
    "pre",
    "s",
    "strong",
    "tg-emoji",
    "u",
}

AUTO_ENTITY_TYPES = {
    "MessageEntityUrl",
    "MessageEntityEmail",
    "MessageEntityPhone",
    "MessageEntityMention",
    "MessageEntityHashtag",
    "MessageEntityCashtag",
    "MessageEntityBotCommand",
}


class TelegramHTMLValidator(HTMLParser):
    """Reject HTML that Telethon's Telegram entity parser does not support."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.errors: list[str] = []

    def _fail(self, message: str) -> None:
        self.errors.append(message)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag not in SUPPORTED_TAGS:
            self._fail(f"unsupported HTML tag <{tag}>")
            return

        attr_map = {key.casefold(): value for key, value in attrs}
        if tag in {"b", "strong", "em", "i", "u", "del", "s", "pre"}:
            if attr_map:
                self._fail(f"HTML tag <{tag}> does not accept attributes")
        elif tag == "blockquote":
            if set(attr_map) != ({"expandable"} if "expandable" in attr_map else set()):
                self._fail("<blockquote> accepts only the boolean expandable attribute")
        elif tag == "a":
            if set(attr_map) != {"href"} or not attr_map.get("href"):
                self._fail("<a> requires exactly one non-empty href attribute")
        elif tag == "tg-emoji":
            if set(attr_map) != {"emoji-id"}:
                self._fail("<tg-emoji> requires exactly one emoji-id attribute")
            else:
                try:
                    if int(attr_map["emoji-id"] or "0") <= 0:
                        raise ValueError
                except ValueError:
                    self._fail("<tg-emoji emoji-id> must be a positive integer")
        elif tag == "code":
            class_name = attr_map.get("class")
            if attr_map and (set(attr_map) != {"class"} or not class_name):
                self._fail("<code> accepts only an optional class=language-* inside <pre>")
            elif class_name and (
                not self.stack
                or self.stack[-1] != "pre"
                or not class_name.startswith("language-")
                or len(class_name) == len("language-")
            ):
                self._fail("code language class is allowed only as <pre><code class=language-...>")

        self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._fail(f"self-closing HTML tag <{tag}> is not supported")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if not self.stack:
            self._fail(f"unexpected closing tag </{tag}>")
            return
        if self.stack[-1] != tag:
            self._fail(f"closing tag </{tag}> does not match <{self.stack[-1]}>")
            return
        self.stack.pop()

    def close(self) -> None:
        super().close()
        if self.stack:
            self._fail("unclosed HTML tags: " + ", ".join(self.stack))


def parse_richtext(source: str) -> tuple[str, list[Any]]:
    """Validate Telegram HTML and return the plain text plus expected entities."""
    if not source:
        raise ValueError("rich-text source cannot be empty")

    validator = TelegramHTMLValidator()
    validator.feed(source)
    validator.close()
    if validator.errors:
        raise ValueError("; ".join(validator.errors))

    text, entities = telethon_html.parse(source)
    if not text:
        raise ValueError("rich-text source produces an empty Telegram message")
    return text, entities


def _json_default(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.hex()
    return repr(value)


def entity_signature(entities: list[Any] | tuple[Any, ...] | None) -> tuple[str, ...]:
    """Create a stable, JSON-safe signature for Telegram message entities."""
    result: list[str] = []
    for entity in entities or []:
        if hasattr(entity, "to_dict"):
            payload = entity.to_dict()
        else:
            payload = {
                key: value
                for key, value in vars(entity).items()
                if not key.startswith("_")
            }
        result.append(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=_json_default))
    return tuple(result)


def _entity_type(signature: str) -> str:
    payload = json.loads(signature)
    return str(payload.get("_", "")).rsplit(".", 1)[-1]


def verify_entities(expected: list[Any] | tuple[Any, ...] | None, actual: list[Any] | tuple[Any, ...] | None) -> dict[str, Any]:
    """Verify explicit entities while allowing Telegram's automatic link entities."""
    expected_counts = Counter(entity_signature(expected))
    actual_counts = Counter(entity_signature(actual))
    missing = expected_counts - actual_counts
    extras = actual_counts - expected_counts
    unexpected = {
        signature: count
        for signature, count in extras.items()
        if _entity_type(signature) not in AUTO_ENTITY_TYPES
    }
    allowed_auto = {
        signature: count
        for signature, count in extras.items()
        if _entity_type(signature) in AUTO_ENTITY_TYPES
    }
    return {
        "verified": not missing and not unexpected,
        "missing": dict(missing),
        "unexpected": unexpected,
        "allowed_auto": allowed_auto,
    }


def entity_summary(entities: list[Any] | tuple[Any, ...] | None) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for entity in entities or []:
        row = {
            "type": type(entity).__name__,
            "offset": getattr(entity, "offset", None),
            "length": getattr(entity, "length", None),
        }
        language = getattr(entity, "language", None)
        url = getattr(entity, "url", None)
        if language:
            row["language"] = language
        if url:
            row["url"] = url
        summary.append(row)
    return summary


def message_payload(message: Any) -> dict[str, Any]:
    text = getattr(message, "message", None) or ""
    return {
        "id": getattr(message, "id", None),
        "outgoing": bool(getattr(message, "out", False)),
        "has_media": bool(getattr(message, "media", None)),
        "text_preview": text.replace("\n", " ")[:160],
        "text_length": len(text),
        "entities": entity_summary(getattr(message, "entities", None)),
    }


def _read_source(args: argparse.Namespace) -> str:
    if (args.text is None) == (args.file is None):
        raise ValueError("provide exactly one of --text or --file")
    if args.file:
        path = Path(args.file).expanduser()
        if not path.is_file():
            raise ValueError(f"rich-text file not found: {path}")
        return path.read_text(encoding="utf-8")
    return args.text


async def _edit_with_one_flood_retry(
    client: TelegramClient,
    entity: Any,
    message_id: int,
    source: str,
    *,
    link_preview: bool,
) -> Any:
    for attempt in range(2):
        try:
            return await client.edit_message(
                entity,
                message_id,
                source,
                parse_mode="html",
                link_preview=link_preview,
            )
        except errors.FloodWaitError as exc:
            if attempt:
                raise
            await asyncio.sleep(exc.seconds + 1)
    raise RuntimeError("unreachable")


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.message_id <= 0:
        raise ValueError("--message-id must be positive")

    source = _read_source(args)
    plain_text, expected_entities = parse_richtext(source)
    settings = load_settings(args.account)
    apply_runtime_env(settings)
    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized; refusing interactive login")
        entity = await resolve_entity(client, args.chat)
        before = await client.get_messages(entity, ids=args.message_id)
        if before is None or getattr(before, "id", None) != args.message_id:
            raise ValueError(f"Message {args.message_id} was not found in the resolved chat")
        if not getattr(before, "out", False):
            raise PermissionError("Refusing to edit a message that is not outgoing from this account")

        plan: dict[str, Any] = {
            "ok": True,
            "dry_run": not args.execute,
            "chat": entity_payload(entity, input_value=args.chat),
            "before": message_payload(before),
            "requested": {
                "message_id": args.message_id,
                "parse_mode": "html",
                "source": source,
                "text_preview": plain_text.replace("\n", " ")[:160],
                "text_length": len(plain_text),
                "entities": entity_summary(expected_entities),
                "link_preview": not args.no_link_preview,
            },
        }
        if not args.execute:
            return plan

        try:
            edited = await _edit_with_one_flood_retry(
                client,
                entity,
                args.message_id,
                source,
                link_preview=not args.no_link_preview,
            )
            status = "edited"
        except errors.MessageNotModifiedError:
            edited = before
            status = "noop_already_matches"

        after = await client.get_messages(entity, ids=args.message_id)
        text_verified = (
            getattr(after, "id", None) == args.message_id
            and getattr(after, "message", None) == plain_text
        )
        entity_verification = verify_entities(expected_entities, getattr(after, "entities", None))
        entities_verified = entity_verification["verified"]
        verified = text_verified and entities_verified
        plan.update(
            {
                "dry_run": False,
                "status": status,
                "returned_message_id": getattr(edited, "id", None),
                "after": message_payload(after),
                "verification": {
                    "text": text_verified,
                    "entities": entities_verified,
                    "verified": verified,
                    "entity_details": entity_verification,
                },
            }
        )
        return plan
    finally:
        await client.disconnect()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Edit one outgoing Telegram message using Telethon HTML rich text"
    )
    result.add_argument("--account", default="main")
    result.add_argument("--chat", required=True, help="Exact username/link or numeric dialog/entity ID")
    result.add_argument("--message-id", required=True, type=int)
    source = result.add_mutually_exclusive_group(required=True)
    source.add_argument("--text", help="Telegram HTML source")
    source.add_argument("--file", help="UTF-8 file containing Telegram HTML source")
    result.add_argument("--no-link-preview", action="store_true")
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
        print(f"richtext failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json_output else json.dumps(result, ensure_ascii=False))
    verification = result.get("verification")
    if verification is not None and not verification.get("verified"):
        return 1
    return 0


def register(client: TelegramClient) -> None:
    """Direct CLI helper; no event handler is attached."""


if __name__ == "__main__":
    raise SystemExit(main())
