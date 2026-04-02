import sqlite3
import asyncio
import os
import time
import logging
from datetime import datetime

from groq import Groq
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ==================== НАСТРОЙКИ ====================
TELEGRAM_TOKEN = "8673766414:AAEJphWXAwRfGS8njXWabHNuh0oT2u3LWp0"
GROQ_API_KEY = "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"

OWNER_ID = 502740939
DB_PATH = "bot.db"
TEMP_DIR = "temp_audio"
os.makedirs(TEMP_DIR, exist_ok=True)

SYSTEM_PROMPT = """
ты эльбек отвечаешь как живой человек
пиши строчными буквами
без знаков препинания
иногда матерись
растягивай буквы
отвечай коротко
не говори что ты бот
"""

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# ==================== БАЗА ДАННЫХ ====================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            role TEXT,
            content TEXT,
            created_at TEXT
        );
    """)
    conn.commit()
    conn.close()

def save_user(user_id, username, first_name):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR IGNORE INTO users VALUES (?, ?, ?, ?)",
        (user_id, username or "", first_name or "", datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()

def save_message(user_id, role, content):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO messages (user_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (user_id, role, content, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()

def get_history(user_id, limit=4):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    conn.close()
    return [{"role": r, "content": c} for r, c in reversed(rows)]

def clear_history(user_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

# ==================== GROQ ====================
client = Groq(api_key=GROQ_API_KEY)

async def transcribe_voice(file_path: str) -> str:
    try:
        with open(file_path, "rb") as audio_file:
            transcription = client.audio.transcriptions.create(
                file=(os.path.basename(file_path), audio_file.read()),
                model="whisper-large-v3",
                response_format="text",
                language="ru",
            )
        return transcription.strip()
    except Exception as e:
        logging.error(f"Transcription error: {e}")
        return "не смог разобрать голосовое бля"

def ask_ai(user_id, user_message):
    history = get_history(user_id, limit=4)
    history.append({"role": "user", "content": user_message})

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history

    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=messages,
            max_tokens=70,
            temperature=0.85,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logging.error(f"Groq chat error: {e}")
        try:
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages,
                max_tokens=70,
                temperature=0.85,
            )
            return response.choices[0].message.content.strip()
        except:
            return "бля щас не могу ответить"

# ==================== ОБРАБОТЧИКИ ====================
last_message_time = {}
pending_messages: dict[int, dict] = {}

async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"твой id: {update.effective_user.id}")

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_history(update.effective_user.id)
    await update.message.reply_text("история очищена")

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.business_message or update.message
    if not message or not message.voice:
        return

    user = message.from_user
    user_id = user.id

    if OWNER_ID and user_id == OWNER_ID:
        return

    now = time.time()
    if user_id in last_message_time and now - last_message_time[user_id] < 1.5:
        return
    last_message_time[user_id] = now

    voice = message.voice
    if voice.duration > 70:
        await message.reply_text("голосовое слишком длинное брат")
        return

    chat_id = message.chat.id
    business_connection_id = getattr(message, 'business_connection_id', None)

    await context.bot.send_chat_action(
        chat_id=chat_id, action="typing", business_connection_id=business_connection_id
    )

    file = await context.bot.get_file(voice.file_id)
    file_path = os.path.join(TEMP_DIR, f"voice_{user_id}_{int(time.time())}.ogg")

    await file.download_to_drive(file_path)

    text = await transcribe_voice(file_path)

    try:
        os.remove(file_path)
    except:
        pass

    if not text or len(text) < 2:
        await message.reply_text("ничего не разобрал")
        return

    save_user(user_id, user.username, user.first_name)
    save_message(user_id, "user", text)

    await message.reply_text(f"распознал:\n{text}")

    reply = ask_ai(user_id, text)
    save_message(user_id, "assistant", reply)

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=reply,
            business_connection_id=business_connection_id
        )
    except Exception as e:
        logging.error(f"Ошибка отправки: {e}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.business_message or update.message
    if not message or not message.text:
        return

    user_id = message.from_user.id
    user_text = message.text.strip()

    if OWNER_ID and user_id == OWNER_ID:
        return

    now = time.time()
    if user_id in last_message_time and now - last_message_time[user_id] < 1.0:
        return
    last_message_time[user_id] = now

    if len(user_text) > 500:
        await message.reply_text("слишком длинное сообщение")
        return

    save_user(user_id, message.from_user.username, message.from_user.first_name)

    chat_id = message.chat.id
    business_connection_id = getattr(message, 'business_connection_id', None)

    await context.bot.send_chat_action(
        chat_id=chat_id, action="typing", business_connection_id=business_connection_id
    )

    if user_id not in pending_messages:
        pending_messages[user_id] = {"texts": [], "task": None, "chat_id": chat_id, "business_id": business_connection_id}

    pending = pending_messages[user_id]
    pending["texts"].append(user_text)
    pending["chat_id"] = chat_id
    pending["business_id"] = business_connection_id

    if pending["task"] and not pending["task"].done():
        pending["task"].cancel()

    async def delayed_reply():
        await asyncio.sleep(1.1)
        if not pending["texts"]:
            return
        combined = " ".join(pending["texts"])
        pending["texts"].clear()

        save_message(user_id, "user", combined)
        reply = ask_ai(user_id, combined)
        save_message(user_id, "assistant", reply)

        try:
            await context.bot.send_message(
                chat_id=pending["chat_id"],
                text=reply,
                business_connection_id=pending["business_id"]
            )
        except Exception as e:
            logging.error(f"Ошибка отправки: {e}")

    pending["task"] = asyncio.create_task(delayed_reply())

# ==================== ЗАПУСК ====================
def main():
    init_db()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("clear", cmd_clear))

    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("бот эльбек с поддержкой голосовых запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
