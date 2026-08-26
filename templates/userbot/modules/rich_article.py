from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import mimetypes
import re
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from telethon import TelegramClient, errors, functions, types, utils

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import apply_runtime_env, load_settings
from core.telegram_targets import entity_payload, resolve_entity


MAX_SOURCE_CHARACTERS = 32_768
MAX_DUPLICATE_WINDOW = 100
MAX_MEDIA_ATTACHMENTS = 50
MEDIA_KINDS = {"photo", "video", "audio", "document"}
MEDIA_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MEDIA_TAG_KINDS = {
    "img": "photo",
    "video": "video",
    "audio": "audio",
    "tg-document": "document",
}
MARKDOWN_MEDIA_REFERENCE = re.compile(
    r"tg://(?P<kind>photo|video|audio|document)\?id=(?P<id>[A-Za-z0-9_-]{1,64})"
)

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


@dataclass(frozen=True)
class MediaSpec:
    """One local attachment referenced by a Rich HTML/Markdown media block."""

    id: str
    kind: str
    path: Path
    size_bytes: int
    sha256: str
    mime_type: str


@dataclass(frozen=True)
class MediaReference:
    id: str
    kind: str


@dataclass(frozen=True)
class UploadedMedia:
    id: str
    kind: str
    attachment_type: str
    telegram_media_id: int


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


