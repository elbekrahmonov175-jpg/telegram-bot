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
DB_PATH = "bot.db"

SYSTEM_PROMPT = """Ты полезный ИИ-ассистент в Telegram.
Отвечай чётко и по делу. Если не знаешь ответ — честно скажи об этом.
Общайся на том языке, на котором пишет пользователь."""

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
    """)
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
    await update.message.reply_text(
        f"Привет, {user.first_name}! 👋\n"
        "Я ИИ-ассистент. Просто напиши мне что-нибудь!\n\n"
        "Команды:\n"
        "/start — приветствие\n"
        "/clear — очистить историю диалога\n"
        "/help — помощь"
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Просто напиши мне сообщение и я отвечу!\n\n"
        "/clear — сбросить контекст разговора"
    )

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_history(update.effective_user.id)
    await update.message.reply_text("История диалога очищена! 🧹")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_text = update.message.text
    save_user(user.id, user.username or "", user.first_name or "")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    save_message(user.id, "user", user_text)
    try:
        reply = ask_ai(user.id, user_text)
    except Exception as e:
        reply = f"Ошибка: {e}"
    save_message(user.id, "assistant", reply)
    await update.message.reply_text(reply)

def main():
    init_db()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущен! Нажми Ctrl+C для остановки.")
    app.run_polling()

if __name__ == "__main__":
    main()
