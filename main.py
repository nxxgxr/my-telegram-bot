import os
import logging
import secrets
import hmac
import hashlib
import json
from datetime import datetime, timezone, timedelta
from threading import Thread

from flask import Flask, request, abort

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, Bot
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes
)

import gspread
from google.oauth2.service_account import Credentials

from yookassa import Configuration, Payment

# --- Логирование ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Переменные окружения ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
CREDS_FILE = "valture-license-bot-account.json"
SPREADSHEET_NAME = "valture"
SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

YOOKASSA_SHOP_ID = "1095145"  # Твой shopId жёстко прописан здесь
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY")
if not BOT_TOKEN:
    raise Exception("BOT_TOKEN не задан!")
if not YOOKASSA_SECRET_KEY:
    raise Exception("YOOKASSA_SECRET_KEY должен быть задан в переменных окружения!")

Configuration.account_id = YOOKASSA_SHOP_ID
Configuration.secret_key = YOOKASSA_SECRET_KEY

# --- Flask ---
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Valture бот работает!"

def verify_signature(secret, body, signature):
    computed = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, signature)

@app.route('/yookassa-webhook', methods=['POST'])
def yookassa_webhook():
    signature = request.headers.get('X-Request-Signature-SHA256')
    body = request.get_data()
    if not verify_signature(YOOKASSA_SECRET_KEY, body, signature):
        logger.warning("Неверная подпись webhook")
        abort(400, "Invalid signature")

    data = json.loads(body)
    event = data.get('event')
    logger.info(f"Событие от YooKassa: {event}")

    if event == 'payment.succeeded':
        payment_obj = data.get('object', {}).get('payment', {})
        username = payment_obj.get('metadata', {}).get('username')
        if username:
            try:
                license_key = generate_license()
                append_license_to_sheet(license_key, username)
                bot = Bot(token=BOT_TOKEN)
                bot.send_message(
                    chat_id=f"@{username}",
                    text=(
                        f"🎉 Поздравляем с покупкой!\n\n"
                        f"Ваш лицензионный ключ:\n`{license_key}`\n\n"
                        "Сохраните его в надежном месте!"
                    ),
                    parse_mode="Markdown"
                )
                logger.info(f"Отправлена лицензия @{username}")
            except Exception as e:
                logger.error(f"Ошибка отправки лицензии: {e}")
        else:
            logger.warning("В webhook нет username в metadata")

    return '', 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# --- Google Sheets ---
sheet_cache = None
def get_sheet():
    global sheet_cache
    if sheet_cache is None:
        creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPE)
        client = gspread.authorize(creds)
        sheet_cache = client.open(SPREADSHEET_NAME).sheet1
        logger.info("Подключено к Google Sheets")
    return sheet_cache

def generate_license(length=32):
    key = ''.join(secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') for _ in range(length))
    logger.info(f"Сгенерирован ключ: {key}")
    return key

def append_license_to_sheet(license_key, username):
    sheet = get_sheet()
    tz = timezone(timedelta(hours=3))  # Москва +3 часа
    now_str = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
    sheet.append_row([license_key, "", username, now_str])
    logger.info(f"Добавлена лицензия {license_key} для {username}")

# --- Телеграм меню и клавиатуры ---
def get_keyboard(buttons):
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=callback)] for text, callback in buttons])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 Добро пожаловать в Valture111!\n\n"
        "Выберите действие:"
    )
    await update.message.reply_text(text, reply_markup=get_keyboard([("📋 Меню", "menu_main")]))

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    buttons = [
        ("ℹ️ О приложении", "menu_about"),
        ("💳 Купить лицензию", "menu_pay"),
        ("❓ FAQ", "menu_faq"),
        ("📞 Поддержка", "menu_support"),
    ]
    await query.edit_message_text("Главное меню:", reply_markup=get_keyboard(buttons))

async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "Valture — инструмент для повышения производительности в играх.\n"
        "Увеличение FPS, оптимизация Windows и многое другое."
    )
    await query.edit_message_text(text, reply_markup=get_keyboard([("🔙 Назад", "menu_main")]))

async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "Стоимость лицензии — 1000 рублей.\n"
        "После оплаты вы получите ключ в этом чате.\n\n"
        "Готовы продолжить?"
    )
    await query.edit_message_text(text, reply_markup=get_keyboard([("✅ Оплатить", "pay_confirm"), ("🔙 Назад", "menu_main")]))

async def pay_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        amount_value = "1000.00"
        username = query.from_user.username or str(query.from_user.id)
        logger.info(f"Создаем платеж для {username} на сумму {amount_value}")

        payment = Payment.create({
            "amount": {
                "value": amount_value,
                "currency": "RUB"
            },
            "confirmation": {
                "type": "redirect",
                "return_url": "https://t.me/valture_support_bot"  # ссылка возврата после оплаты
            },
            "capture": True,
            "description": "Покупка лицензии Valture",
            "metadata": {"username": username}
        }, idempotence_key=secrets.token_hex(16))

        pay_url = payment.confirmation.confirmation_url
        logger.info(f"Платеж создан, ссылка: {pay_url}")

        await query.edit_message_text(
            f"Перейдите по ссылке для оплаты:\n{pay_url}",
            disable_web_page_preview=True
        )
    except Exception as e:
        logger.error(f"Ошибка создания платежа: {e}")
        await query.edit_message_text("❌ Ошибка создания платежа. Попробуйте позже.")

async def faq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "FAQ:\n"
        "- Ключ не пришёл? Свяжитесь с поддержкой.\n"
        "- Лицензия для одного устройства.\n"
        "- Поддержка: @valture_support_bot"
    )
    await query.edit_message_text(text, reply_markup=get_keyboard([("🔙 Назад", "menu_main")]))

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = "Свяжитесь с нами: @valture_support_bot"
    await query.edit_message_text(text, reply_markup=get_keyboard([("🔙 Назад", "menu_main")]))

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
    elif data == "menu_faq":
        await faq(update, context)
    elif data == "menu_support":
        await support(update, context)
    else:
        await query.answer("Неизвестная команда", show_alert=True)

# --- Запуск ---

def main():
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(callback_handler))

    # Запускаем Flask в отдельном потоке для приема webhook от YooKassa
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()

    logger.info("Бот запущен!")
    application.run_polling()

if __name__ == "__main__":
    main()