class RichArticleMediaReferenceParser(HTMLParser):
    """Extract locally managed `tg://…?id=` references from Rich HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.errors: list[str] = []
        self.references: list[MediaReference] = []

    def _handle_media_tag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.casefold()
        expected_kind = MEDIA_TAG_KINDS.get(normalized)
        if expected_kind is None:
            return

        attributes = dict(attrs)
        src = attributes.get("src")
        if not src:
            self.errors.append(f"Rich media block <{tag}> requires a src attribute")
            return
        if not src.startswith("tg://"):
            return

        match = re.fullmatch(
            r"tg://(?P<kind>photo|video|audio|document)\?id=(?P<id>[A-Za-z0-9_-]{1,64})",
            src,
        )
        if match is None:
            self.errors.append(
                f"Rich media src {src!r} must use tg://photo|video|audio|document?id=<id>"
            )
            return

        kind = match.group("kind")
        if kind != expected_kind:
            self.errors.append(
                f"Rich media block <{tag}> must reference tg://{expected_kind}?id=…, not tg://{kind}?id=…"
            )
            return
        self.references.append(MediaReference(id=match.group("id"), kind=kind))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._handle_media_tag(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._handle_media_tag(tag, attrs)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_media_spec(value: str) -> MediaSpec:
    """Parse `--media id:kind:/absolute/or/relative/path` without guessing placement."""

    parts = value.split(":", 2)
    if len(parts) != 3:
        raise ValueError("--media must use id:photo|video|audio|document:path")
    media_id, kind, raw_path = (part.strip() for part in parts)
    if not MEDIA_ID_PATTERN.fullmatch(media_id):
        raise ValueError("--media id must contain 1-64 letters, digits, _ or -")
    if kind not in MEDIA_KINDS:
        raise ValueError("--media kind must be photo, video, audio or document")
    if not raw_path:
        raise ValueError("--media path must not be empty")

    path = Path(raw_path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"rich article media file not found: {path}")
    size_bytes = path.stat().st_size
    if size_bytes <= 0:
        raise ValueError(f"rich article media file is empty: {path}")

    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    expected_prefix = {"photo": "image/", "video": "video/", "audio": "audio/"}.get(kind)
    if expected_prefix is not None and not mime_type.startswith(expected_prefix):
        raise ValueError(f"--media kind {kind!r} does not match file MIME type {mime_type!r}")
    return MediaSpec(
        id=media_id,
        kind=kind,
        path=path,
        size_bytes=size_bytes,
        sha256=file_sha256(path),
        mime_type=mime_type,
    )


def freeze_media_specs(values: list[str] | tuple[str, ...]) -> list[MediaSpec]:
    if len(values) > MAX_MEDIA_ATTACHMENTS:
        raise ValueError(f"a rich article supports at most {MAX_MEDIA_ATTACHMENTS} media attachments")
    specs = [parse_media_spec(value) for value in values]
    duplicates = sorted({spec.id for spec in specs if sum(item.id == spec.id for item in specs) > 1})
    if duplicates:
        raise ValueError("--media ids must be unique: " + ", ".join(duplicates))
    return specs


def extract_media_references(source: str, source_format: str) -> list[MediaReference]:
    if source_format == "markdown":
        return [
            MediaReference(id=match.group("id"), kind=match.group("kind"))
            for match in MARKDOWN_MEDIA_REFERENCE.finditer(source)
        ]

    parser = RichArticleMediaReferenceParser()
    parser.feed(source)
    parser.close()
    if parser.errors:
        raise ValueError("; ".join(parser.errors))
    return parser.references


def validate_media_references(
    source: str,
    source_format: str,
    media_specs: list[MediaSpec],
) -> list[MediaReference]:
    references = extract_media_references(source, source_format)
    if len(references) > MAX_MEDIA_ATTACHMENTS:
        raise ValueError(f"a rich article supports at most {MAX_MEDIA_ATTACHMENTS} media blocks")
    specs_by_id = {spec.id: spec for spec in media_specs}
    for reference in references:
        spec = specs_by_id.get(reference.id)
        if spec is None:
            raise ValueError(
                f"rich media reference tg://{reference.kind}?id={reference.id} has no matching --media file"
            )
        if spec.kind != reference.kind:
            raise ValueError(
                f"rich media reference id {reference.id!r} expects {reference.kind}, "
                f"but --media declares {spec.kind}"
            )

    referenced_ids = {reference.id for reference in references}
    unused_ids = [spec.id for spec in media_specs if spec.id not in referenced_ids]
    if unused_ids:
        raise ValueError("every --media file must be embedded in the article: " + ", ".join(unused_ids))
    return references


def media_spec_payload(spec: MediaSpec) -> dict[str, Any]:
    return {
        "id": spec.id,
        "kind": spec.kind,
        "path": str(spec.path),
        "size_bytes": spec.size_bytes,
        "sha256": spec.sha256,
        "mime_type": spec.mime_type,
    }


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def validate_source(
    source: str,
    source_format: str,
    title: str,
    *,
    media_specs: list[MediaSpec] | None = None,
) -> list[MediaReference]:
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
    return validate_media_references(source, source_format, media_specs or [])


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


def build_rich_message(
    source: str,
    source_format: str,
    *,
    skip_entity_detection: bool,
    files: list[Any] | None = None,
) -> Any:
    kwargs = {"noautolink": True} if skip_entity_detection else {}
    if files:
        kwargs["files"] = files
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


def _media_ids(values: Any) -> set[int]:
    return {
        value
        for value in (getattr(item, "id", None) for item in values or ())
        if isinstance(value, int) and value > 0
    }


def embedded_media_payload(rich_message: Any, expected_media: list[UploadedMedia]) -> dict[str, Any]:
    photo_ids = _media_ids(getattr(rich_message, "photos", None))
    document_ids = _media_ids(getattr(rich_message, "documents", None))
    attachments = []
    for media in expected_media:
        actual_ids = photo_ids if media.attachment_type == "photo" else document_ids
        attachments.append(
            {
                "id": media.id,
                "kind": media.kind,
                "attachment_type": media.attachment_type,
                "present": media.telegram_media_id in actual_ids,
            }
        )
    return {
        "expected_count": len(expected_media),
        "rich_photo_count": len(photo_ids),
        "rich_document_count": len(document_ids),
        "attachments": attachments,
        "all_present": all(item["present"] for item in attachments),
    }


def article_payload(message: Any, title: str, expected_media: list[UploadedMedia]) -> dict[str, Any]:
    rich_message = getattr(message, "rich_message", None)
    text = rendered_text(message)
    return {
        "id": getattr(message, "id", None),
        "outgoing": bool(getattr(message, "out", False)),
        "has_rich_message": rich_message is not None,
        "rich_block_count": len(getattr(rich_message, "blocks", None) or ()),
        "title_present": normalize(title) in normalize(text),
        "text_preview": text.replace("\n", " ")[:160],
        "embedded_media": embedded_media_payload(rich_message, expected_media),
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


async def _with_one_flood_wait(operation: Any) -> Any:
    for attempt in range(2):
        try:
            return await operation()
        except errors.FloodWaitError as exc:
            if attempt:
                raise
            await asyncio.sleep(exc.seconds + 1)
    raise RuntimeError("unreachable")


def document_attributes(spec: MediaSpec) -> tuple[list[Any], str]:
    attributes, mime_type = utils.get_attributes(
        str(spec.path),
        force_document=spec.kind == "document",
        supports_streaming=spec.kind == "video",
    )
    if spec.kind == "document":
        attributes = [
            attribute
            for attribute in attributes
            if not isinstance(attribute, (types.DocumentAttributeAudio, types.DocumentAttributeVideo))
        ]
    elif spec.kind == "audio" and not any(
        isinstance(attribute, types.DocumentAttributeAudio) for attribute in attributes
    ):
        attributes.append(types.DocumentAttributeAudio(duration=0))
    elif spec.kind == "video" and not any(
        isinstance(attribute, types.DocumentAttributeVideo) for attribute in attributes
    ):
        attributes.append(types.DocumentAttributeVideo(duration=0, w=1, h=1, supports_streaming=True))
    return attributes, mime_type


def input_media_id(value: Any, *, kind: str) -> int:
    numeric_id = getattr(value, "id", None)
    if not isinstance(numeric_id, int) or numeric_id <= 0:
        raise RuntimeError(f"Telegram did not return an uploaded {kind} ID for rich article media")
    return numeric_id


async def upload_rich_media(
    client: TelegramClient,
    entity: Any,
    media_specs: list[MediaSpec],
) -> tuple[list[Any], list[UploadedMedia]]:
    """Upload local files to the target chat and bind them to one rich message."""

    rich_files: list[Any] = []
    uploaded_media: list[UploadedMedia] = []
    for spec in media_specs:
        uploaded_file = await _with_one_flood_wait(lambda: client.upload_file(str(spec.path)))
        if spec.kind == "photo":
            uploaded = await _with_one_flood_wait(
                lambda: client(
                    functions.messages.UploadMediaRequest(
                        peer=entity,
                        media=types.InputMediaUploadedPhoto(file=uploaded_file),
                    )
                )
            )
            photo = utils.get_input_photo(uploaded)
            telegram_media_id = input_media_id(photo, kind="photo")
            rich_files.append(types.InputRichFilePhoto(id=spec.id, photo=photo))
            uploaded_media.append(
                UploadedMedia(
                    id=spec.id,
                    kind=spec.kind,
                    attachment_type="photo",
                    telegram_media_id=telegram_media_id,
                )
            )
            continue

        attributes, mime_type = document_attributes(spec)
        uploaded = await _with_one_flood_wait(
            lambda: client(
                functions.messages.UploadMediaRequest(
                    peer=entity,
                    media=types.InputMediaUploadedDocument(
                        file=uploaded_file,
                        mime_type=mime_type,
                        attributes=attributes,
                        force_file=spec.kind == "document",
                    ),
                )
            )
        )
        document = utils.get_input_document(uploaded)
        telegram_media_id = input_media_id(document, kind="document")
        rich_files.append(types.InputRichFileDocument(id=spec.id, document=document))
        uploaded_media.append(
            UploadedMedia(
                id=spec.id,
                kind=spec.kind,
                attachment_type="document",
                telegram_media_id=telegram_media_id,
            )
        )
    return rich_files, uploaded_media


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if not 1 <= args.duplicate_window <= MAX_DUPLICATE_WINDOW:
        raise ValueError(f"--duplicate-window must be between 1 and {MAX_DUPLICATE_WINDOW}")

    source_path = Path(args.file).expanduser().resolve()
    if not source_path.is_file():
        raise ValueError(f"rich article source file not found: {source_path}")
    source = source_path.read_text(encoding="utf-8")
    media_specs = freeze_media_specs(getattr(args, "media", ()) or ())
    media_references = validate_source(
        source,
        args.format,
        args.title,
        media_specs=media_specs,
    )
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
                "media": [media_spec_payload(spec) for spec in media_specs],
                "media_references": [
                    {"id": reference.id, "kind": reference.kind}
                    for reference in media_references
                ],
            },
        }
        if not args.execute:
            return plan

        rich_files, uploaded_media = await upload_rich_media(client, entity, media_specs)
        rich_message = build_rich_message(
            source,
            args.format,
            skip_entity_detection=args.skip_entity_detection,
            files=rich_files,
        )
        updates = await _send_once(client, entity, rich_message)
        message_id = extract_message_id(updates)
        after = await client.get_messages(entity, ids=message_id)
        verification = article_payload(after, args.title, uploaded_media)
        verification["verified"] = (
            getattr(after, "id", None) == message_id
            and verification["outgoing"]
            and verification["has_rich_message"]
            and verification["rich_block_count"] > 0
            and verification["title_present"]
            and verification["embedded_media"]["all_present"]
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
    result.add_argument(
        "--media",
        action="append",
        default=[],
        metavar="ID:KIND:PATH",
        help="Local attachment embedded by tg://<kind>?id=<id>; KIND is photo, video, audio or document",
    )
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
