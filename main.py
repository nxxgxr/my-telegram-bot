import os
import logging
import secrets
import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, timezone, timedelta
from threading import Thread
from flask import Flask, jsonify

# --- Settings ---

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CRYPTOBOT_API_TOKEN = os.environ.get("CRYPTOBOT_API_TOKEN")
CREDS_FILE = os.environ.get("CREDS_FILE")
SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME")
GOOGLE_CREDS_JSON_BASE64 = os.environ.get("GOOGLE_CREDS_JSON_BASE64")
SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# CryptoBot API endpoint
CRYPTO_BOT_API = "https://pay.crypt.bot/api"

# --- Logging ---

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.DEBUG
)
logger = logging.getLogger(__name__)

# --- Flask for keep-alive ---

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

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# --- Google Sheets Setup ---

sheet_cache = None

def setup_google_creds():
    """Decode base64 Google credentials and create a temporary file."""
    logger.debug("Checking Google credentials...")
    if GOOGLE_CREDS_JSON_BASE64:
        try:
            creds_json = base64.b64decode(GOOGLE_CREDS_JSON_BASE64).decode("utf-8")
            with open(CREDS_FILE, "w") as f:
                f.write(creds_json)
            logger.info("Google credentials successfully decoded and saved to temporary file")
        except Exception as e:
            logger.error(f"Error decoding Google credentials: {e}")
            raise
    elif not os.path.exists(CREDS_FILE):
        logger.error("Google credentials file not found, and GOOGLE_CREDS_JSON_BASE64 is not set")
        raise FileNotFoundError("Google credentials file not found, and GOOGLE_CREDS_JSON_BASE64 is not set")
    else:
        logger.info("Using existing Google credentials file")

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

def append_license_to_sheet(license_key, username):
    """Append license key to Google Sheets."""
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

def generate_license(length=32):
    """Generate a secure license key."""
    try:
        key = ''.join(secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') for _ in range(length))
        logger.info(f"Generated key: {key}")
        return key
    except Exception as e:
        logger.error(f"Error generating key: {e}")
        raise

# --- CryptoBot Payment Functions ---

def get_pay_link(amount):
    """Create a payment link via CryptoBot."""
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_API_TOKEN}
    data = {
        "asset": "TON",
        "amount": str(amount),
        "description": "Valture License"
    }
    try:
        response = requests.post(f"{CRYPTO_BOT_API}/createInvoice", headers=headers, json=data, timeout=10)
        if response.ok:
            response_data = response.json()
            return response_data['result']['pay_url'], response_data['result']['invoice_id']
        logger.error(f"Failed to create invoice: {response.text}")
        return None, None
    except Exception as e:
        logger.error(f"Error creating invoice: {e}")
        return None, None

def check_payment_status(invoice_id):
    """Check payment status via CryptoBot."""
    headers = {
        "Crypto-Pay-API-Token": CRYPTOBOT_API_TOKEN,
        "Content-Type": "application/json"
    }
    try:
        response = requests.post(f"{CRYPTO_BOT_API}/getInvoices", headers=headers, json={}, timeout=10)
        if response.ok:
            return response.json()
        logger.error(f"Error checking payment status: {response.status_code}, {response.text}")
        return None
    except Exception as e:
        logger.error(f"Error checking payment status: {e}")
        return None

# --- Telegram Bot Logic ---

