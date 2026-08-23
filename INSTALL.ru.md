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
| Установленный навык Codex | `$CODEX_HOME/skills/userbot` (по умолчанию `~/.codex/skills/userbot`) | Файлы, которые обнаруживает Codex |
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

Codex обнаруживает пользовательские навыки в `$CODEX_HOME/skills`, по умолчанию — в `~/.codex/skills`.

```bash
export CODEX_USER_HOME="${CODEX_HOME:-$HOME/.codex}"
export USERBOT_SKILL_DIR="$CODEX_USER_HOME/skills/userbot"

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

## 8. Настройка аккаунта и первый вход — только владельцем

Выполните эти команды самостоятельно в доверенном локальном терминале, не через агента:

```bash
cd "$HOME/Documents/telethon-userbot"
python3 scripts/setup_account.py --account main
./run.sh --account main
```

Setup-скрипт локально запросит API ID, скрытый API hash, номер телефона и имя session. Затем Telethon launcher попросит Telegram-код и, если включено, пароль 2FA.

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
| Sandbox даёт `PermissionError` на runtime logs | Запросите узкий доступ для точной runtime-команды. Docker не обходит sandbox хоста. |
| Session lock занят | Используйте gateway или остановите проверенный idle owner; не запускайте второй Telethon client с той же session. |

Перенос и восстановление session описаны в [references/session-bootstrap.md](references/session-bootstrap.md). Полный security-контракт находится в [SECURITY.md](SECURITY.md).
