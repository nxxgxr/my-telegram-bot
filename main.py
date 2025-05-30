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
from yookassa import Configuration, Payment

# --- Настройки ---

BOT_TOKEN = os.environ.get("BOT_TOKEN") or "7941872387:AAGZayILmna-qHHyQy5V50wDGylo3yFCZ0A"
CRYPTOBOT_API_TOKEN = os.environ.get("CRYPTOBOT_API_TOKEN")
YOOKASSA_SHOP_ID = os.environ.get("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.environ.get("YOOKASSA_SECRET_KEY")
CREDS_FILE = os.environ.get("CREDS_FILE") or "valture-license-bot-account.json"
SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME") or "valture"
GOOGLE_CREDS_JSON_BASE64 = os.environ.get("GOOGLE_CREDS_JSON_BASE64")
SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# CryptoBot API endpoint
CRYPTO_BOT_API = "https://pay.crypt.bot/api"

# Configure YooKassa
if YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY:
    Configuration.account_id = YOOKASSA_SHOP_ID
    Configuration.secret_key = YOOKASSA_SECRET_KEY

# --- Логирование ---

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.DEBUG
)
logger = logging.getLogger(__name__)

# --- Flask для keep-alive и вебхуков ---

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

@app.route('/yookassa-webhook', methods=['POST'])
def yookassa_webhook():
    """Handle YooKassa payment notifications."""
    try:
        event_json = request.get_json()
        logger.debug(f"YooKassa webhook received: {event_json}")
        
        if not event_json or 'event' not in event_json or 'object' not in event_json:
            logger.error("Invalid webhook payload")
            return jsonify({"status": "error", "message": "Invalid payload"}), 400

        event = event_json['event']
        payment_object = event_json['object']
        
        if event == 'payment.succeeded':
            payment_id = payment_object['id']
            metadata = payment_object.get('metadata', {})
            user_id = metadata.get('user_id')
            username = metadata.get('username')
            
            if not user_id or not username:
                logger.error(f"Missing metadata in webhook: user_id={user_id}, username={username}")
                return jsonify({"status": "error", "message": "Missing metadata"}), 400

            # Store payment confirmation for async processing
            from application import application
            job_queue = application.job_queue
            job_queue.run_once(
                process_yookassa_payment,
                0,
                context={
                    'payment_id': payment_id,
                    'user_id': int(user_id),
                    'username': username,
                    'chat_id': int(user_id)  # Assuming private chat
                },
                name=f"yookassa_payment_{payment_id}"
            )
            logger.info(f"YooKassa payment succeeded: payment_id={payment_id}, user={username}")
            return jsonify({"status": "ok"}), 200
        
        elif event == 'payment.canceled':
            logger.warning(f"YooKassa payment canceled: payment_id={payment_object['id']}")
            return jsonify({"status": "ok"}), 200
        
        return jsonify({"status": "ignored"}), 200
    
    except Exception as e:
        logger.error(f"Error in YooKassa webhook: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

async def process_yookassa_payment(context: ContextTypes.DEFAULT_TYPE):
    """Process confirmed YooKassa payment and issue license key."""
    job_context = context.job.context
    payment_id = job_context['payment_id']
    user_id = job_context['user_id']
    username = job_context['username']
    chat_id = job_context['chat_id']

    try:
        license_key = generate_license()
        append_license_to_sheet(license_key, username)
        text = (
            "🎉 *Поздравляем с покупкой!*\n\n"
            "Ваш лицензионный ключ:\n"
            f"`{license_key}`\n\n"
            "Сохраните его в надежном месте!"
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="Markdown"
        )
        logger.info(f"YooKassa payment processed, key issued: {license_key} for {username}")
    except Exception as e:
        logger.error(f"Error processing YooKassa payment {payment_id}: {e}", exc_info=True)
        error_text = (
            "❌ *Ошибка*\n\n"
            "Не удалось выдать ключ. Обратитесь в поддержку (@s3pt1ck)."
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=error_text,
            parse_mode="Markdown"
        )

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
    """Генерация безопасного лицензионного ключа."""
    try:
        key = ''.join(secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') for _ in range(length))
        logger.info(f"Сгенерирован ключ: {key}")
        return key
    except Exception as e:
        logger.error(f"Ошибка при генерации ключа: {e}")
        raise

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

def create_crypto_invoice(amount, asset="USDT", description="Valture License"):
    """Создание инвойса через CryptoBot."""
    logger.debug(f"Создание инвойса: amount={amount}, asset={asset}, description={description}")
    if not CRYPTOBOT_API_TOKEN:
        logger.error("CRYPTOBOT_API_TOKEN не задан в переменных окружения")
        return None, "CRYPTOBOT_API_TOKEN не задан"

    try:
        payload = {
            "amount": str(amount),
            "asset": asset,
            "description": description,
            "order_id": secrets.token_hex(16),
        }
        headers = {
            "Crypto-Pay-API-Token": CRYPTOBOT_API_TOKEN,
            "Content-Type": "application/json"
        }
        logger.debug(f"Отправка запроса на {CRYPTO_BOT_API}/createInvoice с payload: {payload}")
        
        response = requests.post(f"{CRYPTO_BOT_API}/createInvoice", json=payload, headers=headers, timeout=10)
        logger.debug(f"HTTP статус: {response.status_code}, Ответ: {response.text}")
        
        response.raise_for_status()
        data = response.json()
        
        if data.get("ok"):
            logger.info(f"Инвойс успешно создан: invoice_id={data['result']['invoice_id']}")
            return data["result"], None
        else:
            error_msg = data.get("error", "Неизвестная ошибка от CryptoBot")
            logger.error(f"Ошибка API CryptoBot: {error_msg}")
            return None, f"Ошибка API: {error_msg}"
            
    except requests.exceptions.HTTPError as http_err:
        logger.error(f"HTTP ошибка при создании инвойса: {http_err}, Ответ: {response.text}")
        if response.status_code == 401:
            return None, "Недействительный CRYPTOBOT_API_TOKEN"
        elif response.status_code == 429:
            return None, "Превышен лимит запросов к CryptoBot API"
        return None, f"HTTP ошибка: {http_err}"
    except requests.exceptions.Timeout:
        logger.error("Тайм-аут при обращении к CryptoBot API")
        return None, "Тайм-аут запроса к CryptoBot API"
    except requests.exceptions.RequestException as req_err:
        logger.error(f"Сетевая ошибка при создании инвойса: {req_err}")
        return None, f"Сетевая ошибка: {req_err}"
    except Exception as e:
        logger.error(f"Общая ошибка при создании инвойса: {e}")
        return None, f"Общая ошибка: {e}"

def check_invoice_status(invoice_id):
    """Проверка статуса инвойса CryptoBot."""
    logger.debug(f"Проверка статуса инвойса: invoice_id={invoice_id}")
    try:
        headers = {"Crypto-Pay-API-Token": CRYPTOBOT_API_TOKEN}
        response = requests.get(f"{CRYPTO_BOT_API}/getInvoices?invoice_ids={invoice_id}", headers=headers, timeout=10)
        logger.debug(f"HTTP статус: {response.status_code}, Ответ: {response.text}")
        response.raise_for_status()
        data = response.json()
        if data.get("ok"):
            status = data["result"]["items"][0]["status"]
            logger.info(f"Статус инвойса {invoice_id}: {status}")
            return status
        else:
            logger.error(f"Ошибка проверки статуса инвойса: {data.get('error', 'Неизвестная ошибка')}")
            return None
    except requests.exceptions.HTTPError as http_err:
        logger.error(f"HTTP ошибка при проверке инвойса: {http_err}, Ответ: {response.text}")
        return None
    except requests.exceptions.RequestException as req_err:
        logger.error(f"Сетевая ошибка при проверке инвойса: {req_err}")
        return None
    except Exception as e:
        logger.error(f"Общая ошибка при проверке инвойса: {e}")
        return None

def create_yookassa_payment(amount, description, user_id, username):
    """Создание платежа через YooKassa."""
    logger.debug(f"Создание YooKassa платежа: amount={amount}, description={description}, user_id={user_id}")
    if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET_KEY:
        logger.error("YOOKASSA_SHOP_ID или YOOKASSA_SECRET_KEY не заданы")
        return None, "YooKassa credentials not configured"

    try:
        idempotence_key = str(uuid4())
        payment = Payment.create({
            "amount": {
                "value": f"{amount:.2f}",
                "currency": "RUB"
            },
            "confirmation": {
                "type": "redirect",
                "return_url": "https://t.me/valture_buy_bot"
            },
            "capture": True,
            "description": description,
            "metadata": {
                "user_id": str(user_id),
                "username": username
            }
        }, idempotence_key)

        logger.info(f"YooKassa платеж создан: payment_id={payment.id}")
        return payment, None

    except Exception as e:
        logger.error(f"Ошибка при создании YooKassa платежа: {e}")
        return None, f"YooKassa ошибка: {str(e)}"

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
        "🛳️ Плавность управления: Улучшенная точность и четкость мыши для идеального контроля в любой ситуации.\n"
        "🖥️ Плавность картинки в играх: Наслаждайтесь четкой и плавной картинкой, которая погружает вас в игру.\n\n"
        "➖➖➖➖➖➖➖➖➖➖➖➖➖\n"
        "_Создано для геймеров, которые ценят качество и стремятся к победе._"
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
        "Стоимость: *1000 рублей (~$10 USDT)*\n"
        "Выберите способ оплаты:\n"
        "- *CryptoBot*: Оплата в USDT через криптовалюту.\n"
        "- *YooKassa*: Оплата картой, YooMoney или другими способами.\n\n"
        "После успешной оплаты вы получите уникальный ключ прямо в чат."
    )
    buttons = [
        ("💸 Оплатить через CryptoBot", "pay_crypto"),
        ("💳 Оплатить через YooKassa", "pay_yookassa"),
        ("🔙 Назад", "menu_main")
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def pay_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение оплаты через CryptoBot."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.full_name

    try:
        logger.debug(f"Создание CryptoBot инвойса для пользователя: {username} (ID: {user_id})")
        invoice, error = create_crypto_invoice(amount=10.0, asset="USDT", description="Valture License")
        if not invoice:
            error_msg = f"❌ *Ошибка*\n\nНе удалось создать инвойс: {error or 'Неизвестная ошибка'}. Попробуйте позже или обратитесь в поддержку (@s3pt1ck)."
            logger.error(f"Ошибка в pay_crypto: {error}")
            await query.edit_message_text(error_msg, parse_mode="Markdown")
            return

        invoice_id = invoice["invoice_id"]
        pay_url = invoice["pay_url"]

        context.user_data["payment_type"] = "crypto"
        context.user_data["invoice_id"] = invoice_id
        context.user_data["username"] = username
        logger.info(f"CryptoBot инвойс создан: invoice_id={invoice_id}, pay_url={pay_url}")

        text = (
            "💸 *Оплатите лицензию через CryptoBot*\n\n"
            "Перейдите по ссылке для оплаты 10 USDT:\n"
            f"[Оплатить через CryptoBot]({pay_url})\n\n"
            "После оплаты нажмите кнопку ниже для подтверждения."
        )
        buttons = [
            ("✅ Подтвердить оплату", "pay_verify"),
            ("🔙 Назад", "menu_main")
        ]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons), disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Критическая ошибка в pay_crypto: {e}", exc_info=True)
        await query.edit_message_text(
            "❌ *Ошибка*\n\nНе удалось создать инвойс. Попробуйте позже или обратитесь в поддержку (@s3pt1ck).",
            parse_mode="Markdown"
        )

async def pay_yookassa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение оплаты через YooKassa."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.full_name

    try:
        logger.debug(f"Создание YooKassa платежа для пользователя: {username} (ID: {user_id})")
        payment, error = create_yookassa_payment(
            amount=1000.0,
            description="Valture License",
            user_id=user_id,
            username=username
        )
        if not payment:
            error_msg = f"❌ *Ошибка*\n\nНе удалось создать платеж: {error or 'Неизвестная ошибка'}. Попробуйте позже или обратитесь в поддержку (@s3pt1ck)."
            logger.error(f"Ошибка в pay_yookassa: {error}")
            await query.edit_message_text(error_msg, parse_mode="Markdown")
            return

        payment_id = payment.id
        confirmation_url = payment.confirmation.confirmation_url

        context.user_data["payment_type"] = "yookassa"
        context.user_data["payment_id"] = payment_id
        context.user_data["username"] = username
        logger.info(f"YooKassa платеж создан: payment_id={payment_id}, confirmation_url={confirmation_url}")

        text = (
            "💳 *Оплатите лицензию через YooKassa*\n\n"
            "Перейдите по ссылке для оплаты 1000 RUB:\n"
            f"[Оплатить через YooKassa]({confirmation_url})\n\n"
            "После оплаты вы получите ключ автоматически."
        )
        buttons = [("🔙 Назад", "menu_main")]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons), disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Критическая ошибка в pay_yookassa: {e}", exc_info=True)
        await query.edit_message_text(
            "❌ *Ошибка*\n\nНе удалось создать платеж. Попробуйте позже или обратитесь в поддержку (@s3pt1ck).",
            parse_mode="Markdown"
        )

