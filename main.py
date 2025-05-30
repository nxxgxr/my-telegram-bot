import os
import logging
import secrets
import time
import requests
from datetime import datetime, timezone, timedelta
from threading import Thread

from flask import Flask

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

import gspread
from google.oauth2.service_account import Credentials

# --- Настройки ---

# Все чувствительные данные теперь берутся из переменных окружения Railway
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CREDS_FILE = "valture-license-bot-account.json"
SPREADSHEET_NAME = "valture"
SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Настройки Crypto Bot
CRYPTO_BOT_TOKEN = os.environ.get("CRYPTO_BOT_TOKEN")
CRYPTO_BOT_API_URL = "https://pay.crypt.bot/api"
PAYMENT_AMOUNT = 1000  # Сумма в рублях
PAYMENT_CURRENCY = "RUB"
PAYMENT_EXPIRES_IN = 3600  # Время жизни платежа в секундах (1 час)

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

# --- Логика Telegram бота ---

# Кэш для данных Google Sheets
sheet_cache = None

# Кэш для активных платежей: {user_id: {invoice_id: str, license_key: str, paid: bool}}
active_payments = {}

def get_sheet():
    """Получение кэшированного объекта Google Sheets."""
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

def get_keyboard(buttons):
    """Создание клавиатуры с кнопками."""
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=callback)] for text, callback in buttons])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start."""
    welcome_text = (
        "👋 *Добро пожаловать в Valture777!*\n\n"
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
        "🛳️  Плавность управления: Улучшенная точность и четкость мыши для идеального контроля в любой ситуации.\n"
        "🖥️  Плавность картинки в играх: Наслаждайтесь четкой и плавной картинкой, которая погружает вас в игру.\n\n"
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
        "Стоимость: *1000 рублей*\n"
        "После оплаты вы получите уникальный ключ прямо в чат.\n\n"
        "Готовы продолжить?"
    )
    buttons = [("✅ Оплатить", "pay_confirm"), ("🔙 Назад", "menu_main")]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def create_crypto_invoice(user_id: int, username: str) -> dict:
    """Создание инвойса в Crypto Bot."""
    try:
        # Генерируем лицензионный ключ заранее
        license_key = generate_license()
        
        headers = {
            "Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN,
            "Content-Type": "application/json"
        }
        
        payload = {
            "amount": PAYMENT_AMOUNT,
            "currency": PAYMENT_CURRENCY,
            "expires_in": PAYMENT_EXPIRES_IN,
            "description": f"Лицензия Valture для @{username}",
            "hidden_message": f"После оплаты вы получите лицензионный ключ.",
            "payload": str(user_id)  # Для идентификации платежа
        }
        
        response = requests.post(
            f"{CRYPTO_BOT_API_URL}/createInvoice",
            headers=headers,
            json=payload
        )
        
        response.raise_for_status()
        data = response.json()
        
        if data.get("ok"):
            invoice = data["result"]
            
            # Сохраняем информацию о платеже
            active_payments[user_id] = {
                "invoice_id": invoice["invoice_id"],
                "license_key": license_key,
                "paid": False,
                "username": username
            }
            
            return invoice
        
        logger.error(f"Ошибка при создании инвойса: {data}")
        return None
        
    except Exception as e:
        logger.error(f"Ошибка при создании инвойса Crypto Bot: {e}")
        return None

async def check_payment_status(invoice_id: str) -> bool:
    """Проверка статуса платежа в Crypto Bot."""
    try:
        headers = {
            "Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN
        }
        
        response = requests.get(
            f"{CRYPTO_BOT_API_URL}/getInvoices?invoice_ids={invoice_id}",
            headers=headers
        )
        
        response.raise_for_status()
        data = response.json()
        
        if data.get("ok"):
            invoice = data["result"]["items"][0]
            return invoice.get("status") == "paid"
        
        return False
        
    except Exception as e:
        logger.error(f"Ошибка при проверке статуса платежа: {e}")
        return False

async def pay_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение оплаты и создание инвойса в Crypto Bot."""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.full_name
    
    try:
        # Создаем инвойс в Crypto Bot
        invoice = await create_crypto_invoice(user_id, username)
        
        if not invoice:
            await query.edit_message_text(
                "❌ *Ошибка*\n\nНе удалось создать платеж. Попробуйте позже или обратитесь в поддержку.",
                parse_mode="Markdown"
            )
            return
        
        payment_url = invoice["pay_url"]
        invoice_id = invoice["invoice_id"]
        
        text = (
            "💳 *Оплата лицензии Valture*\n\n"
            f"Сумма: *{PAYMENT_AMOUNT} {PAYMENT_CURRENCY}*\n"
            f"Ссылка для оплаты: [Нажмите здесь]({payment_url})\n\n"
            "После успешной оплаты вы автоматически получите лицензионный ключ.\n"
            "Платеж будет действителен в течение 1 часа."
        )
        
        buttons = [
            ("🔄 Проверить оплату", f"check_payment_{invoice_id}"),
            ("🔙 Назад", "menu_pay")
        ]
        
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))
        
        # Запускаем проверку платежа в фоне
        context.application.create_task(check_payment_background(context.bot, user_id, invoice_id))
        
    except Exception as e:
        logger.error(f"Ошибка при создании платежа: {e}")
        await query.edit_message_text(
            "❌ *Ошибка*\n\nНе удалось создать платеж. Попробуйте позже или обратитесь в поддержку.",
            parse_mode="Markdown"
        )

