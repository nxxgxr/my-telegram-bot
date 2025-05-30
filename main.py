import os
import logging
import secrets
import base64
from datetime import datetime, timezone, timedelta
from threading import Thread

from flask import Flask

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

import gspread
from google.oauth2.service_account import Credentials
import aiohttp

# --- Настройки из переменных окружения ---

BOT_TOKEN = os.environ.get("BOT_TOKEN")
SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME")

# Либо путь к JSON-файлу, либо base64 содержимое
CREDS_FILE = os.environ.get("CREDS_FILE")  # Имя файла, если загружаете в Railway как файл
GOOGLE_CREDS_JSON_BASE64 = os.environ.get("GOOGLE_CREDS_JSON_BASE64")  # Если используете base64

if GOOGLE_CREDS_JSON_BASE64:
    with open("valture-license-bot-account.json", "wb") as f:
        f.write(base64.b64decode(GOOGLE_CREDS_JSON_BASE64))
    CREDS_FILE = "valture-license-bot-account.json"

SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

CRYPTOBOT_API_TOKEN = os.environ.get("CRYPTOBOT_API_TOKEN")  # Токен CryptoBot

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

# --- Логика Google Sheets ---

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
    try:
        key = ''.join(secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') for _ in range(length))
        logger.info(f"Сгенерирован ключ: {key}")
        return key
    except Exception as e:
        logger.error(f"Ошибка при генерации ключа: {e}")
        raise

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

# --- Обработчики ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 *Добро пожаловать в Valture!*\n\n"
        "Мы предлагаем профессиональный инструмент для геймеров, "
        "которые стремятся к максимальной производительности и стабильности.\n\n"
        "Выберите действие в меню ниже:"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown", reply_markup=get_keyboard([("📋 Открыть меню", "menu_main")]))

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
    await query.edit_message_text("🏠 *Главное меню*\n\nВыберите раздел:", parse_mode="Markdown", reply_markup=get_keyboard(buttons))

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

    logger.info(f"Попытка создания платежа для пользователя {query.from_user.id}")

    invoice_data = {
        "chat_id": query.from_user.id,
        "amount": 1000,  # рубли
        "currency": "RUB",
        "payload": "valture_license_purchase"
    }

    headers = {
        "Authorization": f"Bearer {CRYPTOBOT_API_TOKEN}",
        "Content-Type": "application/json"
    }

    logger.info(f"Данные для платежа: {invoice_data}")

    CRYPTOBOT_CREATE_INVOICE_URL = "https://api.cryptobot.org/v1/payments/create"

    async with aiohttp.ClientSession() as session:
        async with session.post(CRYPTOBOT_CREATE_INVOICE_URL, json=invoice_data, headers=headers) as resp:
            text = await resp.text()
            logger.info(f"CryptoBot response status: {resp.status}")
            logger.info(f"CryptoBot response body: {text}")

            if resp.status == 200:
                data = await resp.json()
                pay_url = data.get("payment_url") or data.get("pay_url")
                if pay_url:
                    buttons = [[InlineKeyboardButton("Перейти к оплате", url=pay_url)]]
                    keyboard = InlineKeyboardMarkup(buttons)
                    await query.edit_message_text("Нажмите кнопку ниже, чтобы оплатить лицензию:", reply_markup=keyboard)
                    return
            elif resp.status == 401:
                await query.edit_message_text("❌ Ошибка авторизации (401). Проверьте API токен CryptoBot.")
                logger.error("Ошибка 401 Unauthorized — проверьте CRYPTOBOT_API_TOKEN")
                return
            await query.edit_message_text("❌ Ошибка создания платежа. Попробуйте позже.")

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
        "После оплаты вы получите ключ, который нужно ввести в приложении.\n\n"
        "— Сколько времени действует лицензия?\n"
        "Лицензия бессрочная.\n\n"
        "— Можно ли вернуть деньги?\n"
        "Возврат средств возможен в течение 14 дней при отсутствии использования лицензии."
    )
    buttons = [("🔙 Назад", "menu_main")]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "📰 *Новости Valture*\n\n"
        "- Версия 1.0 вышла!\n"
        "- Добавлена поддержка новых игр.\n"
        "- Скоро новые функции!"
    )
    buttons = [("🔙 Назад", "menu_main")]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Извините, я не понимаю эту команду.")

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    menu_map = {
        "menu_main": main_menu,
        "menu_about": about,
        "menu_pay": pay,
        "pay_confirm": pay_confirm,
        "menu_support": support,
        "menu_faq": faq,
        "menu_news": news,
    }

    handler = menu_map.get(data)
    if handler:
        await handler(update, context)
    else:
        await query.answer("Неизвестная команда.", show_alert=True)

# --- Основная функция ---

def main():
    # Запуск Flask в отдельном потоке (для keep-alive на хостинге)
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(callback_router))
    application.add_handler(CommandHandler("help", start))
    application.add_handler(CommandHandler("faq", faq))

    application.add_handler(CommandHandler("news", news))

    application.run_polling()

if __name__ == "__main__":
    main()
