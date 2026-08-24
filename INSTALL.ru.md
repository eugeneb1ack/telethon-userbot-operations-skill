# Инструкция по установке

<p align="center">
  <a href="INSTALL.md">English</a> ·
  <a href="INSTALL.ru.md"><strong>Русский</strong></a>
</p>

Этот гайд устанавливает навык **`userbot`** для coding agent и создаёт отдельный локальный Telethon runtime для владельца аккаунта. Документ можно целиком передать агенту: он выполнит все шаги, не связанные с секретами.

> [!CAUTION]
> **Установка этого навыка технически даёт ИИ-агенту возможность управлять вашим Telegram-аккаунтом.** Сам репозиторий не содержит секретов, но созданный позднее runtime хранит мощную Telegram-сессию. Агент не должен просить, читать, вводить, копировать, загружать или коммитить API hash, номер телефона, код входа, пароль 2FA, account env и файл `.session`.

## Граница установки

Используются три разные папки:

| Папка | Рекомендуемый путь | Назначение |
|---|---|---|
| Checkout исходников | `~/telethon-userbot-skill` | Git clone для проверки и обновлений |
| Установленный навык Codex | `~/.agents/skills/userbot` для текущего Codex Desktop | Файлы, которые обнаруживает Codex |
| Приватный runtime | `~/Documents/telethon-userbot` | Профиль аккаунта, session, логи, база событий, медиа |

Никогда не переносите account или runtime-файлы в checkout исходников или установленный навык.

## Передайте этот гайд агенту

Скопируйте следующий блок в coding agent или после открытия репозитория отправьте агенту ссылку на этот файл:

```text
Установи Telegram Userbot Skill из репозитория:
https://github.com/eugeneb1ack/telethon-userbot-operations-skill

Следуй INSTALL.ru.md из этого репозитория. Итоговое состояние:
1. Проверенный checkout исходников в ~/telethon-userbot-skill.
2. Навык установлен под точным именем userbot в документированную папку skills этого агента.
3. Новый приватный runtime создан в ~/Documents/telethon-userbot, только если такого пути ещё нет.
4. Создано виртуальное окружение проекта и установлены requirements.
5. Пройдены offline-проверки пакета, bootstrap и метаданных session.

Требования безопасности:
- Перед записью проверь существующие пути исходников, навыка и runtime.
- Не перезаписывай, не объединяй, не удаляй и не запускай bootstrap поверх существующего runtime.
- Если навык userbot уже установлен, изучи его и спроси разрешение перед заменой или созданием backup.
- Не проси и не читай API_ID, API_HASH, номер телефона, Telegram-код, пароль 2FA, содержимое account env и .session.
- Не выполняй первый Telegram login за пользователя.
- Если у владельца ещё нет API ID/API hash, проведи его по разделу 0: дай точную официальную ссылку, объясни, куда нажать и чем эти реквизиты отличаются от токена BotFather. Не проси присылать полученные значения.
- Перед первой runtime-командой сравни путь приватного runtime с текущими workspace/permission roots. Если путь вне sandbox, сразу используй узкое нативное разрешение для точной канонической команды; не запускай сначала заведомо падающую пробу и не копируй SQLite/session в доступную папку.
- После установки зависимостей остановись и попроси владельца самому выполнить две owner-only команды из INSTALL.ru.md в локальном терминале.
- После подтверждения владельца запусти offline verifier. Online-проверку авторизации выполняй только после явного разрешения.
- Не делай никаких Telegram write-операций во время установки.
- В конце сообщи точные пути установки и все успешно пройденные проверки.
```

Остальная часть документа — детерминированная процедура, которой должен следовать агент.

## Требования

