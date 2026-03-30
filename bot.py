import sqlite3
import asyncio
import pandas as pd
from datetime import datetime, timedelta
from groq import Groq
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ===================== НАСТРОЙКИ =====================
TELEGRAM_TOKEN = "8673766414:AAG97_IplE9OaWphm__RQEiYBo7UYDHSa7A"
GROQ_API_KEY = "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"

OWNER_ID = 0  # ←←← ОБЯЗАТЕЛЬНО замени на свой реальный Telegram ID

EXCEL_FILE = "appointments.xlsx"
DB_PATH = "clinic_bot.db"

# Рабочие часы клиники
WORKING_HOURS = {
    "start": 9,   # 09:00
    "end": 20,    # 20:00
    "weekend": 6  # воскресенье (0=понедельник)
}

# Ориентировочные цены (в сумах)
PRICES = """
Консультация врача — 100 000 сум
Анестезия — 50 000 – 150 000 сум
Лечение кариеса (световая пломба) — от 300 000 до 700 000 сум
Лечение пульпита (1 канал) — от 450 000 сум
Профессиональная чистка зубов (Air Flow) — от 600 000 сум
Отбеливание зубов (Amazing White / ZOOM) — от 2 000 000 до 3 500 000 сум
Рентген одного зуба — 50 000 – 60 000 сум
"""

SYSTEM_PROMPT = """Ты — вежливый, дружелюбный и профессиональный администратор стоматологической клиники «Улыбка» в Ташкенте.

Общайся максимально тепло, корректно и заботливо. Используй эмодзи умеренно. 
Отвечай на языке пациента (русский или узбекский).
Помогай с вопросами о услугах и ценах.
При записи собирай данные пошагово: ФИО, телефон, удобная дата и время, основная жалоба.
Всегда подтверждай запись и говори, что администратор свяжется для окончательного подтверждения.
Никогда не используй мат, капс или неформальный сленг."""

# ===================== БАЗА ДАННЫХ =====================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            patient_name TEXT,
            phone TEXT,
            appointment_date TEXT,
            complaint TEXT,
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

def is_duplicate_appointment(phone: str, appointment_date: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT id FROM appointments WHERE phone = ? AND appointment_date = ?",
        (phone, appointment_date)
    ).fetchone()
    conn.close()
    return row is not None

