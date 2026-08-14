from __future__ import annotations

import logging
import os
import aiohttp
import asyncio
import random
import re
import json
import subprocess
import tempfile
import shutil
from telethon import TelegramClient, events, Button, functions, types
from pathlib import Path
from dotenv import load_dotenv
import speech_recognition as sr

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
env_file = Path(os.getenv("USERBOT_ENV_FILE", BASE_DIR / ".env"))
load_dotenv(env_file)

BOT_TOKEN = os.getenv("bot_token")
LOG_GROUP_ID_RAW = os.getenv("Group_autorespomder")
OPENROUTER_KEY = os.getenv("Open_Router_Key")

runtime_dir = Path(os.getenv("USERBOT_RUNTIME_DIR", BASE_DIR))
memory_root = Path(os.getenv("USERBOT_MEMORY_DIR", runtime_dir / "memory"))
transcripts_root = Path(os.getenv("USERBOT_TRANSCRIPTS_DIR", runtime_dir / "data" / "transcripts"))

DATA_DIR = transcripts_root
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = memory_root / "userbot_config.json"
PEOPLE_DIR = memory_root / "people"
PEOPLE_DIR.mkdir(parents=True, exist_ok=True)
SOUL_FILE = memory_root / "SOUL.md"

# Config Management
def load_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {
                    "global_auto": data.get("global_auto", False),
                    "auto_chats": data.get("auto_chats", []),
                    "auto_users": data.get("auto_users", []),
                    "chat_styles": data.get("chat_styles", {}),
                    "user_styles": data.get("user_styles", {}),
                    "global_prompt": data.get("global_prompt", "")
                }
        except Exception:
            pass
    return {"global_auto": False, "auto_chats": [], "auto_users": [], "chat_styles": {}, "user_styles": {}, "global_prompt": ""}

def save_config(config_data):
    clean = {
        "global_auto": config_data.get("global_auto", False),
        "auto_chats": config_data.get("auto_chats", []),
        "auto_users": config_data.get("auto_users", []),
        "chat_styles": config_data.get("chat_styles", {}),
        "user_styles": config_data.get("user_styles", {}),
        "global_prompt": config_data.get("global_prompt", "")
    }
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)

# Short memory per user
DEFAULT_MEMORY = {
    "nick": "",
    "relation": "",
    "last_topics": []
}

def load_user_memory(user_id):
    path = PEOPLE_DIR / f"{user_id}.json"
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {
                    "nick": data.get("nick", ""),
                    "relation": data.get("relation", ""),
                    "last_topics": data.get("last_topics", [])[-3:]
                }
        except Exception:
            pass
    return DEFAULT_MEMORY.copy()

def save_user_memory(user_id, data):
    path = PEOPLE_DIR / f"{user_id}.json"
    clean = {
        "nick": (data.get("nick") or "").strip(),
        "relation": (data.get("relation") or "").strip(),
        "last_topics": [str(x).strip() for x in data.get("last_topics", []) if str(x).strip()][-3:]
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)

def get_soul():
    if SOUL_FILE.exists():
        return SOUL_FILE.read_text(encoding='utf-8')
    return "Ты — Лэйн, цифровой фамильяр disroot. Коротко, цинично, без эмодзи."

# Tools
async def gif_search_and_download(query: str) -> str | None:
    try:
        cmd = ["gifgrep", "search", "--json", "--max", "1", query]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if data and len(data) > 0:
                gif_url = data[0].get("url")
                path = DATA_DIR / f"gif_{random.randint(0,1000)}.gif"
                async with aiohttp.ClientSession() as session:
                    async with session.get(gif_url) as resp:
                        if resp.status == 200:
                            path.write_bytes(await resp.read())
                            return str(path)
    except Exception:
        pass
    return None

async def ddg_search(query: str) -> str:
    url = f"https://html.duckduckgo.com/html/?q={query}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    snippets = re.findall(r'<a class="result__snippet".*?>(.*?)</a>', html, re.S)[:3]
                    results = "\n".join([re.sub('<[^<]+?>', '', s).strip() for s in snippets])
                    return results if results else "Инфы нет."
    except Exception:
        return "Ошибка поиска."
    return "Ничего не нашел."