- macOS или Linux. На Windows рекомендуется WSL; нативный Windows пока не проверен.
- Python 3.10 или новее.
- Git.
- Доступ к сети для клонирования репозитория и установки Python-зависимостей.
- Telegram-аккаунт, принадлежащий пользователю, который выполняет локальный login.
- API ID и API hash, созданные владельцем на [my.telegram.org/apps](https://my.telegram.org/apps).

API hash является секретом. Владелец вводит его только в локальный интерактивный setup-скрипт.

## 0. Получите Telegram API ID и API hash

Это реквизиты **Telegram application** для подключения Telethon к вашему личному аккаунту. Это не токен Telegram-бота и не данные от BotFather.

1. Откройте официальный сайт [my.telegram.org/apps](https://my.telegram.org/apps). Нужен именно поддомен `my.telegram.org`, а не главная страница `telegram.org`.
2. Введите номер своего Telegram-аккаунта в международном формате, например `+79991234567`, и нажмите `Next`.
3. Telegram отправит код подтверждения в Telegram, а не по SMS. Введите этот код только на странице `my.telegram.org`.
4. После входа откройте `API development tools`.
5. Если Telegram предлагает зарегистрировать приложение, заполните обязательные поля. Набор полей может немного меняться. Для локального userbot подходят нейтральные значения:
   - `App title`: например `Local Telethon Userbot`;
   - `Short name`: короткое имя латиницей, например `localuserbot`;
   - `Platform`: `Desktop`;
   - `URL`: оставьте пустым, если поле необязательное;
   - `Description`: например `Local Telegram client for my own account`.
6. После создания приложения страница покажет `App api_id` и `App api_hash`. `api_id` — число, `api_hash` — строка из 32 шестнадцатеричных символов.
7. Не отправляйте эти значения агенту и не вставляйте их в GitHub issue, чат или README. Оставьте страницу открытой и позже введите оба значения самостоятельно в локальный `setup_account.py` из шага 8.

Не используйте случайные API ID/API hash из примеров или чужого репозитория. Если приложение для этого номера уже зарегистрировано, `API development tools` покажет существующие реквизиты — создавать новый Telegram-аккаунт для установки не нужно.

Если установку ведёт агент, он должен объяснить этот раздел по одному действию за раз и дождаться сообщения вроде «API ID и API hash получил». Самих значений агенту не нужны.

## 1. Клонируйте исходники

При новой установке целевая папка не должна существовать:

```bash
export USERBOT_SKILL_SOURCE="$HOME/telethon-userbot-skill"

git clone https://github.com/eugeneb1ack/telethon-userbot-operations-skill.git \
  "$USERBOT_SKILL_SOURCE"
cd "$USERBOT_SKILL_SOURCE"
```

Если путь уже существует, не клонируйте поверх него. Убедитесь, что это нужный репозиторий, проверьте `git status` и сохраните локальные изменения до обновления.

## 2. Проверьте пакет до установки

Эти команды не подключаются к Telegram:

```bash
cd "$USERBOT_SKILL_SOURCE"

python3 scripts/validate_package.py
python3 scripts/test_bootstrap_userbot_project.py
python3 scripts/test_interactive_account_setup.py
python3 scripts/test_session_checker.py
```

Ожидаемые маркеры успеха:

```text
package_validation=ok
bootstrap_userbot_project_test=ok
Ran 3 tests ... OK
session_checker_test=ok
```

Если валидатор сообщает о runtime-файлах или возможных ключах в пакете, продолжать нельзя.

## 3. Установите навык в Codex

Текущий Codex Desktop использует `~/.agents/skills` для пользовательских навыков. Это отдельно от встроенных системных навыков под `.codex/skills/.system`: туда копировать репозиторий нельзя. Если ваша версия Codex явно показывает другой каталог пользовательских навыков, используйте показанный ею путь и не угадывайте его.

```bash
export CODEX_USER_SKILLS_DIR="$HOME/.agents/skills"
export USERBOT_SKILL_DIR="$CODEX_USER_SKILLS_DIR/userbot"

test ! -e "$USERBOT_SKILL_DIR"
mkdir -p "$USERBOT_SKILL_DIR"
rsync -a --exclude '.git' --exclude '.DS_Store' \
  "$USERBOT_SKILL_SOURCE/" "$USERBOT_SKILL_DIR/"

test -f "$USERBOT_SKILL_DIR/SKILL.md"
rg -n '^name: userbot$' "$USERBOT_SKILL_DIR/SKILL.md"
python3 "$USERBOT_SKILL_DIR/scripts/validate_package.py"
```

Если `test ! -e` завершился ошибкой, навык уже установлен. Остановитесь, изучите его и спросите владельца перед изменением. Безопасная замена использует backup с timestamp за пределами активной папки `skills/`; нельзя молча перезаписывать кастомизированный навык.

После установки начните новую задачу Codex, чтобы обновилось обнаружение навыков. Навык можно вызвать явно как `$userbot`; он также может автоматически включаться для подходящих Telegram-задач.

## 4. Установите навык в другого агента

Если агент поддерживает пакеты `SKILL.md`, используйте каталог skills из официальной документации этого продукта и установите репозиторий под точным именем `userbot`. Не угадывайте путь.

```bash
export AGENT_SKILLS_DIR="<папка из документации агента>"
export USERBOT_SKILL_DIR="$AGENT_SKILLS_DIR/userbot"

test ! -e "$USERBOT_SKILL_DIR"
mkdir -p "$USERBOT_SKILL_DIR"
rsync -a --exclude '.git' --exclude '.DS_Store' \
  "$USERBOT_SKILL_SOURCE/" "$USERBOT_SKILL_DIR/"
```

Если у агента нет системы навыков, но есть shell, он всё равно может вызывать JSON CLI рабочего runtime. Агент должен обращаться к тому же runtime, а не открывать вторую копию Telegram-сессии.

## 5. Проверьте, существует ли runtime

Рекомендуемый путь приватного runtime:

```bash
export USERBOT_ROOT="$HOME/Documents/telethon-userbot"
```

Если здесь уже находится рабочий userbot, **не запускайте bootstrap**. Направьте навык на существующий runtime и проверьте его отдельно. Bootstrap намеренно запрещает существующие пути: это не инструмент обновления или merge.

Переходите к следующему шагу только тогда, когда `USERBOT_ROOT` не существует.

## 6. Создайте новый приватный runtime

Сначала получите план:

```bash
python3 "$USERBOT_SKILL_DIR/scripts/bootstrap_userbot_project.py" \
  --destination "$USERBOT_ROOT"
```

Проверьте JSON: в нём должны быть `dry_run: true` и `destination_exists: false`. Затем создайте проект:

```bash
python3 "$USERBOT_SKILL_DIR/scripts/bootstrap_userbot_project.py" \
  --destination "$USERBOT_ROOT" --execute
```

Шаблон создаёт исходники и `accounts/main.env.example`. В нём нет credentials, Telegram session, runtime-баз, логов или медиа.

## 7. Создайте Python-окружение

```bash
cd "$USERBOT_ROOT"

python3 -m venv venv
venv/bin/python -m pip install --upgrade pip
venv/bin/python -m pip install -r requirements.txt
venv/bin/python -m compileall -q . -x '/(venv|\.git|__pycache__)/'
venv/bin/python -m unittest discover -s tests -v
```

На этом агент обязан остановиться. Настройка аккаунта и первый login принадлежат владельцу.

### Доступ к runtime из новых задач агента

Приватный runtime намеренно находится отдельно от репозитория навыка. Поэтому новая задача Codex с `workspace-write` может видеть исходники, но не иметь права изменять `runtime/main/data`, логи, lock-файлы или Unix socket. Даже получение сохранённой сводки локально не является чистым чтением: SQLite использует WAL/SHM и обновляет метаданные проверки/LRU.

Навык должен определить это **до первого runtime-вызова**. Для редких запросов безопасный маршрут — сразу выполнить точную каноническую команду через узкое нативное разрешение harness. В Codex это первый вызов с `require_escalated`; если включён auto-review, он может пройти без отдельного вопроса владельцу. Нельзя сначала запускать команду, которая ожидаемо упадёт, разрешать общий `python`, выдавать full access, переносить базу в `/tmp` или открывать вторую Telethon-сессию.

Для частого обращения только к семантической памяти владелец может явно добавить точную папку `runtime/main/data` как дополнительный writable root своего permission profile. Навык не меняет `~/.codex/config.toml` или постоянные правила самостоятельно. Полный контракт описан в [references/runtime-access.md](references/runtime-access.md).

## 8. Настройка аккаунта и первый вход — только владельцем

Выполните эти команды самостоятельно в доверенном локальном терминале, не через агента:

```bash
cd "$HOME/Documents/telethon-userbot"
python3 scripts/setup_account.py --account main
./run.sh --account main
```

Setup-скрипт локально запросит API ID, скрытый API hash, номер телефона и имя session. Затем Telethon launcher попросит Telegram-код и, если включено, пароль 2FA.

Если API ID/API hash ещё не получены, вернитесь к [шагу 0](#0-получите-telegram-api-id-и-api-hash). Не вводите сюда bot token от BotFather: это другой тип учётных данных.

- Введите код и пароль самостоятельно.
- Не вставляйте их в чат с агентом.
- Не разрешайте агенту читать терминал, account-файл или session-базу.
- После строки `Юзербот запущен!` session зарегистрирована. Можно нажать `Ctrl+C`.

Ожидаемый путь session для аккаунта `main`:

```text
~/Documents/telethon-userbot/runtime/main/sessions/main.session
```

Не создавайте пустую session вручную и не редактируйте её SQLite-таблицы.

## 9. Безопасно проверьте session

После подтверждения владельца агент может выполнить offline-проверку:

```bash
export USERBOT_ROOT="$HOME/Documents/telethon-userbot"
export USERBOT_PY="$USERBOT_ROOT/venv/bin/python"

"$USERBOT_PY" "$USERBOT_SKILL_DIR/scripts/verify_userbot_session.py" \
  --project-root "$USERBOT_ROOT" --account main
```

Offline verifier проверяет пути, читаемость SQLite и права файлов. Он не выводит содержимое session и не подключается к Telegram.

После явного разрешения владельца можно проверить авторизацию online:

```bash
"$USERBOT_PY" "$USERBOT_SKILL_DIR/scripts/verify_userbot_session.py" \
  --project-root "$USERBOT_ROOT" --account main --online
```

Online-проверка работает read-only: вызывает только `connect()` и `is_user_authorized()` и не запускает интерактивный login.

## 10. Smoke test gateway

```bash
cd "$USERBOT_ROOT"

venv/bin/python scripts/userbotctl.py --account main status
venv/bin/python scripts/userbotctl.py --account main recent-dms --limit 3
venv/bin/python scripts/userbotctl.py --account main events list --unread
```

`userbotctl.py` использует существующий Unix socket или запускает gateway-only процесс. On-demand owner завершается через 60 секунд без локальных RPC. Lock не даёт второму процессу одновременно открыть session того же аккаунта.

Установка завершена, когда:

- агент обнаруживает навык под именем `userbot`;
- проверки пакета и bootstrap проходят;
- у приватного runtime есть собственный `venv`;
- владелец выполнил локальный login без передачи секретов;
- offline verification проходит;
- опциональная online-проверка возвращает `authorized: true`;
- `userbotctl.py --account main status` возвращает корректный JSON.

## Безопасное обновление

Сначала обновите чистый checkout исходников:

```bash
cd "$USERBOT_SKILL_SOURCE"
git status --short
git pull --ff-only

python3 scripts/validate_package.py
python3 scripts/test_bootstrap_userbot_project.py
python3 scripts/test_interactive_account_setup.py
python3 scripts/test_session_checker.py
```

Не выполняйте pull при непонятных локальных изменениях. После валидации осознанно обновите установленный навык. Если установленная копия изменялась вручную, сначала создайте backup за пределами активной папки skills.

Никогда не запускайте bootstrap повторно поверх существующего runtime. Обновления runtime-кода должны сохранять `accounts/`, `runtime/`, session, логи и локальные данные и проходить обычный code review.

## Решение проблем

| Симптом | Правильное действие |
|---|---|
| Репозиторий не клонируется | Убедитесь, что он уже открыт и HTTPS URL правильный; не просите GitHub credentials в чате. |
| Старая версия `python3` | Установите Python 3.10+ и пересоздайте virtual environment. |
| Папка установленного `userbot` уже есть | Изучите её и спросите разрешение перед backup или заменой; не перезаписывайте. |
| Путь runtime уже существует | Не запускайте bootstrap. Определите, рабочий ли это runtime, и сохраните его. |
| `session.present: false` | Владелец должен пройти первый локальный login. Агент не выполняет его. |
| `safe_permissions: false` | Выполните `chmod 600` для указанного account/session-файла, не печатая его содержимое. |
| `authorized: false` | Владелец должен создать новую локальную session; не удаляйте старую сгоряча. |
| Новая задача Codex не пускает к SQLite/log/socket runtime | Не ищите другой путь и не повторяйте сначала sandboxed-команду. Выполните access preflight и сразу отправьте ту же каноническую команду через узкое нативное разрешение; см. [runtime-access.md](references/runtime-access.md). |
| Session lock занят | Используйте gateway или остановите проверенный idle owner; не запускайте второй Telethon client с той же session. |

Перенос и восстановление session описаны в [references/session-bootstrap.md](references/session-bootstrap.md). Полный security-контракт находится в [SECURITY.md](SECURITY.md).
