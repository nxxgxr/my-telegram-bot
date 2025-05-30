import os
import logging
import secrets
import base64
from datetime import datetime, timezone, timedelta
from threading import Thread

from flask import Flask

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import aiohttp

import gspread
from google.oauth2.service_account import Credentials

# --- Настройки из переменных окружения ---

BOT_TOKEN = os.environ.get("BOT_TOKEN")
SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME")

CREDS_FILE = os.environ.get("CREDS_FILE")
GOOGLE_CREDS_JSON_BASE64 = os.environ.get("GOOGLE_CREDS_JSON_BASE64")

if GOOGLE_CREDS_JSON_BASE64 and not CREDS_FILE:
    with open("valture-license-bot-account.json", "wb") as f:
        f.write(base64.b64decode(GOOGLE_CREDS_JSON_BASE64))
    CREDS_FILE = "valture-license-bot-account.json"

SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

CRYPTOBOT_API_TOKEN = os.environ.get("CRYPTOBOT_API_TOKEN")

# --- Логирование ---

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# --- Flask для keep-alive ---

app = Flask(__name__)

@app.route("/")
def home():
    return "✅ Valture бот работает!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# --- Работа с Google Sheets ---

sheet_cache = None

def get_sheet():
    global sheet_cache
    if sheet_cache is None:
        try:
            creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPE)
            client = gspread.authorize(creds)
            sheet_cache = client.open(SPREADSHEET_NAME).sheet1
            logger.info("Успешно подключено к Google Sheets")
        except Exception as e:
            logger.error(f"Ошибка подключения к Google Sheets: {e}")
            raise
    return sheet_cache

def generate_license(length=32):
    return ''.join(secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') for _ in range(length))

def append_license_to_sheet(license_key, username):
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
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=callback)] for text, callback in buttons])

# --- Обработчики Telegram ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 *Добро пожаловать в Valture!*\n\n"
        "Мы предлагаем профессиональный инструмент для геймеров, "
        "которые стремятся к максимальной производительности и стабильности.\n\n"
        "Выберите действие в меню ниже:"
    )
    await update.message.reply_text(
        welcome_text,
        parse_mode="Markdown",
        reply_markup=get_keyboard([("📋 Открыть меню", "menu_main")]),
    )

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    buttons = [
        ("ℹ️ О приложении", "menu_about"),
        ("📰 Новости", "menu_news"),
        ("💳 Купить лицензию", "menu_pay"),
        ("❓ FAQ", "menu_faq"),
        ("📞 Поддержка", "menu_support"),
    ]
    await query.edit_message_text(
        "🏠 *Главное меню*\n\nВыберите раздел:",
        parse_mode="Markdown",
        reply_markup=get_keyboard(buttons),
    )

async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "✨ *Valture — Ваш путь к совершенству в играх*\n\n"
        "➖➖➖➖➖➖➖➖➖➖➖➖➖\n"
        "Valture — это передовой инструмент, созданный для геймеров, которые не готовы мириться с компромиссами. "
        "Наша миссия — вывести вашу игровую производительность на новый уровень, обеспечив максимальную плавность, "
        "стабильность и отзывчивость системы. С Valture вы получите конкурентное преимущество, о котором всегда мечтали.\n\n"
        "🔥 *Почему выбирают Valture?*\n"
        "🚀 Увеличение FPS на 20–30%: Оптимизируйте производительность вашей системы, чтобы добиться максимальной частоты кадров.\n"
        "🛡️ Стабильный фреймрейт: Забудьте о лагах и просадках FPS — Valture обеспечивает плавный игровой процесс.\n"
        "💡 Молниеносная отзывчивость: Сократите время отклика системы, чтобы каждый ваш клик или движение были мгновенными.\n"
        "🔋 Оптимизация Windows: Полная настройка операционной системы для максимальной производительности в играх.\n"
        "🛳️  Плавность управления: Улучшенная точность и четкость мыши для идеального контроля в любой ситуации.\n"
        "🖥️  Плавность картинки в играх: Наслаждайтесь четкой и плавной картинкой, которая погружает вас в игру.\n\n"
        "➖➖➖➖➖➖➖➖➖➖➖➖➖\n"
        "_Создано для геймеров, которые ценят качество и стремятся к победе._"
    )
    buttons = [("🔙 Назад", "menu_main")]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    query = update.callback_query
    await query.answer()

    if not CRYPTOBOT_API_TOKEN:
        await query.edit_message_text("❌ Токен CryptoBot не настроен. Обратитесь к администратору.")
        return

    invoice_data = {
        "chat_id": query.from_user.id,
        "amount": 1000,  # сумма в рублях
        "currency": "RUB",
        "payload": "valture_license_purchase",
    }

    headers = {
        "Authorization": f"Bearer {CRYPTOBOT_API_TOKEN}",
        "Content-Type": "application/json",
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://pay.crypt.bot/invoice", json=invoice_data, headers=headers
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                pay_url = data.get("pay_url")
                if pay_url:
                    buttons = [[InlineKeyboardButton("Перейти к оплате", url=pay_url)]]
                    keyboard = InlineKeyboardMarkup(buttons)
                    await query.edit_message_text(
                        "Нажмите кнопку ниже, чтобы оплатить лицензию:", reply_markup=keyboard
                    )
                    return
            await query.edit_message_text("❌ Ошибка создания платежа. Попробуйте позже.")

# ЗАГОТОВКА для webhook CryptoBot — допиши сам по нуждам
async def cryptobot_webhook(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # сюда придут уведомления о платежах
    # надо проверить, что оплата успешна, сгенерировать ключ,
    # записать в Google Sheets и отправить пользователю
    pass

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    query = update.callback_query
    await query.answer()
    text = (
        "❓ *Часто задаваемые вопросы*\n\n"
        "— Как активировать лицензию?\n"
        "После оплаты ключ придет в этот чат.\n\n"
        "— Можно ли вернуть деньги?\n"
        "Нет, все продажи окончательные.\n\n"
        "— Что делать, если не получил ключ?\n"
        "Свяжитесь с поддержкой."
    )
    buttons = [("🔙 Назад", "menu_main")]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚠️ Неизвестная команда. Используйте /start для начала.")

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data == "menu_main":
        await main_menu(update, context)
    elif data == "menu_about":
        await about(update, context)
    elif data == "menu_pay":
        await pay(update, context)
    elif data == "pay_confirm":
        await pay_confirm(update, context)
    elif data == "menu_support":
        await support(update, context)
    elif data == "menu_faq":
        await faq(update, context)
    else:
        await query.answer("Неизвестная команда", show_alert=True)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Ошибка: {context.error}")

# --- Запуск бота и Flask ---

def main():
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(MessageHandler(filters.COMMAND, unknown))

    application.add_error_handler(error_handler)

    # Запускаем Flask в отдельном потоке
    Thread(target=run_flask, daemon=True).start()

    # Запуск бота
    application.run_polling()

if __name__ == "__main__":
    main()