def save_appointment(user_id, name, phone, app_date, complaint):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """INSERT INTO appointments 
           (user_id, patient_name, phone, appointment_date, complaint, created_at) 
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, name, phone, app_date, complaint, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

def get_all_appointments():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM appointments ORDER BY id DESC", conn)
    conn.close()
    return df

def save_message(user_id, role, content):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO messages (user_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (user_id, role, content, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

def init_excel():
    try:
        pd.read_excel(EXCEL_FILE)
    except:
        df = pd.DataFrame(columns=["id", "user_id", "patient_name", "phone", "appointment_date", "complaint", "created_at"])
        df.to_excel(EXCEL_FILE, index=False)

# ===================== GROQ =====================
client = Groq(api_key=GROQ_API_KEY)

def ask_ai(user_message):
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message}
    ]
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        max_tokens=350,
        temperature=0.75,
    )
    return response.choices[0].message.content.strip()

# ===================== КНОПКИ =====================
def get_main_menu():
    keyboard = [
        [InlineKeyboardButton("📅 Записаться на приём", callback_data="start_record")],
        [InlineKeyboardButton("💰 Посмотреть цены", callback_data="show_prices")],
        [InlineKeyboardButton("❓ Задать вопрос", callback_data="ask_question")],
    ]
    return InlineKeyboardMarkup(keyboard)

# ===================== КОМАНДЫ =====================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Здравствуйте! 👋\n"
        "Добро пожаловать в стоматологическую клинику «Улыбка».\n"
        "Я ваш персональный администратор. Чем могу помочь сегодня?",
        reply_markup=get_main_menu()
    )

async def cmd_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Актуальные ориентировочные цены:\n\n{PRICES}\n\nТочные цены зависят от клинического случая. Для уточнения рекомендую записаться на консультацию.")

async def cmd_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("Доступ запрещён.")
        return

    df = get_all_appointments()
    if df.empty:
        await update.message.reply_text("Пока нет записей.")
        return

    df.to_excel(EXCEL_FILE, index=False)
    with open(EXCEL_FILE, "rb") as f:
        await context.bot.send_document(
            chat_id=OWNER_ID,
            document=InputFile(f, filename=f"записи_{datetime.now().strftime('%Y-%m-%d_%H%M')}.xlsx"),
            caption="📋 Актуальный список всех записей"
        )

# ===================== ОБРАБОТКА КНОПОК =====================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    user_id = query.from_user.id

    if data == "start_record":
        context.user_data["record_step"] = "name"
        context.user_data["record_data"] = {}
        await query.edit_message_text("Пожалуйста, напишите ваше ФИО полностью:")

    elif data == "show_prices":
        await query.edit_message_text(f"💰 Ориентировочные цены:\n\n{PRICES}\n\nХотите записаться на приём?", reply_markup=get_main_menu())

    elif data == "ask_question":
        await query.edit_message_text("Задайте любой вопрос, я с радостью отвечу!")

# ===================== ОСНОВНОЙ ОБРАБОТЧИК СООБЩЕНИЙ =====================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return

    user_id = message.from_user.id
    text = message.text.strip()

    # Игнорируем владельца
    if user_id == OWNER_ID and text.startswith("/"):
        return

    save_message(user_id, "user", text)

    # === Режим записи на приём ===
    if context.user_data.get("record_step"):
        step = context.user_data["record_step"]
        data = context.user_data.setdefault("record_data", {})

        if step == "name":
            data["name"] = text
            context.user_data["record_step"] = "phone"
            await message.reply_text("Отлично! Теперь укажите ваш номер телефона (+998 XX XXX XX XX):")

        elif step == "phone":
            data["phone"] = text
            context.user_data["record_step"] = "date"
            await message.reply_text("На какую дату и время вам удобно записаться?\nПример: 05 апреля в 14:00")

        elif step == "date":
            data["date"] = text
            context.user_data["record_step"] = "complaint"
            await message.reply_text("Расскажите, пожалуйста, какая у вас жалоба или какая услуга нужна?")

        elif step == "complaint":
            data["complaint"] = text

            # Проверка дублирования
            if is_duplicate_appointment(data["phone"], data["date"]):
                await message.reply_text("Извините, на эту дату и время уже есть запись с вашего номера. Выберите другое время или свяжитесь с нами.")
                context.user_data.clear()
                await message.reply_text("Главное меню:", reply_markup=get_main_menu())
                return

            # Сохраняем запись
            save_appointment(
                user_id,
                data["name"],
                data["phone"],
                data["date"],
                data["complaint"]
            )

            confirmation = (f"✅ Спасибо! Ваша запись принята.\n\n"
                            f"ФИО: {data['name']}\n"
                            f"Телефон: {data['phone']}\n"
                            f"Дата и время: {data['date']}\n"
                            f"Жалоба: {data['complaint']}\n\n"
                            f"В ближайшее время администратор свяжется с вами для подтверждения.")

            await message.reply_text(confirmation, reply_markup=get_main_menu())

            # Уведомление владельцу
            try:
                notification = (f"🆕 Новая запись!\n\n"
                                f"Пациент: {data['name']}\n"
                                f"Телефон: {data['phone']}\n"
                                f"Дата: {data['date']}\n"
                                f"Жалоба: {data['complaint']}\n"
                                f"От: {message.from_user.full_name}")
                await context.bot.send_message(chat_id=OWNER_ID, text=notification)
            except:
                pass

            context.user_data.clear()

        return

    # Обычный режим (вопросы)
    await context.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    try:
        reply = ask_ai(text)
    except:
        reply = "Извините, произошла небольшая ошибка. Пожалуйста, попробуйте ещё раз или воспользуйтесь меню."

    save_message(user_id, "assistant", reply)
    await message.reply_text(reply, reply_markup=get_main_menu())

def main():
    init_db()
    init_excel()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("prices", cmd_prices))
    app.add_handler(CommandHandler("excel", cmd_excel))

    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ Стоматологический бот запущен (с пошаговой записью и кнопками)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