async def pay_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка статуса оплаты (только для CryptoBot)."""
    query = update.callback_query
    await query.answer()

    payment_type = context.user_data.get("payment_type")
    if payment_type != "crypto":
        await query.edit_message_text(
            "❌ *Ошибка*\n\nЭтот метод для проверки CryptoBot оплаты. Для YooKassa оплата подтверждается автоматически.",
            parse_mode="Markdown"
        )
        return

    invoice_id = context.user_data.get("invoice_id")
    username = context.user_data.get("username")

    if not invoice_id or not username:
        logger.error(f"Данные об оплате отсутствуют: invoice_id={invoice_id}, username={username}")
        await query.edit_message_text(
            "❌ *Ошибка*\n\nДанные об оплате не найдены. Попробуйте снова или обратитесь в поддержку (@s3pt1ck).",
            parse_mode="Markdown"
        )
        return

    try:
        status = check_invoice_status(invoice_id)
        if status == "paid":
            license_key = generate_license()
            append_license_to_sheet(license_key, username)
            text = (
                "🎉 *Поздравляем с покупкой!*\n\n"
                "Ваш лицензионный ключ:\n"
                f"`{license_key}`\n\n"
                "Сохраните его в надежном месте!"
            )
            logger.info(f"CryptoBot оплата подтверждена, ключ выдан: {license_key} для {username}")
            await query.edit_message_text(text, parse_mode="Markdown")
            context.user_data.clear()
        else:
            logger.warning(f"CryptoBot оплата не подтверждена для invoice_id={invoice_id}, статус: {status}")
            await query.edit_message_text(
                "⏳ *Оплата не подтверждена*\n\n"
                "Пожалуйста, завершите оплату или попробуйте снова. Если возникли проблемы, обратитесь в поддержку (@s3pt1ck).",
                parse_mode="Markdown",
                reply_markup=get_keyboard([("🔄 Проверить снова", "pay_verify"), ("🔙 Назад", "menu_main")])
            )
    except Exception as e:
        logger.error(f"Ошибка при проверке CryptoBot оплаты: {e}", exc_info=True)
        await query.edit_message_text(
            "❌ *Ошибка*\n\nНе удалось проверить статус оплаты. Попробуйте позже или обратитесь в поддержку (@s3pt1ck).",
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
        "Перейдите в раздел 'Купить лицензию', выберите способ оплаты и оплатите.\n\n"
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
    elif data == "pay_crypto":
        await pay_crypto(update, context)
    elif data == "pay_yookassa":
        await pay_yookassa(update, context)
    elif data == "pay_verify":
        await pay_verify(update, context)
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
