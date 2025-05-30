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

# --- Конфигурация (цены и ссылка на приложение) ---

PRICES = {
    "crypto": {"amount": 4.0, "currency": "TON", "approx_usd": 12.7},
    "yookassa": {"amount": 1.0, "currency": "RUB", "approx_usd": 12.7}
}
APP_DOWNLOAD_LINK = "https://www.dropbox.com/scl/fi/ze5ebd909z2qeaaucn56q/VALTURE.exe?rlkey=ihdzk8voej4oikrdhq0wfzvbb&st=jj5tgroa&dl=1"

# --- Настройки ---

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CRYPTOBOT_API_TOKEN = os.environ.get("CRYPTOBOT_API_TOKEN")
YOOKASSA_SHOP_ID = os.environ.get("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.environ.get("YOOKASSA_SECRET_KEY")
CREDS_FILE = os.environ.get("CREDS_FILE")
SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME")
GOOGLE_CREDS_JSON_BASE64 = os.environ.get("GOOGLE_CREDS_JSON_BASE64")
SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
CRYPTO_BOT_API = "https://pay.crypt.bot/api"

# Configure YooKassa
if YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY:
    Configuration.account_id = YOOKASSA_SHOP_ID
    Configuration.secret_key = YOOKASSA_SECRET_KEY
else:
    raise ValueError("YooKassa credentials not configured")

# --- Логирование ---

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.DEBUG
)
logger = logging.getLogger(__name__)

# --- Flask приложение ---

app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Valture bot is running!"

@app.route('/test-yookassa')
def test_yookassa():
    """Debug endpoint to test YooKassa configuration."""
    try:
        payment = Payment.create({
            "amount": {"value": "1.00", "currency": "RUB"},
            "confirmation": {"type": "redirect", "return_url": "https://t.me/valture_buy_bot"},
            "description": "Test payment"
        }, str(uuid4()))
        return f"YooKassa test payment created: {payment.id}"
    except Exception as e:
        return f"YooKassa test error: {str(e)}"

@app.route('/yookassa-webhook', methods=['POST'])
def yookassa_webhook():
    """Handle YooKassa payment notifications."""
    try:
        event_json = request.get_json()
        logger.debug(f"YooKassa webhook received: {json.dumps(event_json, indent=2, ensure_ascii=False)}")
        
        if not event_json or 'event' not in event_json or 'object' not in event_json:
            logger.error("Invalid webhook payload")
            return jsonify({"status": "error", "message": "Invalid payload"}), 400

        event = event_json['event']
        payment_object = event_json['object']
        
        if event == 'payment.succeeded':
            payment_id = payment_object.get('id')
            metadata = payment_object.get('metadata', {})
            user_id = metadata.get('user_id')
            username = metadata.get('username')
            
            if not payment_id or not user_id or not username:
                logger.error(f"Missing critical data: payment_id={payment_id}, user_id={user_id}, username={username}")
                return jsonify({"status": "error", "message": "Missing data"}), 400

            from application import application
            job_queue = application.job_queue
            job_queue.run_once(
                process_yookassa_payment,
                0,
                context={
                    'payment_id': payment_id,
                    'user_id': int(user_id),
                    'username': username,
                    'chat_id': int(user_id)
                },
                name=f"yookassa_payment_{payment_id}"
            )
            logger.info(f"YooKassa payment succeeded: payment_id={payment_id}, user={username}")
            return jsonify({"status": "ok"}), 200
        
        elif event == 'payment.canceled':
            logger.warning(f"YooKassa payment canceled: payment_id={payment_object.get('id')}")
            return jsonify({"status": "ok"}), 200
        
        logger.debug(f"Ignored webhook event: {event}")
        return jsonify({"status": "ignored"}), 200
    
    except Exception as e:
        logger.error(f"YooKassa webhook error: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

async def process_yookassa_payment(context: ContextTypes.DEFAULT_TYPE):
    """Process confirmed YooKassa payment and issue license key."""
    job_context = context.job.context
    payment_id = job_context['payment_id']
    user_id = job_context['user_id']
    username = job_context['username']
    chat_id = job_context['chat_id']

    try:
        # Verify payment status
        payment = Payment.find_one(payment_id)
        logger.debug(f"YooKassa payment {payment_id} status: {payment.status}")
        if payment.status != "succeeded":
            logger.warning(f"YooKassa payment not succeeded: payment_id={payment_id}, status={payment.status}")
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ *Ошибка оплаты!*\n\nПлатеж не подтвержден. Свяжитесь с @s3pt1ck.",
                parse_mode="Markdown"
            )
            return

        license_key = generate_license()
        append_license_to_sheet(license_key, username, payment_id, "yookassa")
        text = (
            "🎉 *Поздравляем с покупкой!*\n\n"
            "Ваш лицензионный ключ (HWID):\n"
            f"`{license_key}`\n\n"
            "Скачайте приложение:\n"
            f"[VALTURE.exe]({APP_DOWNLOAD_LINK})\n\n"
            "Сохраните ключ в надежном месте! 🚀"
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
        logger.info(f"YooKassa payment processed, key issued: {license_key[:8]}... for {username}, payment_id={payment_id}")
    except Exception as e:
        logger.error(f"Error processing YooKassa payment {payment_id}: {str(e)}", exc_info=True)
        error_text = (
            "❌ *Произошла ошибка!*\n\n"
            "Не удалось выдать ключ. Обратитесь в поддержку: @s3pt1ck."
        )
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=error_text,
                parse_mode="Markdown"
            )
        except Exception as send_error:
            logger.error(f"Failed to send error message to chat_id {chat_id}: {str(send_error)}")

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# --- Google Credentials ---