def get_keyboard(buttons):
    """Create an inline keyboard."""
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
    """Main menu without 'Back' button."""
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
    """About section with 'Back' button."""
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
    """Payment menu with updated price and 'Back' button."""
    query = update.callback_query
    await query.answer()
    text = (
        "💳 *Покупка лицензии Valture*\n\n"
        "Цена: *4 TON*\n"
        "Оплата через CryptoBot.\n\n"
        "После оплаты вы получите документ и лицензионный ключ."
    )
    buttons = [
        ("💸 Оплатить через CryptoBot", "pay_crypto"),
        ("🔙 Назад в главное меню", "menu_main")
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def pay_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Initiate CryptoBot payment."""
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.full_name

    try:
        pay_link, invoice_id = get_pay_link('0.1')
        if pay_link and invoice_id:
            context.user_data["invoice_id"] = invoice_id
            context.user_data["username"] = username
            logger.info(f"CryptoBot invoice created: invoice_id={invoice_id}, pay_url={pay_link}")
            text = (
                "💸 *Оплатите через CryptoBot*\n\n"
                "Нажмите ниже для оплаты *4 TON*:\n"
                f"[Оплатить через CryptoBot]({pay_link})\n\n"
                "После оплаты нажмите 'Проверить оплату'."
            )
            buttons = [
                ("✅ Проверить оплату", f"check_payment_{invoice_id}"),
                ("🔙 Назад к способам оплаты", "menu_pay")
            ]
            await query.edit_message_text(
                text,
                parse_mode="Markdown",
                reply_markup=get_keyboard(buttons),
                disable_web_page_preview=True
            )
        else:
            error_msg = (
                "❌ *Не удалось создать счет на оплату!*\n\n"
                "Попробуйте снова или свяжитесь с @s3pt1ck."
            )
            buttons = [
                ("🔄 Попробовать снова", "pay_crypto"),
                ("🔙 Назад к способам оплаты", "menu_pay")
            ]
            await query.edit_message_text(error_msg, parse_mode="Markdown", reply_markup=get_keyboard(buttons))
    except Exception as e:
        logger.error(f"Error initiating CryptoBot payment: {e}", exc_info=True)
        error_msg = (
            "❌ *Что-то сломалось!*\n\n"
            "Не удалось обработать запрос. Попробуйте снова или свяжитесь с @s3pt1ck."
        )
        buttons = [
            ("🔄 Попробовать снова", "pay_crypto"),
            ("🔙 Назад к способам оплаты", "menu_pay")
        ]
        await query.edit_message_text(error_msg, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def check_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check CryptoBot payment status."""
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    invoice_id = query.data.split('check_payment_')[1]
    username = context.user_data.get("username")

    try:
        payment_status = check_payment_status(invoice_id)
        if payment_status and payment_status.get('ok'):
            if 'items' in payment_status['result']:
                invoice = next((inv for inv in payment_status['result']['items'] if str(inv['invoice_id']) == invoice_id), None)
                if invoice:
                    status = invoice['status']
                    if status == 'paid':
                        license_key = generate_license()
                        append_license_to_sheet(license_key, username)
                        text = (
                            "🎉 *Поздравляем с покупкой!*\n\n"
                            "Ваш лицензионный ключ:\n"
                            f"`{license_key}`\n\n"
                            "Сохраните его в надежном месте! 🚀"
                        )
                        await query.edit_message_text(
                            text,
                            parse_mode="Markdown",
                            reply_markup=get_keyboard([("🏠 Назад в главное меню", "menu_main")])
                        )
                        # Send document
                        try:
                            with open('qw.docx', 'rb') as document:
                                await context.bot.send_document(chat_id, document)
                            logger.info(f"Document sent to {username} (chat_id: {chat_id})")
                        except FileNotFoundError:
                            logger.error("Document 'qw.docx' not found")
                            await context.bot.send_message(
                                chat_id,
                                "❌ Не удалось отправить документ. Свяжитесь с @s3pt1ck."
                            )
                        context.user_data.clear()
                    else:
                        await query.edit_message_text(
                            "⏳ *Оплата еще не подтверждена*\n\n"
                            "Завершите оплату или попробуйте снова. Свяжитесь с @s3pt1ck, если нужна помощь.",
                            parse_mode="Markdown",
                            reply_markup=get_keyboard([
                                ("🔄 Проверить снова", f"check_payment_{invoice_id}"),
                                ("🔙 Назад к способам оплаты", "menu_pay")
                            ])
                        )
                else:
                    await query.answer("Счет не найден.", show_alert=True)
            else:
                logger.error(f"API response missing 'items': {payment_status}")
                await query.answer("Ошибка при получении статуса оплаты.", show_alert=True)
        else:
            logger.error(f"Error checking payment status: {payment_status}")
            await query.answer("Ошибка при получении статуса оплаты.", show_alert=True)
    except Exception as e:
        logger.error(f"Error verifying payment: {e}", exc_info=True)
        text = (
            "❌ *Что-то пошло не так!*\n\n"
            "Не удалось проверить оплату. Попробуйте снова или свяжитесь с @s3pt1ck."
        )
        buttons = [
            ("🔄 Проверить снова", f"check_payment_{invoice_id}"),
            ("🔙 Назад к способам оплаты", "menu_pay")
        ]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Support menu with 'Back' button."""
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
    """FAQ with brief answers and 'Back' button."""
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
    """News section with 'Back' button."""
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
    # Start Flask in a separate thread
    Thread(target=run_flask).start()

    # Start the bot
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Valture bot started")
    application.run_polling()