# Voice STT Logic
async def get_voice_transcript(client: TelegramClient, event: events.NewMessage.Event) -> str | None:
    is_voice = bool(getattr(event.message, "voice", None))
    is_video_note = bool(getattr(event.message, "video_note", None))
    if not (is_voice or is_video_note):
        return None
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        return "[ошибка: ffmpeg не найден]"
    language = os.getenv("GOOGLE_SPEECH_LANGUAGE", "ru-RU")
    with tempfile.TemporaryDirectory(prefix="stt_") as tmp_dir:
        src_path_str = await client.download_media(event.message, file=tmp_dir)
        if not src_path_str:
            return "[ошибка скачивания]"
        src_path = Path(src_path_str)
        wav_path = Path(tmp_dir) / "input.wav"
        try:
            proc = subprocess.run([ffmpeg_bin, "-y", "-i", str(src_path), "-vn", "-ac", "1", "-ar", "16000", str(wav_path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if proc.returncode != 0:
                return "[ошибка конвертации]"
            recognizer = sr.Recognizer()
            with sr.AudioFile(str(wav_path)) as source:
                audio_data = recognizer.record(source)
            text = await asyncio.to_thread(recognizer.recognize_google, audio_data, language=language)
            return text.strip() if text else None
        except Exception as e:
            logger.error(f"STT Error: {e}")
            return "[ошибка расшифровки]"

# Context
async def get_full_context(client, chat_id, limit=20):
    messages = await client.get_messages(chat_id, limit=limit)
    context_lines = []
    for m in reversed(messages):
        if m.out:
            sender_display = "Я (disroot)"
        else:
            sender = m.sender
            name = getattr(sender, 'first_name', 'Кто-то')
            sender_display = f"{name} | ID:{m.sender_id}"
        text = m.text or "[Медиа/Стикер/Голосовое]"
        context_lines.append(f"{sender_display}: {text}")
    return "\n".join(context_lines)

# Agent Logic
async def agent_loop(chat_id, sender_id, context_text, instruction=None):
    if not OPENROUTER_KEY:
        return {"text": "Error", "actions": []}

    current_config = load_config()
    user_mem = load_user_memory(sender_id)
    soul = get_soul()

    # Style priority: User > Chat > Neutral
    chat_style = current_config.get("user_styles", {}).get(str(sender_id))
    if not chat_style:
        chat_style = current_config.get("chat_styles", {}).get(str(chat_id), "neutral")

    style_desc = "disroot. " + ("Элитный мизантроп с 2ch. Стеб, мат." if chat_style == "funny" else "Технарь. По делу, но живо.")
    extra_prompt = current_config.get("global_prompt", "").strip()

    memory_block = []
    if user_mem.get("nick"):
        memory_block.append(f"- Как обычно называть собеседника: {user_mem['nick']}")
    if user_mem.get("relation"):
        memory_block.append(f"- Краткая заметка: {user_mem['relation']}")
    if user_mem.get("last_topics"):
        memory_block.append(f"- Последние темы: {', '.join(user_mem['last_topics'])}")

    memory_text = "\n".join(memory_block) if memory_block else "- Память пустая"

    full_system = (
        f"{soul}\n\n"
        f"ТЕКУЩИЙ СТИЛЬ ОТВЕТА: {style_desc}\n\n"
        "КРАТКАЯ ПАМЯТЬ О СОБЕСЕДНИКЕ:\n"
        f"{memory_text}\n\n"
        "Используй память только как лёгкий контекст, если она реально помогает. "
        "Не строй психологические профили, не придумывай архетипы, не анализируй личность, не подстраивай манеру как будто у тебя досье на человека. "
        "Отвечай естественно, ровно и по содержанию текущего диалога. "
        "Если уместно, можешь обновить краткую память, но только в простом формате.\n"
        "Сначала напиши <thought>...</thought> (внутренний монолог: что хочет человек и как лучше ответить). "
        "Затем выдай финальный текст (длина любая, уместная теме), БЕЗ ЭМОДЗИ, без префиксов.\n"
        "ИНСТРУМЕНТЫ: [SEARCH: запрос], [GIF: тема], [REACT: emoji], [UPDATE_MEMORY: кличка | краткая заметка | тема], [IGNORE].\n"
    )

    if extra_prompt:
        full_system += f"\nДОПОЛНИТЕЛЬНАЯ ИНСТРУКЦИЯ:\n{extra_prompt}\n"

    user_task = f"ЛОГ ЧАТА:\n{context_text}\n\nЗАДАЧА: Ответь естественно и без повторяющихся клише."
    if instruction:
        user_task += f"\n\nДОП. ИНСТРУКЦИЯ ОТ ОПЕРАТОРА:\n{instruction}"

    messages = [
        {"role": "system", "content": full_system},
        {"role": "user", "content": user_task}
    ]

    async def call_llm(msgs):
        payload = {"model": "deepseek/deepseek-chat", "messages": msgs, "temperature": 1.0}
        async with aiohttp.ClientSession() as session:
            async with session.post("https://openrouter.ai/api/v1/chat/completions", headers={"Authorization": f"Bearer {OPENROUTER_KEY}"}, json=payload) as resp:
                data = await resp.json()
                return data['choices'][0]['message']['content'].strip()

    def sanitize(raw_text):
        clean = re.sub(r'<thought>.*?</thought>', '', raw_text, flags=re.DOTALL | re.IGNORECASE)
        clean = re.sub(r'\[(SEARCH|GIF|REACT|IGNORE|UPDATE_MEMORY):.*?\]', '', clean, flags=re.IGNORECASE)
        clean = re.sub(r'(Подумаю вслух|Внутренний монолог|Думаю|Рассуждение|Think|Thought|Reasoning)[:\s\-]*.*?\n', '', clean, flags=re.IGNORECASE)
        clean = re.sub(r"^(Ответ|disroot|дизрут|User_\d+|Текст)[:\s\-]*", "", clean.strip(), flags=re.IGNORECASE).strip()
        clean = clean.strip('"').strip("'")
        if clean.endswith(".") and not clean.endswith(".."):
            clean = clean[:-1]
        return clean.strip()

    for _ in range(2):
        ans = await call_llm(messages)
        logger.debug("Agent raw output received (%s characters)", len(ans))
        if "[SEARCH:" in ans:
            q_match = re.search(r"\[SEARCH:\s*(.*?)\]", ans, re.IGNORECASE)
            if q_match:
                res = await ddg_search(q_match.group(1))
                messages.append({"role": "assistant", "content": ans})
                messages.append({"role": "user", "content": f"РЕЗУЛЬТАТ ПОИСКА: {res}\nФормируй ответ."})
                continue
        actions = []
        if "[IGNORE]" in ans:
            return {"text": None, "actions": [{"type": "ignore"}]}

        mem_match = re.search(r"\[UPDATE_MEMORY:\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\]", ans)
        if mem_match:
            nick = mem_match.group(1).strip()
            relation = mem_match.group(2).strip()
            topic = mem_match.group(3).strip()
            new_mem = load_user_memory(sender_id)
            if nick and nick != "-":
                new_mem["nick"] = nick
            if relation and relation != "-":
                new_mem["relation"] = relation
            if topic and topic != "-":
                topics = new_mem.get("last_topics", [])
                topics.append(topic)
                new_mem["last_topics"] = topics[-3:]
            save_user_memory(sender_id, new_mem)

        gif_match = re.search(r"\[GIF:\s*(.*?)\]", ans, re.IGNORECASE)
        if gif_match:
            actions.append({"type": "gif", "query": gif_match.group(1)})
        react_match = re.search(r"\[REACT:\s*(.*?)\]", ans, re.IGNORECASE)
        if react_match:
            actions.append({"type": "react", "emoji": react_match.group(1).strip()})
        return {"text": sanitize(ans), "actions": actions}
    return {"text": "...", "actions": []}

async def send_human_reply(client, target_id, agent_res, reply_to=None):
    if any(a["type"] == "ignore" for a in agent_res.get("actions", [])):
        return
    for action in agent_res.get("actions", []):
        if action["type"] == "react" and reply_to:
            try:
                await client(functions.messages.SendReactionRequest(peer=target_id, msg_id=reply_to, reaction=[types.ReactionEmoji(emoticon=action["emoji"])]))
            except Exception:
                pass
    for action in agent_res.get("actions", []):
        if action["type"] == "gif":
            gif_path = await gif_search_and_download(action["query"])
            if gif_path:
                await asyncio.sleep(random.uniform(1, 2))
                await client.send_file(target_id, gif_path, reply_to=reply_to)
                if os.path.exists(gif_path):
                    os.remove(gif_path)
                return
    text = agent_res.get("text")
    if text:
        await asyncio.sleep(random.uniform(2.0, 4.0))
        async with client.action(target_id, 'typing'):
            await asyncio.sleep(len(text) * 0.05 + random.uniform(1.0, 2.0))
            await client.send_message(target_id, text, reply_to=reply_to)

def register(client: TelegramClient) -> None:
    if not BOT_TOKEN or not LOG_GROUP_ID_RAW:
        return
    bot_target_group = int(LOG_GROUP_ID_RAW)
    bot_client = TelegramClient(str(runtime_dir / "bot_assistant"), client.api_id, client.api_hash)
    owner_id: int | None = None

    async def is_owner(event: Any) -> bool:
        nonlocal owner_id
        if owner_id is None:
            owner = await client.get_me()
            owner_id = owner.id
        return event.sender_id == owner_id

    @client.on(events.NewMessage())
    async def handler(event: events.NewMessage.Event):
        if event.chat_id == bot_target_group:
            return

        msg_content = event.text or ""
        keywords = [r"рут", r"рутик"]
        pattern = r"\b(" + "|".join(keywords) + r")\b"
        has_keyword = bool(re.search(pattern, msg_content, re.IGNORECASE))

        is_mention = event.mentioned or event.is_private or has_keyword
        if not is_mention and event.is_reply:
            reply = await event.get_reply_message()
            if reply and reply.out:
                is_mention = True
        if is_mention:
            sender = await event.get_sender()
            if not sender or (hasattr(sender, 'bot') and sender.bot) or (hasattr(sender, 'is_self') and sender.is_self):
                return
            msg_text = event.text
            if not msg_text and (event.voice or event.video_note):
                transcript = await get_voice_transcript(client, event)
                msg_text = f"[🎙 расшифровка]: {transcript}" if transcript else "[🎙 Голосовое]"
            elif not msg_text:
                msg_text = "[Медиа]"
            context_text = await get_full_context(client, event.chat_id)
            if not bot_client.is_connected():
                await bot_client.start(bot_token=BOT_TOKEN)
            curr_config = load_config()
            user_mem = load_user_memory(sender.id)
            cid_str = str(event.chat_id)
            uid_str = str(sender.id)
            is_auto = cid_str in curr_config.get("auto_chats", []) or uid_str in curr_config.get("auto_users", []) or curr_config.get("global_auto", False)

            chat_title = "Личка"
            if event.is_group:
                chat = await event.get_chat()
                chat_title = getattr(chat, 'title', 'Группа')

            style = curr_config.get("user_styles", {}).get(uid_str)
            if not style:
                style = curr_config.get("chat_styles", {}).get(cid_str, "neutral")

            memory_note = user_mem.get("relation") or "—"
            if user_mem.get("nick"):
                memory_note = f"{user_mem['nick']} | {memory_note}"

            report = (
                f"👤 **{sender.first_name}** (UserID: `{sender.id}`)\n"
                f"📍 Чат: **{chat_title}**\n"
                f"🧠 Память: {memory_note}\n"
                f"💬: {msg_text}\n"
                f"📊 Режим: {'🟢 AUTO' if is_auto else '⚪️ MANUAL'} ({style})"
            )
            buttons = [
                [Button.inline("👔 Chat Biz", data=f"st_business_{event.chat_id}"), Button.inline("😐 Chat Neut", data=f"st_neutral_{event.chat_id}"), Button.inline("🤡 Chat Fun", data=f"st_funny_{event.chat_id}")],
                [Button.inline("👔 User Biz", data=f"ust_business_{sender.id}"), Button.inline("😐 User Neut", data=f"ust_neutral_{sender.id}"), Button.inline("🤡 User Fun", data=f"ust_funny_{sender.id}")],
                [Button.inline("✅ CHAT", data=f"on_{event.chat_id}"), Button.inline("👤 USER", data=f"usr_on_{event.chat_id}_{sender.id}"), Button.inline("❌ OFF", data=f"off_{event.chat_id}_{sender.id}"), Button.inline("🗑 Скрыть", data="del")]
            ]
            await bot_client.send_message(bot_target_group, report, buttons=buttons)
            if is_auto:
                res = await agent_loop(event.chat_id, sender.id, context_text)
                client.loop.create_task(send_human_reply(client, event.chat_id, res, reply_to=event.id))

    @bot_client.on(events.CallbackQuery)
    async def cb_handler(event: events.CallbackQuery.Event):
        if not await is_owner(event):
            await event.answer("Недоступно.", alert=True)
            return
        data = event.data.decode()
        curr_config = load_config()
        if data.startswith("st_"):
            parts = data.split("_")
            curr_config["chat_styles"][str(parts[2])] = parts[1]
            save_config(curr_config)
            await event.answer(f"Стиль чата: {parts[1]}")
            msg = await event.get_message()
            await event.edit(re.sub(r"\(.*?\)", f"({parts[1]})", msg.text), buttons=msg.buttons)
        elif data.startswith("ust_"):
            parts = data.split("_")
            curr_config.setdefault("user_styles", {})[str(parts[2])] = parts[1]
            save_config(curr_config)
            await event.answer(f"Стиль юзера: {parts[1]}")
            msg = await event.get_message()
            await event.edit(re.sub(r"\(.*?\)", f"({parts[1]})", msg.text), buttons=msg.buttons)
        elif data.startswith("on_"):
            cid = data.split("_")[1]
            auto_chats = curr_config.get("auto_chats", [])
            if str(cid) not in auto_chats:
                auto_chats.append(str(cid))
                curr_config["auto_chats"] = auto_chats
                save_config(curr_config)
            await event.answer("CHAT ON")
            msg = await event.get_message()
            await event.edit(msg.text.replace("⚪️ MANUAL", "🟢 AUTO"), buttons=msg.buttons)
        elif data.startswith("usr_on_"):
            parts = data.split("_")
            uid = parts[3]
            auto_users = curr_config.get("auto_users", [])
            if str(uid) not in auto_users:
                auto_users.append(str(uid))
                curr_config["auto_users"] = auto_users
                save_config(curr_config)
            await event.answer("USER ON")
            msg = await event.get_message()
            await event.edit(msg.text.replace("⚪️ MANUAL", "🟢 AUTO"), buttons=msg.buttons)
        elif data.startswith("off_"):
            parts = data.split("_")
            cid = parts[1]
            uid = parts[2] if len(parts) > 2 else None

            auto_chats = curr_config.get("auto_chats", [])
            if str(cid) in auto_chats:
                auto_chats = [c for c in auto_chats if c != str(cid)]
                curr_config["auto_chats"] = auto_chats

            auto_users = curr_config.get("auto_users", [])
            if uid and str(uid) in auto_users:
                auto_users = [u for u in auto_users if u != str(uid)]
                curr_config["auto_users"] = auto_users

            save_config(curr_config)
            await event.answer("OFF")
            msg = await event.get_message()
            await event.edit(msg.text.replace("🟢 AUTO", "⚪️ MANUAL"), buttons=msg.buttons)
        elif data == "del":
            await event.delete()

    @bot_client.on(events.NewMessage(chats=bot_target_group))
    async def bot_reply_handler(event: events.NewMessage.Event):
        if not await is_owner(event):
            return
        if event.text.startswith("/prompt "):
            new_prompt = event.text[8:].strip()
            curr_config = load_config()
            curr_config["global_prompt"] = new_prompt
            save_config(curr_config)
            await event.reply("✅ Ок.")
            return
        if not event.is_reply:
            return
        reply_to_msg = await event.get_reply_message()
        target_id = None
        if reply_to_msg.buttons:
            for row in reply_to_msg.buttons:
                for btn in row:
                    if btn.data and '_' in btn.data.decode():
                        parts = btn.data.decode().split('_')
                        target_id = int(parts[-1])
        if not target_id:
            return
        context_text = await get_full_context(client, target_id)
        messages = await client.get_messages(target_id, limit=1)
        sender_id = messages[0].sender_id if messages else target_id
        last_id = messages[0].id if messages else None
        await event.reply("⌛")
        res = await agent_loop(target_id, sender_id, context_text, instruction=event.text)
        client.loop.create_task(send_human_reply(client, target_id, res, reply_to=last_id))
        await event.reply(f"🚀 `{res.get('text') if res.get('text') else 'OK'}`")

    async def start_bot():
        await bot_client.start(bot_token=BOT_TOKEN)

    def log_start_failure(task):
        try:
            task.result()
        except Exception:
            logger.exception("Control bot startup failed")

    client.loop.create_task(start_bot()).add_done_callback(log_start_failure)
