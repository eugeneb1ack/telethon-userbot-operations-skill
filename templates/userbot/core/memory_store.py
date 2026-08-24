from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MEMORY_ITEM_SCHEMA = "userbot_memory_item.v1"
MEMORY_DATABASE_NAME = "userbot_memory.sqlite3"
LEGACY_SUMMARY_DATABASE_NAME = "summary_memory.sqlite3"
MAX_ITEM_BYTES = 16 * 1024
MAX_ITEMS_PER_SCOPE = 128
MAX_ITEMS_PER_ACCOUNT = 1024
MAX_RECALL_LIMIT = 20
MAX_TAGS = 16
ALLOWED_KINDS = {
    "fact",
    "preference",
    "decision",
    "procedure",
    "entity_context",
    "task_result",
}
ALLOWED_VALIDITY = {"stable", "temporal", "historical"}
FORBIDDEN_FIELD_NAMES = {
    "api_hash",
    "auth_code",
    "auth_token",
    "bot_token",
    "login_code",
    "password",
    "phone_number",
    "raw_message",
    "raw_messages",
    "refresh_token",
    "session",
    "session_string",
    "transcript",
    "transcripts",
    "two_factor_password",
}
_SCOPE_RE = re.compile(r"[A-Za-z0-9_.:@/-]{1,160}\Z")
_KEY_RE = re.compile(r"[A-Za-z0-9_.:@/-]{1,160}\Z")
_ACCOUNT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def memory_database_path(data_dir: str | Path) -> Path:
    data_path = Path(data_dir)
    preferred = data_path / MEMORY_DATABASE_NAME
    legacy = data_path / LEGACY_SUMMARY_DATABASE_NAME
    if preferred.exists() or not legacy.exists():
        return preferred
    return legacy


def normalize_memory_account(value: str | None) -> str:
    account = (value or "").strip()
    if not _ACCOUNT_RE.fullmatch(account):
        raise ValueError(
            "memory account must start with an ASCII letter or digit and contain only letters, "
            "digits, _ or -"
        )
    return account


