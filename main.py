import os
import logging
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

BOT_TOKEN = os.environ.get("BOT_TOKEN")  # Telegram bot token from @BotFather
CRYPTOBOT_API_TOKEN = os.environ.get("CRYPTOBOT_API_TOKEN")  # CryptoBot token from @CryptoBot
YOOKASSA_SHOP_ID = os.environ.get("YOOKASSA_SHOP_ID")  # YooKassa shop ID
YOOKASSA_SECRET_KEY = os.environ.get("YOOKASSA_SECRET_KEY")  # YooKassa secret key
CREDS_FILE = os.environ.get("CREDS_FILE", "creds.json")  # Path to Google credentials file
SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME")  # Google Sheets spreadsheet name
GOOGLE_CREDS_JSON_BASE64 = os.environ.get("GOOGLE_CREDS_JSON_BASE64")  # Base64-encoded Google credentials
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
else:
    logging.warning("YooKassa credentials not set. YooKassa payments disabled.")

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
        logger.debug(f"CryptoBot API test response: status={response.status_code}, headers={response.headers}, content={response.content[:1000]}")
        return f"API Response: {response.json()}"
    except Exception as e:
        logger.error(f"Error testing CryptoBot API: {e}")
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
                    'chat_id': int(user_id)
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
            "Сохраните его в надежном месте! 🚀"
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
            "❌ *Произошла ошибка!*\n\n"
            "Не удалось выдать ключ. Обратитесь в поддержку: @s3pt1ck."
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
    """Decode base64 Google credentials and create a temporary file."""
    logger.debug("Checking Google credentials...")
    if GOOGLE_CREDS_JSON_BASE64:
        try:
            creds_json = base64.b64decode(GOOGLE_CREDS_JSON_BASE64).decode("utf-8")
            with open(CREDS_FILE, "w") as f:
                f.write(creds_json)
            logger.info("Google credentials decoded and saved to temporary file")
        except Exception as e:
            logger.error(f"Error decoding Google credentials: {e}")
            raise
    elif not os.path.exists(CREDS_FILE):
        logger.error("Google credentials file not found and GOOGLE_CREDS_JSON_BASE64 not set")
        raise FileNotFoundError("Google credentials file not found")
    else:
        logger.info("Using existing Google credentials file")

# --- Логика Telegram бота ---

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
            logger.error(f"Error connecting to Google Sheets: {e}")
            raise
    return sheet_cache

def generate_license():
    """Generate HWID key."""
    return str(uuid4())

def append_license_to_sheet(license_key, username):
    """Add license to Google Sheets."""
    try:
        sheet = get_sheet()
        utc_plus_2 = timezone(timedelta(hours=2))
        now_utc_plus_2 = datetime.now(utc_plus_2)
        now_str = now_utc_plus_2.strftime('%Y-%m-%d %H:%M:%S')
        sheet.append_row([license_key, "", username, now_str])
        logger.info(f"✅ License {license_key} added for {username}")
    except Exception as e:
        logger.error(f"Error adding license: {e}")
        raise

def create_crypto_invoice(amount, asset="TON", description="HWID Key"):
    """Create a CryptoBot invoice."""
    logger.debug(f"Creating invoice: amount={amount}, asset={asset}, description={description}")
    if not CRYPTOBOT_API_TOKEN:
        logger.error("CRYPTOBOT_API_TOKEN not set")
        return None, "CRYPTOBOT_API_TOKEN not found. Check settings."

    try:
        payload = {
            "amount": str(amount),
            "asset": asset,
            "description": description,
            "order_id": str(uuid4()),
        }
        headers = {
            "Crypto-Pay-API-Token": CRYPTOBOT_API_TOKEN,
            "Content-Type": "application/json"
        }
        logger.debug(f"Sending request to {CRYPTO_BOT_API}/createInvoice with payload={payload}")
        
        response = requests.post(f"{CRYPTO_BOT_API}/createInvoice", json=payload, headers=headers, timeout=10)
        logger.debug(f"HTTP status: {response.status_code}, Content-Type: {response.headers.get('Content-Type')}, Response: {response.content[:1000]}")
        
        if not response.headers.get('Content-Type', '').lower().startswith('application/json'):
            logger.error(f"Non-JSON response: {response.content[:1000]}")
            return None, "Non-JSON response from CryptoBot. Check CRYPTOBOT_API_TOKEN."

        data = response.json()
        
        if not data.get("ok"):
            error_msg = data.get("error", "Unknown CryptoBot error")
            logger.error(f"CryptoBot API error: {error_msg}")
            return None, f"API error: {error_msg}"
            
        logger.info(f"Invoice created: invoice_id={data['result']['invoice_id']}")
        return data["result"], None

    except requests.exceptions.HTTPError as http_err:
        logger.error(f"HTTP error creating invoice: {http_err}, Response: {response.content[:1000]}")
        if response.status_code == 401:
            return None, "Invalid CRYPTOBOT_API_TOKEN"
        elif response.status_code == 429:
            return None, "CryptoBot API rate limit exceeded"
        return None, f"HTTP error: {http_err}"
    except requests.exceptions.Timeout:
        logger.error("Timeout accessing CryptoBot API")
        return None, "Request timeout to CryptoBot API"
    except requests.exceptions.RequestException as req_err:
        logger.error(f"Network error creating invoice: {req_err}")
        return None, f"Network error: {req_err}"
    except ValueError as e:
        logger.error(f"JSON decode error: {e}, content: {response.content[:1000]}")
        return None, "Invalid response from CryptoBot"
    except Exception as e:
        logger.error(f"General error creating invoice: {e}")
        return None, f"Error: {e}"

