#!/usr/bin/env python3
"""Interactively create a protected local Telegram account profile."""

from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ACCOUNT_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
SESSION_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
API_HASH_RE = re.compile(r"[0-9a-fA-F]{32}\Z")
PHONE_NUMBER_RE = re.compile(r"\+[1-9]\d{6,14}\Z")
RUSSIAN_PHONE_NUMBER_WITHOUT_PLUS_RE = re.compile(r"7\d{10}\Z")


@dataclass(frozen=True)
class AccountValues:
    api_id: int
    api_hash: str
    phone_number: str
    session_name: str


def _normalize_account_name(value: str) -> str:
    account = value.strip()
    if not ACCOUNT_NAME_RE.fullmatch(account):
        raise ValueError("Имя account может содержать только латинские буквы, цифры, _ и -")
    return account


def _prompt_until_valid(
    prompt: str,
    read_value: Callable[[str], str],
    validate: Callable[[str], str],
    output: Callable[[str], None],
) -> str:
    while True:
        try:
            return validate(read_value(prompt).strip())
        except ValueError as exc:
            output(f"Ошибка: {exc}. Попробуй ещё раз.")


def _validate_api_id(value: str) -> str:
    if not value.isdecimal() or int(value) <= 0:
        raise ValueError("API_ID должен быть положительным целым числом без пробелов и символов")
    return value


def _validate_api_hash(value: str) -> str:
    if not API_HASH_RE.fullmatch(value):
        raise ValueError("API_HASH должен состоять из 32 шестнадцатеричных символов")
    return value


def _validate_phone_number(value: str) -> str:
    if PHONE_NUMBER_RE.fullmatch(value):
        return value
    if RUSSIAN_PHONE_NUMBER_WITHOUT_PLUS_RE.fullmatch(value):
        return f"+{value}"
    raise ValueError(
        "Номер укажи в международном формате, например +79991234567 или 79991234567"
    )


def _validate_session_name(value: str) -> str:
    if not SESSION_NAME_RE.fullmatch(value):
        raise ValueError("SESSION_NAME может содержать только латинские буквы, цифры, _, - и .")
    return value


def collect_account_values(
    account: str,
    *,
    input_func: Callable[[str], str] = input,
    secret_input: Callable[[str], str] = getpass.getpass,
    output: Callable[[str], None] = print,
) -> AccountValues:
    api_id = int(_prompt_until_valid("Telegram API ID: ", input_func, _validate_api_id, output))
    api_hash = _prompt_until_valid(
        "Telegram API hash (скрыт): ", secret_input, _validate_api_hash, output
    )
    phone_number = _prompt_until_valid(
        "Номер телефона (+79991234567 или 79991234567): ",
        input_func,
        _validate_phone_number,
        output,
    )
    session_name = _prompt_until_valid(
        f"Имя session [{account}]: ",
        input_func,
        lambda value: _validate_session_name(value or account),
        output,
    )
    return AccountValues(api_id, api_hash, phone_number, session_name)


def write_account_file(path: Path, values: AccountValues, *, replace: bool) -> None:
    if path.exists() or path.is_symlink():
        if not replace:
            raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(
                f"API_ID={values.api_id}\n"
                f"API_HASH={values.api_hash}\n"
                f"PHONE_NUMBER={values.phone_number}\n"
                f"SESSION_NAME={values.session_name}\n"
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        path.chmod(0o600)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Interactively create a local Telegram account profile while hiding the API hash"
    )
    result.add_argument("--account", default="main", help="Account profile name; default: main")
    result.add_argument(
        "--replace",
        action="store_true",
        help="Allow replacing an existing account file after local REPLACE confirmation",
    )
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        account = _normalize_account_name(args.account)
    except ValueError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2

    target = PROJECT_ROOT / "accounts" / f"{account}.env"
    if target.exists() or target.is_symlink():
        if not args.replace:
            print(
                f"Профиль уже существует: accounts/{account}.env. "
                "Для интерактивной замены запусти скрипт с --replace.",
                file=sys.stderr,
            )
            return 2
        confirmation = input(
            f"Заменить accounts/{account}.env? Текущие значения будут потеряны. Введи REPLACE: "
        ).strip()
        if confirmation != "REPLACE":
            print("Замена отменена.", file=sys.stderr)
            return 2

    print("Значения остаются в этом терминале и не выводятся в лог.")
    values = collect_account_values(account)
    write_account_file(target, values, replace=args.replace)
    print(f"Создан защищённый локальный профиль: accounts/{account}.env")
    print(f"Дальше запусти: ./run.sh --account {account}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