def connect_memory_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    new_database = not path.exists()
    connection = sqlite3.connect(path, timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA wal_autocheckpoint=200")
    connection.execute("PRAGMA journal_size_limit=1048576")
    if new_database:
        connection.execute("PRAGMA auto_vacuum=INCREMENTAL")
    secure_memory_files(path)
    return connection


def secure_memory_files(path: Path) -> None:
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        try:
            candidate.chmod(0o600)
        except OSError:
            pass


def _normalized_datetime(
    value: Any,
    *,
    field: str,
    default: str | None = None,
) -> str | None:
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError(f"memory field '{field}' must be an ISO datetime string or null")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"memory field '{field}' must be a valid ISO datetime") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"memory field '{field}' requires an explicit timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _reject_forbidden_fields(value: Any, *, path: str = "document") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = str(key).casefold().replace("-", "_")
            if normalized_key in FORBIDDEN_FIELD_NAMES:
                raise ValueError(f"memory field '{path}.{key}' is forbidden")
            _reject_forbidden_fields(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_forbidden_fields(item, path=f"{path}[{index}]")


def _stable_key(value: dict[str, Any]) -> str:
    supplied = value.get("key")
    if supplied is not None:
        if not isinstance(supplied, str) or not _KEY_RE.fullmatch(supplied):
            raise ValueError("memory key must be 1-160 safe ASCII characters")
        return supplied
    source = "\x1f".join(
        (
            str(value.get("kind") or "").casefold(),
            str(value.get("scope") or "").casefold(),
            str(value.get("subject") or "").casefold(),
        )
    ).encode("utf-8")
    return f"auto:{hashlib.sha256(source).hexdigest()[:32]}"


def normalize_memory_item(
    value: Any,
    *,
    default_observed_at: str | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("memory document must be a JSON object")
    _reject_forbidden_fields(value)

    schema = value.get("schema") or MEMORY_ITEM_SCHEMA
    if schema != MEMORY_ITEM_SCHEMA:
        raise ValueError(f"memory schema must be {MEMORY_ITEM_SCHEMA}")
    kind = value.get("kind")
    if kind not in ALLOWED_KINDS:
        raise ValueError(f"memory kind must be one of {sorted(ALLOWED_KINDS)}")
    validity = value.get("validity")
    if validity not in ALLOWED_VALIDITY:
        raise ValueError(f"memory validity must be one of {sorted(ALLOWED_VALIDITY)}")

    scope = value.get("scope")
    if not isinstance(scope, str) or not _SCOPE_RE.fullmatch(scope):
        raise ValueError("memory scope must be 1-160 safe ASCII characters")
    subject = value.get("subject")
    if not isinstance(subject, str) or not subject.strip() or len(subject.strip()) > 200:
        raise ValueError("memory subject must be a non-empty string of at most 200 characters")
    summary = value.get("summary")
    if not isinstance(summary, str) or not summary.strip() or len(summary.strip()) > 4000:
        raise ValueError("memory summary must be a non-empty string of at most 4000 characters")

    details = value.get("details", {})
    if not isinstance(details, (dict, list)):
        raise ValueError("memory details must be a JSON object or list")
    provenance = value.get("provenance", {})
    if not isinstance(provenance, dict):
        raise ValueError("memory provenance must be a JSON object")
    source = provenance.get("source")
    if not isinstance(source, str) or not source.strip() or len(source.strip()) > 500:
        raise ValueError("memory provenance.source must be a non-empty string of at most 500 characters")
    provenance = {**provenance, "source": source.strip()}

    raw_tags = value.get("tags", [])
    if not isinstance(raw_tags, list) or len(raw_tags) > MAX_TAGS:
        raise ValueError(f"memory tags must be a list with at most {MAX_TAGS} items")
    tags: list[str] = []
    for tag in raw_tags:
        if not isinstance(tag, str) or not tag.strip() or len(tag.strip()) > 48:
            raise ValueError("each memory tag must be a non-empty string of at most 48 characters")
        normalized_tag = tag.strip().casefold()
        if normalized_tag not in tags:
            tags.append(normalized_tag)

    confidence = value.get("confidence", 1.0)
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ValueError("memory confidence must be a number between 0 and 1")
    confidence = float(confidence)
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("memory confidence must be a number between 0 and 1")

    observed_at = _normalized_datetime(
        value.get("observed_at"),
        field="observed_at",
        default=default_observed_at or utc_now(),
    )
    expires_at = _normalized_datetime(value.get("expires_at"), field="expires_at")
    if validity == "temporal" and expires_at is None:
        raise ValueError("temporal memory requires expires_at")
    if expires_at is not None and _parse_datetime(expires_at) <= datetime.now(timezone.utc):
        raise ValueError("memory expires_at must be in the future")

    normalized = {
        "schema": MEMORY_ITEM_SCHEMA,
        "key": _stable_key(value),
        "kind": kind,
        "scope": scope,
        "subject": subject.strip(),
        "summary": summary.strip(),
        "details": details,
        "tags": tags,
        "provenance": provenance,
        "validity": validity,
        "confidence": confidence,
        "observed_at": observed_at,
        "expires_at": expires_at,
    }
    serialized = _canonical_json(normalized).encode("utf-8")
    if len(serialized) > MAX_ITEM_BYTES:
        raise ValueError(f"memory document exceeds {MAX_ITEM_BYTES} bytes")
    return normalized


def memory_id(account: str, stable_key: str) -> str:
    digest = hashlib.sha256(f"{account}:{stable_key}".encode("utf-8")).hexdigest()
    return f"MEM-{digest[:16].upper()}"


class MemoryStore:
    """Bounded, structured semantic memory for reusable userbot knowledge."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.connection = connect_memory_database(path)
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS memory_items (
                id TEXT PRIMARY KEY,
                account TEXT NOT NULL,
                stable_key TEXT NOT NULL,
                kind TEXT NOT NULL,
                scope TEXT NOT NULL,
                subject TEXT NOT NULL,
                summary TEXT NOT NULL,
                details_json TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                provenance_json TEXT NOT NULL,
                validity TEXT NOT NULL,
                confidence REAL NOT NULL,
                observed_at TEXT NOT NULL,
                expires_at TEXT,
                content_hash TEXT NOT NULL,
                revision INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_accessed_at TEXT NOT NULL,
                access_count INTEGER NOT NULL DEFAULT 0,
                UNIQUE(account, stable_key)
            );
            CREATE INDEX IF NOT EXISTS memory_items_scope_idx
                ON memory_items(account, scope, last_accessed_at DESC);
            CREATE INDEX IF NOT EXISTS memory_items_expiry_idx
                ON memory_items(expires_at);
            CREATE INDEX IF NOT EXISTS memory_items_subject_idx
                ON memory_items(account, subject);
            """
        )
        self.connection.commit()
        secure_memory_files(path)

    def close(self) -> None:
        self.connection.close()
        secure_memory_files(self.path)

    def __enter__(self) -> MemoryStore:
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    @staticmethod
    def _public(row: sqlite3.Row, *, include_details: bool) -> dict[str, Any]:
        result = {
            "schema": MEMORY_ITEM_SCHEMA,
            "id": row["id"],
            "key": row["stable_key"],
            "kind": row["kind"],
            "scope": row["scope"],
            "subject": row["subject"],
            "summary": row["summary"],
            "tags": json.loads(row["tags_json"]),
            "validity": row["validity"],
            "confidence": row["confidence"],
            "observed_at": row["observed_at"],
            "expires_at": row["expires_at"],
            "revision": row["revision"],
            "updated_at": row["updated_at"],
        }
        if include_details:
            result["details"] = json.loads(row["details_json"])
            result["provenance"] = json.loads(row["provenance_json"])
        return result

    def _row_by_key(self, account: str, stable_key: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM memory_items WHERE account = ? AND stable_key = ?",
            (account, stable_key),
        ).fetchone()

    def get(self, account: str, identifier: str, *, include_details: bool = True) -> dict[str, Any] | None:
        self.purge_expired()
        row = self.connection.execute(
            "SELECT * FROM memory_items WHERE account = ? AND id = ?",
            (account, identifier),
        ).fetchone()
        if row is None:
            return None
        self._mark_accessed([identifier])
        return self._public(row, include_details=include_details)

    def remember(
        self,
        account: str,
        document: Any,
        *,
        expected_revision: int | None = None,
    ) -> tuple[str, dict[str, Any]]:
        account = normalize_memory_account(account)
        if not isinstance(document, dict):
            raise ValueError("memory document must be a JSON object")
        stable_key = _stable_key(document)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self._row_by_key(account, stable_key)
            current_revision = int(existing["revision"]) if existing else 0
            if expected_revision is not None and expected_revision != current_revision:
                raise RuntimeError("memory revision is stale; recall the item before updating it")
            now = utc_now()
            normalized = normalize_memory_item(
                document,
                default_observed_at=existing["observed_at"] if existing else now,
            )
            content_hash = hashlib.sha256(
                _canonical_json(normalized).encode("utf-8")
            ).hexdigest()
            identifier = memory_id(account, normalized["key"])
            if existing is not None and existing["content_hash"] == content_hash:
                self.connection.execute(
                    """
                    UPDATE memory_items
                    SET last_accessed_at = ?, access_count = access_count + 1
                    WHERE account = ? AND id = ?
                    """,
                    (now, account, identifier),
                )
                row = self._row_by_key(account, stable_key)
                self.connection.commit()
                if row is None:
                    raise RuntimeError("unchanged memory item disappeared")
                return "unchanged", self._public(row, include_details=True)

            if existing is not None and document.get("observed_at") is None:
                normalized = normalize_memory_item(document, default_observed_at=now)
                content_hash = hashlib.sha256(
                    _canonical_json(normalized).encode("utf-8")
                ).hexdigest()
            revision = current_revision + 1
            created_at = existing["created_at"] if existing else now
            values = {
                "id": identifier,
                "account": account,
                "stable_key": normalized["key"],
                "kind": normalized["kind"],
                "scope": normalized["scope"],
                "subject": normalized["subject"],
                "summary": normalized["summary"],
                "details_json": _canonical_json(normalized["details"]),
                "tags_json": _canonical_json(normalized["tags"]),
                "provenance_json": _canonical_json(normalized["provenance"]),
                "validity": normalized["validity"],
                "confidence": normalized["confidence"],
                "observed_at": normalized["observed_at"],
                "expires_at": normalized["expires_at"],
                "content_hash": content_hash,
                "revision": revision,
                "created_at": created_at,
                "updated_at": now,
                "last_accessed_at": now,
            }
            self.connection.execute(
                """
                INSERT INTO memory_items (
                    id, account, stable_key, kind, scope, subject, summary,
                    details_json, tags_json, provenance_json, validity,
                    confidence, observed_at, expires_at, content_hash, revision,
                    created_at, updated_at, last_accessed_at, access_count
                ) VALUES (
                    :id, :account, :stable_key, :kind, :scope, :subject, :summary,
                    :details_json, :tags_json, :provenance_json, :validity,
                    :confidence, :observed_at, :expires_at, :content_hash, :revision,
                    :created_at, :updated_at, :last_accessed_at, 0
                )
                ON CONFLICT(account, stable_key) DO UPDATE SET
                    kind = excluded.kind,
                    scope = excluded.scope,
                    subject = excluded.subject,
                    summary = excluded.summary,
                    details_json = excluded.details_json,
                    tags_json = excluded.tags_json,
                    provenance_json = excluded.provenance_json,
                    validity = excluded.validity,
                    confidence = excluded.confidence,
                    observed_at = excluded.observed_at,
                    expires_at = excluded.expires_at,
                    content_hash = excluded.content_hash,
                    revision = excluded.revision,
                    updated_at = excluded.updated_at,
                    last_accessed_at = excluded.last_accessed_at
                """,
                values,
            )
            self._prune(account=account, scope=normalized["scope"])
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        self.connection.execute("PRAGMA incremental_vacuum(200)")
        result = self.get(account, identifier, include_details=True)
        if result is None:
            raise RuntimeError("memory write did not produce a readable item")
        return ("updated" if existing else "created"), result

    def recall(
        self,
        account: str,
        query: str,
        *,
        scope: str | None = None,
        limit: int = 5,
        include_details: bool = False,
    ) -> list[dict[str, Any]]:
        if not query.strip():
            raise ValueError("memory recall query must not be empty")
        if not 1 <= limit <= MAX_RECALL_LIMIT:
            raise ValueError(f"memory recall limit must be between 1 and {MAX_RECALL_LIMIT}")
        if scope is not None and not _SCOPE_RE.fullmatch(scope):
            raise ValueError("memory scope must be 1-160 safe ASCII characters")
        self.purge_expired()
        if scope and scope != "global":
            rows = self.connection.execute(
                """
                SELECT * FROM memory_items
                WHERE account = ? AND scope IN (?, 'global')
                ORDER BY last_accessed_at DESC, updated_at DESC
                LIMIT ?
                """,
                (account, scope, MAX_ITEMS_PER_ACCOUNT),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT * FROM memory_items
                WHERE account = ?
                ORDER BY last_accessed_at DESC, updated_at DESC
                LIMIT ?
                """,
                (account, MAX_ITEMS_PER_ACCOUNT),
            ).fetchall()

        raw_query = query.casefold().strip()
        tokens = {
            token
            for token in re.findall(r"[\w@.-]+", raw_query)
            if len(token) > 1
        }
        ranked: list[tuple[int, sqlite3.Row]] = []
        for row in rows:
            tags = " ".join(json.loads(row["tags_json"]))
            details = row["details_json"]
            subject = row["subject"].casefold()
            summary = row["summary"].casefold()
            haystack = f"{subject} {summary} {tags} {details}".casefold()
            score = 0
            if raw_query in subject:
                score += 12
            if raw_query in summary:
                score += 8
            score += sum(4 for token in tokens if token in subject)
            score += sum(2 for token in tokens if token in tags)
            score += sum(1 for token in tokens if token in haystack)
            if score:
                ranked.append((score, row))
        ranked.sort(
            key=lambda item: (item[0], item[1]["last_accessed_at"], item[1]["updated_at"]),
            reverse=True,
        )
        selected = ranked[:limit]
        self._mark_accessed([row["id"] for _, row in selected])
        results = []
        for score, row in selected:
            item = self._public(row, include_details=include_details)
            item["score"] = score
            results.append(item)
        return results

    def list_recent(
        self,
        account: str,
        *,
        scope: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise ValueError("memory list limit must be between 1 and 100")
        self.purge_expired()
        if scope is None:
            rows = self.connection.execute(
                """
                SELECT * FROM memory_items WHERE account = ?
                ORDER BY updated_at DESC LIMIT ?
                """,
                (account, limit),
            ).fetchall()
        else:
            if not _SCOPE_RE.fullmatch(scope):
                raise ValueError("memory scope must be 1-160 safe ASCII characters")
            rows = self.connection.execute(
                """
                SELECT * FROM memory_items WHERE account = ? AND scope = ?
                ORDER BY updated_at DESC LIMIT ?
                """,
                (account, scope, limit),
            ).fetchall()
        return [self._public(row, include_details=False) for row in rows]

    def forget(self, account: str, identifier: str) -> dict[str, Any] | None:
        item = self.get(account, identifier, include_details=False)
        if item is None:
            return None
        with self.connection:
            self.connection.execute(
                "DELETE FROM memory_items WHERE account = ? AND id = ?",
                (account, identifier),
            )
        return item

    def purge_expired(self) -> int:
        cursor = self.connection.execute(
            "DELETE FROM memory_items WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (utc_now(),),
        )
        self.connection.commit()
        return cursor.rowcount

    def stats(self, account: str) -> dict[str, Any]:
        self.purge_expired()
        row = self.connection.execute(
            """
            SELECT COUNT(*) AS item_count, COUNT(DISTINCT scope) AS scope_count,
                   COALESCE(SUM(LENGTH(summary) + LENGTH(details_json)), 0) AS payload_bytes
            FROM memory_items WHERE account = ?
            """,
            (account,),
        ).fetchone()
        return {
            "item_count": row["item_count"],
            "scope_count": row["scope_count"],
            "payload_bytes": row["payload_bytes"],
            "database_bytes": self.path.stat().st_size if self.path.exists() else 0,
            "max_items_per_scope": MAX_ITEMS_PER_SCOPE,
            "max_items_per_account": MAX_ITEMS_PER_ACCOUNT,
            "max_item_bytes": MAX_ITEM_BYTES,
        }

    def _mark_accessed(self, identifiers: list[str]) -> None:
        if not identifiers:
            return
        now = utc_now()
        with self.connection:
            self.connection.executemany(
                """
                UPDATE memory_items
                SET last_accessed_at = ?, access_count = access_count + 1
                WHERE id = ?
                """,
                ((now, identifier) for identifier in identifiers),
            )

    def _prune(self, *, account: str, scope: str) -> None:
        self.connection.execute(
            "DELETE FROM memory_items WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (utc_now(),),
        )
        scope_rows = self.connection.execute(
            """
            SELECT id FROM memory_items
            WHERE account = ? AND scope = ?
            ORDER BY last_accessed_at DESC, updated_at DESC
            LIMIT -1 OFFSET ?
            """,
            (account, scope, MAX_ITEMS_PER_SCOPE),
        ).fetchall()
        account_rows = self.connection.execute(
            """
            SELECT id FROM memory_items
            WHERE account = ?
            ORDER BY last_accessed_at DESC, updated_at DESC
            LIMIT -1 OFFSET ?
            """,
            (account, MAX_ITEMS_PER_ACCOUNT),
        ).fetchall()
        stale = {row["id"] for row in (*scope_rows, *account_rows)}
        self.connection.executemany(
            "DELETE FROM memory_items WHERE id = ?",
            ((identifier,) for identifier in stale),
        )
