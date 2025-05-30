import os
import logging
import secrets
import json
from datetime import datetime, timezone, timedelta
from threading import Thread

from flask import Flask, request
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

import gspread
from google.oauth2.service_account import Credentials

# --- Настройки ---
BOT_TOKEN = os.environ.get("BOT_TOKEN") or "7941872387:AAGZayILmna-qHHyQy5V50wDGylo3yFCZ0A"
CREDS_FILE = "valture-license-bot-account.json"
SPREADSHEET_NAME = "valture"
CRYPTOBOT_BOT_USERNAME = "CryptoBot"  # Не меняй
PAYMENT_AMOUNT = "1000"
CRYPTO_CURRENCY = "TON"

SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Flask ---
app = Flask(__name__)
paid_users = {}

@app.route('/')
def home():
    return "✅ Valture бот работает!"

@app.route('/cryptobot/webhook', methods=["POST"])
def cryptobot_webhook():
    data = request.json
    if not data:
        return "No data", 400

    try:
        if data["status"] == "success":
            user_id = int(data["user"]["id"])
            paid_users[user_id] = True
            logger.info(f"Оплата прошла успешно от пользователя {user_id}")
    except Exception as e:
        logger.error(f"Ошибка обработки webhook: {e}")
        return "Error", 500

    return "OK", 200

# --- Google Sheets ---
sheet_cache = None

def get_sheet():
    global sheet_cache
    if sheet_cache is None:
        creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPE)
        client = gspread.authorize(creds)
        sheet_cache = client.open(SPREADSHEET_NAME).sheet1
    return sheet_cache

def generate_license(length=32):
    return ''.join(secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') for _ in range(length))

def append_license_to_sheet(license_key, username):
    sheet = get_sheet()
    utc_plus_2 = timezone(timedelta(hours=2))
    now_str = datetime.now(utc_plus_2).strftime("%Y-%m-%d %H:%M:%S")
    sheet.append_row([license_key, "", username, now_str])

def get_keyboard(buttons):
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=callback)] for text, callback in buttons])

# --- Telegram Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Добро пожаловать в Valture!*",
        parse_mode="Markdown",
        reply_markup=get_keyboard([("📋 Открыть меню", "menu_main")])
    )

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
    await query.edit_message_text("🏠 *Главное меню*", parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    payment_link = f"https://t.me/{CRYPTOBOT_BOT_USERNAME}?start=payment_{user_id}"

    text = (
        f"💳 *Лицензия Valture — {PAYMENT_AMOUNT}₽ ({CRYPTO_CURRENCY})*\n\n"
        "Нажмите кнопку ниже для оплаты. После успешной оплаты вы получите лицензионный ключ в этот чат.\n\n"
        f"[ОПЛАТИТЬ]({payment_link})"
    )

    buttons = [("🔄 Проверить оплату", "check_payment"), ("🔙 Назад", "menu_main")]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons), disable_web_page_preview=True)

async def check_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.full_name

    if paid_users.get(user_id):
        license_key = generate_license()
        append_license_to_sheet(license_key, username)
        await query.edit_message_text(
            f"🎉 *Оплата подтверждена!*\n\nВаш ключ:\n`{license_key}`",
            parse_mode="Markdown"
        )
    else:
        await query.edit_message_text(
            "⏳ *Оплата не найдена.*\n\nУбедитесь, что вы оплатили и подождите пару минут, затем нажмите \"Проверить оплату\".",
            parse_mode="Markdown",
            reply_markup=get_keyboard([("🔄 Проверить оплату", "check_payment"), ("🔙 Назад", "menu_main")])
        )

async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("ℹ️ О приложении Valture...", reply_markup=get_keyboard([("🔙 Назад", "menu_main")]))

async def faq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("❓ Часто задаваемые вопросы", reply_markup=get_keyboard([("🔙 Назад", "menu_main")]))

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("📞 Поддержка: @s3pt1ck", reply_markup=get_keyboard([("🔙 Назад", "menu_main")]))

async def news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("📰 Новости пока отсутствуют", reply_markup=get_keyboard([("🔙 Назад", "menu_main")]))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    handlers = {
        "menu_main": main_menu,
        "menu_pay": pay,
        "menu_about": about,
        "menu_news": news,
        "menu_support": support,
        "menu_faq": faq,
        "check_payment": check_payment
    }
    if data in handlers:
        await handlers[data](update, context)

# --- Запуск ---
if __name__ == "__main__":
    Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))).start()

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))

    print("✅ Бот Valture запущен")
    application.run_polling()