def check_invoice_status(invoice_id):
    """Check CryptoBot invoice status."""
    logger.debug(f"Checking invoice status: invoice_id={invoice_id}")
    if not invoice_id:
        logger.error("Invoice ID missing")
        return None, "Invoice ID missing"
    
    if not CRYPTOBOT_API_TOKEN:
        logger.error("CRYPTOBOT_API_TOKEN not set")
        return None, "CRYPTOBOT_API_TOKEN not set. Check settings."

    # Validate token format
    if not isinstance(CRYPTOBOT_API_TOKEN, str) or len(CRYPTOBOT_API_TOKEN) < 24 or ":" not in CRYPTOBOT_API_TOKEN:
        logger.error("CRYPTOBOT_API_TOKEN appears invalid")
        return None, "Invalid CRYPTOBOT_API_TOKEN. Get a new token from @CryptoBot."

    # Test token validity
    try:
        headers = {"Crypto-Pay-API-Token": CRYPTOBOT_API_TOKEN}
        test_response = requests.get(f"{CRYPTO_BOT_API}/getMe", headers=headers, timeout=5)
        if test_response.status_code == 401:
            logger.error("CRYPTOBOT_API_TOKEN invalid")
            return None, "Invalid CRYPTOBOT_API_TOKEN. Check settings."
        test_response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"Token validation error: {e}")
        return None, f"Token validation error: {str(e)}. Check CRYPTOBOT_API_TOKEN."

    try:
        headers = {
            "Crypto-Pay-API-Token": CRYPTOBOT_API_TOKEN,
            "Content-Type": "application/json"
        }
        payload = {"invoice_ids": str(invoice_id)}
        response = requests.post(f"{CRYPTO_BOT_API}/getInvoices", json=payload, headers=headers, timeout=10)
        logger.debug(f"HTTP status: {response.status_code}, Headers: {response.headers}, Response: {response.content[:1000]}")

        # Check Content-Type
        content_type = response.headers.get('Content-Type', '').lower()
        if not content_type.startswith('application/json'):
            logger.error(f"Unexpected response type: {content_type}, content: {response.content[:1000]}")
            return None, f"Non-JSON response: {content_type}. Check CRYPTOBOT_API_TOKEN or contact @CryptoBot."

        # Try decoding response
        try:
            data = response.json()
        except ValueError as json_err:
            logger.error(f"JSON parsing error: {json_err}, content: {response.content[:1000]}")
            return None, f"JSON parsing error: {json_err}. Check CRYPTOBOT_API_TOKEN or contact @CryptoBot."

        if not data.get("ok"):
            error_msg = data.get("error", "Unknown CryptoBot error")
            logger.error(f"CryptoBot API error: {error_msg}")
            return None, f"API error: {error_msg}. Check invoice_id or contact @CryptoBot."

        items = data.get("result", {}).get("items", [])
        if not items:
            logger.error(f"Empty invoice list for invoice_id={invoice_id}")
            return None, "Invoice not found. Try again or create a new invoice."

        invoice = next((inv for inv in items if str(inv['invoice_id']) == invoice_id), None)
        if not invoice:
            logger.error(f"Invoice not found in response for invoice_id={invoice_id}")
            return None, "Invoice not found."

        status = invoice.get("status")
        if not status:
            logger.error(f"Invoice status missing for invoice_id={invoice_id}")
            return None, "Invoice status not found."

        logger.info(f"Invoice status {invoice_id}: {status}")
        return status, None

    except requests.exceptions.HTTPError as http_err:
        logger.error(f"HTTP error: {http_err}, Response: {response.content[:1000]}")
        if response.status_code == 401:
            return None, "Invalid CRYPTOBOT_API_TOKEN. Check settings."
        elif response.status_code == 404:
            return None, "Invoice not found on CryptoBot server."
        elif response.status_code == 429:
            return None, "CryptoBot API rate limit exceeded. Try later."
        return None, f"HTTP error: {http_err}. Contact @CryptoBot."
    except requests.exceptions.Timeout:
        logger.error("Timeout accessing CryptoBot API")
        return None, "Request timeout to CryptoBot API. Try again."
    except requests.exceptions.RequestException as req_err:
        logger.error(f"Network error checking invoice: {req_err}")
        return None, f"Network error: {req_err}. Check connection."
    except Exception as e:
        logger.error(f"General error checking invoice: {e}")
        return None, f"Error: {str(e)}. Contact @s3pt1ck."

