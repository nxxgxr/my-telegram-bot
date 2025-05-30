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
            "🎉 *Purchase Successful!*\n\n"
            "Your license key:\n"
            f"`{license_key}`\n\n"
            "Keep it safe and enjoy Valture! 🚀"
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
            "❌ *Something went wrong!*\n\n"
            "We couldn’t issue your key. Please contact @s3pt1ck for help."
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

def create_crypto_invoice(amount, asset="TON", description="Valture License"):
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
    """Welcome message with a friendly introduction."""
    welcome_text = (
        "🎮 *Welcome to Valture!*\n\n"
        "Your ultimate gaming performance tool awaits! 🚀\n"
        "Choose an option below to get started:"
    )
    buttons = [("🏠 Main Menu", "menu_main")]
    await update.message.reply_text(welcome_text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display the main menu without a Back button."""
    query = update.callback_query
    await query.answer()
    buttons = [
        ("ℹ️ About Valture", "menu_about"),
        ("📰 News", "menu_news"),
        ("💳 Buy License", "menu_pay"),
        ("❓ FAQ", "menu_faq"),
        ("📞 Support", "menu_support"),
    ]
    await query.edit_message_text(
        "🏠 *Main Menu*\n\nSelect an option:",
        parse_mode="Markdown",
        reply_markup=get_keyboard(buttons)
    )

async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Improved About section with concise text and Back button."""
    query = update.callback_query
    await query.answer()
    text = (
        "✨ *Valture: Game Like a Pro*\n\n"
        "Valture boosts your gaming with:\n"
        "🚀 *20–30% FPS boost*\n"
        "🛡️ *Stable frame rates*\n"
        "⚡ *Lightning-fast response*\n"
        "🔧 *Optimized Windows settings*\n"
        "🖱️ *Precise mouse control*\n\n"
        "Ready to dominate? Get your license now!"
    )
    buttons = [("🔙 Back to Main Menu", "menu_main")]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Payment menu with updated price and Back button."""
    query = update.callback_query
    await query.answer()
    text = (
        "💳 *Buy a Valture License*\n\n"
        "Price: *4 TON* or *1000 RUB (~$10)*\n"
        "Choose your payment method:\n"
        "- *CryptoBot*: Pay 4 TON with cryptocurrency.\n"
        "- *YooKassa*: Pay with card or other methods.\n\n"
        "Your license key will be sent here after payment."
    )
    buttons = [
        ("💸 Pay with CryptoBot", "pay_crypto"),
        ("💳 Pay with YooKassa", "pay_yookassa"),
        ("🔙 Back to Main Menu", "menu_main")
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def pay_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """CryptoBot payment with confirmation prompt and Back button."""
    query = update.callback_query
    await query.answer()
    text = (
        "💸 *Confirm CryptoBot Payment*\n\n"
        "You’re about to pay *4 TON* for a Valture license.\n"
        "Proceed with payment?"
    )
    buttons = [
        ("✅ Confirm Payment", "pay_crypto_confirm"),
        ("🔙 Back to Payment Options", "menu_pay")
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def pay_crypto_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle CryptoBot payment after confirmation."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.full_name

    try:
        logger.debug(f"Creating CryptoBot invoice for user: {username} (ID: {user_id})")
        invoice, error = create_crypto_invoice(amount=4.0, asset="TON", description="Valture License")
        if not invoice:
            error_msg = (
                "❌ *Oops, something went wrong!*\n\n"
                f"Couldn’t create invoice: {error or 'Unknown error'}.\n"
                "Please try again or contact @s3pt1ck."
            )
            buttons = [
                ("🔄 Try Again", "pay_crypto"),
                ("🔙 Back to Payment Options", "menu_pay")
            ]
            logger.error(f"Error in pay_crypto: {error}")
            await query.edit_message_text(error_msg, parse_mode="Markdown", reply_markup=get_keyboard(buttons))
            return

        invoice_id = invoice["invoice_id"]
        pay_url = invoice["pay_url"]

        context.user_data["payment_type"] = "crypto"
        context.user_data["invoice_id"] = invoice_id
        context.user_data["username"] = username
        logger.info(f"CryptoBot invoice created: invoice_id={invoice_id}, pay_url={pay_url}")

        text = (
            "💸 *Pay with CryptoBot*\n\n"
            "Click below to pay *4 TON*:\n"
            f"[Pay via CryptoBot]({pay_url})\n\n"
            "After payment, confirm below to receive your key."
        )
        buttons = [
            ("✅ Confirm Payment", "pay_verify"),
            ("🔙 Back to Payment Options", "menu_pay")
        ]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons), disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Critical error in pay_crypto_confirm: {e}", exc_info=True)
        error_msg = (
            "❌ *Something broke!*\n\n"
            "We couldn’t process your request. Please try again or contact @s3pt1ck."
        )
        buttons = [
            ("🔄 Try Again", "pay_crypto"),
            ("🔙 Back to Payment Options", "menu_pay")
        ]
        await query.edit_message_text(error_msg, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def pay_yookassa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """YooKassa payment with confirmation prompt."""
    query = update.callback_query
    await query.answer()
    text = (
        "💳 *Confirm YooKassa Payment*\n\n"
        "You’re about to pay *1000 RUB* for a Valture license.\n"
        "Proceed with payment?"
    )
    buttons = [
        ("✅ Confirm Payment", "pay_yookassa_confirm"),
        ("🔙 Back to Payment Options", "menu_pay")
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def pay_yookassa_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle YooKassa payment after confirmation."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.full_name

    try:
        logger.debug(f"Creating YooKassa payment for user: {username} (ID: {user_id})")
        payment, error = create_yookassa_payment(
            amount=1000.0,
            description="Valture License",
            user_id=user_id,
            username=username
        )
        if not payment:
            error_msg = (
                "❌ *Oops, something went wrong!*\n\n"
                f"Couldn’t create payment: {error or 'Unknown error'}.\n"
                "Please try again or contact @s3pt1ck."
            )
            buttons = [
                ("🔄 Try Again", "pay_yookassa"),
                ("🔙 Back to Payment Options", "menu_pay")
            ]
            logger.error(f"Error in pay_yookassa: {error}")
            await query.edit_message_text(error_msg, parse_mode="Markdown", reply_markup=get_keyboard(buttons))
            return

        payment_id = payment.id
        confirmation_url = payment.confirmation.confirmation_url

        context.user_data["payment_type"] = "yookassa"
        context.user_data["payment_id"] = payment_id
        context.user_data["username"] = username
        logger.info(f"YooKassa payment created: payment_id={payment_id}, confirmation_url={confirmation_url}")

        text = (
            "💳 *Pay with YooKassa*\n\n"
            "Click below to pay *1000 RUB*:\n"
            f"[Pay via YooKassa]({confirmation_url})\n\n"
            "Your key will be sent automatically after payment."
        )
        buttons = [("🔙 Back to Payment Options", "menu_pay")]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons), disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Critical error in pay_yookassa_confirm: {e}", exc_info=True)
        error_msg = (
            "❌ *Something broke!*\n\n"
            "We couldn’t process your request. Please try again or contact @s3pt1ck."
        )
        buttons = [
            ("🔄 Try Again", "pay_yookassa"),
            ("🔙 Back to Payment Options", "menu_pay")
        ]
        await query.edit_message_text(error_msg, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def pay_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Improved payment verification with Back button."""
    query = update.callback_query
    await query.answer()

    payment_type = context.user_data.get("payment_type")
    if payment_type != "crypto":
        text = (
            "❌ *Whoops!*\n\n"
            "This is for CryptoBot payments only. YooKassa payments are confirmed automatically."
        )
        buttons = [("🔙 Back to Payment Options", "menu_pay")]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))
        return

    invoice_id = context.user_data.get("invoice_id")
    username = context.user_data.get("username")

    if not invoice_id or not username:
        logger.error(f"Payment data missing: invoice_id={invoice_id}, username={username}")
        text = (
            "❌ *Something’s missing!*\n\n"
            "We couldn’t find your payment details. Try again or contact @s3pt1ck."
        )
        buttons = [
            ("🔄 Try Again", "pay_crypto"),
            ("🔙 Back to Payment Options", "menu_pay")
        ]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))
        return

    try:
        text = "⏳ *Checking your payment...*\n\nPlease wait a moment."
        buttons = [("🔙 Back to Payment Options", "menu_pay")]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

        status = check_invoice_status(invoice_id)
        if status == "paid":
            license_key = generate_license()
            append_license_to_sheet(license_key, username)
            text = (
                "🎉 *Purchase Successful!*\n\n"
                "Your license key:\n"
                f"`{license_key}`\n\n"
                "Keep it safe and enjoy Valture! 🚀"
            )
            buttons = [("🏠 Back to Main Menu", "menu_main")]
            logger.info(f"CryptoBot payment confirmed, key issued: {license_key} for {username}")
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))
            context.user_data.clear()
        else:
            logger.warning(f"CryptoBot payment not confirmed for invoice_id={invoice_id}, status: {status}")
            text = (
                "⏳ *Payment Not Confirmed Yet*\n\n"
                "Please complete the payment or try again. Contact @s3pt1ck if you need help."
            )
            buttons = [
                ("🔄 Check Again", "pay_verify"),
                ("🔙 Back to Payment Options", "menu_pay")
            ]
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))
    except Exception as e:
        logger.error(f"Error checking CryptoBot payment: {e}", exc_info=True)
        text = (
            "❌ *Something went wrong!*\n\n"
            "We couldn’t verify your payment. Try again or contact @s3pt1ck."
        )
        buttons = [
            ("🔄 Try Again", "pay_verify"),
            ("🔙 Back to Payment Options", "menu_pay")
        ]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Support menu with Back button."""
    query = update.callback_query
    await query.answer()
    text = (
        "📞 *Need Help?*\n\n"
        "Reach out to our support team:\n"
        "👉 *@s3pt1ck*\n\n"
        "We’ll get back to you ASAP! 😊"
    )
    buttons = [("🔙 Back to Main Menu", "menu_main")]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def faq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """FAQ with concise answers and Back button."""
    query = update.callback_query
    await query.answer()
    text = (
        "❓ *FAQ*\n\n"
        "🔹 *How do I get a license?*\n"
        "Go to 'Buy License' and choose a payment method.\n\n"
        "🔹 *What if my key doesn’t work?*\n"
        "Contact @s3pt1ck for quick help.\n\n"
        "🔹 *Can I use the key on multiple devices?*\n"
        "No, each key is for one device only."
    )
    buttons = [("🔙 Back to Main Menu", "menu_main")]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """News section with Back button."""
    query = update.callback_query
    await query.answer()
    text = (
        "📰 *Valture News*\n\n"
        "Stay tuned for updates!\n"
        "No new announcements yet. 📅"
    )
    buttons = [("🔙 Back to Main Menu", "menu_main")]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Updated button handler with new callback."""
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
