import os
import logging
import secrets
import requests
import base64
import json
from datetime import datetime, timezone, timedelta
from threading import Thread

from flask import Flask
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

import gspread
from google.oauth2.service_account import Credentials

# --- Настройки ---

BOT_TOKEN = os.environ.get("BOT_TOKEN") or "7941872387:AAGZayILmna-qHHyQy5V50wDGylo3yFCZ0A"
CRYPTOBOT_API_TOKEN = os.environ.get("CRYPTOBOT_API_TOKEN")  # Updated to match your variable
CREDS_FILE = os.environ.get("CREDS_FILE") or "valture-license-bot-account.json"
SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME") or "valture"
GOOGLE_CREDS_JSON_BASE64 = os.environ.get("GOOGLE_CREDS_JSON_BASE64")
SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# CryptoBot API endpoint
CRYPTO_BOT_API = "https://pay.crypt.bot/api"

# --- Логирование ---

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Flask для keep-alive ---

app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Valture бот работает!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# --- Обработка Google Credentials ---

def setup_google_creds():
    """Декодирование base64-креденшлов Google и создание временного файла."""
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
        raise FileNotFoundError("Файл Google credentials не найден, и GOOGLE_CREDS_JSON_BASE64 не задан")

# --- Логика Telegram бота ---

# Кэш для данных Google Sheets
sheet_cache = None

def get_sheet():
    """Получение кэшированного объекта Google Sheets."""
    global sheet_cache
    if sheet_cache is None:
        try:
            setup_google_creds()  # Декодируем и сохраняем креденшлы при необходимости
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

def create_crypto_invoice(amount, currency="USDT", description="Valture License"):
    """Создание инвойса через CryptoBot."""
    try:
        payload = {
            "amount": str(amount),
            "currency": currency,
            "description": description,
            "order_id": secrets.token_hex(16),  # Уникальный ID заказа
        }
        headers = {"Crypto-Pay-API-Token": CRYPTOBOT_API_TOKEN}
        response = requests.post(f"{CRYPTO_BOT_API}/createInvoice", json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        if data.get("ok"):
            return data["result"]
        else:
            logger.error(f"Ошибка создания инвойса: {data}")
            return None
    except Exception as e:
        logger.error(f"Ошибка при создании инвойса: {e}")
        return None

def check_invoice_status(invoice_id):
    """Проверка статуса инвойса."""
    try:
        headers = {"Crypto-Pay-API-Token": CRYPTOBOT_API_TOKEN}
        response = requests.get(f"{CRYPTO_BOT_API}/getInvoices?invoice_ids={invoice_id}", headers=headers)
        response.raise_for_status()
        data = response.json()
        if data.get("ok"):
            return data["result"]["items"][0]["status"]
        else:
            logger.error(f"Ошибка проверки статуса инвойса: {data}")
            return None
    except Exception as e:
        logger.error(f"Ошибка при проверке статуса инвойса: {e}")
        return None

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
        "Оплата принимается через CryptoBot в USDT.\n"
        "После успешной оплаты вы получите уникальный ключ прямо в чат.\n\n"
        "Готовы продолжить?"
    )
    buttons = [("✅ Оплатить", "pay_confirm"), ("🔙 Назад", "menu_main")]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def pay_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение оплаты и создание инвойса."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.full_name

    try:
        # Создаем инвойс на 10 USDT
        invoice = create_crypto_invoice(amount=10.0, currency="USDT", description="Valture License")
        if not invoice:
            await query.edit_message_text(
                "❌ *Ошибка*\n\nНе удалось создать инвойс. Попробуйте позже или обратитесь в поддержку (@s3pt1ck).",
                parse_mode="Markdown"
            )
            return

        invoice_id = invoice["invoice_id"]
        pay_url = invoice["pay_url"]

        # Сохраняем данные инвойса в context.user_data для последующей проверки
        context.user_data["invoice_id"] = invoice_id
        context.user_data["username"] = username

        text = (
            "💸 *Оплатите лицензию*\n\n"
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
        logger.error(f"Ошибка при создании инвойса: {e}")
        await query.edit_message_text(
            "❌ *Ошибка*\n\nНе удалось создать инвойс. Попробуйте позже или обратитесь в поддержку (@s3pt1ck).",
            parse_mode="Markdown"
        )

async def pay_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка статуса оплаты и выдача ключа."""
    query = update.callback_query
    await query.answer()
    invoice_id = context.user_data.get("invoice_id")
    username = context.user_data.get("username")

    if not invoice_id or not username:
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
            await query.edit_message_text(text, parse_mode="Markdown")
            # Очищаем данные после успешной оплаты
            context.user_data.clear()
        else:
            await query.edit_message_text(
                "⏳ *Оплата не подтверждена*\n\n"
                "Пожалуйста, завершите оплату или попробуйте снова. Если возникли проблемы, обратитесь в поддержку (@s3pt1ck).",
                parse_mode="Markdown",
                reply_markup=get_keyboard([("🔄 Проверить снова", "pay_verify"), ("🔙 Назад", "menu_main")])
            )
    except Exception as e:
        logger.error(f"Ошибка при проверке оплаты: {e}")
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
        "Перейдите в раздел 'Купить лицензию', оплатите через CryptoBot и получите ключ.\n\n"
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
    elif data == "pay_confirm":
        await pay_confirm(update, context)
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

    print("✅ Valture бот запущен и работает!")
    application.run_polling()
