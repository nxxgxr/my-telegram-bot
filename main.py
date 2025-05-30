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
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

import gspread
from google.oauth2.service_account import Credentials

from yookassa import Configuration, Payment

# --- Настройки ---

BOT_TOKEN = os.environ.get("BOT_TOKEN") or "7941872387:AAGZayILmna-qHHyQy5V50wDGylo3yFCZ0A"
CREDS_FILE = "valture-license-bot-account.json"
SPREADSHEET_NAME = "valture"
SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

YOOKASSA_SHOP_ID = os.environ.get("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.environ.get("YOOKASSA_SECRET_KEY")

if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET_KEY:
    raise Exception("YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY должны быть заданы в переменных окружения")

Configuration.account_id = YOOKASSA_SHOP_ID
Configuration.secret_key = YOOKASSA_SECRET_KEY

# --- Логирование ---

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Flask для keep-alive и вебхука ---

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
        logger.warning("Неверная подпись в webhook")
        abort(400, "Invalid signature")

    data = json.loads(body)
    event = data.get('event')
    logger.info(f"Получено событие от ЮKassa: {event}")

    if event == 'payment.succeeded':
        payment_obj = data.get('object', {}).get('payment', {})
        username = payment_obj.get('metadata', {}).get('username')
        if username:
            try:
                license_key = generate_license()
                append_license_to_sheet(license_key, username)
                # Отправка сообщения пользователю через Telegram Bot API
                bot = Bot(token=BOT_TOKEN)
                bot.send_message(chat_id=f"@{username}",
                                 text=f"🎉 *Поздравляем с покупкой!*\n\nВаш лицензионный ключ:\n`{license_key}`\n\nСохраните его в надежном месте!",
                                 parse_mode="Markdown")
                logger.info(f"Лицензия отправлена пользователю @{username}")
            except Exception as e:
                logger.error(f"Ошибка при отправке лицензии: {e}")
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
        try:
            creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPE)
            client = gspread.authorize(creds)
            sheet_cache = client.open(SPREADSHEET_NAME).sheet1
            logger.info("Успешно подключено к Google Sheets")
        except Exception as e:
            logger.error(f"Ошибка подключения к Google Sheets: {e}")
            raise
    return sheet_cache

# --- Генерация и сохранение лицензии ---

def generate_license(length=32):
    key = ''.join(secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') for _ in range(length))
    logger.info(f"Сгенерирован ключ: {key}")
    return key

def append_license_to_sheet(license_key, username):
    sheet = get_sheet()
    utc_plus_2 = timezone(timedelta(hours=2))
    now_utc_plus_2 = datetime.now(utc_plus_2)
    now_str = now_utc_plus_2.strftime("%Y-%m-%d %H:%M:%S")
    sheet.append_row([license_key, "", username, now_str])
    logger.info(f"Лицензия {license_key} добавлена для {username}")

# --- Клавиатура ---

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

    username = query.from_user.username
    if not username:
        await query.edit_message_text("❌ Для оплаты необходимо, чтобы в вашем профиле был указан username Telegram. Пожалуйста, добавьте его и попробуйте снова.")
        return

    try:
        # Создаем платеж в ЮKassa
        payment = Payment.create({
            "amount": {
                "value": "1000.00",
                "currency": "RUB"
            },
            "confirmation": {
                "type": "redirect",
                "return_url": "https://t.me/valture_bot"  # Замени на ссылку на бота или свой сайт
            },
            "capture": True,
            "description": f"Покупка лицензии Valture пользователем @{username}",
            "metadata": {
                "username": username
            }
        }, uuid=secrets.token_hex(16))

        payment_url = payment.confirmation.confirmation_url

        text = (
            f"🔗 *Перейдите по ссылке для оплаты:*\n\n"
            f"[Оплатить лицензию за 1000₽]({payment_url})\n\n"
            f"После успешной оплаты лицензионный ключ будет автоматически выслан вам в этот чат."
        )
        await query.edit_message_text(text, parse_mode="Markdown", disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Ошибка при создании платежа: {e}")
        await query.edit_message_text("❌ Произошла ошибка при создании платежа. Попробуйте позже.")

async def news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = "📰 *Новости Valture*\n\nЗдесь будут появляться обновления и важные анонсы."
    buttons = [("🔙 Назад", "menu_main")]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def faq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = "❓ *FAQ*\n\nВопросы и ответы по использованию Valture."
    buttons = [("🔙 Назад", "menu_main")]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = "📞 *Поддержка*\n\nЕсли у вас возникли вопросы, пишите сюда: @valture_support"
    buttons = [("🔙 Назад", "menu_main")]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    elif data == "menu_news":
        await news(update, context)
    elif data == "menu_faq":
        await faq(update, context)
    elif data == "menu_support":
        await support(update, context)
    else:
        await query.answer("Неизвестная команда")

# --- Основная функция запуска бота и Flask ---

def main():
    app_thread = Thread(target=run_flask)
    app_thread.start()

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Бот запущен.")
    application.run_polling()

if __name__ == '__main__':
    main()
