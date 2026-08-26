# Как добавлять модули Telethon Userbot

Этот документ — инструкция для модели, которая реализует новую функцию в текущем `$USERBOT_ROOT`.

Цель: добавить **одну маленькую, проверяемую операцию** в persistent gateway или отдельный модуль, а не абстрактную обёртку над всем Telegram API.

## 0. До кода: выбери правильную границу

### Можно реализовывать как обычный модуль

- чтение истории, диалогов, участников, статистики;
- отправку, редактирование, forwarding, реакции, пины;
- профиль, custom emoji, стикеры;
- группы/каналы, если права пользователя уже позволяют действие;
- контакты, папки, stories.

### Не автоматизируй без отдельного прямого решения владельца

- `auth.*`, пароль, recovery email, passkeys, phone-number change;
- reset/logout sessions;
- удаление аккаунта;
- платежи, Stars, gifts, subscriptions, refunds, transfers;
- массовые импорты чужих контактов или массовые инвайты;
- любое действие, которое нельзя безопасно показать в dry-run.

Если операция здесь — остановись и объясни риск. Не пиши “универсальный raw request runner”.

## 1. Собери authoring context, не вспоминай API

Используем Telethon, реально установленный в проекте. Его версия и сигнатуры — источник истины.

```bash
cd "${USERBOT_ROOT:?set USERBOT_ROOT to the userbot project}"
PY="${USERBOT_PY:-venv/bin/python}"
SKILL=${USERBOT_SKILL_DIR:?set USERBOT_SKILL_DIR to the installed userbot skill}

# Реестр + pin/installed version + точные сигнатуры + официальные ссылки
$PY "$SKILL/scripts/telethon_authoring_context.py" \
  --project-root "$USERBOT_ROOT" --query '<исходный запрос>' \
  --client-method edit_message \
  --raw-request messages.EditMessageRequest --json
```

Если точный API ещё не выбран, сначала не указывай `--client-method`/`--raw-request`, затем перезапусти packet с выбранной поверхностью. При `use_existing_operation` не пиши новый модуль; при `clarify_before_coding` уточни запрос; при `resolve_version_alignment_before_coding` или `resolve_api_surface_before_coding` сначала устрани блокер; пиши код только при `author_one_focused_operation`.

Требуй `telethon.version_match=true`. После packet открой указанную официальную страницу `tl.telethon.dev` или `docs.telethon.dev` и проверь, что заголовок документации соответствует `installed_version`. Не смешивай stable v1 с v2/development и не выдумывай поля constructor-а, права админа, типы реакций или ошибки «по памяти».

Если задачу полностью покрывает документированный high-level метод `TelegramClient`, предпочитай его raw TL: у friendly methods выше гарантия совместимости. Для истории и поиска передавай поддерживаемые фильтры (`from_user`, `search`, `filter`) в `iter_messages`: Telethon использует server-side search там, где Telegram это поддерживает, а в private chat может оставить локальную sender-проверку. Клиентская проверка модуля остаётся safety assertion. Перед сочетанием фильтров проверяй установленную реализацию; не комбинируй их с forum `reply_to`.

## 2. Сначала проверь, не существует ли функция

Частые read-only операции должны использовать `scripts/userbotctl.py`: он переиспользует или сам запускает короткоживущий gateway. Новый direct helper нужен только когда gateway/registry ещё не покрывают запрос. Его команда в registry обязана идти через `scripts/userbotrun.py`, а не напрямую: runner сериализует доступ к session и завершает зависший subprocess.

Если оболочка вернула session/process handle, команда ещё работает. Сохрани handle и дождись exit status; partial stdout, progress и обрезанный вывод не считаются завершением. При отмене или замене задачи заверши точный handle и дождись reaping до запуска следующего helper для того же account. `userbotrun.py` перехватывает внешние `SIGHUP`/`SIGINT`/`SIGTERM`, завершает process group и освобождает account lock; не посылай сигналы угаданному дочернему PID.

```bash
cd "${USERBOT_ROOT:?set USERBOT_ROOT to the userbot project}"
"$PY" scripts/userbot_module_registry.py --query '<запрос пользователя>' --json
```

- Если похожий модуль уже есть — исправь/расширь его минимально.
- Если это отдельная повторяемая операция — создай `modules/<snake_case_name>.py`.
- Один файл = одна операция. Не смешивай profile, stickers, stories, admin actions и payments в один «super_telegram.py».

## 3. Обязательная форма direct-модуля

Скопируй эту форму и меняй только отмеченные места:

