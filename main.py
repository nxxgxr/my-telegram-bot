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

# --- Settings ---

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

# --- Logging ---

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.DEBUG
)
logger = logging.getLogger(__name__)

# --- Flask for keep-alive and webhooks ---

app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Valture bot is running!"

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
            "🎉 *Поздравляем с покупкой!* 🎮\n\n"
            "Ваш лицензионный ключ:\n"
            f"`{license_key}`\n\n"
            "Сохраните его в надежном месте! 🔐"
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
            "❌ *Ошибка* 😔\n\n"
            "Не удалось выдать ключ. Напишите в поддержку: @s3pt1ck"
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=error_text,
            parse_mode="Markdown"
        )

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# --- Google Credentials Handling ---

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
        raise FileNotFoundError("Google credentials file not found and GOOGLE_CREDS_JSON_BASE64 not set")
    else:
        logger.info("Using existing Google credentials file")

# --- Telegram Bot Logic ---

# Cache for Google Sheets data
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
            logger.info("Successfully connected to Google Sheets")
        except Exception as e:
            logger.error(f"Error connecting to Google Sheets: {e}")
            raise
    return sheet_cache

def generate_license(length=32):
    """Generate a secure license key."""
    try:
        key = ''.join(secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') for _ in range(length))
        logger.info(f"Generated key: {key}")
        return key
    except Exception as e:
        logger.error(f"Error generating key: {e}")
        raise

def append_license_to_sheet(license_key, username):
    """Append license to Google Sheets."""
    try:
        sheet = get_sheet()
        utc_plus_2 = timezone(timedelta(hours=2))
        now_utc_plus_2 = datetime.now(utc_plus_2)
        now_str = now_utc_plus_2.strftime("%Y-%m-%d %H:%M:%S")
        sheet.append_row([license_key, "", username, now_str])
        logger.info(f"License {license_key} added for {username}")
    except Exception as e:
        logger.error(f"Error appending license: {e}")
        raise

def create_crypto_invoice(amount, asset="USDT", description="Valture License"):
    """Create an invoice via CryptoBot."""
    logger.debug(f"Creating invoice: amount={amount}, asset={asset}, description={description}")
    if not CRYPTOBOT_API_TOKEN:
        logger.error("CRYPTOBOT_API_TOKEN not set in environment variables")
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
        logger.debug(f"Sending request to {CRYPTO_BOT_API}/createInvoice with payload: {payload}")
        
        response = requests.post(f"{CRYPTO_BOT_API}/createInvoice", json=payload, headers=headers, timeout=10)
        logger.debug(f"HTTP status: {response.status_code}, Response: {response.text}")
        
        response.raise_for_status()
        data = response.json()
        
        if data.get("ok"):
            logger.info(f"Invoice created: invoice_id={data['result']['invoice_id']}")
            return data["result"], None
        else:
            error_msg = data.get("error", "Unknown CryptoBot API error")
            logger.error(f"CryptoBot API error: {error_msg}")
            return None, f"API error: {error_msg}"
            
    except requests.exceptions.HTTPError as http_err:
        logger.error(f"HTTP error creating invoice: {http_err}, Response: {response.text}")
        if response.status_code == 401:
            return None, "Invalid CRYPTOBOT_API_TOKEN"
        elif response.status_code == 429:
            return None, "CryptoBot API rate limit exceeded"
        return None, f"HTTP error: {http_err}"
    except requests.exceptions.Timeout:
        logger.error("Timeout accessing CryptoBot API")
        return None, "CryptoBot API request timed out"
    except requests.exceptions.RequestException as req_err:
        logger.error(f"Network error creating invoice: {req_err}")
        return None, f"Network error: {req_err}"
    except Exception as e:
        logger.error(f"General error creating invoice: {e}")
        return None, f"General error: {e}"

def check_invoice_status(invoice_id):
    """Check CryptoBot invoice status."""
    logger.debug(f"Checking invoice status: invoice_id={invoice_id}")
    try:
        headers = {"Crypto-Pay-API-Token": CRYPTOBOT_API_TOKEN}
        response = requests.get(f"{CRYPTO_BOT_API}/getInvoices?invoice_ids={invoice_id}", headers=headers, timeout=10)
        logger.debug(f"HTTP status: {response.status_code}, Response: {response.text}")
        response.raise_for_status()
        data = response.json()
        if data.get("ok"):
            status = data["result"]["items"][0]["status"]
            logger.info(f"Invoice {invoice_id} status: {status}")
            return status
        else:
            logger.error(f"Error checking invoice status: {data.get('error', 'Unknown error')}")
            return None
    except requests.exceptions.HTTPError as http_err:
        logger.error(f"HTTP error checking invoice: {http_err}, Response: {response.text}")
        return None
    except requests.exceptions.RequestException as req_err:
        logger.error(f"Network error checking invoice: {req_err}")
        return None
    except Exception as e:
        logger.error(f"General error checking invoice: {e}")
        return None

def create_yookassa_payment(amount, description, user_id, username):
    """Create a payment via YooKassa."""
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
    """Create a keyboard with buttons."""
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=callback)] for text, callback in buttons])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    welcome_text = (
        "👋 *Welcome to Valture!* 🎮\n\n"
        "Your journey to *peak gaming performance* starts here! 🚀\n"
        "───\n"
        "Valture is a tool for gamers who demand *smoothness* and *stability*.\n"
        "Choose an action below:"
    )
    await update.message.reply_text(
        welcome_text,
        parse_mode="Markdown",
        reply_markup=get_keyboard([("🏠 Open Menu", "menu_main")])
    )

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display main menu."""
    query = update.callback_query
    await query.answer()
    text = (
        "🏠 *Main Menu* 🎮\n\n"
        "───\n"
        "Select a section:"
    )
    buttons = [
        ("🔍 About Valture", "menu_about"),
        ("📰 News", "menu_news"),
        ("💳 Buy License", "menu_pay"),
        ("❓ FAQ", "menu_faq"),
        ("📞 Support", "menu_support"),
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display app information."""
    query = update.callback_query
    await query.answer()
    text = (
        "🔍 *About Valture* 🎮\n\n"
        "───\n"
        "*Valture* is a *revolutionary tool* for gamers chasing *excellence*. 🚀\n"
        "We boost your system’s performance with:\n"
        "✅ *+20–30% FPS* for smooth gameplay\n"
        "✅ *Stable framerate* without lags\n"
        "✅ *Lightning-fast* mouse and system response\n"
        "✅ *Windows optimization* for gaming\n\n"
        "Ready for *victory*? 🏆"
    )
    buttons = [
        ("🔙 Back", "menu_main"),
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display news section."""
    query = update.callback_query
    await query.answer()
    text = (
        "📰 *Valture News* 📢\n\n"
        "───\n"
        "Find the *latest updates* and announcements here.\n"
        "_No news yet, but stay tuned!_ 😉\n"
        "Keep an eye out!"
    )
    buttons = [
        ("🔙 Back", "menu_main"),
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def faq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display FAQ."""
    query = update.callback_query
    await query.answer()
    text = (
        "❓ *FAQ* 💡\n\n"
        "───\n"
        "*Answers to common questions:*\n"
        "1️⃣ *How to get a license?*\n"
        "Go to 'Buy License' and pay via your preferred method.\n\n"
        "2️⃣ *Key not working?*\n"
        "Contact support (@s3pt1ck) — we’ll fix it! 😊\n\n"
        "3️⃣ *Key for multiple devices?*\n"
        "No, the key is tied to *one device*."
    )
    buttons = [
        ("🔙 Back", "menu_main"),
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display support section."""
    query = update.callback_query
    await query.answer()
    text = (
        "📞 *Valture Support* 🤝\n\n"
        "───\n"
        "Got questions? We’re *always here*! 💬\n"
        "Message us: 👉 *@s3pt1ck*\n"
        "We’ll reply *ASAP*! 🚀"
    )
    buttons = [
        ("🔙 Back", "menu_main"),
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display payment menu."""
    query = update.callback_query
    await query.answer()
    text = (
        "💳 *Buy Valture License* 💸\n\n"
        "───\n"
        "Price: *1000 ₽ (~10 USDT)*\n"
        "Choose a payment method:\n"
        "• *CryptoBot*: Pay in USDT (crypto)\n"
        "• *YooKassa*: Card, YooMoney, or other methods\n\n"
        "Your key will arrive in chat after payment! 🔑"
    )
    buttons = [
        ("💸 CryptoBot", "pay_crypto"),
        ("💳 YooKassa", "pay_yookassa"),
        ("🔙 Back", "menu_main")
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def pay_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle CryptoBot payment."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.full_name

    try:
        logger.debug(f"Creating CryptoBot invoice for user: {username} (ID: {user_id})")
        invoice, error = create_crypto_invoice(amount=10.0, asset="USDT", description="Valture License")
        if not invoice:
            error_msg = (
                "❌ *Error* 😔\n\n"
                f"Failed to create invoice: {error or 'Unknown error'}.\n"
                "Try again later or contact support: @s3pt1ck"
            )
            logger.error(f"Error in pay_crypto: {error}")
            await query.edit_message_text(error_msg, parse_mode="Markdown")
            return

        invoice_id = invoice["invoice_id"]
        pay_url = invoice["pay_url"]

        context.user_data["payment_type"] = "crypto"
        context.user_data["invoice_id"] = invoice_id
        context.user_data["username"] = username
        logger.info(f"CryptoBot invoice created: invoice_id={invoice_id}, pay_url={pay_url}")

        text = (
            "💸 *Pay via CryptoBot* ₿\n\n"
            "───\n"
            "Pay *10 USDT* using the link:\n"
            f"[Go to Payment]({pay_url})\n\n"
            "Click *Confirm* below after paying. ✅"
        )
        buttons = [
            ("✅ Confirm", "pay_verify"),
            ("🔙 Back", "menu_pay")
        ]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons), disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Critical error in pay_crypto: {e}", exc_info=True)
        await query.edit_message_text(
            "❌ *Error* 😔\n\n"
            "Failed to create invoice. Contact support: @s3pt1ck",
            parse_mode="Markdown"
        )

async def pay_yookassa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle YooKassa payment."""
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
                "❌ *Error* 😔\n\n"
                f"Failed to create payment: {error or 'Unknown error'}.\n"
                "Try again later or contact support: @s3pt1ck"
            )
            logger.error(f"Error in pay_yookassa: {error}")
            await query.edit_message_text(error_msg, parse_mode="Markdown")
            return

        payment_id = payment.id
        confirmation_url = payment.confirmation.confirmation_url

        context.user_data["payment_type"] = "yookassa"
        context.user_data["payment_id"] = payment_id
        context.user_data["username"] = username
        logger.info(f"YooKassa payment created: payment_id={payment_id}, confirmation_url={confirmation_url}")

        text = (
            "💳 *Pay via YooKassa* 🏧\n\n"
            "───\n"
            "Pay *1000 ₽* using the link:\n"
            f"[Go to Payment]({confirmation_url})\n\n"
            "Your key will arrive *automatically* after payment! 🔑"
        )
        buttons = [
            ("🔙 Back", "menu_pay")
        ]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons), disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Critical error in pay_yookassa: {e}", exc_info=True)
        await query.edit_message_text(
            "❌ *Error* 😔\n\n"
            "Failed to create payment. Contact support: @s3-corner1ck",
            parse_mode="Markdown"
        )

async def pay_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Verify CryptoBot payment status."""
    query = update.callback_query
    await query.answer()

    payment_type = context.user_data.get("payment_type")
    if payment_type != "crypto":
        text = (
            "❌ *Error* 😔\n\n"
            "This button is for verifying *CryptoBot* payments.\n"
            "For *YooKassa*, the key arrives automatically."
        )
        buttons = [
            ("🔙 Back", "menu_pay")
        ]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))
        return

    invoice_id = context.user_data.get("invoice_id")
    username = context.user_data.get("username")

    if not invoice_id or not username:
        logger.error(f"Payment data missing: invoice_id={invoice_id}, username={username}")
        text = (
            "❌ *Error* 😔\n\n"
            "Payment data not found.\n"
            "Try again or contact support: @s3pt1ck"
        )
        buttons = [
            ("🔙 Back", "menu_pay")
        ]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))
        return

    try:
        status = check_invoice_status(invoice_id)
        if status == "paid":
            license_key = generate_license()
            append_license_to_sheet(license_key, username)
            text = (
                "🎉 *Congratulations on your purchase!* 🎮\n\n"
                "Your license key:\n"
                f"`{license_key}`\n\n"
                "Keep it safe! 🔐"
            )
            buttons = [
                ("🔙 Back", "menu_main")
            ]
            logger.info(f"CryptoBot payment confirmed, key issued: {license_key} for {username}")
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))
            context.user_data.clear()
        else:
            logger.warning(f"CryptoBot payment not confirmed for invoice_id={invoice_id}, status: {status}")
            text = (
                "⏳ *Payment not confirmed* ⏰\n\n"
                "Complete the payment or try again.\n"
                "Issues? Contact: @s3pt1ck"
            )
            buttons = [
                ("🔄 Check Again", "pay_verify"),
                ("🔙 Back", "menu_pay")
            ]
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))
    except Exception as e:
        logger.error(f"Error verifying CryptoBot payment: {e}", exc_info=True)
        text = (
            "❌ *Error* 😔\n\n"
            "Failed to verify payment.\n"
            "Try later or contact: @s3pt1ck"
        )
        buttons = [
            ("🔙 Back", "menu_pay")
        ]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display support section."""
    query = update.callback_query
    await query.answer()
    text = (
        "📞 *Valture Support* 🤝\n\n"
        "───\n"
        "Got questions? We’re *always here*! 💬\n"
        "Message us: 👉 *@s3pt1ck*\n"
        "We’ll reply *ASAP*! 🚀"
    )
    buttons = [
        ("🔙 Back", "menu_main"),
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def faq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display FAQ."""
    query = update.callback_query
    await query.answer()
    text = (
        "❓ *FAQ* 💡\n\n"
        "───\n"
        "*Answers to common questions:*\n"
        "1️⃣ *How to get a license?*\n"
        "Go to 'Buy License' and pay via your preferred method.\n\n"
        "2️⃣ *Key not working?*"
        "Contact support (@s3pt1ck) — we’ll fix it! 😊\n\n"
        "3️⃣ *Key for multiple devices?*\n"
        "No, the key is tied to *one device*."
    )
    buttons = [
        ("🔙 Back", "menu_main")
    ]
    await query.edit_message_text(text, parse_mode="Markup", reply_markup=get_keyboard(buttons)))

async def news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display news."""
    query = update.callback_query
    await query.answer()
    text = (
        "📰 *Valture News* 📢\n\n"
        "───\n"
        "Find the *latest updates* and announcements here.\n"
        "_No news yet, but stay tuned!_ 😉\n"
        "Keep an eye out!"
    )
    buttons = [
        ("🔙 Back", "menu_main")
    ]
    await query.edit_message_text(text, parse_mode="Markup", reply_markup=get_keyboard(buttons))

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
    # Start Flask in a thread
    Thread(target=run_flask).start()

    # Initialize and run bot
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Valture bot started")
    application.run_polling()
