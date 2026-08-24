# Регистрация и перенос Telethon session

Этот документ для **пользователя** и **агента**. Session-файл — не настройка, а фактически ключ от Telegram-аккаунта: тот, у кого есть рабочий `.session`, может действовать от имени аккаунта.

Никогда не отправляй его в чат, GitHub, email, облако, форму браузера или агенту.

## Для пользователя: новая session с нуля

### 0. Получи Telegram API ID и API hash

Открой официальный сайт [my.telegram.org/apps](https://my.telegram.org/apps), войди по номеру своего Telegram-аккаунта и коду, который Telegram присылает в Telegram, а не по SMS. Затем открой `API development tools`.

Если приложение ещё не зарегистрировано, заполни обязательную форму. Для локального клиента можно использовать `Local Telethon Userbot` как `App title`, `localuserbot` как `Short name`, `Desktop` как `Platform` и короткое честное описание. Точный набор полей может меняться.

После создания будут показаны `App api_id` и `App api_hash`. API ID — положительное число, API hash — строка из 32 шестнадцатеричных символов. Это не bot token от BotFather. Не присылай значения агенту: они понадобятся только в локальном терминале на шаге 2.

### 1. Подготовь локальный userbot-проект

```bash
export USERBOT_ROOT="$HOME/Documents/telethon-userbot"
cd "$USERBOT_ROOT"

python3 -m venv venv              # только если venv ещё нет
venv/bin/python -m pip install -r requirements.txt
```

### 2. Создай account profile интерактивно

```bash
python3 scripts/setup_account.py --account main
```

Скрипт спросит API ID, API hash и номер только в локальном терминале. API hash вводится без эха, а номер виден, чтобы владелец мог проверить его перед сохранением. Российский номер можно ввести как `+79991234567` или `79991234567`: второй вариант скрипт сохранит в международном виде `+79991234567`. `API_ID` проверяется как положительное целое, а `accounts/main.env` создаётся с правами `600`. `SESSION_NAME` можно оставить пустым, чтобы использовать `main`; он должен быть простым именем без слешей.

Скрипт не перезаписывает существующий профиль. Если там остался незаполненный пример, осознанно запусти `python3 scripts/setup_account.py --account main --replace` и введи `REPLACE` в том же терминале. Никому не показывай содержимое созданного файла.

### 3. Запусти **только локально** первый интерактивный login

```bash
./run.sh --account main
```

Текущий trusted launcher запрашивает код через Telethon только в локальном терминале и перед вводом показывает выбранный Telegram способ доставки: чат `Telegram` на другом авторизованном устройстве, SMS, звонок или email. При включённой 2FA он дополнительно запросит пароль.

- Введи код подтверждения **сам в своём терминале**.
- Введи 2FA-пароль **сам в своём терминале**.
- Не вставляй код или пароль в переписку с агентом.
- Не разрешай агенту вводить код, читать экран с кодом или открывать `.session`.

После строки вида `Юзербот запущен!` session уже создана. Нажми `Ctrl+C`, если сейчас не хочешь оставлять userbot запущенным.

Launcher сразу после подключения ограничивает права локального session-файла до `600`; он не читает и не выводит его содержимое.

### 4. Где появится файл

При `--account main` и `SESSION_NAME=main` ожидаемый путь:

```text
$USERBOT_ROOT/runtime/main/sessions/main.session
```

Telethon создаёт SQLite session сам. Не создавай пустой `.session` руками и не редактируй его SQLite-таблицы.

### 5. Проверь локально, не раскрывая содержимое session

```bash
SKILL_DIR="${USERBOT_SKILL_DIR:?set USERBOT_SKILL_DIR to the installed userbot skill}"
USERBOT_PY="$USERBOT_ROOT/venv/bin/python"

# Offline: проверяет наличие, SQLite-формат и file permissions. В Telegram не подключается.
"$USERBOT_PY" "$SKILL_DIR/scripts/verify_userbot_session.py" \
  --project-root "$USERBOT_ROOT" --account main

# Опционально: read-only connect + is_user_authorized(). Не запускает login.
"$USERBOT_PY" "$SKILL_DIR/scripts/verify_userbot_session.py" \
  --project-root "$USERBOT_ROOT" --account main --online
```

Готовый профиль имеет:

```json
{
  "session": {
    "present": true,
    "sqlite_readable": true,
    "safe_permissions": true
  },
  "online_check": {
    "authorized": true
  }
}
```

Закрой права и на session:

```bash
chmod 600 runtime/main/sessions/main.session
```

## Для пользователя: перенос уже существующей session

Переносить можно только из своего доверенного локального источника и для того же Telegram-аккаунта.

1. В новом проекте создай `accounts/main.env` с теми же корректными account credentials и `SESSION_NAME=main`.
2. Создай целевую папку:

   ```bash
   mkdir -p "$USERBOT_ROOT/runtime/main/sessions"
   ```

3. Скопируй файл **локально** с ограниченными правами:

   ```bash
   install -m 600 /trusted/local/path/main.session \
     "$USERBOT_ROOT/runtime/main/sessions/main.session"
   ```

4. Запусти offline, затем `--online` проверку из предыдущего раздела.

Не копируй session через Telegram, GitHub, email, публичное облако или shared folder. Не добавляй `.session-journal`: при остановленном userbot нужен только основной `.session`.

## Если проверка не проходит

| Результат | Что делать |
|---|---|
| `project_root_not_found` | Проверь `$USERBOT_ROOT` и путь к userbot-проекту. |
| `settings_error:*` | Проверь, что `accounts/main.env` существует, а `API_ID`, `API_HASH`, `PHONE_NUMBER`, `SESSION_NAME` заполнены локально. Не присылай значения агенту. |
| `session.present: false` | Выполни локальный `./run.sh --account main` и заверши интерактивный Telegram login. |
| `sqlite_readable: false` | Не редактируй и не выкладывай файл. Возьми новую доверенную копию или зарегистрируй новую session через launcher. |
| `safe_permissions: false` | Выполни `chmod 600 runtime/main/sessions/main.session` и `chmod 600 accounts/main.env`. |
| `authorized: false` или ошибка онлайн-проверки | Session могла быть отозвана/устареть. Не удаляй старый файл сгоряча: сначала измени `SESSION_NAME` на новое имя и пройди локальный interactive login заново. |

## Для агента: жёсткие границы

Агент может:

- вести установку по одному следующему шагу, включая точную ссылку [my.telegram.org/apps](https://my.telegram.org/apps), вход в `API development tools` и отличие API ID/API hash от токена BotFather;
- дождаться, пока владелец сам получит реквизиты и завершит локальный login, не запрашивая значения или снимок экрана с ними;
- выполнить только offline `verify_userbot_session.py`;
- после явного запроса выполнить `--online` проверку, которая вызывает только `connect()` и `is_user_authorized()`;
- использовать уже зарегистрированную session через modules, которые делают `connect()` + `is_user_authorized()`.

Агент **никогда не должен**:

- попросить код Telegram, 2FA-пароль, `.session`, API hash или содержимое account env;
- вводить код/пароль, кликать approval/login UI или запускать login от лица пользователя;
- читать SQLite-содержимое `.session`, загружать/копировать/архивировать session;
- добавлять `client.start()` в обычный direct helper;
- считать session готовой только потому, что файл существует: нужна offline проверка, а для реальной готовности — `--online authorized: true`.

Когда пользователь просит «установить userbot» или «подключить session», агент не ограничивается общей фразой про Telegram credentials. Он называет точный следующий шаг, останавливается на owner-only вводе, а после подтверждения продолжает offline-проверку без раскрытия секретов.
