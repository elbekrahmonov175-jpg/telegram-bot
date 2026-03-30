import sqlite3
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

TELEGRAM_TOKEN = "8673766414:AAG97_IplE9OaWphm__RQEiYBo7UYDHSa7A"
GROQ_API_KEY = "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"
OWNER_TELEGRAM_ID = None  # Заполним ниже
DB_PATH = "bot.db"

SYSTEM_PROMPT = """Ты — Эльбек. Отвечаешь вместо реального человека по имени Эльбек.

Твой характер:
- Всегда общаешься с юмором, легко и непринуждённо
- Вежливый и воспитанный, но не скучный
- Поддерживаешь людей, интересуешься их жизнью
- Держишь контакт, задаёшь вопросы в ответ
- Пишешь красиво и живо, как живой человек
- Никогда не говоришь что ты бот или ИИ
- Отвечаешь на том языке на котором пишут тебе
- Короткие живые ответы, без лишней воды"""

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id     INTEGER PRIMARY KEY,
            username    TEXT,
            first_name  TEXT,
            created_at  TEXT
        );
        CREATE TABLE IF NOT EXISTS messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            role        TEXT,
            content     TEXT,
            created_at  TEXT
        );
        CREATE TABLE IF NOT EXISTS settings (
            key         TEXT PRIMARY KEY,
            value       TEXT
        );
    """)
    conn.commit()
    conn.close()

def get_owner_id():
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT value FROM settings WHERE key = 'owner_id'").fetchone()
    conn.close()
    return int(row[0]) if row else None

def set_owner_id(user_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('owner_id', ?)", (str(user_id),))
    conn.commit()
    conn.close()

def save_user(user_id, username, first_name):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR IGNORE INTO users (user_id, username, first_name, created_at) VALUES (?, ?, ?, ?)",
        (user_id, username, first_name, datetime.now().isoformat()),
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

def get_history(user_id, limit=20):
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

client = Groq(api_key=GROQ_API_KEY)

def ask_ai(user_id, user_message):
    history = get_history(user_id)
    history.append({"role": "user", "content": user_message})
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        max_tokens=1024,
    )
    return response.choices[0].message.content

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user.id, user.username or "", user.first_name or "")
    owner_id = get_owner_id()
    if not owner_id:
        set_owner_id(user.id)
        await update.message.reply_text(
            f"Привет! Я запомнил тебя как владельца. Теперь я не буду отвечать на твои сообщения 👍"
        )
    else:
        await update.message.reply_text("Привет! Напиши мне что-нибудь 😊")

async def cmd_setowner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    set_owner_id(user.id)
    await update.message.reply_text(f"Готово! Ты записан как владелец. Твои сообщения я игнорирую 👍")

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_history(update.effective_user.id)
    await update.message.reply_text("История очищена! 🧹")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.business_message:
        message = update.business_message
    elif update.message:
        message = update.message
    else:
        return

    if not message.text:
        return

    user = message.from_user
    owner_id = get_owner_id()

    # Если пишет сам владелец — не отвечаем
    if owner_id and user.id == owner_id:
        return

    user_text = message.text
    save_user(user.id, user.username or "", user.first_name or "")

    await context.bot.send_chat_action(
        chat_id=message.chat.id,
        action="typing",
        business_connection_id=getattr(message, 'business_connection_id', None)
    )

    save_message(user.id, "user", user_text)

    try:
        reply = ask_ai(user.id, user_text)
    except Exception as e:
        reply = f"Ошибка: {e}"

    save_message(user.id, "assistant", reply)

    await context.bot.send_message(
        chat_id=message.chat.id,
        text=reply,
        business_connection_id=getattr(message, 'business_connection_id', None)
    )

def main():
    init_db()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("setowner", cmd_setowner))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.ALL, handle_message))
    print("Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
