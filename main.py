import os
import logging
import secrets
import requests
import base64
import json
from datetime import datetime, timezone, timedelta
from threading import Thread
from uuid import uuid4

from flask import Flask, request, jsonify
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

import gspread
from google.oauth2.service_account import Credentials

# --- Настройки ---

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CRYPTOBOT_API_TOKEN = os.environ.get("CRYPTOBOT_API_TOKEN")
CREDS_FILE = os.environ.get("CREDS_FILE")
SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME")
GOOGLE_CREDS_JSON_BASE64 = os.environ.get("GOOGLE_CREDS_JSON_BASE64")
PAYMENT_AMOUNT = 4.0  # Цена в TON, изменить здесь для настройки
SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# CryptoBot API endpoint
CRYPTO_BOT_API = "https://pay.crypt.bot/api"

# --- Логирование ---

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.DEBUG
)
logger = logging.getLogger(__name__)

# --- Flask для keep-alive ---

app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Valture бот работает!"

@app.route('/test-crypto-api')
def test_crypto_api():
    """Debug endpoint to test CryptoBot API connectivity."""
    try:
        headers = {"Crypto-Pay-API-Token": CRYPTOBOT_API_TOKEN}
        response = requests.get(f"{CRYPTO_BOT_API}/getMe", headers=headers, timeout=10)
        return f"API Response: {response.json()}"
    except Exception as e:
        return f"Error: {str(e)}"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# --- Обработка Google Credentials ---

def setup_google_creds():
    """Декодирование base64-креденшлов Google и создание временного файла."""
    logger.debug("Проверка Google credentials...")
    if GOOGLE_CREDS_JSON_BASE64:
        try:
            creds_json = base64.b64decode(GOOGLE_CREDS_JSON_BASE64).decode("utf-8")
            with open(CREDS_FILE, "w") as f:
                f.write(creds_json)
            logger.info("Google credentials успешно декодированы и сохранены во временный файл")
        except Exception as e:
            logger.error(f"Ошибка при декодировании Google credentials: {e}")
            raise
    elif not os.path.exists(CREDS_FILE):
        logger.error("Файл Google credentials не найден, и GOOGLE_CREDS_JSON_BASE64 не задан")
        raise FileNotFoundError("Файл Google credentials не найден, и GOOGLE_CREDS_JSON_BASE64 не задан")
    else:
        logger.info("Используется существующий файл Google credentials")

# --- Логика Telegram бота ---

# Кэш для данных Google Sheets
sheet_cache = None
invoices = {}  # Кэш для хранения инвойсов

def get_sheet():
    """Получение кэшированного объекта Google Sheets."""
    global sheet_cache
    if sheet_cache is None:
        try:
            setup_google_creds()
            creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPE)
            client = gspread.authorize(creds)
            sheet_cache = client.open(SPREADSHEET_NAME).sheet1
            logger.info("Успешно подключено к Google Sheets")
        except Exception as e:
            logger.error(f"Ошибка подключения к Google Sheets: {e}")
            raise
    return sheet_cache

def generate_license(length=32):
    """Генерация безопасного HWID-ключа."""
    try:
        key = ''.join(secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') for _ in range(length))
        logger.info(f"Сгенерирован HWID-ключ: {key}")
        return key
    except Exception as e:
        logger.error(f"Ошибка при генерации ключа: {e}")
        raise

def append_license_to_sheet(license_key, username):
    """Добавление HWID-ключа в Google Sheets."""
    try:
        sheet = get_sheet()
        utc_plus_2 = timezone(timedelta(hours=2))
        now_utc_plus_2 = datetime.now(utc_plus_2)
        now_str = now_utc_plus_2.strftime("%Y-%m-%d %H:%M:%S")
        sheet.append_row([license_key, "", username, now_str])
        logger.info(f"HWID-ключ {license_key} добавлен для {username}")
    except Exception as e:
        logger.error(f"Ошибка при добавлении HWID-ключа: {e}")
        raise

def create_crypto_invoice(amount, asset="TON", description="Valture License"):
    """Создание инвойса через CryptoBot."""
    logger.debug(f"Создание инвойса: amount={amount}, asset={asset}, description={description}")
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_API_TOKEN}
    data = {
        "asset": asset,
        "amount": str(amount),
        "description": description
    }
    try:
        response = requests.post(f"{CRYPTO_BOT_API}/createInvoice", headers=headers, json=data, timeout=10)
        logger.debug(f"HTTP статус: {response.status_code}, Ответ: {response.text}")
        if response.ok:
            response_data = response.json()
            logger.info(f"Инвойс успешно создан: invoice_id={response_data['result']['invoice_id']}")
            return response_data['result']['pay_url'], response_data['result']['invoice_id']
        return None, None
    except Exception as e:
        logger.error(f"Ошибка при создании инвойса: {e}, Ответ: {response.text if 'response' in locals() else 'нет ответа'}")
        return None, None

