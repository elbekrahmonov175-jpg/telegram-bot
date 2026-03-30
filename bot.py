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

# ==================== НАСТРОЙКИ ====================
TELEGRAM_TOKEN = "8673766414:AAG97_IplE9OaWphm__RQEiYBo7UYDHSa7A"
GROQ_API_KEY = "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"
OWNER_ID = 502740939                    # ←←← ВСТАВЬ СЮДА СВОЙ TELEGRAM ID !!!
DB_PATH = "bot.db"

SYSTEM_PROMPT = """ты эльбек. ты отвечаешь вместо него в телеграме в личных сообщениях.

правила которые строго соблюдаешь:
- пиши только строчными буквами, никаких заглавных букв вообще
- никаких восклицательных знаков
- отвечай коротко: максимум 1-2 предложения
- если к тебе пишут грубо или с матами — отвечай агрессивно, с матами и троллингом
- если человек пишет капсом — можешь ответить тоже капсом
- говори естественно, как живой человек, с лёгким юмором и подколами
- поддерживай разговор, иногда задавай вопрос
- никогда не говори что ты бот или ии
- отвечай на том языке, на котором пишет человек"""

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

def get_history(user_id, limit=12):
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
    
    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            max_tokens=200,
            temperature=0.88,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Groq error: {e}")
        return "бля ща не могу нормально ответить, давай чуть позже"

# ==================== ОБРАБОТЧИКИ ====================
async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"твой id: {update.effective_user.id}")

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_history(update.effective_user.id)
    await update.message.reply_text("история очищена")

# Словарь для накопления коротких сообщений
pending_messages = {}  # user_id -> {"texts": [], "task": None, "chat_id": int, "business_id": str|None}

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.business_message if update.business_message else update.message
    if not message or not message.text:
        return

    user = message.from_user
    user_id = user.id
    user_text = message.text.strip()

    # Игнорируем сообщения от владельца
    if OWNER_ID and user_id == OWNER_ID:
        return

    save_user(user_id, user.username or "", user.first_name or "")
    save_message(user_id, "user", user_text)

    # Сохраняем информацию о чате
    chat_id = message.chat.id
    business_connection_id = getattr(message, 'business_connection_id', None)

    await context.bot.send_chat_action(
        chat_id=chat_id,
        action="typing",
        business_connection_id=business_connection_id
    )

    # Логика накопления коротких сообщений
    current_time = datetime.now().timestamp()
    
    if user_id not in pending_messages:
        pending_messages[user_id] = {
            "texts": [], 
            "task": None, 
            "chat_id": chat_id,
            "business_id": business_connection_id,
            "last_time": current_time
        }

    pending = pending_messages[user_id]
    pending["texts"].append(user_text)
    pending["last_time"] = current_time
    pending["chat_id"] = chat_id
    pending["business_id"] = business_connection_id

    # Отменяем предыдущую задачу если есть
    if pending["task"] and not pending["task"].done():
        pending["task"].cancel()

    async def delayed_reply():
        await asyncio.sleep(1.35)
        
        if not pending["texts"]:
            return
            
        combined_text = " ".join(pending["texts"])
        
        reply = ask_ai(user_id, combined_text)
        
        save_message(user_id, "assistant", reply)
        
        try:
            await context.bot.send_message(
                chat_id=pending["chat_id"],
                text=reply,
                business_connection_id=pending["business_id"]
            )
        except Exception as e:
            print(f"Ошибка отправки сообщения: {e}")
        
        pending["texts"].clear()

    pending["task"] = asyncio.create_task(delayed_reply())

def main():
    init_db()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("clear", cmd_clear))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
