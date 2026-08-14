from __future__ import annotations

from typing import Any


def parse_peer(value: str) -> str | int:
    value = value.strip()
    if not value:
        raise ValueError("Telegram target cannot be empty")
    return int(value) if value.lstrip("-").isdigit() else value


def entity_payload(entity: Any, *, input_value: str | None = None) -> dict[str, Any]:
    first_name = getattr(entity, "first_name", None)
    last_name = getattr(entity, "last_name", None)
    display_name = " ".join(part for part in (first_name, last_name) if part).strip()
    display_name = display_name or getattr(entity, "title", None) or getattr(entity, "username", None)
    return {
        "input": input_value,
        "id": getattr(entity, "id", None),
        "title_or_name": display_name,
        "username": getattr(entity, "username", None),
        "type": type(entity).__name__,
    }


async def resolve_entity(client: Any, value: str) -> Any:
    """Resolve a username/link directly or a numeric peer via authorized dialogs."""
    peer = parse_peer(value)
    try:
        return await client.get_entity(peer)
    except (TypeError, ValueError) as exc:
        if not isinstance(peer, int):
            raise

        matches: list[Any] = []
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            if peer in {getattr(dialog, "id", None), getattr(entity, "id", None)}:
                matches.append(entity)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(f"Numeric peer {value!r} is ambiguous in authorized dialogs") from exc
        raise ValueError(f"Numeric peer {value!r} was not found in authorized dialogs") from exc


def parse_message_ids(value: str) -> list[int]:
    try:
        result = [int(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise ValueError("Message IDs must be comma-separated positive integers") from exc
    if not result or any(item <= 0 for item in result):
        raise ValueError("At least one positive message ID is required")
    if len(set(result)) != len(result):
        raise ValueError("Message IDs must not contain duplicates")
    return result