def create_yookassa_payment(amount, description, user_id, username):
    """Create a YooKassa payment."""
    logger.debug(f"Creating YooKassa payment: amount={amount}, description={description}, user_id={user_id}")
    if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET_KEY:
        logger.error("YOOKASSA_SHOP_ID or YOOKASSA_SECRET_KEY not set")
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

        logger.info(f"YooKassa payment created: payment_id={payment.id}")
        return payment, None

    except Exception as e:
        logger.error(f"Error creating YooKassa payment: {e}")
        return None, f"YooKassa error: {str(e)}"

def get_keyboard(buttons):
    """Create keyboard with buttons."""
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=callback)] for text, callback in buttons])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message."""
    welcome_text = (
        "🎮 *Добро пожаловать в Valture!*\n\n"
        "Ваш лучший инструмент для игровой производительности! 🚀\n"
        "Выберите опцию ниже, чтобы начать:"
    )
    buttons = [("🏠 Главное меню", "menu_main")]
    await update.message.reply_text(welcome_text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main menu."""
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
    """About Valture."""
    query = update.callback_query
    await query.answer()
    text = (
        "✨ *Valture — Ваш путь к совершенству в играх*\n\n"
        "Valture — это передовой инструмент, созданный для геймеров, которые не готовы мириться с компромиссами. "
        "Наша миссия — вывести вашу игровую производительность на новый уровень, обеспечив максимальную плавность, "
        "стабильность и отзывчивость системы. С Valture вы получите конкурентное преимущество, о котором всегда мечтали.\n\n"
        "🔥 *Почему выбирают Valture?*\n"
        "🚀 Увеличение FPS на 20–30%\n"
        "🛡️ Стабильный фреймрейт\n"
        "💡 Молниеносная отзывчивость\n"
        "🔋 Оптимизация Windows\n"
        "🛳️ Плавность управления\n"
        "🖥️ Плавность картинки в играх\n\n"
        "_Создано для геймеров, которые ценят качество и стремятся к победе._"
    )
    buttons = [("🔙 Назад в главное меню", "menu_main")]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Payment menu."""
    query = update.callback_query
    await query.answer()
    text = (
        "💳 *Покупка лицензии Valture*\n\n"
        "Цена: *4 TON* или *1000 RUB*\n"
        "Выберите способ оплаты:\n"
        "- *CryptoBot*: Оплата через криптовалюту.\n"
        "- *YooKassa*: Оплата картой.\n\n"
        "Ключ будет отправлен в чат после оплаты."
    )
    buttons = [
        ("💸 Оплатить через CryptoBot", "pay_crypto"),
        ("💳 Оплатить через YooKassa", "pay_yookassa"),
        ("🔙 Назад в главное меню", "menu_main")
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def pay_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Initiate CryptoBot payment."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.full_name

    try:
        logger.debug(f"Creating CryptoBot invoice for user: {username} (ID: {user_id})")
        invoice, error = create_crypto_invoice(amount=4.0, asset="TON", description="HWID Key")
        if not invoice:
            error_msg = (
                "❌ *Ошибка при создании инвойса!*\n\n"
                f"Причина: {error or 'Неизвестная ошибка'}.\n"
                "Проверьте CRYPTOBOT_API_TOKEN или свяжитесь с @s3pt1ck."
            )
            buttons = [
                ("🔄 Попробовать снова", "pay_crypto"),
                ("🔙 Назад к способам оплаты", "menu_pay")
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
            "💸 *Оплатите через CryptoBot*\n\n"
            "Цена: *4 TON*\n"
            f"[Оплатить через CryptoBot]({pay_url})\n\n"
            "После оплаты нажмите 'Подтвердить оплату'."
        )
        buttons = [
            ("✅ Подтвердить оплату", "pay_crypto_confirm"),
            ("🔙 Назад к способам оплаты", "menu_pay")
        ]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons), disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Critical error in pay_crypto: {e}", exc_info=True)
        error_msg = (
            "❌ *Критическая ошибка!*\n\n"
            f"Причина: {str(e)}.\n"
            "Проверьте CRYPTOBOT_API_TOKEN или свяжитесь с @s3pt1ck."
        )
        buttons = [
            ("🔄 Попробовать снова", "pay_crypto"),
            ("🔙 Назад к способам оплаты", "menu_pay")
        ]
        await query.edit_message_text(error_msg, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def pay_crypto_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check payment and issue HWID key."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    username = context.user_data.get("username")

    payment_type = context.user_data.get("payment_type")
    if payment_type != "crypto":
        text = (
            "❌ *Ошибка!*\n\n"
            "Эта кнопка только для CryptoBot. YooKassa подтверждается автоматически."
        )
        buttons = [("🔙 Назад к способам оплаты", "menu_pay")]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))
        return

    invoice_id = context.user_data.get("invoice_id")
    if not invoice_id or not username:
        logger.error(f"Payment data missing: invoice_id={invoice_id}, username={username}")
        text = (
            "❌ *Ошибка!*\n\n"
            "Данные об оплате отсутствуют. Начните заново или свяжитесь с @s3pt1ck."
        )
        buttons = [
            ("🔄 Попробовать снова", "pay_crypto"),
            ("🔙 Назад к способам оплаты", "menu_pay")
        ]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))
        return

    try:
        text = "⏳ *Проверка оплаты...*\n\nПожалуйста, подождите."
        buttons = [("🔙 Назад к способам оплаты", "menu_pay")]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

        status, error = check_invoice_status(invoice_id)
        if error:
            logger.error(f"Error checking invoice: {error}")
            text = (
                f"❌ *Ошибка при проверке оплаты!*\n\n"
                f"Причина: {error}\n"
                "Проверьте CRYPTOBOT_API_TOKEN или свяжитесь с @s3pt1ck."
            )
            buttons = [
                ("🔄 Проверить снова", "pay_crypto_confirm"),
                ("🔙 Назад к способам оплаты", "menu_pay")
            ]
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))
            return

        if status == "paid":
            hwid = generate_license()
            append_license_to_sheet(hwid, username)
            text = (
                "🎉 *Поздравляем с покупкой!*\n\n"
                "Ваш HWID ключ:\n"
                f"`{hwid}`\n\n"
                "Сохраните его в надежном месте! 🚀"
            )
            buttons = [("🏠 Назад в главное меню", "menu_main")]
            logger.info(f"CryptoBot payment confirmed, key issued: {hwid} for {username}")
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))
            context.user_data.clear()
        else:
            logger.warning(f"CryptoBot payment not confirmed: invoice_id={invoice_id}, status={status}")
            text = (
                "⏳ *Оплата еще не подтверждена!*\n\n"
                f"Статус: {status}.\n"
                "Завершите оплату или попробуйте снова. Свяжитесь с @s3pt1ck."
            )
            buttons = [
                ("🔄 Проверить снова", "pay_crypto_confirm"),
                ("🔙 Назад к способам оплаты", "menu_pay")
            ]
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))
    except Exception as e:
        logger.error(f"Critical error in pay_crypto_confirm: {e}", exc_info=True)
        text = (
            f"❌ *Критическая ошибка!*\n\n"
            f"Причина: {str(e)}.\n"
            "Проверьте CRYPTOBOT_API_TOKEN или свяжитесь с @s3pt1ck."
        )
        buttons = [
            ("🔄 Проверить снова", "pay_crypto_confirm"),
            ("🔙 Назад к способам оплаты", "menu_pay")
        ]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def pay_yookassa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm YooKassa payment."""
    query = update.callback_query
    await query.answer()
    text = (
        "💳 *Подтверждение оплаты YooKassa*\n\n"
        "Вы собираетесь оплатить *1000 RUB* за лицензию Valture.\n"
        "Продолжить оплату?"
    )
    buttons = [
        ("✅ Подтвердить оплату", "pay_yookassa_confirm"),
        ("🔙 Назад к способам оплаты", "menu_pay")
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def pay_yookassa_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process YooKassa payment."""
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
                "❌ *Ошибка!*\n\n"
                f"Не удалось создать платеж: {error or 'Неизвестная ошибка'}.\n"
                "Проверьте YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY или свяжитесь с @s3pt1ck."
            )
            buttons = [
                ("🔄 Попробовать снова", "pay_yookassa"),
                ("🔙 Назад к способам оплаты", "menu_pay")
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
            "💳 *Оплатите через YooKassa*\n\n"
            "Нажмите ниже для оплаты *1000 RUB*:\n"
            f"[Оплатить через YooKassa]({confirmation_url})\n\n"
            "Ключ будет отправлен автоматически."
        )
        buttons = [("🔙 Назад к способам оплаты", "menu_pay")]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons), disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Critical error in pay_yookassa_confirm: {e}", exc_info=True)
        error_msg = (
            "❌ *Ошибка!*\n\n"
            f"Ошибка: {str(e)}.\n"
            "Проверьте YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY или свяжитесь с @s3pt1ck."
        )
        buttons = [
            ("🔄 Попробовать снова", "pay_yookassa"),
            ("🔙 Назад к способам оплаты", "menu_pay")
        ]
        await query.edit_message_text(error_msg, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def pay_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Verify payment."""
    query = update.callback_query
    await query.answer()

    payment_type = context.user_data.get("payment_type")
    if payment_type != "crypto":
        text = (
            "❌ *Ошибка!*\n\n"
            "Эта кнопка только для CryptoBot. YooKassa подтверждается автоматически."
        )
        buttons = [("🔙 Назад к способам оплаты", "menu_pay")]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))
        return

    invoice_id = context.user_data.get("invoice_id")
    username = context.user_data.get("username")

    if not invoice_id or not username:
        logger.error(f"Payment data missing: invoice_id={invoice_id}, username={username}")
        text = (
            "❌ *Ошибка!*\n\n"
            "Данные об оплате отсутствуют. Начните заново или свяжитесь с @s3pt1ck."
        )
        buttons = [
            ("🔄 Попробовать снова", "pay_crypto"),
            ("🔙 Назад к способам оплаты", "menu_pay")
        ]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))
        return

    try:
        text = "⏳ *Проверка оплаты...*\n\nПожалуйста, подождите."
        buttons = [("🔙 Назад к способам оплаты", "menu_pay")]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

        status, error = check_invoice_status(invoice_id)
        if error:
            logger.error(f"Error checking invoice: {error}")
            text = (
                f"❌ *Ошибка!*\n\n"
                f"Ошибка: {error}.\n"
                "Проверьте CRYPTOBOT_API_TOKEN или свяжитесь с @s3pt1ck."
            )
            buttons = [
                ("🔄 Проверить снова", "pay_verify"),
                ("🔙 Назад к способам оплаты", "menu_pay")
            ]
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))
            return

        if status == "paid":
            license_key = generate_license()
            append_license_to_sheet(license_key, username)
            text = (
                "🎉 *Поздравляем с покупкой!*\n\n"
                "Ваш HWID ключ:\n"
                f"`{license_key}`\n\n"
                "Сохраните его в надежном месте! 🚀"
            )
            buttons = [("🏠 Назад в главное меню", "menu_main")]
            logger.info(f"CryptoBot payment confirmed, key issued: {license_key} for {username}")
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))
            context.user_data.clear()
        else:
            logger.warning(f"CryptoBot payment not confirmed: invoice_id={invoice_id}, status={status}")
            text = (
                "⏳ *Оплата еще не подтверждена*\n\n"
                f"Статус: {status}. Завершите оплату или попробуйте снова."
            )
            buttons = [
                ("🔄 Проверить снова", "pay_verify"),
                ("🔙 Назад к способам оплаты", "menu_pay")
            ]
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))
    except Exception as e:
        logger.error(f"Error verifying CryptoBot payment: {e}", exc_info=True)
        text = (
            f"❌ *Ошибка!*\n\n"
            f"Ошибка: {str(e)}.\n"
            "Проверьте CRYPTOBOT_API_TOKEN или свяжитесь с @s3pt1ck."
        )
        buttons = [
            ("🔄 Проверить снова", "pay_verify"),
            ("🔙 Назад к способам оплаты", "menu_pay")
        ]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Support menu."""
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
    """FAQ section."""
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
