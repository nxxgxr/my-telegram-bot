import os
import logging
import secrets
from datetime import datetime, timezone, timedelta
from threading import Thread

from flask import Flask

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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

# Кэш для данных Google Sheets
sheet_cache = None

def get_sheet():
    """Получение кэшированного объекта Google Sheets."""
    global sheet_cache
    if sheet_cache is None:
        creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPE)
        client = gspread.authorize(creds)
        sheet_cache = client.open(SPREADSHEET_NAME).sheet1
    return sheet_cache

def generate_license(length=32):
    """Генерация безопасного лицензионного ключа."""
    return ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(length))

def append_license_to_sheet(license_key, username):
    """Добавление лицензии в Google Sheets."""
    try:
        sheet = get_sheet()
        utc_plus_2 = timezone(timedelta(hours=2))
        now_utc_plus_2 = datetime.now(utc_plus_2)
        now_str = now_utc_plus_2.strftime("%Y-%m-%d %H:%M:%S")
        sheet.append_row([license_key, "", username, now_str])
        logger.info(f"Лицензия {license_key} добавлена для {username}")
    except Exception as e:
        logger.error(f"Ошибка при добавлении лицензии: {e}")
        raise

def get_keyboard(buttons):
    """Создание клавиатуры с кнопками."""
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=callback)] for text, callback in buttons])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start."""
    welcome_text = (
        "👋 *Добро пожаловать в Valture!*\n\n"
        "Мы предлагаем профессиональный инструмент для геймеров, "
        "которые стремятся к максимальной производительности и стабильности.\n\n"
        "Выберите действие в меню ниже:"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown", reply_markup=get_keyboard([("📋 Открыть меню", "menu_main")]))

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отображение главного меню."""
    query = update.callback_query
    await query.answer()
    buttons = [
        ("ℹ️ О приложении", "menu_about"),
        ("📰 Новости", "menu_news"),
        ("💳 Купить лицензию", "menu_pay"),
        ("❓ FAQ", "menu_faq"),
        ("📞 Поддержка", "menu_support"),
    ]
    await query.edit_message_text("🏠 *Главное меню*\n\nВыберите раздел:", parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Информация о приложении."""
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
        "_Создано для геймеров, которые ценят качество._"
    )
    buttons = [
        ("🔙 Назад", "menu_main"),
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню оплаты."""
    query = update.callback_query
    await query.answer()
    text = (
        "💳 *Приобретение лицензии Valture*\n\n"
        "Стоимость: *1000 рублей*\n"
        "После оплаты вы получите уникальный ключ прямо в чат.\n\n"
        "Готовы продолжить?"
    )
    buttons = [("✅ Оплатить", "pay_confirm"), ("🔙 Назад", "menu_main")]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def pay_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение оплаты и выдача ключа."""
    query = update.callback_query
    await query.answer()
    try:
        license_key = generate_license()
        username = query.from_user.username or query.from_user.full_name
        append_license_to_sheet(license_key, username)
        text = (
            "🎉 *Поздравляем с покупкой!*\n\n"
            "Ваш лицензионный ключ:\n"
            f"`{license_key}`\n\n"
            "Сохраните его в надежном месте!"
        )
        await query.edit_message_text(text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Ошибка при генерации ключа: {e}")
        await query.edit_message_text(
            "❌ *Ошибка*\n\nНе удалось сгенерировать ключ. Попробуйте позже или обратитесь в поддержку.",
            parse_mode="Markdown"
        )

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню поддержки."""
    query = update.callback_query
    await query.answer()
    text = (
        "📞 *Поддержка Valture*\n\n"
        "Возникли вопросы? Свяжитесь с нами:\n"
        "👉 *@s3pt1ck*\n\n"
        "Мы ответим максимально быстро!"
    )
    buttons = [("🔙 Назад", "menu_main")]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def faq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Часто задаваемые вопросы."""
    query = update.callback_query
    await query.answer()
    text = (
        "❓ *FAQ*\n\n"
        "**1. Как получить лицензию?**\n"
        "Перейдите в раздел 'Купить лицензию' и следуйте инструкциям.\n\n"
        "**2. Что делать, если ключ не работает?**\n"
        "Напишите в поддержку — мы поможем!\n\n"
        "**3. Можно ли использовать ключ на нескольких устройствах?**\n"
        "Нет, ключ привязан к одному устройству."
    )
    buttons = [("🔙 Назад", "menu_main")]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Раздел новостей."""
    query = update.callback_query
    await query.answer()
    text = (
        "📰 *Новости Valture*\n\n"
        "Следите за обновлениями здесь!\n"
        "Пока новых сообщений нет."
    )
    buttons = [("🔙 Назад", "menu_main")]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий кнопок."""
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
    elif data == "menu_news":
        await news(update, context)

if __name__ == "__main__":
    # Запуск Flask в отдельном потоке
    Thread(target=run_flask).start()

    # Запуск бота
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))

    print("✅ Valture бот запущен и работает!")
    application.run_polling()
