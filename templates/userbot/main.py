from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from telethon import TelegramClient

from core.config import BASE_DIR, apply_runtime_env, load_settings
from core.module_loader import load_modules

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Telethon userbot launcher")
    parser.add_argument(
        "--account",
        help="Имя аккаунта из accounts/<name>.env (например: main, second)",
    )
    return parser.parse_args()


async def run(account: str | None = None) -> None:
    settings = load_settings(account=account)
    apply_runtime_env(settings)

    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)

    account_label = settings.account or "legacy"
    print(f"Запуск юзербота (account={account_label})...")
    await client.start(phone=settings.phone_number)

    me = await client.get_me()
    print(f"Юзербот запущен! Аккаунт: {me.first_name} (@{me.username})")

    loaded = await load_modules(client, Path(BASE_DIR) / "modules")
    if loaded:
        logger.info("Загружено модулей: %s", ", ".join(loaded))
    else:
        logger.info("Модули не найдены. Запуск в чистом режиме.")

    print("Юзербот работает. Нажмите Ctrl+C для остановки.")
    await client.run_until_disconnected()


if __name__ == "__main__":
    cli_args = parse_args()
    try:
        asyncio.run(run(account=cli_args.account))
    except ValueError as exc:
        raise SystemExit(str(exc))
    except KeyboardInterrupt:
        print("Юзербот остановлен.")