```python
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from telethon import TelegramClient, errors

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import apply_runtime_env, load_settings
from core.telegram_targets import entity_payload, resolve_entity


def register(client: TelegramClient) -> None:
    """Direct CLI helper; no event handler is attached."""


async def run(args: argparse.Namespace) -> dict[str, Any]:
    settings = load_settings(args.account)
    apply_runtime_env(settings)
    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized; refusing interactive login")

        entity = await resolve_entity(client, args.chat)
        plan = {
            "ok": True,
            "dry_run": not args.execute,
            "target": entity_payload(entity, input_value=args.chat),
        }

        # Collect exact IDs/current state here.
        # Dry-run MUST finish before any mutating call.
        if not args.execute:
            return plan

        # Execute only the frozen plan. Catch FloodWaitError once.
        # Read back exact state and include verification in the result.
        return plan
    finally:
        await client.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(description="One exact operation")
    parser.add_argument("--account", default="main")
    parser.add_argument("--chat", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

### Never violate these rules

1. Use `load_settings()` and `apply_runtime_env()` from `core.config`.
2. Use `connect()` + `is_user_authorized()`. **Never `client.start()` in a direct helper.**
3. Never hard-code API ID, API hash, phone, session path, username, chat ID, or emoji document ID.
4. Never print phone numbers, secrets, session paths, full private histories, or raw API credentials.
5. A numeric peer must use `core.telegram_targets.resolve_entity()`; it scans authorized dialogs if the local entity cache misses. Do not solve this through `abs(id)` or by guessing PeerChannel/PeerUser.
6. Textual fuzzy resolution with multiple matches must fail with a candidate list. Never choose the first match for a write.
7. Every direct write defaults to dry-run and needs `--execute`.

## 4. Dry-run → execute → verification

A mutating module must have all three stages.

### Dry-run

It resolves target(s) and returns a compact plan:

```json
{
  "dry_run": true,
  "target": {"id": 123, "title_or_name": "Example", "type": "Channel"},
  "candidate_ids": [7, 8, 9],
  "requested": {"action": "..."}
}
```

- Freeze exact IDs before writing.
- If IDs are missing, duplicated, belong to another user, or the target changes — stop.
- Do not hide missing candidates. Report them.

### Execute

- Requires `--execute` **and** an explicit owner request in the parent conversation.
- Do not calculate a second moving “latest N” set after the dry-run. Reuse frozen IDs.
- Use a bounded FloodWait pattern:

```python
for attempt in range(2):
    try:
        await client(request)
        break
    except errors.FloodWaitError as exc:
        if attempt:
            raise
        await asyncio.sleep(exc.seconds + 1)
```

- Do not catch broad `Exception` and call deletion, retry forever, or report success.
- Treat `MessageNotModifiedError` as an idempotent no-op only when the desired final state has been read back.

### Verification

Read exact returned state:

- sent/edited/forwarded message: `get_messages(target, ids=returned_id)`;
- reaction: fetch frozen IDs and inspect the account’s own reaction;
- deletion: re-audit frozen IDs/remaining candidates;
- profile: `get_me()` and `users.GetFullUserRequest`;
- rights: `get_permissions(group, user)`;
- sticker/custom emoji pack: `messages.GetStickerSetRequest` and expected count/flags.

Return `verified: true` only on actual matching state. If verification fails, return `verified: false` with evidence — never call it success.

## 5. Custom emoji is four different features

Use the right one:

| User asks for | Correct API |
|---|---|
| Emoji inside a text/caption | `client.send_message` or `edit_message`, `parse_mode="html"`, `<tg-emoji emoji-id="ID">visible text</tg-emoji>` |
| Custom emoji reaction | `types.ReactionCustomEmoji(ID)` in the full preserved list passed to `messages.SendReactionRequest` |
| Profile/channel emoji status | `account.UpdateEmojiStatusRequest` or `channels.UpdateEmojiStatusRequest` with `types.EmojiStatus(ID)` |
| Custom emoji pack | `stickers.CreateStickerSetRequest(..., emojis=True)` and sticker set lifecycle methods |

Before status/reaction/pack operations, resolve IDs through `messages.GetCustomEmojiDocumentsRequest`. Never invent `access_hash` or `file_reference`.

## 6. Group/admin actions have extra rules

- Read current permissions first with `client.get_permissions(group, user)`.
- For admin rights, render the **complete intended set** before changing it: APIs replace rights rather than magically merge them.
- Restrictions must be time bounded unless the owner explicitly says permanent.
- Never modify the own account’s permissions automatically.
- After `edit_admin`, `edit_permissions`, kick or ban, read permissions back. `UserNotParticipantError` is evidence only for a kick/remove result.

## 7. Tests are mandatory

Create or extend `tests/test_<topic>.py`.

At minimum test:

1. parser/validation rejects invalid or ambiguous input;
2. dry-run fake client reaches **zero mutating calls**;
3. frozen IDs are preserved, positive and unique;
4. execute uses expected options (`revoke=True`, preserved reactions, bounded restriction deadline, etc.);
5. an error cannot silently turn an edit into a delete.

Fake clients are good. Do **not** write real Telegram data to test a module.

## 8. Definition of done

До gate добавь модуль в registry и `MODULES.md`. Для direct CLI команда в registry обязана использовать `scripts/userbotrun.py`.

```bash
venv/bin/python scripts/userbot_module_registry.py --validate-catalog --json
venv/bin/python scripts/check_module.py modules/<name>.py --full
```

`check_module.py --full` проверяет AST-контракт, регистрацию, CLI help, focused tests, полный suite и `pip check` без реальной записи в Telegram. Независимые offline `--help` проверки выполняются параллельно, но ни одна не пропускается. Если gate падает, модуль не готов.

## Copy-paste task prompt for a weaker model

```text
Implement exactly one new guarded Telethon userbot module in
$USERBOT_ROOT/modules/<name>.py.

First build one context packet for the original request and exact candidate API with:
PY=venv/bin/python
SKILL=${USERBOT_SKILL_DIR:?set USERBOT_SKILL_DIR to the installed userbot skill}
$PY "$SKILL/scripts/telethon_authoring_context.py" --project-root "$USERBOT_ROOT" \
  --query '<original request>' --client-method '<method>' \
  --raw-request '<namespace.Request>' --json

Follow docs/TELETHON_MODULE_AUTHORING.md exactly.
Require a matching installed/project Telethon version and confirm the official docs header matches it.
Prefer a documented high-level TelegramClient method and server-side filters when they cover the task.
Use core.config and core.telegram_targets; never hard-code credentials or call client.start().
Default must be dry-run. Telegram writes only with --execute after an exact target/ID plan.
Handle FloodWait once, read back final server state, return JSON, add fake-client tests, update MODULES.md,
and run the registry validator plus check_module.py --full.
Do not make Telegram network writes during verification.
```
