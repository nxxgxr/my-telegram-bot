import os
import logging
import secrets
import uuid
import requests
from datetime import datetime, timezone, timedelta
from threading import Thread
from flask import Flask, request, jsonify

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

import gspread
from google.oauth2.service_account import Credentials
from yookassa import Configuration, Payment

# --- Настройки ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
YOOKASSA_SHOP_ID = os.environ.get("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.environ.get("YOOKASSA_SECRET_KEY")
CREDS_FILE = "valture-license-bot-account.json"
SPREADSHEET_NAME = "valture"
SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Конфигурация ЮKassa
Configuration.account_id = YOOKASSA_SHOP_ID
Configuration.secret_key = YOOKASSA_SECRET_KEY

# --- Логирование ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Flask для вебхука ---
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Valture бот работает!"

@app.route('/yookassa-webhook', methods=['POST'])
def yookassa_webhook():
    event_json = request.json
    logger.info(f"Получен вебхук: {event_json}")
    
    # Проверка типа события
    if event_json['event'] == 'payment.succeeded':
        payment_id = event_json['object']['id']
        metadata = event_json['object'].get('metadata', {})
        
        chat_id = metadata.get('chat_id')
        username = metadata.get('username')
        
        if not chat_id or not username:
            logger.error("Отсутствуют метаданные в платеже")
            return jsonify({'status': 'error', 'message': 'Missing metadata'}), 400
        
        logger.info(f"Обработка успешного платежа {payment_id} для {username}")
        
        try:
            # Генерируем лицензионный ключ
            license_key = generate_license()
            
            # Добавляем в таблицу
            append_license_to_sheet(license_key, username, payment_id)
            
            # Отправляем ключ пользователю
            send_telegram_message(
                chat_id,
                f"🎉 *Оплата прошла успешно!*\n\n"
                f"Ваш лицензионный ключ:\n"
                f"`{license_key}`\n\n"
                "Сохраните его в надежном месте!"
            )
            
            return jsonify({'status': 'success'}), 200
            
        except Exception as e:
            logger.error(f"Ошибка обработки вебхука: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500
    
    return jsonify({'status': 'ignored'}), 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# --- Вспомогательные функции ---
def send_telegram_message(chat_id: int, text: str):
    """Отправка сообщения через Telegram API"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload)
        if response.status_code != 200:
            logger.error(f"Ошибка отправки сообщения: {response.status_code} - {response.text}")
    except Exception as e:
        logger.error(f"Ошибка при отправке сообщения: {e}")

def generate_license(length=32):
    """Генерация безопасного лицензионного ключа."""
    try:
        key = ''.join(secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') for _ in range(length))
        logger.info(f"Сгенерирован ключ: {key}")
        return key
    except Exception as e:
        logger.error(f"Ошибка при генерации ключа: {e}")
        raise

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

def append_license_to_sheet(license_key: str, username: str, payment_id: str):
    """Добавление лицензии в Google Sheets с payment_id"""
    try:
        sheet = get_sheet()
        utc_plus_2 = timezone(timedelta(hours=2))
        now_utc_plus_2 = datetime.now(utc_plus_2)
        now_str = now_utc_plus_2.strftime("%Y-%m-%d %H:%M:%S")
        
        # Проверяем заголовки столбцов
        headers = sheet.row_values(1)
        if "Payment ID" not in headers:
            sheet.insert_cols([["Payment ID"]], 5)  # Добавляем столбец Payment ID
        
        # Добавляем новую запись
        sheet.append_row([license_key, "", username, now_str, payment_id])
        logger.info(f"Лицензия {license_key} добавлена для {username} (Payment ID: {payment_id})")
    except Exception as e:
        logger.error(f"Ошибка при добавлении лицензии: {e}")
        raise

def get_keyboard(buttons):
    """Создание клавиатуры с кнопками."""
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=callback)] for text, callback in buttons])

# --- Обработчики Telegram ---
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

async def pay_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Создание платежа в ЮKassa"""
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    username = user.username or user.full_name
    chat_id = user.id
    
    try:
        # Создаем платеж
        payment = Payment.create({
            "amount": {
                "value": "1000.00",
                "currency": "RUB"
            },
            "confirmation": {
                "type": "redirect",
                "return_url": "https://t.me/valture_bot"
            },
            "capture": True,
            "description": f"Лицензия Valture для {username}",
            "metadata": {
                "chat_id": chat_id,
                "username": username
            }
        }, str(uuid.uuid4()))
        
        # Сохраняем ID платежа в контексте пользователя
        context.user_data['payment_id'] = payment.id
        
        # Отправляем ссылку пользователю
        text = (
            "➡️ *Перейдите по ссылке для оплаты:*\n\n"
            f"[Оплатить 1000 рублей]({payment.confirmation.confirmation_url})\n\n"
            "После успешной оплаты бот пришлет вам лицензионный ключ в этот чат."
        )
        buttons = [
            ("🔄 Проверить оплату", "check_payment"),
            ("🔙 В меню", "menu_main")
        ]
        await query.edit_message_text(
            text, 
            parse_mode="Markdown", 
            reply_markup=get_keyboard(buttons),
            disable_web_page_preview=True
        )
        
    except Exception as e:
        logger.error(f"Ошибка создания платежа: {e}")
        await query.edit_message_text(
            "❌ *Ошибка!*\nНе удалось создать платеж. Попробуйте позже или обратитесь в поддержку.",
            parse_mode="Markdown"
        )

async def check_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ручная проверка оплаты"""
    query = update.callback_query
    await query.answer()
    
    payment_id = context.user_data.get('payment_id')
    if not payment_id:
        await query.edit_message_text(
            "❌ Не найден ID платежа. Попробуйте начать процесс оплаты заново.",
            parse_mode="Markdown"
        )
        return
    
    try:
        # Проверяем статус платежа
        payment = Payment.find_one(payment_id)
        
        if payment.status == "succeeded":
            # Генерируем ключ
            username = query.from_user.username or query.from_user.full_name
            license_key = generate_license()
            append_license_to_sheet(license_key, username, payment_id)
            
            text = (
                "🎉 *Оплата подтверждена!*\n\n"
                "Ваш лицензионный ключ:\n"
                f"`{license_key}`\n\n"
                "Сохраните его в надежном месте!"
            )
            await query.edit_message_text(text, parse_mode="Markdown")
            
        elif payment.status == "pending":
            await query.answer("⌛ Платеж еще обрабатывается... Попробуйте позже.", show_alert=True)
        else:
            await query.edit_message_text(
                "❌ Платеж не найден или отменен. Попробуйте оплатить снова.",
                parse_mode="Markdown"
            )
            
    except Exception as e:
        logger.error(f"Ошибка проверки платежа: {e}")
        await query.edit_message_text(
            "⚠️ Ошибка при проверке платежа. Попробуйте позже или обратитесь в поддержку.",
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
    elif data == "check_payment":
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
    # Проверка обязательных переменных
    required_vars = ["BOT_TOKEN", "YOOKASSA_SHOP_ID", "YOOKASSA_SECRET_KEY"]
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    
    if missing_vars:
        logger.error(f"Отсутствуют обязательные переменные окружения: {', '.join(missing_vars)}")
        exit(1)
    
    # Запуск Flask в отдельном потоке
    Thread(target=run_flask, daemon=True).start()
    
    # Запуск бота
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))

    logger.info("✅ Valture бот запущен и работает!")
    application.run_polling()
