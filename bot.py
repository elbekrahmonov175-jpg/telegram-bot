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

# ❗ токены теперь берутся из переменных окружения
TELEGRAM_TOKEN = os.getenv("8673766414:AAG97_IplE9OaWphm__RQEiYBo7UYDHSa7A")
GROQ_API_KEY = os.getenv("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")

OWNER_ID = 502740939
DB_PATH = "bot.db"

# короткий SYSTEM PROMPT (экономит токены)
SYSTEM_PROMPT = """
ты эльбек отвечаешь как живой человек

пиши строчными буквами
без знаков препинания
иногда матерись
растягивай буквы
отвечай коротко
не говори что ты бот
"""

# логирование
logging.basicConfig(level=logging.INFO)

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
        (
            user_id,
            username,
            first_name,
            datetime.now().isoformat(),
        ),
    )

    conn.commit()
    conn.close()

def save_message(user_id, role, content):
    conn = sqlite3.connect(DB_PATH)

    conn.execute(
        "INSERT INTO messages (user_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (
            user_id,
            role,
            content,
            datetime.now().isoformat(),
        ),
    )

    conn.commit()
    conn.close()

def get_history(user_id, limit=4):  # ограничение истории
    conn = sqlite3.connect(DB_PATH)

    rows = conn.execute(
        """
        SELECT role, content
        FROM messages
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (user_id, limit),
    ).fetchall()

    conn.close()

    return [
        {"role": r, "content": c}
        for r, c in reversed(rows)
    ]

def clear_history(user_id):
    conn = sqlite3.connect(DB_PATH)

    conn.execute(
        "DELETE FROM messages WHERE user_id = ?",
        (user_id,)
    )

    conn.commit()
    conn.close()

# ==================== GROQ ====================

client = Groq(api_key=GROQ_API_KEY)

def ask_ai(user_id, user_message):

    history = get_history(user_id, limit=4)

    history.append({
        "role": "user",
        "content": user_message
    })

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT}
    ] + history

    try:
        # основная лёгкая модель
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=messages,
            max_tokens=40,
            temperature=0.8,
        )

        return response.choices[0].message.content.strip()

    except Exception as e:

        logging.error(f"Groq error: {e}")

        # fallback модель если первая не работает
        try:

            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages,
                max_tokens=40,
                temperature=0.8,
            )

            return response.choices[0].message.content.strip()

        except:

            return "бля щас не могу ответить"

# ==================== ОБРАБОТЧИКИ ====================

last_message_time = {}

async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        f"твой id: {update.effective_user.id}"
    )

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):

    clear_history(update.effective_user.id)

    await update.message.reply_text(
        "история очищена"
    )

pending_messages: dict[int, dict] = {}

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):

    message = (
        update.business_message
        if update.business_message
        else update.message
    )

    if not message or not message.text:
        return

    user = message.from_user
    user_id = user.id
    user_text = message.text.strip()

    # игнор владельца
    if OWNER_ID and user_id == OWNER_ID:
        return

    # анти-спам (1 сообщение в секунду)
    now = time.time()

    if user_id in last_message_time:
        if now - last_message_time[user_id] < 1:
            return

    last_message_time[user_id] = now

    # ограничение длины
    if len(user_text) > 500:
        await message.reply_text(
            "слишком длинное сообщение"
        )
        return

    save_user(
        user_id,
        user.username or "",
        user.first_name or "",
    )

    chat_id = message.chat.id

    business_connection_id = getattr(
        message,
        'business_connection_id',
        None
    )

    await context.bot.send_chat_action(
        chat_id=chat_id,
        action="typing",
        business_connection_id=business_connection_id
    )

    if user_id not in pending_messages:

        pending_messages[user_id] = {
            "texts": [],
            "task": None,
            "chat_id": chat_id,
            "business_id": business_connection_id,
        }

    pending = pending_messages[user_id]

    pending["texts"].append(user_text)

    pending["chat_id"] = chat_id
    pending["business_id"] = business_connection_id

    if pending["task"] and not pending["task"].done():
        pending["task"].cancel()

    async def delayed_reply():

        await asyncio.sleep(1.2)

        if not pending["texts"]:
            return

        combined_text = " ".join(
            pending["texts"]
        )

        pending["texts"].clear()

        save_message(
            user_id,
            "user",
            combined_text
        )

        reply = ask_ai(
            user_id,
            combined_text
        )

        save_message(
            user_id,
            "assistant",
            reply
        )

        try:

            await context.bot.send_message(
                chat_id=pending["chat_id"],
                text=reply,
                business_connection_id=pending["business_id"]
            )

        except Exception as e:

            logging.error(
                f"Ошибка отправки: {e}"
            )

    pending["task"] = asyncio.create_task(
        delayed_reply()
    )

# ==================== ЗАПУСК ====================

def main():

    init_db()

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler("myid", cmd_myid)
    )

    app.add_handler(
        CommandHandler("clear", cmd_clear)
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message
        )
    )

    print("бот запущен!")

    app.run_polling(
        allowed_updates=Update.ALL_TYPES
    )

if __name__ == "__main__":
    main()
