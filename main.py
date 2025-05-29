import os
import logging
import random
import string
from datetime import datetime, timezone, timedelta

from flask import Flask
from threading import Thread

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

import gspread
from google.oauth2.service_account import Credentials

# --- Настройки ---

BOT_TOKEN = os.environ.get("BOT_TOKEN") or "7941872387:AAGZayILmna-qHHyQy5V50wDGylo3yFCZ0A"
CREDS_FILE = "valture-license-bot-account.json"
SPREADSHEET_NAME = "valture"
SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# --- Логирование ---

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Flask для keep-alive ---

app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Valture бот работает!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# --- Логика Telegram бота ---

def generate_license(length=32):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

def append_license_to_sheet(license_key, username):
    creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPE)
    client = gspread.authorize(creds)
    sheet = client.open(SPREADSHEET_NAME).sheet1

    # Время UTC+2 (фиксированное смещение)
    utc_plus_2 = timezone(timedelta(hours=2))
    now_utc_plus_2 = datetime.now(utc_plus_2)
    now_str = now_utc_plus_2.strftime("%Y-%m-%d %H:%M:%S")

    # Записываем в колонки: A - ключ, B - пусто, C - имя пользователя, D - время покупки
    sheet.append_row([license_key, "", username, now_str])

def get_keyboard(buttons):
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=callback)] for text, callback in buttons])

async def start(update: "Update", context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Добро пожаловать в Valture — профессиональный инструмент для геймеров, которые ценят максимальную производительность и стабильность!\n\n"
        "Нажмите кнопку ниже, чтобы открыть меню.",
        reply_markup=get_keyboard([("📋 Меню", "menu_main")])
    )

async def main_menu(update: "Update", context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    buttons = [
        ("💳 Оплата", "menu_pay"),
        ("📞 Поддержка", "menu_support"),
        ("❓ FAQ", "menu_faq"),
        ("ℹ️ О приложении", "menu_about"),
    ]
    await query.edit_message_text("🏠 Главное меню. Выберите раздел:", reply_markup=get_keyboard(buttons))

async def about(update: "Update", context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "✨ *О приложении Valture*\n\n"
        "➖➖➖➖➖➖➖➖➖➖➖➖➖\n"
        "🚀 +20–30% FPS\n"
        "➖➖➖➖➖➖➖➖➖➖➖➖➖\n"
        "🛡️ Стабильность фреймрейта\n"
        "➖➖➖➖➖➖➖➖➖➖➖➖➖\n"
        "💡 Отзывчивость системы\n"
        "➖➖➖➖➖➖➖➖➖➖➖➖➖\n"
        "🔰 Уменьшение задержек\n"
        "➖➖➖➖➖➖➖➖➖➖➖➖➖\n"
        "🛳️ Плавность и четкость мыши\n"
        "➖➖➖➖➖➖➖➖➖➖➖➖➖\n"
        "🖥️ Плавность картинки в играх\n"
        "➖➖➖➖➖➖➖➖➖➖➖➖➖\n"
        "🔋 Полная настройка Windows\n\n"
        "➖➖➖➖➖➖➖➖➖➖➖➖➖\n"
        "_Создано для геймеров, которые ценят максимальную производительность и стабильность._"
    )
    buttons = [
        ("💳 Оплата", "menu_pay"),
        ("📞 Поддержка", "menu_support"),
        ("❓ FAQ", "menu_faq"),
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def pay(update: "Update", context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "💳 *Оплата лицензии Valture*\n\n"
        "цена 1000 рублей.\n"
        "После оплаты лицензия будет автоматически выслана вам в чат.\n"
        "Нажмите кнопку ниже, чтобы получить лицензию."
    )
    buttons = [
        ("оплатить", "pay_confirm"),
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def pay_confirm(update: "Update", context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    license_key = generate_license()
    username = query.from_user.username or query.from_user.full_name
    append_license_to_sheet(license_key, username)

    await query.edit_message_text(f"✅ Вот ваш лицензионный ключ:\n\n`{license_key}`", parse_mode="Markdown")

async def support(update: "Update", context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "📞 *Поддержка Valture*\n\n"
        "Если у вас возникли вопросы, пишите сюда: @your_support_username"
    )
    buttons = [("🏠 Главное меню", "menu_main")]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def faq(update: "Update", context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "❓ *FAQ*\n\n"
        "1. Как получить лицензию?\n"
        "- Используйте кнопку 'оплатить' в меню оплаты.\n\n"
        "2. Что делать, если ключ не работает?\n"
        "- Свяжитесь с поддержкой.\n\n"
        "3. Можно ли использовать на нескольких устройствах?\n"
        "- Нет, ключ привязан к одному устройству."
    )
    buttons = [("🏠 Главное меню", "menu_main")]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def button_handler(update: "Update", context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data == "menu_main":
        await main_menu(update, context)
    elif data == "menu_pay":
        await pay(update, context)
    elif data == "pay_confirm":
        await pay_confirm(update, context)
    elif data == "menu_support":
        await support(update, context)
    elif data == "menu_faq":
        await faq(update, context)
    elif data == "menu_about":
        await about(update, context)

if __name__ == "__main__":
    # Запускаем Flask в отдельном потоке (keep alive)
    Thread(target=run_flask).start()

    # Запускаем бота
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))

    print("✅ Valture бот запущен и работает!")
    application.run_polling()