def check_invoice_status(invoice_id):
    """Проверка статуса инвойса CryptoBot."""
    logger.debug(f"Проверка статуса инвойса: invoice_id={invoice_id}")
    headers = {
        "Crypto-Pay-API-Token": CRYPTOBOT_API_TOKEN,
        "Content-Type": "application/json"
    }
    try:
        response = requests.post(f"{CRYPTO_BOT_API}/getInvoices", headers=headers, json={}, timeout=10)
        logger.debug(f"HTTP статус: {response.status_code}, Ответ: {response.text}")
        if response.ok:
            return response.json()
        else:
            logger.error(f"Ошибка при запросе к API: {response.status_code}, {response.text}")
            return None
    except Exception as e:
        logger.error(f"Ошибка при запросе статуса оплаты: {e}")
        return None

def get_keyboard(buttons):
    """Создание клавиатуры с кнопками."""
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=callback)] for text, callback in buttons])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветственное сообщение."""
    welcome_text = (
        "🎮 *Добро пожаловать в Valture!*\n\n"
        "Ваш лучший инструмент для игровой производительности! 🚀\n"
        "Выберите опцию ниже, чтобы начать:"
    )
    buttons = [("🏠 Главное меню", "menu_main")]
    await update.message.reply_text(welcome_text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главное меню без кнопки 'Назад'."""
    query = update.callback_query
    await query.answer()
    buttons = [
        ("ℹ️ О Valture", "menu_about"),
        ("📰 Новости", "menu_news"),
        ("💳 Купить лицензию", "menu_pay"),
        ("❓ FAQ", "menu_faq"),
        ("📞 Поддержка", "menu_support"),
    ]
    await query.edit_message_text(
        "🏠 *Главное меню*\n\nВыберите раздел:",
        parse_mode="Markdown",
        reply_markup=get_keyboard(buttons)
    )

async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Информация о приложении с кнопкой 'Назад'."""
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
    buttons = [("🔙 Назад в главное меню", "menu_main")]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню оплаты."""
    query = update.callback_query
    await query.answer()
    buttons = [(f"💳 Оплатить {PAYMENT_AMOUNT} TON", f"get_{PAYMENT_AMOUNT}")]
    await query.edit_message_text(
        f"Добро пожаловать! Нажмите кнопку ниже, чтобы купить данный товар за {PAYMENT_AMOUNT} TON.",
        parse_mode="Markdown",
        reply_markup=get_keyboard(buttons)
    )

