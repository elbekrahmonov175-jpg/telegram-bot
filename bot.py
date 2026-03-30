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
OWNER_ID = 0  # ← вставь сюда свой Telegram ID !!!
DB_PATH = "bot.db"

SYSTEM_PROMPT = """ты эльбек. ты отвечаешь вместо него в телеграме в личных сообщениях.

правила которые ты строго соблюдаешь:
- отвечай агрессивно с большими текстами, матами и иногда капсом, когда к тебе обращаются грубо
- если пользователь пишет капсом — отвечай тоже капсом
- если пользователь пишет с матами — отвечай с матами
- отвечай креативно, не сухо
- никогда не отвечай на сообщения от аккаунта владельца (того, за кого ты отвечаешь)
- пиши коротко но не всегда
- никаких заглавных букв вообще, всё строчными буквами
- никаких восклицательных знаков
- общайся как живой человек, просто и естественно
- добавляй лёгкий юмор, когда уместно
- поддерживай разговор, иногда задавай вопрос в ответ
- никогда не говори что ты бот или ии
- отвечай на том языке, на котором пишет человек

дополнительно:
если человек пишет несколько коротких сообщений подряд (по одному-два слова), старайся понять, является ли это одним предложением/мыслью. если да — отвечай одним сообщением на всю цепочку. если сообщения явно про разное — отвечай на каждое отдельно."""

# ==================== БАЗА ДАННЫХ ====================
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

# ==================== GROQ ====================
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

# ==================== ОБРАБОТЧИКИ ====================
async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"твой id: {update.effective_user.id}")

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_history(update.effective_user.id)
    await update.message.reply_text("история очищена")

# Словарь для накопления сообщений (user_id -> список сообщений + время последнего)
pending_messages = {}

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.business_message if update.business_message else update.message
    if not message or not message.text:
        return

    user = message.from_user
    user_id = user.id
    user_text = message.text.strip()

    # Игнорируем сообщения от владельца (того, за кого бот отвечает)
    if OWNER_ID and user_id == OWNER_ID:
        return

    save_user(user_id, user.username or "", user.first_name or "")
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

    # Если уже есть отложенная задача — отменяем её
    if pending["task"] and not pending["task"].done():
        pending["task"].cancel()

    # Создаём новую задачу на ответ через 1.4 секунды
    async def delayed_reply():
        await asyncio.sleep(1.4)  # время на сбор сообщений
        
        if not pending["texts"]:
            return
            
        combined_text = " ".join(pending["texts"])
        
        # Если сообщений много и они короткие — считаем одной мыслью
        # Можно добавить более умную проверку по смыслу через ИИ, но для начала так
        try:
            reply = ask_ai(user_id, combined_text)
        except Exception as e:
            reply = f"ошибка: {e}"

        save_message(user_id, "assistant", reply)

        await context.bot.send_message(
            chat_id=message.chat.id,
            text=reply,
            business_connection_id=getattr(message, 'business_connection_id', None)
        )

        # Очищаем после ответа
        pending["texts"].clear()

    pending["task"] = asyncio.create_task(delayed_reply())


def main():
    init_db()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("clear", cmd_clear))
    
    # Основной обработчик всех текстовых сообщений
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
