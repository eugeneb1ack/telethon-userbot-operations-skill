from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ACCOUNTS_DIR = BASE_DIR / "accounts"
SHARED_ENV_FILE = ACCOUNTS_DIR / "_shared.env"
_ACCOUNT_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_SESSION_BASENAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_PHONE_NUMBER_RE = re.compile(r"\+[1-9]\d{6,14}\Z")
_RUSSIAN_PHONE_NUMBER_WITHOUT_PLUS_RE = re.compile(r"7\d{10}\Z")


@dataclass(frozen=True)
class Settings:
    api_id: int
    api_hash: str
    phone_number: str
    session_name: str
    account: str | None
    env_file: str
    runtime_dir: str
    data_dir: str
    memory_dir: str
    transcripts_dir: str


def _normalize_account_name(account: str | None) -> str | None:
    if account is None:
        return None
    value = account.strip()
    if not value:
        return None
    if not _ACCOUNT_NAME_RE.fullmatch(value):
        raise ValueError(
            "Имя аккаунта может содержать только латинские буквы, цифры, _ и - "
            "и должно начинаться с буквы или цифры"
        )
    return value


def _selected_account_name(account: str | None) -> str | None:
    return _normalize_account_name(account) or _normalize_account_name(os.getenv("USERBOT_ACCOUNT"))


def _normalize_phone_number(value: str) -> str:
    phone_number = value.strip()
    if _PHONE_NUMBER_RE.fullmatch(phone_number):
        return phone_number
    if _RUSSIAN_PHONE_NUMBER_WITHOUT_PLUS_RE.fullmatch(phone_number):
        return f"+{phone_number}"
    raise ValueError(
        "PHONE_NUMBER укажи в международном формате, например +79991234567 или 79991234567"
    )


def resolve_env_file(account: str | None = None) -> Path:
    account_name = _selected_account_name(account)
    if account_name:
        return ACCOUNTS_DIR / f"{account_name}.env"
    return BASE_DIR / ".env"


def _is_isolated_account_mode(account: str | None, env_path: Path) -> bool:
    return account is not None or env_path.parent == ACCOUNTS_DIR


def _runtime_paths(account: str | None, isolated: bool) -> tuple[Path, Path, Path, Path]:
    if isolated:
        account_name = account or "default"
        runtime_dir = BASE_DIR / "runtime" / account_name
        data_dir = runtime_dir / "data"
        memory_dir = runtime_dir / "memory"
        transcripts_dir = data_dir / "transcripts"
    else:
        runtime_dir = BASE_DIR
        data_dir = BASE_DIR / "data"
        memory_dir = BASE_DIR / "memory"
        transcripts_dir = data_dir / "transcripts"

    for path in (runtime_dir, data_dir, memory_dir, transcripts_dir):
        path.mkdir(parents=True, exist_ok=True)

    return runtime_dir, data_dir, memory_dir, transcripts_dir


def apply_runtime_env(settings: Settings) -> None:
    os.environ["USERBOT_ENV_FILE"] = settings.env_file
    os.environ["USERBOT_RUNTIME_DIR"] = settings.runtime_dir
    os.environ["USERBOT_DATA_DIR"] = settings.data_dir
    os.environ["USERBOT_MEMORY_DIR"] = settings.memory_dir
    os.environ["USERBOT_TRANSCRIPTS_DIR"] = settings.transcripts_dir
    if settings.account:
        os.environ["USERBOT_ACCOUNT"] = settings.account


def load_settings(account: str | None = None) -> Settings:
    account_name = _selected_account_name(account)
    env_path = resolve_env_file(account_name)

    if not env_path.exists():
        if account_name:
            raise ValueError(
                f"Не найден профиль аккаунта: {env_path}. Создайте файл accounts/{account_name}.env"
            )
        raise ValueError(f"Не найден файл окружения: {env_path}")

    # dotenv only overwrites present keys. Clear account-scoped values first so
    # loading two profiles in one process cannot reuse another account's
    # credentials when the second profile is incomplete.
    for key in ("API_ID", "API_HASH", "PHONE_NUMBER", "phone_number", "SESSION_NAME"):
        os.environ.pop(key, None)

    if account_name and SHARED_ENV_FILE.exists():
        load_dotenv(SHARED_ENV_FILE, override=True)
    load_dotenv(env_path, override=True)

    api_id_raw = os.getenv("API_ID")
    api_hash = os.getenv("API_HASH")
    phone_number = os.getenv("PHONE_NUMBER") or os.getenv("phone_number")
    session_name = os.getenv("SESSION_NAME", "userbot")

    if not api_id_raw or api_id_raw.startswith("<"):
        raise ValueError(
            f"API_ID не задан в {env_path.name}. "
            f"Запусти python3 scripts/setup_account.py --account {account_name or 'main'}"
        )

    if not api_hash or api_hash.startswith("<"):
        raise ValueError(
            f"API_HASH не задан в {env_path.name}. "
            f"Запусти python3 scripts/setup_account.py --account {account_name or 'main'}"
        )

    if not phone_number or phone_number.startswith("<"):
        raise ValueError(
            f"PHONE_NUMBER не задан в {env_path.name}. "
            f"Запусти python3 scripts/setup_account.py --account {account_name or 'main'}"
        )
    phone_number = _normalize_phone_number(phone_number)

    try:
        api_id = int(api_id_raw)
    except ValueError as exc:
        raise ValueError("API_ID должен быть целым числом") from exc

    isolated = _is_isolated_account_mode(account_name, env_path)
    runtime_dir, data_dir, memory_dir, transcripts_dir = _runtime_paths(account_name, isolated)

    session_value = Path(session_name)
    if isolated:
        if (
            session_value.is_absolute()
            or len(session_value.parts) != 1
            or not _SESSION_BASENAME_RE.fullmatch(session_value.name)
        ):
            raise ValueError("SESSION_NAME для account-профиля должен быть простым именем файла без пути")
        final_session_path = runtime_dir / "sessions" / session_value.name
    elif session_value.is_absolute():
        final_session_path = session_value
    else:
        final_session_path = BASE_DIR / session_value

    final_session_path.parent.mkdir(parents=True, exist_ok=True)

    return Settings(
        api_id=api_id,
        api_hash=api_hash,
        phone_number=phone_number,
        session_name=str(final_session_path),
        account=account_name,
        env_file=str(env_path),
        runtime_dir=str(runtime_dir),
        data_dir=str(data_dir),
        memory_dir=str(memory_dir),
        transcripts_dir=str(transcripts_dir),
    )