async def get_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Создание инвойса для оплаты."""
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.full_name

    try:
        pay_url, invoice_id = create_crypto_invoice(PAYMENT_AMOUNT)
        if pay_url and invoice_id:
            invoices[chat_id] = invoice_id
            logger.info(f"Инвойс создан для {username}: invoice_id={invoice_id}")
            text = (
                f"💸 *Оплатите через CryptoBot*\n\n"
                f"Перейдите по этой [ссылке для оплаты]({pay_url}) *{PAYMENT_AMOUNT} TON* и нажмите 'Проверить оплату'."
            )
            buttons = [
                (f"Оплатить {PAYMENT_AMOUNT} TON", f"url_{pay_url}"),
                ("Проверить оплату", f"check_payment_{invoice_id}"),
                ("🔙 Назад в главное меню", "menu_main")
            ]
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(text=buttons[0][0], url=buttons[0][1].split("url_")[1])],
                [InlineKeyboardButton(text=buttons[1][0], callback_data=buttons[1][1])],
                [InlineKeyboardButton(text=buttons[2][0], callback_data=buttons[2][1])]
            ]), disable_web_page_preview=True)
        else:
            await query.edit_message_text(
                "❌ *Ошибка: Не удалось создать счет на оплату.*\n\nПопробуйте снова или свяжитесь с @s3pt1ck.",
                parse_mode="Markdown",
                reply_markup=get_keyboard([
                    ("🔄 Попробовать снова", f"get_{PAYMENT_AMOUNT}"),
                    ("🔙 Назад в главное меню", "menu_main")
                ])
            )
    except Exception as e:
        logger.error(f"Ошибка при создании инвойса: {e}", exc_info=True)
        await query.edit_message_text(
            "❌ *Что-то пошло не так!*\n\nНе удалось создать счет. Попробуйте снова или свяжитесь с @s3pt1ck.",
            parse_mode="Markdown",
            reply_markup=get_keyboard([
                ("🔄 Попробовать снова", f"get_{PAYMENT_AMOUNT}"),
                ("🔙 Назад в главное меню", "menu_main")
            ])
        )

async def check_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка статуса оплаты и выдача HWID-ключа."""
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id
    invoice_id = query.data.split("check_payment_")[1]
    username = query.from_user.username or query.from_user.full_name

    try:
        payment_status = check_invoice_status(invoice_id)
        if payment_status and payment_status.get('ok'):
            if 'items' in payment_status['result']:
                invoice = next((inv for inv in payment_status['result']['items'] if str(inv['invoice_id']) == invoice_id), None)
                if invoice:
                    status = invoice['status']
                    if status == 'paid':
                        hwid_key = generate_license()
                        append_license_to_sheet(hwid_key, username)
                        text = (
                            "🎉 *Оплата прошла успешно!✅*\n\n"
                            "Ваш HWID-ключ:\n"
                            f"`{hwid_key}`\n\n"
                            "Сохраните его в надежном месте! 🚀"
                        )
                        buttons = [("🏠 Назад в главное меню", "menu_main")]
                        logger.info(f"Оплата подтверждена, HWID-ключ выдан: {hwid_key} для {username}")
                        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))
                        if chat_id in invoices:
                            del invoices[chat_id]
                    else:
                        logger.warning(f"Оплата не подтверждена для invoice_id={invoice_id}, статус: {status}")
                        await query.edit_message_text(
                            "❌ *Оплата не найдена*\n\nЗавершите оплату или попробуйте снова. Свяжитесь с @s3pt1ck, если нужна помощь.",
                            parse_mode="Markdown",
                            reply_markup=get_keyboard([
                                ("🔄 Проверить снова", f"check_payment_{invoice_id}"),
                                ("🔙 Назад в главное меню", "menu_main")
                            ])
                        )
                else:
                    logger.error(f"Счет не найден для invoice_id={invoice_id}")
                    await query.edit_message_text(
                        "❌ *Счет не найден.*\n\nПопробуйте снова или свяжитесь с @s3pt1ck.",
                        parse_mode="Markdown",
                        reply_markup=get_keyboard([
                            ("🔄 Проверить снова", f"check_payment_{invoice_id}"),
                            ("🔙 Назад в главное меню", "menu_main")
                        ])
                    )
            else:
                logger.error(f"Ответ от API не содержит ключа 'items': {payment_status}")
                await query.edit_message_text(
                    "❌ *Ошибка при получении статуса оплаты.*\n\nПопробуйте снова или свяжитесь с @s3pt1ck.",
                    parse_mode="Markdown",
                    reply_markup=get_keyboard([
                        ("🔄 Проверить снова", f"check_payment_{invoice_id}"),
                        ("🔙 Назад в главное меню", "menu_main")
                    ])
                )
        else:
            logger.error(f"Ошибка при запросе статуса оплаты: {payment_status}")
            await query.edit_message_text(
                "❌ *Ошибка при проверке оплаты!*\n\nПопробуйте снова или свяжитесь с @s3pt1ck.",
                parse_mode="Markdown",
                reply_markup=get_keyboard([
                    ("🔄 Проверить снова", f"check_payment_{invoice_id}"),
                    ("🔙 Назад в главное меню", "menu_main")
                ])
            )
    except Exception as e:
        logger.error(f"Критическая ошибка при проверке оплаты: {e}", exc_info=True)
        await query.edit_message_text(
            "❌ *Ошибка при проверке оплаты!*\n\nПопробуйте снова или свяжитесь с @s3pt1ck.",
            parse_mode="Markdown",
            reply_markup=get_keyboard([
                ("🔄 Проверить снова", f"check_payment_{invoice_id}"),
                ("🔙 Назад в главное меню", "menu_main")
            ])
        )

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню поддержки с кнопкой 'Назад'."""
    query = update.callback_query
    await query.answer()
    text = (
        "📞 *Нужна помощь?*\n\n"
        "Свяжитесь с нашей поддержкой:\n"
        "👉 *@s3pt1ck*\n\n"
        "Мы ответим максимально быстро! 😊"
    )
    buttons = [("🔙 Назад в главное меню", "menu_main")]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def faq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """FAQ с краткими ответами и кнопкой 'Назад'."""
    query = update.callback_query
    await query.answer()
    text = (
        "❓ *Часто задаваемые вопросы*\n\n"
        "🔹 *Как получить лицензию?*\n"
        "Перейдите в 'Купить лицензию' и выберите способ оплаты.\n\n"
        "🔹 *Что делать, если ключ не работает?*\n"
        "Напишите в поддержку @s3pt1ck.\n\n"
        "🔹 *Можно ли использовать ключ на нескольких устройствах?*\n"
        "Нет, ключ привязан к одному устройству."
    )
    buttons = [("🔙 Назад в главное меню", "menu_main")]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Раздел новостей с кнопкой 'Назад'."""
    query = update.callback_query
    await query.answer()
    text = (
        "📰 *Новости Valture*\n\n"
        "Следите за обновлениями!\n"
        "Пока новых объявлений нет. 📅"
    )
    buttons = [("🔙 Назад в главное меню", "menu_main")]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий кнопок."""
    query = update.callback_query
    data = query.data

    if data == "menu_main":
        await main_menu(update, context)
    elif data == "menu_pay":
        await pay(update, context)
    elif data.startswith("get_"):
        await get_payment(update, context)
    elif data.startswith("check_payment_"):
        await check_payment(update, context)
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

    logger.info("Valture бот запущен")
    application.run_polling()
