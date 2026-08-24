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

## Быстрые запросы без переподключения

`./run.sh` по умолчанию поднимает локальный gateway. Пока он работает, агенты используют один уже авторизованный Telegram-клиент:

```bash
venv/bin/python scripts/userbotctl.py --account main status
venv/bin/python scripts/userbotctl.py --account main recent-dms --limit 3
venv/bin/python scripts/userbotctl.py --account main dialogs --kind personal
venv/bin/python scripts/userbotctl.py --account main recent --chat '@chat' --limit 20
venv/bin/python scripts/userbotctl.py --account main events list --unread
```

Сокет `runtime/<account>/userbot.sock` и база событий доступны только локальному пользователю. По умолчанию автозагрузки нет: gateway работает в foreground или запускается по требованию:

```bash
venv/bin/python scripts/userbotd.py --account main start   # optional manual start
venv/bin/python scripts/userbotd.py --account main status
venv/bin/python scripts/userbotd.py --account main stop
```

`userbotctl.py` сам запускает gateway, если сокета нет. Такой процесс загружает
только gateway и завершается через 60 секунд после последнего локального RPC.
Опциональный подписанный webhook настраивается локально через
`venv/bin/python scripts/setup_gateway.py`. `install_gateway_service.py` нужен
только для явно запрошенного непрерывного мониторинга в текущей login-сессии;
в `~/Library/LaunchAgents` он ничего не пишет.

Event inbox ограничен 10 000 строками и 2 000 подтверждёнными событиями; webhook перестаёт повторять одну доставку после 12 ошибок. SQLite-файл и его WAL/SHM имеют права только владельца.

## Ограниченная локальная память

Перед повторной дорогой задачей агент может искать короткий проверенный результат локально:

```bash
venv/bin/python scripts/userbot_memory.py --account main recall \
  --query '<что нужно вспомнить>' --scope '<узкий scope>'
```

Сохраняются только компактные `fact`, `preference`, `decision`, `procedure`, `entity_context` и `task_result`. Сырые сообщения, транскрипты, медиа, session/credentials и спекулятивные профили не сохраняются. Общая память ограничена 16 КиБ на запись, 128 записями на scope и 1 024 на аккаунт; устаревшие и LRU-записи вытесняются. Любой изменяемый факт перепроверяется, а результат из памяти не разрешает Telegram write.

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

`setup_account.py` запросит API ID, API hash и телефон только в локальном терминале; API hash не отображается, а телефон виден, чтобы проверить его перед сохранением. Российский номер можно ввести как `+79991234567` или `79991234567`: перед сохранением второй вариант будет приведён к `+79991234567`. По умолчанию скрипт не перезаписывает существующий профиль. Для осознанной замены используй `--replace` и введи `REPLACE` в том же терминале.

Перед запросом кода launcher явно напишет способ его доставки: чат `Telegram` на другом авторизованном устройстве, SMS, звонок или email. После подключения он ограничивает права локального session-файла до `600`, не читая и не выводя его содержимое. Telegram code и 2FA введи сам в терминале. Не отправляй их, `.session`, `API_HASH` или содержимое `accounts/main.env` агенту, в GitHub или в чат. После строки `Юзербот запущен!` можно нажать `Ctrl+C`: session уже зарегистрирована.

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

После первого саммари модуль хранит в SQLite только структурированную сводку, участников, cursor и до 128 fingerprints последних сообщений — без сырого текста и транскриптов. Точный повтор того же account/chat/window обычно требует только bounded tail-check и возвращает `cache_hit`; новые сообщения обрабатываются как `delta`, а изменение проверяемого хвоста вызывает `refresh`. Разовый запрос другого периода, например месяца после годового диапазона, сканирует только этот месяц и затем получает собственный cache. Лимиты: 64 КиБ на сводку, 24 периода на чат и 512 всего.

## Офлайн-проверка после изменений

```bash
venv/bin/python -m compileall -q . -x '/(venv|\.git|__pycache__)/'
venv/bin/python -m unittest discover -s tests -v
venv/bin/python scripts/userbot_module_registry.py --validate-catalog --json
for f in modules/*.py; do venv/bin/python "$f" --help >/dev/null; done
```

## Частые безопасные операции

Read-only gateway-команды выполняются сразу. Команды, изменяющие Telegram, сначала печатают точный план; добавляй `--execute` только после подтверждения target/ID/action.

Для естественного запроса сначала можно спросить local router — он не подключается к Telegram и подсказывает конкретный модуль:

```bash
venv/bin/python scripts/userbot_module_registry.py --query 'дай список участников чата Example' --json
```

Используй команду только при `status=match`. `ambiguous` требует уточнения, а `no_match` не выбирает слабого кандидата и запускает контракт добавления одного модуля.

```bash
# Проверить профиль или подготовить смену bio/custom-emoji status
venv/bin/python scripts/userbotrun.py --account main modules/profile_settings.py
venv/bin/python scripts/userbotrun.py --account main modules/profile_settings.py --about 'Новый bio'

# Отредактировать ровно одно своё сообщение; HTML поддерживает <tg-emoji>
venv/bin/python scripts/userbotrun.py --account main modules/message_edit.py --chat @example --message-id 42 --text 'Новый текст'

# Forward только заранее перечисленных message ID
venv/bin/python scripts/userbotrun.py --account main modules/forward_messages.py \
  --source-chat @source --destination-chat @destination --message-ids 42,41

# Посмотреть текущие права участника группы
venv/bin/python scripts/userbotrun.py --account main modules/group_member.py --group @group --user @member

# Поиск по истории: компактные previews, без полного дампа чата
venv/bin/python scripts/userbotrun.py --account main modules/search_messages.py --chat @group --query 'важно' --limit 50

# Список участников группы через постоянный read-only маршрут
venv/bin/python scripts/userbotrun.py --account main modules/list_group_members.py --chat 'Example Group'

# Preview скачивания одного вложения. --execute сохранит его только в runtime data.
venv/bin/python scripts/userbotrun.py --account main modules/download_media.py --chat @group --message-ids 42

# Проверить pin либо подготовить pin/unpin одного message ID
venv/bin/python scripts/userbotrun.py --account main modules/pin_message.py --chat @group --message-id 42 --action pin
```

Полный контракт для добавления следующего модуля, включая команду для младшей модели: `docs/TELETHON_MODULE_AUTHORING.md`.

## Рекомендуемый запуск

После локальной настройки `accounts/main.env` запускай основной профиль явно:
```bash
./run.sh --account main
```
