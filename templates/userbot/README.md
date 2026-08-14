# telethon-userbot

## Быстрый запуск (гарантированно через venv)

```bash
./run.sh
```

Скрипт всегда использует `./venv/bin/python` и не зависит от текущей `cwd`.

## CLI

```bash
./run.sh --help
./run.sh --account main
./run.sh --account second
```

## Режимы конфигурации

### 1) Рекомендуемая схема: shared + account profiles
- Общие ключи/integration settings: `accounts/_shared.env`
- Профили аккаунтов: `accounts/<name>.env`
- Пример: `accounts/main.env`, `accounts/second.env`
- Для каждого аккаунта изолируются:
  - session-файлы: `runtime/<name>/sessions/`
  - data: `runtime/<name>/data/`
  - memory/config: `runtime/<name>/memory/`

`accounts/_shared.env` хранит общее вроде OpenRouter, bot token, speech settings.
`accounts/<name>.env` хранит только Telegram account credentials (`API_ID`, `API_HASH`, `PHONE_NUMBER`, `SESSION_NAME`).

## Первая регистрация session

Для account profile с `SESSION_NAME=main` Telethon создаёт session в:

```text
runtime/main/sessions/main.session
```

Первый login делай только локально через trusted launcher:

```bash
python3 scripts/setup_account.py --account main
./run.sh --account main
```

`setup_account.py` запросит API ID, API hash и телефон только в локальном терминале; hash и телефон не отображаются. По умолчанию он не перезаписывает существующий профиль. Для осознанной замены используй `--replace` и введи `REPLACE` в том же терминале.

Telegram code и 2FA введи сам в терминале. Не отправляй их, `.session`, `API_HASH` или содержимое `accounts/main.env` агенту, в GitHub или в чат. После строки `Юзербот запущен!` можно нажать `Ctrl+C`: session уже зарегистрирована.

Для детальной offline/online проверки session используй `references/session-bootstrap.md` из канонического skill `userbot`; direct helpers никогда не должны запускать интерактивный login.

### 2) Legacy fallback (только для совместимости)
- Используется файл `.env`.
- Runtime остаётся в старых путях проекта (`data/`, `memory/`, `userbot.session`).
- Запуск: `./run.sh`

## Как добавить второй аккаунт

1. Убедитесь, что общие ключи лежат в `accounts/_shared.env`.
2. Создайте профиль интерактивно:
```bash
python3 scripts/setup_account.py --account second
```
3. Первый запуск:
```bash
./run.sh --account second
```
4. Telethon попросит код подтверждения из Telegram — введите вручную.

> Без ввода кода логин не произойдёт (и это нормально/безопасно).

## Проверка native summary-модуля

```bash
./venv/bin/python modules/summarize_chat_native.py --help
./venv/bin/python modules/summarize_chat_native.py \
  --account main --chat @example --date 2026-08-14 --output runtime/main/data/transcripts/example.json
```

`modules/summarize.py` сохранён только для legacy-совместимости: он использует Google/ffmpeg/OpenRouter. Для новых сводок используй `summarize_chat_native.py`, который работает через native Telegram STT и сохраняет данные внутри `runtime/<account>/data/`.

## Офлайн-проверка после изменений

```bash
venv/bin/python -m compileall -q . -x '/(venv|\.git|__pycache__)/'
venv/bin/python -m unittest discover -s tests -v
for f in modules/*.py; do venv/bin/python "$f" --help >/dev/null; done
```

## Частые безопасные операции

Все команды сначала печатают план и ничего не пишут в Telegram. Добавляй `--execute` только после проверки target/ID.

Для естественного запроса сначала можно спросить local router — он не подключается к Telegram и подсказывает конкретный модуль:

```bash
venv/bin/python scripts/userbot_module_registry.py --query 'дай список участников чата Цыгане'
```

```bash
# Проверить профиль или подготовить смену bio/custom-emoji status
venv/bin/python modules/profile_settings.py --account main
venv/bin/python modules/profile_settings.py --account main --about 'Новый bio'

# Отредактировать ровно одно своё сообщение; HTML поддерживает <tg-emoji>
venv/bin/python modules/message_edit.py --account main --chat @example --message-id 42 --text 'Новый текст'

# Forward только заранее перечисленных message ID
venv/bin/python modules/forward_messages.py \
  --account main --source-chat @source --destination-chat @destination --message-ids 42,41

# Посмотреть текущие права участника группы
venv/bin/python modules/group_member.py --account main --group @group --user @member

# Поиск по истории: компактные previews, без полного дампа чата
venv/bin/python modules/search_messages.py --account main --chat @group --query 'важно' --limit 50

# Список участников группы; у «Цыган» это теперь постоянный read-only маршрут
venv/bin/python modules/list_group_members.py --account main --chat 'Цыгане'

# Preview скачивания одного вложения. --execute сохранит его только в runtime data.
venv/bin/python modules/download_media.py --account main --chat @group --message-ids 42

# Проверить pin либо подготовить pin/unpin одного message ID
venv/bin/python modules/pin_message.py --account main --chat @group --message-id 42 --action pin
```

Полный контракт для добавления следующего модуля, включая команду для младшей модели: `docs/TELETHON_MODULE_AUTHORING.md`.

## Текущая рекомендуемая миграция

- Основной аккаунт уже перенесён в `accounts/main.env`
- Общие ключи уже вынесены в `accounts/_shared.env`
- Корневой `.env` оставлен только как legacy fallback-заглушка

Рекомендуемый запуск основного аккаунта теперь такой:
```bash
./run.sh --account main
```