def setup_google_creds():
    """Decode base64 Google credentials and create a temporary file."""
    logger.debug("Checking Google credentials...")
    if GOOGLE_CREDS_JSON_BASE64:
        try:
            creds_json = base64.b64decode(GOOGLE_CREDS_JSON_BASE64).decode("utf-8")
            with open(CREDS_FILE, "w") as f:
                f.write(creds_json)
            logger.info("Google credentials decoded and saved")
        except Exception as e:
            logger.error(f"Error decoding Google credentials: {str(e)}")
            raise
    elif not os.path.exists(CREDS_FILE):
        logger.error("Google credentials file not found")
        raise FileNotFoundError("Google credentials file not found")
    else:
        logger.info("Using existing Google credentials file")

# --- Google Sheets ---

sheet_cache = None

def get_sheet():
    """Get cached Google Sheets object."""
    global sheet_cache
    if sheet_cache is None:
        try:
            setup_google_creds()
            creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPE)
            client = gspread.authorize(creds)
            sheet_cache = client.open(SPREADSHEET_NAME).sheet1
            logger.info("Connected to Google Sheets")
        except Exception as e:
            logger.error(f"Error connecting to Google Sheets: {str(e)}")
            raise
    return sheet_cache

def generate_license(length=32):
    """Generate a secure license key."""
    try:
        key = ''.join(secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') for _ in range(length))
        logger.info(f"Generated license key: {key[:8]}...")
        return key
    except Exception as e:
        logger.error(f"Error generating license key: {str(e)}")
        raise

def append_license_to_sheet(license_key, username, payment_id, payment_type):
    """Append license to Google Sheets."""
    try:
        sheet = get_sheet()
        utc_plus_2 = timezone(timedelta(hours=2))
        now_utc_plus_2 = datetime.now(utc_plus_2)
        now_str = now_utc_plus_2.strftime("%Y-%m-%d %H:%M:%S")
        sheet.append_row([license_key, username, payment_id, payment_type, now_str])
        logger.info(f"License {license_key[:8]}... added for {username}, payment_id={payment_id}, type={payment_type}")
    except Exception as e:
        logger.error(f"Error appending license: {str(e)}")
        raise

# --- Платежная логика ---

def create_crypto_invoice(amount, asset="TON", description="Valture License"):
    """Create a CryptoBot invoice."""
    logger.debug(f"Creating invoice: amount={amount}, asset={asset}")
    if not CRYPTOBOT_API_TOKEN:
        logger.error("CRYPTOBOT_API_TOKEN not set")
        return None, "CRYPTOBOT_API_TOKEN not set"

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
        response = requests.post(f"{CRYPTO_BOT_API}/createInvoice", json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if data.get("ok"):
            logger.info(f"Invoice created: invoice_id={data['result']['invoice_id']}")
            return data["result"], None
        else:
            error_msg = data.get("error", "Unknown CryptoBot error")
            logger.error(f"CryptoBot API error: {error_msg}")
            return None, f"API error: {error_msg}"
            
    except requests.exceptions.HTTPError as http_err:
        logger.error(f"HTTP error creating invoice: {str(http_err)}, Response: {response.text}")
        return None, f"HTTP error: {str(http_err)}"
    except requests.exceptions.Timeout:
        logger.error("Timeout accessing CryptoBot API")
        return None, "Request timeout"
    except requests.exceptions.RequestException as req_err:
        logger.error(f"Network error creating invoice: {str(req_err)}")
        return None, f"Network error: {str(req_err)}"
    except Exception as e:
        logger.error(f"General error creating invoice: {str(e)}")
        return None, f"Error: {str(e)}"

def check_invoice_status(invoice_id):
    """Check CryptoBot invoice status."""
    logger.debug(f"Checking invoice status: invoice_id={invoice_id}")
    try:
        headers = {"Crypto-Pay-API-Token": CRYPTOBOT_API_TOKEN}
        response = requests.get(f"{CRYPTO_BOT_API}/getInvoices?invoice_ids={invoice_id}", headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get("ok"):
            status = data["result"]["items"][0]["status"]
            logger.info(f"Invoice {invoice_id} status: {status}")
            return status
        else:
            logger.error(f"Error checking invoice status: {data.get('error', 'Unknown error')}")
            return None
    except Exception as e:
        logger.error(f"Error checking invoice: {str(e)}")
        return None

def create_yookassa_payment(amount, description, user_id, username):
    """Create a YooKassa payment."""
    logger.debug(f"Creating YooKassa payment: amount={amount}, user_id={user_id}")
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

        logger.info(f"YooKassa payment created: payment_id={payment.id}")
        return payment, None

    except Exception as e:
        logger.error(f"Error creating YooKassa payment: {str(e)}")
        return None, f"YooKassa error: {str(e)}"

async def check_yookassa_payment(context: ContextTypes.DEFAULT_TYPE):
    """Periodically check YooKassa payment status."""
    job_context = context.job.context
    payment_id = job_context['payment_id']
    user_id = job_context['user_id']
    username = job_context['username']
    chat_id = job_context['chat_id']

    try:
        payment = Payment.find_one(payment_id)
        logger.debug(f"Checking YooKassa payment {payment_id} status: {payment.status}")
        if payment.status == "succeeded":
            license_key = generate_license()
            append_license_to_sheet(license_key, username, payment_id, "yookassa")
            text = (
                "🎉 *Поздравляем с покупкой!*\n\n"
                "Ваш лицензионный ключ (HWID):\n"
                f"`{license_key}`\n\n"
                "Скачайте приложение:\n"
                f"[VALTURE.exe]({APP_DOWNLOAD_LINK})\n\n"
                "Сохраните ключ в надежном месте! 🚀"
            )
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
            logger.info(f"YooKassa payment confirmed via check, key issued: {license_key[:8]}... for {username}, payment_id={payment_id}")
            context.job.schedule_removal()
            context.user_data.clear()
        elif payment.status in ["canceled", "waiting_for_capture"]:
            logger.warning(f"YooKassa payment {payment_id} not completed: status={payment.status}")
            if context.job.current_run_count > 30:  # Stop after ~5 minutes
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="❌ *Оплата не завершена!*\n\nПлатеж истек или отменен. Попробуйте снова или свяжитесь с @s3pt1ck.",
                    parse_mode="Markdown"
                )
                context.job.schedule_removal()
                context.user_data.clear()
    except Exception as e:
        logger.error(f"Error checking YooKassa payment {payment_id}: {str(e)}")
        if context.job.current_run_count > 30:
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ *Ошибка проверки оплаты!*\n\nПопробуйте снова или свяжитесь с @s3pt1ck.",
                parse_mode="Markdown"
            )
            context.job.schedule_removal()

# --- Логика Telegram-бота ---

def get_keyboard(buttons):
    """Create an inline keyboard."""
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=callback)] for text, callback in buttons])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message."""
    welcome_text = (
        "🎮 *Добро пожаловать в Valture!*\n\n"
        "Ваш лучший инструмент для игровой производительности! 🚀\n"
        "Выберите опцию ниже:"
    )
    buttons = [("🏠 Главное меню", "menu_main")]
    await update.message.reply_text(welcome_text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main menu."""
    query = update.callback_query
    await query.answer()
    buttons = [
        ("💳 Купить лицензию", "menu_pay"),
        ("ℹ️ О Valture", "menu_about"),
        ("📰 Новости", "menu_news"),
        ("❓ FAQ", "menu_faq"),
        ("📞 Поддержка", "menu_support"),
    ]
    await query.edit_message_text(
        "🏠 *Главное меню*\n\nВыберите раздел:",
        parse_mode="Markdown",
        reply_markup=get_keyboard(buttons)
    )

