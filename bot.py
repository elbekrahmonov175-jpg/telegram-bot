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
OWNER_ID = 502740939
DB_PATH = "bot.db"

SYSTEM_PROMPT = """ты эльбек, отвечаешь вместо него в телеграме.

как ты пишешь (строго соблюдай):
- только строчные буквы — никогда никаких заглавных вообще
- никаких знаков препинания — ни точек ни запятых ни восклицательных
- часто одно слово как ответ: реально, да, нет, хоп, ну, похуй, пиздец, чо, ниче
- растягиваешь буквы для эмоций: бляяяя, неееет, почемууу, привееет, окэээ
- иногда капслок для удивления: ПОЧЕМУ, БЛЯТЬ, НУ ТЫ ЧЕ
- смеёшься так: хахахвха, ахаха, хвхвхв — не "хаха" и не "лол"
- материшься естественно: блять, бля, нахуй, пиздец, похуй
- пишешь "чо" вместо "что", "ваще" вместо "вообще", "щас" вместо "сейчас", "типо" вместо "типа"
- никогда не говоришь что ты бот или ии
- если грубят — отвечаешь агрессивно с матом
- отвечай на том языке на котором пишут

реальные примеры как ты отвечаешь (это твои настоящие сообщения, копируй этот стиль):
"привет" -> "о привет" или "привееет" или "привет" или "рассказывай"
"как дела" / "как ты" -> "нормально а ты" или "нормально" или "хуего"
"ты где" -> "щас дома" или "дома" или "заай"
"чо делаешь" -> "ниче", "лежу", "да похуй"
"ну и чо" -> "реально", "ну и ладно", "похуй"
на грубость -> "пошла нахуй", "чо блять", "сам иди нахуй"
согласие -> "хоп", "принял", "ладно"
удивление -> "ваааауу", "пиздец", "ПОЧЕМУУ"

главное правило: отвечай ЗЕРКАЛЬНО по длине и тону. если пишут "привет" — пиши "о привет", не выдумывай ничего лишнего. не добавляй слова которые не нужны."""

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

def get_history(user_id, limit=10):
    """История только этого конкретного пользователя"""
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
    # История строго по user_id — каждый человек видит только свою переписку
    history = get_history(user_id)
    history.append({"role": "user", "content": user_message})

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            max_tokens=80,
            temperature=0.95,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Groq error: {e}")
        return "бля щас не могу нормально ответить"

# ==================== ОБРАБОТЧИКИ ====================
async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"твой id: {update.effective_user.id}")

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_history(update.effective_user.id)
    await update.message.reply_text("история очищена")

# Словарь для накопления коротких сообщений — ключ user_id, изолировано по каждому
pending_messages: dict[int, dict] = {}

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

    chat_id = message.chat.id
    business_connection_id = getattr(message, 'business_connection_id', None)

    await context.bot.send_chat_action(
        chat_id=chat_id,
        action="typing",
        business_connection_id=business_connection_id
    )

    # Накопление сообщений — строго изолировано по user_id
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

    # Отменяем предыдущую задачу если есть
    if pending["task"] and not pending["task"].done():
        pending["task"].cancel()

    async def delayed_reply():
        await asyncio.sleep(1.35)

        if not pending["texts"]:
            return

        combined_text = " ".join(pending["texts"])
        pending["texts"].clear()

        # Сохраняем сообщение пользователя и получаем ответ
        save_message(user_id, "user", combined_text)
        reply = ask_ai(user_id, combined_text)
        save_message(user_id, "assistant", reply)

        try:
            await context.bot.send_message(
                chat_id=pending["chat_id"],
                text=reply,
                business_connection_id=pending["business_id"]
            )
        except Exception as e:
            print(f"Ошибка отправки: {e}")

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
