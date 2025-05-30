import os
import logging
import secrets
import uuid
from datetime import datetime, timezone, timedelta
from threading import Thread

from flask import Flask

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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

# --- Конфигурация ЮKassa ---

Configuration.account_id = YOOKASSA_SHOP_ID
Configuration.secret_key = YOOKASSA_SECRET_KEY

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

# --- Работа с Google Sheets ---

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

def append_license_to_sheet(license_key, username):
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

# --- Генерация лицензионного ключа ---

def generate_license(length=32):
    try:
        key = ''.join(secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') for _ in range(length))
        logger.info(f"Сгенерирован ключ: {key}")
        return key
    except Exception as e:
        logger.error(f"Ошибка при генерации ключа: {e}")
        raise

# --- Создание платежа в ЮKassa ---

def create_payment(amount="1000.00", description="Покупка лицензии Valture") -> Payment:
    payment = Payment.create({
        "amount": {
            "value": amount,
            "currency": "RUB"
        },
        "confirmation": {
            "type": "redirect",
            "return_url": "https://t.me/valture_buy_bot"  # Сюда пользователь вернётся после оплаты
        },
        "capture": True,
        "description": description
    }, str(uuid.uuid4()))
    return payment

# --- Вспомогательные функции ---

def get_keyboard(buttons):
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=callback)] for text, callback in buttons])

# --- Обработчики команд и кнопок ---

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

    try:
        payment = create_payment()
        pay_url = payment.confirmation.confirmation_url

        buttons = [
            ("Оплатить", pay_url),
            ("Проверить оплату", "check_payment")
        ]

        # Кнопки — первая с url, вторая с callback_data
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Оплатить", url=pay_url)],
            [InlineKeyboardButton("Проверить оплату", callback_data="check_payment")]
        ])

        # Сохраняем payment_id для проверки
        context.user_data["payment_id"] = payment.id

        await query.edit_message_text(
            "💳 Нажмите кнопку ниже, чтобы перейти к оплате.\n\n"
            "После оплаты нажмите кнопку «Проверить оплату», чтобы получить лицензию.",
            reply_markup=keyboard
        )

    except Exception as e:
        logger.error(f"Ошибка при создании платежа: {e}")
        await query.edit_message_text("❌ Не удалось создать платеж. Попробуйте позже.")

async def check_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    payment_id = context.user_data.get("payment_id")
    if not payment_id:
        await query.edit_message_text("❌ Нет информации о платеже. Пожалуйста, начните процесс оплаты заново.")
        return

    try:
        payment = Payment.find_one(payment_id)
        if payment.status == "succeeded":
            license_key = generate_license()
            username = query.from_user.username or query.from_user.full_name
            append_license_to_sheet(license_key, username)

            await query.edit_message_text(
                f"🎉 Оплата прошла успешно!\n\nВаш лицензионный ключ:\n`{license_key}`\n\nСохраните его!",
                parse_mode="Markdown"
            )
            context.user_data.pop("payment_id", None)

        elif payment.status == "pending":
            await query.answer("Оплата еще не завершена, попробуйте чуть позже.", show_alert=True)

        else:
            await query.edit_message_text(f"Статус платежа: {payment.status}. Попробуйте оплатить заново.")
            context.user_data.pop("payment_id", None)

    except Exception as e:
        logger.error(f"Ошибка проверки оплаты: {e}")
        await query.edit_message_text("❌ Ошибка при проверке оплаты. Попробуйте позже.")

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "📞 *Поддержка Valture*\n\n"
        "Если у вас возникли вопросы или проблемы, свяжитесь с нами:\n"
        "Telegram: @valture_support\n"
        "Email: support@valture.com"
    )
    buttons = [("🔙 Назад", "menu_main")]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def faq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "❓ *FAQ*\n\n"
        "Q: Что такое Valture?\n"
        "A: Это оптимизатор для повышения производительности в играх.\n\n"
        "Q: Как получить лицензию?\n"
        "A: Оплатить через ЮKassa и получить ключ здесь.\n\n"
        "Q: Куда вводить ключ?\n"
        "A: В приложении Valture.\n\n"
        "Если не нашли ответ, пишите в поддержку."
    )
    buttons = [("🔙 Назад", "menu_main")]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "📰 *Новости Valture*\n\n"
        "- Новая версия с улучшенной оптимизацией.\n"
        "- Скидка 10% до конца месяца.\n"
        "- Скоро выйдет мобильное приложение.\n\n"
        "Следите за обновлениями!"
    )
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
    elif data == "check_payment":
        await check_payment(update, context)
    elif data == "menu_support":
        await support(update, context)
    elif data == "menu_faq":
        await faq(update, context)
    elif data == "menu_news":
        await news(update, context)
    else:
        await query.answer("Неизвестная команда.", show_alert=True)

# --- Запуск бота ---

def main():
    # Запускаем Flask в отдельном потоке (для keep-alive)
    Thread(target=run_flask).start()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Бот запущен")
    application.run_polling()

if __name__ == '__main__':
    main()