async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """About information."""
    query = update.callback_query
    await query.answer()
    text = (
        "✨ *Valture — Ваш путь к совершенству в играх*\n\n"
        "Valture — это передовой инструмент для геймеров, которые не идут на компромиссы. "
        "Мы повышаем производительность, обеспечивая плавность, стабильность и отзывчивость.\n\n"
        "🔥 *Почему выбирают Valture?*\n"
        "🚀 Увеличение FPS на 20–30%\n"
        "🛡️ Стабильный фреймрейт\n"
        "💡 Молниеносная отзывчивость\n"
        "🔋 Оптимизация Windows\n"
        "🛳️ Плавность управления\n"
        "🖥️ Плавность картинки\n\n"
        "_Для геймеров, стремящихся к победе._"
    )
    buttons = [("🔙 Назад в главное меню", "menu_main")]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Payment menu with prices."""
    query = update.callback_query
    await query.answer()
    text = (
        "💳 *Покупка лицензии Valture*\n\n"
        f"💰 *Цены:*\n"
        f"- *{PRICES['crypto']['amount']} {PRICES['crypto']['currency']}* (~${PRICES['crypto']['approx_usd']})\n"
        f"- *{PRICES['yookassa']['amount']} {PRICES['yookassa']['currency']}* (~${PRICES['yookassa']['approx_usd']})\n\n"
        "Выберите способ оплаты:\n"
        "- *CryptoBot*: Оплата криптовалютой.\n"
        "- *YooKassa*: Оплата картой.\n\n"
        "После оплаты вы автоматически получите HWID-ключ и ссылку на приложение."
    )
    buttons = [
        ("💸 Оплатить через CryptoBot", "pay_crypto"),
        ("💳 Оплатить через YooKassa", "pay_yookassa"),
        ("🔙 Назад в главное меню", "menu_main")
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def pay_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm CryptoBot payment."""
    query = update.callback_query
    await query.answer()
    text = (
        "💸 *Подтверждение оплаты CryptoBot*\n\n"
        f"Вы собираетесь оплатить *{PRICES['crypto']['amount']} {PRICES['crypto']['currency']}* за лицензию Valture.\n"
        "Продолжить?"
    )
    buttons = [
        ("✅ Подтвердить оплату", "pay_crypto_confirm"),
        ("🔙 Назад к способам оплаты", "menu_pay")
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def pay_crypto_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process CryptoBot payment with auto-checking."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.full_name

    try:
        logger.debug(f"Creating CryptoBot invoice for user: {username} (ID: {user_id})")
        invoice, error = create_crypto_invoice(
            amount=PRICES['crypto']['amount'],
            asset=PRICES['crypto']['currency'],
            description="Valture License"
        )
        if not invoice:
            error_msg = (
                "❌ *Ошибка!*\n\n"
                f"Не удалось создать инвойс: {error or 'Неизвестная ошибка'}.\n"
                "Попробуйте снова или свяжитесь с @s3pt1ck."
            )
            buttons = [
                ("🔄 Попробовать снова", "pay_crypto"),
                ("🔙 Назад к способам оплаты", "menu_pay")
            ]
            logger.error(f"CryptoBot error: {error}")
            await query.edit_message_text(error_msg, parse_mode="Markdown", reply_markup=get_keyboard(buttons))
            return

        invoice_id = invoice["invoice_id"]
        pay_url = invoice["pay_url"]

        context.user_data["payment_type"] = "crypto"
        context.user_data["invoice_id"] = invoice_id
        context.user_data["username"] = username
        context.user_data["chat_id"] = query.message.chat_id
        logger.info(f"CryptoBot invoice created: invoice_id={invoice_id}")

        text = (
            "💸 *Оплатите через CryptoBot*\n\n"
            f"Нажмите ниже для оплаты *{PRICES['crypto']['amount']} {PRICES['crypto']['currency']}*:\n"
            f"[Оплатить через CryptoBot]({pay_url})\n\n"
            "Ключ и ссылка будут отправлены автоматически после оплаты."
        )
        buttons = [("🔙 Назад к способам оплаты", "menu_pay")]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons), disable_web_page_preview=True)

        context.job_queue.run_repeating(
            check_crypto_payment,
            interval=10,
            first=10,
            context={
                'invoice_id': invoice_id,
                'user_id': user_id,
                'username': username,
                'chat_id': query.message.chat_id
            },
            name=f"crypto_check_{invoice_id}"
        )
    except Exception as e:
        logger.error(f"Critical error in pay_crypto_confirm: {str(e)}", exc_info=True)
        error_msg = (
            "❌ *Ошибка!*\n\n"
            "Не удалось обработать запрос. Попробуйте снова или свяжитесь с @s3pt1ck."
        )
        buttons = [
            ("🔄 Попробовать снова", "pay_crypto"),
            ("🔙 Назад к способам оплаты", "menu_pay")
        ]
        await query.edit_message_text(error_msg, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def check_crypto_payment(context: ContextTypes.DEFAULT_TYPE):
    """Auto-check CryptoBot payment status."""
    job_context = context.job.context
    invoice_id = job_context['invoice_id']
    user_id = job_context['user_id']
    username = job_context['username']
    chat_id = job_context['chat_id']

    try:
        status = check_invoice_status(invoice_id)
        if status == "paid":
            license_key = generate_license()
            append_license_to_sheet(license_key, username, invoice_id, "crypto")
            text = (
                "🎉 *Поздравляем с покупкой!*\n\n"
                "Ваш лицензионный ключ (HWID):\n"
                f"`{license_key}`\n\n"
                "Скачайте приложение:\n"
                f"[VALTURE.exe]({APP_DOWNLOAD_LINK})\n\n"
                "Сохраните ключ в надежном месте! 🚀"
            )
            buttons = [("🏠 Назад в главное меню", "menu_main")]
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=get_keyboard(buttons),
                disable_web_page_preview=True
            )
            logger.info(f"CryptoBot payment confirmed, key issued: {license_key[:8]}... for {username}, invoice_id={invoice_id}")
            context.job.schedule_removal()
            context.user_data.clear()
        elif status in ["expired", "cancelled"]:
            logger.warning(f"CryptoBot invoice {invoice_id} expired or cancelled: {status}")
            text = (
                "❌ *Оплата не завершена*\n\n"
                "Срок действия инвойса истек или он был отменен. Попробуйте снова."
            )
            buttons = [
                ("🔄 Попробовать снова", "pay_crypto"),
                ("🔙 Назад к способам оплаты", "menu_pay")
            ]
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=get_keyboard(buttons)
            )
            context.job.schedule_removal()
            context.user_data.clear()
    except Exception as e:
        logger.error(f"Error checking CryptoBot payment: {str(e)}")
        text = (
            "❌ *Ошибка проверки оплаты!*\n\n"
            "Попробуйте снова или свяжитесь с @s3pt1ck."
        )
        buttons = [
            ("🔄 Попробовать снова", "pay_crypto"),
            ("🔙 Назад к способам оплаты", "menu_pay")
        ]
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="Markdown",
            reply_markup=get_keyboard(buttons)
        )
        context.job.schedule_removal()

