from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
from pathlib import Path

from telethon import TelegramClient
from telethon import errors

from core.config import BASE_DIR, apply_runtime_env, load_settings
from core.gateway import UserbotGateway, load_gateway_options
from core.module_loader import load_modules
from core.runtime_lock import AccountRuntimeLock

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
MAX_LOGIN_ATTEMPTS = 3


def idle_seconds(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("idle seconds must be an integer") from exc
    if not 0 <= parsed <= 3600:
        raise argparse.ArgumentTypeError("idle seconds must be between 0 and 3600")
    return parsed


def describe_code_delivery(sent_code: object) -> str:
    """Return a safe, human-readable description of Telegram's code route."""

    code_type = getattr(sent_code, "type", None)
    type_name = type(code_type).__name__
    routes = {
        "SentCodeTypeApp": "в чат «Telegram» на уже авторизованном устройстве",
        "SentCodeTypeSms": "по SMS",
        "SentCodeTypeCall": "голосовым звонком",
        "SentCodeTypeFlashCall": "входящим звонком",
        "SentCodeTypeMissedCall": "пропущенным звонком",
        "SentCodeTypeEmailCode": "на привязанный адрес электронной почты",
        "SentCodeTypeFirebaseSms": "по SMS через официальный мобильный клиент Telegram",
        "SentCodeTypeFragmentSms": "через Fragment SMS",
        "SentCodeTypeSetUpEmailRequired": "после дополнительной настройки email",
    }
    route = routes.get(
        type_name,
        "выбранным Telegram способом; проверь чат «Telegram» на уже авторизованных устройствах",
    )
    message = f"Telegram выбрал способ подтверждения: {route}."
    length = getattr(code_type, "length", None)
    if isinstance(length, int) and length > 0:
        message += f" Ожидается {length}-значный код."
    return message


def restrict_session_permissions(client: TelegramClient) -> None:
    """Keep the local SQLite session private without inspecting its contents."""

    filename = getattr(client.session, "filename", None)
    if not filename:
        return
    session_path = Path(filename)
    if session_path.exists():
        session_path.chmod(0o600)


async def sign_in_interactively(client: TelegramClient, phone_number: str) -> None:
    """Authorize the trusted local launcher without exposing credential values."""

    await client.connect()
    restrict_session_permissions(client)
    if await client.is_user_authorized():
        return

    try:
        sent_code = await client.send_code_request(phone_number)
    except errors.FloodWaitError as exc:
        raise RuntimeError(
            f"Telegram временно ограничил запрос кодов. Подожди {exc.seconds} сек. и запусти launcher один раз."
        ) from exc

    print(describe_code_delivery(sent_code))
    two_step_required = False
    for _ in range(MAX_LOGIN_ATTEMPTS):
        code = input("Введи полученный код: ").strip()
        if not code:
            print("Код пустой. Попробуй ещё раз.")
            continue
        try:
            await client.sign_in(phone=phone_number, code=code)
            return
        except errors.SessionPasswordNeededError:
            two_step_required = True
            break
        except (
            errors.PhoneCodeEmptyError,
            errors.PhoneCodeExpiredError,
            errors.PhoneCodeHashEmptyError,
            errors.PhoneCodeInvalidError,
        ):
            print("Код не принят. Проверь, что вводишь самый последний код Telegram.")

    if not two_step_required:
        raise RuntimeError("Не удалось подтвердить код после трёх попыток.")

    for _ in range(MAX_LOGIN_ATTEMPTS):
        password = getpass.getpass("Пароль двухэтапной проверки: ")
        try:
            await client.sign_in(password=password)
            return
        except errors.PasswordHashInvalidError:
            print("Пароль не принят. Попробуй ещё раз.")

    raise RuntimeError("Не удалось завершить вход после трёх попыток.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Telethon userbot launcher")
    parser.add_argument(
        "--account",
        help="Имя аккаунта из accounts/<name>.env (например: main, second)",
    )
    parser.add_argument(
        "--no-gateway",
        action="store_true",
        help="Emergency mode: disable the local socket, event inbox and webhook",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Use an existing authorized session and fail instead of starting login",
    )
    parser.add_argument(
        "--gateway-only",
        action="store_true",
        help="Start only the local RPC gateway; skip regular command modules",
    )
    parser.add_argument(
        "--idle-seconds",
        type=idle_seconds,
        default=0,
        help="Disconnect after this many seconds without a local RPC (0 disables)",
    )
    return parser.parse_args()


async def run(
    account: str | None = None,
    *,
    no_gateway: bool = False,
    non_interactive: bool = False,
    gateway_only: bool = False,
    idle_timeout_seconds: int = 0,
) -> None:
    if gateway_only and no_gateway:
        raise ValueError("--gateway-only cannot be combined with --no-gateway")
    if idle_timeout_seconds and no_gateway:
        raise ValueError("--idle-seconds requires the gateway")
    settings = load_settings(account=account)
    apply_runtime_env(settings)
    runtime_lock = AccountRuntimeLock(settings.runtime_dir)
    runtime_lock.acquire()

    client: TelegramClient | None = None
    gateway: UserbotGateway | None = None
    try:
        client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)
        account_label = settings.account or "legacy"
        print(f"Запуск юзербота (account={account_label})...")
        if non_interactive:
            await client.connect()
            restrict_session_permissions(client)
            if not await client.is_user_authorized():
                raise RuntimeError(
                    "Telegram session is not authorized; run ./run.sh --account <name> interactively once"
                )
        else:
            await sign_in_interactively(client, settings.phone_number)

        me = await client.get_me()
        print(f"Юзербот запущен! Аккаунт: {me.first_name} (@{me.username})")

        options = load_gateway_options(settings)
        if options.enabled and not no_gateway:
            gateway = UserbotGateway(
                client,
                settings,
                options,
                idle_timeout_seconds=idle_timeout_seconds,
            )
            await gateway.start()
            # Register the durable inbox before fetching missed updates. Existing
            # behavior modules are loaded afterwards, so catch-up cannot replay old
            # messages into an auto-responder.
            await client.catch_up()
            print(f"Локальный gateway: {options.socket_path}")

        if not gateway_only:
            loaded = await load_modules(client, Path(BASE_DIR) / "modules")
            if loaded:
                logger.info("Загружено модулей: %s", ", ".join(loaded))
            else:
                logger.info("Модули не найдены. Запуск в чистом режиме.")

        if idle_timeout_seconds:
            print(
                "Юзербот работает по запросу и завершится через "
                f"{idle_timeout_seconds} сек. без локальных RPC."
            )
        else:
            print("Юзербот работает. Нажмите Ctrl+C для остановки.")
        await client.run_until_disconnected()
    finally:
        try:
            if gateway is not None:
                await gateway.stop()
        finally:
            try:
                if client is not None and client.is_connected():
                    await client.disconnect()
            finally:
                runtime_lock.release()


if __name__ == "__main__":
    cli_args = parse_args()
    try:
        asyncio.run(
            run(
                account=cli_args.account,
                no_gateway=cli_args.no_gateway,
                non_interactive=cli_args.non_interactive,
                gateway_only=cli_args.gateway_only,
                idle_timeout_seconds=cli_args.idle_seconds,
            )
        )
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc))
    except KeyboardInterrupt:
        print("Юзербот остановлен.")
