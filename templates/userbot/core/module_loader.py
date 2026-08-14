from __future__ import annotations

import importlib.util
import inspect
import logging
import sys
from pathlib import Path

from telethon import TelegramClient

logger = logging.getLogger(__name__)


def _event_handlers(client: TelegramClient) -> list[tuple[object, object]] | None:
    list_handlers = getattr(client, "list_event_handlers", None)
    if list_handlers is None:
        return None
    return list(list_handlers())


def _rollback_new_handlers(client: TelegramClient, before: list[tuple[object, object]] | None) -> None:
    if before is None:
        return
    existing = {(id(callback), id(builder)) for callback, builder in before}
    remove_handler = getattr(client, "remove_event_handler", None)
    if remove_handler is None:
        return
    for callback, builder in _event_handlers(client) or []:
        if (id(callback), id(builder)) not in existing:
            remove_handler(callback, builder)


async def load_modules(client: TelegramClient, modules_dir: Path) -> list[str]:
    modules_dir.mkdir(parents=True, exist_ok=True)
    loaded: list[str] = []

    for file_path in sorted(modules_dir.glob("*.py")):
        if file_path.name.startswith("__"):
            continue

        module_name = file_path.stem
        spec = importlib.util.spec_from_file_location(f"modules.{module_name}", file_path)

        if spec is None or spec.loader is None:
            logger.warning("Пропуск модуля %s: не удалось собрать spec", module_name)
            continue

        module = None
        handlers_before: list[tuple[object, object]] | None = None
        try:
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            try:
                spec.loader.exec_module(module)
            except ModuleNotFoundError as exc:
                sys.modules.pop(spec.name, None)
                logger.warning(
                    "Пропуск модуля %s: отсутствует зависимость %s",
                    module_name,
                    exc.name,
                )
                continue
            except Exception:
                sys.modules.pop(spec.name, None)
                raise

            register = getattr(module, "register", None)
            if register is None:
                sys.modules.pop(spec.name, None)
                logger.warning("Пропуск модуля %s: нет функции register(client)", module_name)
                continue

            handlers_before = _event_handlers(client)
            result = register(client)
            if inspect.isawaitable(result):
                await result

            loaded.append(module_name)
            logger.info("Модуль %s загружен", module_name)
        except Exception:
            _rollback_new_handlers(client, handlers_before)
            sys.modules.pop(spec.name, None)
            logger.exception("Ошибка при загрузке модуля %s", module_name)

    return loaded