async def pay_yookassa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm YooKassa payment."""
    query = update.callback_query
    await query.answer()
    text = (
        "💳 *Подтверждение оплаты YooKassa*\n\n"
        f"Вы собираетесь оплатить *{PRICES['yookassa']['amount']} {PRICES['yookassa']['currency']}* за лицензию Valture.\n"
        "Продолжить?"
    )
    buttons = [
        ("✅ Подтвердить оплату", "pay_yookassa_confirm"),
        ("🔙 Назад к способам оплаты", "menu_pay")
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def pay_yookassa_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process YooKassa payment after confirmation."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.full_name

    try:
        logger.debug(f"Creating YooKassa payment for user: {username} (ID: {user_id})")
        payment, error = create_yookassa_payment(
            amount=PRICES['yookassa']['amount'],
            description="Valture License",
            user_id=user_id,
            username=username
        )
        if not payment:
            error_msg = (
                "❌ *Ошибка!*\n\n"
                f"Не удалось создать платеж: {error or 'Неизвестная ошибка'}.\n"
                "Попробуйте снова или свяжитесь с @s3pt1ck."
            )
            buttons = [
                ("🔄 Попробовать снова", "pay_yookassa"),
                ("🔙 Назад к способам оплаты", "menu_pay")
            ]
            logger.error(f"YooKassa error: {error}")
            await query.edit_message_text(error_msg, parse_mode="Markdown", reply_markup=get_keyboard(buttons))
            return

        payment_id = payment.id
        confirmation_url = payment.confirmation.confirmation_url

        context.user_data["payment_type"] = "yookassa"
        context.user_data["payment_id"] = payment_id
        context.user_data["username"] = username
        context.user_data["chat_id"] = query.message.chat_id
        logger.info(f"YooKassa payment created: payment_id={payment_id}")

        text = (
            "💳 *Оплатите через YooKassa*\n\n"
            f"Нажмите ниже для оплаты *{PRICES['yookassa']['amount']} {PRICES['yookassa']['currency']}*:\n"
            f"[Оплатить через YooKassa]({confirmation_url})\n\n"
            "Ключ и ссылка будут отправлены автоматически после оплаты."
        )
        buttons = [("🔙 Назад к способам оплаты", "menu_pay")]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons), disable_web_page_preview=True)

        # Start periodic payment check
        context.job_queue.run_repeating(
            check_yookassa_payment,
            interval=10,
            first=10,
            context={
                'payment_id': payment_id,
                'user_id': user_id,
                'username': username,
                'chat_id': query.message.chat_id
            },
            name=f"yookassa_check_{payment_id}"
        )
    except Exception as e:
        logger.error(f"Critical error in pay_yookassa_confirm: {str(e)}", exc_info=True)
        error_msg = (
            "❌ *Ошибка!*\n\n"
            "Не удалось обработать запрос. Попробуйте снова или свяжитесь с @s3pt1ck."
        )
        buttons = [
            ("🔄 Попробовать снова", "pay_yookassa"),
            ("🔙 Назад к способам оплаты", "menu_pay")
        ]
        await query.edit_message_text(error_msg, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Support menu."""
    query = update.callback_query
    await query.answer()
    text = (
        "📞 *Нужна помощь?*\n\n"
        "Свяжитесь с поддержкой:\n"
        "👉 *@s3pt1ck*\n\n"
        "Мы ответим максимально быстро! 😊"
    )
    buttons = [("🔙 Назад в главное меню", "menu_main")]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def faq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """FAQ."""
    query = update.callback_query
    await query.answer()
    text = (
        "❓ *Часто задаваемые вопросы*\n\n"
        "🔹 *Как получить лицензию?*\n"
        "Перейдите в 'Купить лицензию' и выберите способ оплаты.\n\n"
        "🔹 *Что делать, если ключ не пришел?*\n"
        "Напишите в поддержку: @s3pt1ck.\n\n"
        "🔹 *Можно ли использовать ключ на нескольких устройствах?*\n"
        "Нет, ключ привязан к одному устройству."
    )
    buttons = [("🔙 Назад в главное меню", "menu_main")]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """News section."""
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
    """Handle button presses."""
    query = update.callback_query
    data = query.data

    if data == "menu_main":
        await main_menu(update, context)
    elif data == "menu_pay":
        await pay(update, context)
    elif data == "pay_crypto":
        await pay_crypto(update, context)
    elif data == "pay_crypto_confirm":
        await pay_crypto_confirm(update, context)
    elif data == "pay_yookassa":
        await pay_yookassa(update, context)
    elif data == "pay_yookassa_confirm":
        await pay_yookassa_confirm(update, context)
    elif data == "menu_support":
        await support(update, context)
    elif data == "menu_faq":
        await faq(update, context)
    elif data == "menu_about":
        await about(update, context)
    elif data == "menu_news":
        await news(update, context)

if __name__ == "__main__":
    # Start Flask in a separate thread
    Thread(target=run_flask).start()

    # Start bot
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Valture bot started")
    application.run_polling()