async def check_payment_background(bot, user_id: int, invoice_id: str):
    """Фоновая проверка статуса платежа."""
    max_attempts = 30  # Максимальное количество проверок
    attempt = 0
    delay = 10  # Задержка между проверками в секундах
    
    while attempt < max_attempts:
        try:
            # Проверяем статус платежа
            is_paid = await check_payment_status(invoice_id)
            
            if is_paid:
                # Получаем информацию о платеже
                payment_info = active_payments.get(user_id)
                if payment_info and not payment_info["paid"]:
                    # Помечаем как оплаченный
                    payment_info["paid"] = True
                    
                    # Добавляем лицензию в Google Sheets
                    append_license_to_sheet(payment_info["license_key"], payment_info["username"])
                    
                    # Отправляем ключ пользователю
                    text = (
                        "🎉 *Оплата прошла успешно!*\n\n"
                        "Ваш лицензионный ключ:\n"
                        f"`{payment_info['license_key']}`\n\n"
                        "Сохраните его в надежном месте!"
                    )
                    
                    await bot.send_message(
                        chat_id=user_id,
                        text=text,
                        parse_mode="Markdown"
                    )
                    
                    # Удаляем информацию о платеже из кэша
                    active_payments.pop(user_id, None)
                    
                    return
                
            attempt += 1
            time.sleep(delay)
            
        except Exception as e:
            logger.error(f"Ошибка при фоновой проверке платежа: {e}")
            attempt += 1
            time.sleep(delay)
    
    # Если платеж не прошел
    logger.info(f"Платеж {invoice_id} не был подтвержден после {max_attempts} попыток")
    active_payments.pop(user_id, None)

async def check_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ручная проверка статуса платежа пользователем."""
    query = update.callback_query
    await query.answer()
    
    # Извлекаем invoice_id из callback_data
    callback_data = query.data
    invoice_id = callback_data.replace("check_payment_", "")
    
    user_id = query.from_user.id
    payment_info = active_payments.get(user_id)
    
    if not payment_info or payment_info["invoice_id"] != invoice_id:
        await query.edit_message_text(
            "❌ *Ошибка*\n\nНе удалось найти информацию о платеже. Попробуйте создать новый платеж.",
            parse_mode="Markdown",
            reply_markup=get_keyboard([("🔙 Назад", "menu_pay")])
        )
        return
    
    try:
        is_paid = await check_payment_status(invoice_id)
        
        if is_paid:
            # Помечаем как оплаченный
            payment_info["paid"] = True
            
            # Добавляем лицензию в Google Sheets
            append_license_to_sheet(payment_info["license_key"], payment_info["username"])
            
            text = (
                "🎉 *Оплата прошла успешно!*\n\n"
                "Ваш лицензионный ключ:\n"
                f"`{payment_info['license_key']}`\n\n"
                "Сохраните его в надежном месте!"
            )
            
            await query.edit_message_text(text, parse_mode="Markdown")
            
            # Удаляем информацию о платеже из кэша
            active_payments.pop(user_id, None)
        else:
            text = (
                "⏳ *Ожидание оплаты*\n\n"
                "Платеж еще не получен. Попробуйте проверить позже или перейдите по ссылке для оплаты.\n\n"
                "Если вы уже оплатили, подождите несколько минут - обработка платежа может занять время."
            )
            
            buttons = [
                ("🔄 Проверить снова", f"check_payment_{invoice_id}"),
                ("🔙 Назад", "menu_pay")
            ]
            
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))
            
    except Exception as e:
        logger.error(f"Ошибка при проверке платежа: {e}")
        await query.edit_message_text(
            "❌ *Ошибка*\n\nНе удалось проверить статус платежа. Попробуйте позже или обратитесь в поддержку.",
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
        "Перейдите в раздел 'Купить лицензию' и следуйте инструкциям.\n\n"
        "**2. Что делать, если ключ не работает?**\n"
        "Напишите в поддержку — мы поможем!\n\n"
        "**3. Можно ли использовать ключ на нескольких устройствах?**\n"
        "Нет, ключ привязан к одному устройству.\n\n"
        "**4. Какие методы оплаты доступны?**\n"
        "Мы принимаем платежи через криптовалюты (USDT, BTC, ETH и другие) с помощью Crypto Bot."
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

    print("✅ Valture бот запущен и работает!")
    application.run_polling()
