import logging
from datetime import datetime
import random
import string

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

import gspread
from google.oauth2.service_account import Credentials

from keep_alive import keep_alive  # 🟢 Добавлено для работы 24/7

# Настройки
BOT_TOKEN = "7713643772:AAG4LsuhHbg5efhMayuzeVwiyAxnCfq8txA"
CREDS_FILE = "valture-license-bot-account.json"
SPREADSHEET_NAME = "valture"
SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Логирование
logger = logging.getLogger()
logger.setLevel(logging.INFO)
for handler in logger.handlers[:]:
    logger.removeHandler(handler)
logging.basicConfig(format='%(message)s', level=logging.INFO)
logging.getLogger("telegram").setLevel(logging.CRITICAL)
logging.getLogger("telegram.ext").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.CRITICAL)

# Генерация ключа
def generate_license(length=32):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

# Запись в Google Таблицу
def append_license_to_sheet(license_key, username):
    creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPE)
    client = gspread.authorize(creds)
    sheet = client.open(SPREADSHEET_NAME).sheet1
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sheet.append_row([license_key, "", username, now_str])

# Клавиатура
def get_keyboard(buttons):
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=callback)] for text, callback in buttons])

# Команды и меню
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Добро пожаловать в Valture — профессиональный инструмент для геймеров, которые ценят максимальную производительность и стабильность!\n\n"
        "Нажмите кнопку ниже, чтобы открыть меню.",
        reply_markup=get_keyboard([("📋 Меню", "menu_main")])
    )

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    buttons = [
        ("💳 Оплата", "menu_pay"),
        ("📞 Поддержка", "menu_support"),
        ("❓ FAQ", "menu_faq"),
        ("ℹ️ О приложении", "menu_about"),
    ]
    await query.edit_message_text("🏠 Главное меню. Выберите раздел:", reply_markup=get_keyboard(buttons))

async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "✨ *О приложении Valture*\n\n"
        "✅ +20–30% FPS\n"
        "✅ Стабильность фреймрейта\n"
        "✅ Отзывчивость системы\n"
        "✅ Уменьшение задержек\n"
        "✅ Плавность и четкость мыши\n"
        "✅ Плавность картинки в играх\n"
        "✅ Полная настройка Windows\n\n"
        "_Создано для геймеров, которые ценят максимальную производительность и стабильность._"
    )
    query = update.callback_query
    await query.answer()
    buttons = [
        ("💳 Оплата", "menu_pay"),
        ("📞 Поддержка", "menu_support"),
        ("❓ FAQ", "menu_faq"),
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "💳 *Оплата лицензии Valture*\n\n"
        "После оплаты лицензия будет автоматически выслана вам в чат.\n"
        "Нажмите кнопку ниже, чтобы получить лицензию."
    )
    query = update.callback_query
    await query.answer()
    buttons = [
        ("Получить ключ (тест)", "pay_confirm"),
        ("⬅️ Назад", "menu_main"),
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📞 *Поддержка*\n\n"
        "Если у вас возникли вопросы или нужна помощь, напишите нашему специалисту поддержки:\n\n"
        "@s3pt1ck"
    )
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard([("⬅️ Назад", "menu_main")]))

async def faq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "❓ *Часто задаваемые вопросы (FAQ)*\n\n"
        "1️⃣ *Как установить Valture?*\n"
        "Установите приложение, следуя инструкциям из официального руководства.\n\n"
        "2️⃣ *Как получить лицензию?*\n"
        "Лицензия выдается после оплаты или по тестовой кнопке ниже.\n\n"
        "3️⃣ *Что делать, если возникли проблемы?*\n"
        "Обратитесь в поддержку через меню или напрямую @s3pt1ck."
    )
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard([("⬅️ Назад", "menu_main")]))

async def pay_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    license_key = generate_license()
    try:
        user = query.from_user
        username = f"@{user.username}" if user.username else "no_username"
        append_license_to_sheet(license_key, username)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        logging.info(f"Ключ: {license_key} ({now_str})")
        logging.info(f"Выдан пользователю: {username} (ID: {user.id})")

        await query.edit_message_text(
            f"✅ Ваша лицензия:\n`{license_key}`\n\n"
            "Спасибо, что доверяете нам! Желаем успехов в играх и новых побед! 🎮",
            parse_mode="Markdown",
            reply_markup=get_keyboard([("⬅️ Назад", "menu_main")])
        )
    except Exception:
        await query.edit_message_text("❌ Ошибка при выдаче лицензии. Попробуйте позже.")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data == "menu_main":
        await main_menu(update, context)
    elif data == "menu_pay":
        await pay(update, context)
    elif data == "menu_support":
        await support(update, context)
    elif data == "menu_faq":
        await faq(update, context)
    elif data == "menu_about":
        await about(update, context)
    elif data == "pay_confirm":
        await pay_confirm(update, context)

# 🚀 Запуск
if __name__ == "__main__":
    keep_alive()  # 🟢 Запуск веб-сервера, чтобы Replit не спал
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.run_polling()
