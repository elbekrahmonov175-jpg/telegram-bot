import sqlite3
import asyncio
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

OWNER_ID = 123456789  # ←←← ОБЯЗАТЕЛЬНО ИЗМЕНИ НА СВОЙ НАСТОЯЩИЙ TELEGRAM ID !!!

DB_PATH = "bot.db"

SYSTEM_PROMPT = """ты эльбек. ты отвечаешь вместо него в телеграме в личных сообщениях.

правила которые ты строго соблюдаешь:
- отвечай агрессивно с большими текстами, матами и иногда капсом, когда к тебе обращаются грубо
- если пользователь пишет капсом — отвечай тоже капсом
- если пользователь пишет с матами — отвечай с матами
- отвечай креативно, не сухо
- пиши коротко: максимум 1-3 предложения
- никаких заглавных букв вообще, всё строчными буквами
- никаких восклицательных знаков
- общайся как живой человек, просто и естественно
- добавляй лёгкий юмор, когда уместно
- поддерживай разговор, иногда задавай вопрос в ответ
- никогда не говори что ты бот или ии
- отвечай на том языке, на котором пишет человек

если человек пишет несколько коротких сообщений подряд — старайся понять, одно ли это предложение/мысль. если да — отвечай одним сообщением на всю цепочку. если явно про разное — можно отвечать отдельно."""

# ====================== БАЗА ДАННЫХ ======================
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

def get_history(user_id, limit=15):
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

# ====================== GROQ ======================
client = Groq(api_key=GROQ_API_KEY)

def ask_ai(user_id, user_message):
    history = get_history(user_id)
    history.append({"role": "user", "content": user_message})
    
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
    
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        max_tokens=180,
        temperature=0.85,
    )
    return response.choices[0].message.content.strip()

# ====================== ОБРАБОТЧИКИ ======================
async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"твой id: {update.effective_user.id}")

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_history(update.effective_user.id)
    await update.message.reply_text("история очищена")

# Для накопления быстрых сообщений от одного человека
pending_messages = {}

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.business_message if getattr(update, 'business_message', None) else update.message
    if not message or not message.text:
        return

    user_id = message.from_user.id
    user_text = message.text.strip()

    # === ГЛАВНОЕ ИЗМЕНЕНИЕ ===
    # Если это сообщение от тебя (владельца) — полностью игнорируем, бот ничего не делает
    if user_id == OWNER_ID:
        return

    # Сохраняем пользователя и сообщение
    save_user(user_id, message.from_user.username or "", message.from_user.first_name or "")
    save_message(user_id, "user", user_text)

    await context.bot.send_chat_action(
        chat_id=message.chat.id,
        action="typing",
        business_connection_id=getattr(message, 'business_connection_id', None)
    )

    # Логика накопления коротких сообщений
    current_time = datetime.now().timestamp()

    if user_id not in pending_messages:
        pending_messages[user_id] = {"texts": [], "task": None, "last_time": current_time}

    pending = pending_messages[user_id]
    pending["texts"].append(user_text)
    pending["last_time"] = current_time

    if pending["task"] and not pending["task"].done():
        pending["task"].cancel()

    async def delayed_reply():
        await asyncio.sleep(1.5)   # время для сбора сообщений

        if not pending["texts"]:
            return

        combined_text = " ".join(pending["texts"])

        try:
            reply = ask_ai(user_id, combined_text)
        except Exception as e:
            reply = "что-то пошло не так, попробуй позже"

        save_message(user_id, "assistant", reply)

        await context.bot.send_message(
            chat_id=message.chat.id,
            text=reply,
            business_connection_id=getattr(message, 'business_connection_id', None)
        )

        pending["texts"].clear()

    pending["task"] = asyncio.create_task(delayed_reply())


def main():
    init_db()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("clear", cmd_clear))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("бот запущен! отвечает только другим людям")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
