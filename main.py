import telebot
from telebot import types
import requests
import os
import random
import string
from datetime import datetime, timezone, timedelta
import gspread
from google.oauth2.service_account import Credentials
import logging
import time
from flask import Flask, request, jsonify
from threading import Thread, Timer
from uuid import uuid4
from yookassa import Configuration, Payment
import sqlite3

# --- Настройки ---
# Цены, ссылка на приложение и новости
CRYPTO_AMOUNT = 4.0  # TON для CryptoBot
YOOKASSA_AMOUNT = 1000.0  # RUB для YooKassa
APP_DOWNLOAD_URL = "https://www.dropbox.com/scl/fi/ze5ebd909z2qeaaucn56q/VALTURE.exe?rlkey=ihdzk8voej4oikrdhq0wfzvbb&st=7lufvad0&dl=1"
NEWS_TEXT = (
    "📰 *Новости Valture*\n\n"
    "Новостей пока нет, следите за обновлениями!"
)

TOKEN = os.environ.get("BOT_TOKEN", 'YOUR_BOT_TOKEN')
CRYPTOBOT_API_TOKEN = os.environ.get("CRYPTOBOT_API_TOKEN", 'YOUR_CRYPTOBOT_TOKEN')
YOOKASSA_SHOP_ID = os.environ.get("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.environ.get("YOOKASSA_SECRET_KEY")
CREDS_FILE = os.environ.get("CREDS_FILE", "valture-license-bot-account.json")
SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME", "Valture_Licenses")
TEST_PAYMENT_AMOUNT = 0.1  # TON для тестовых платежей CryptoBot

SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

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

# --- Инициализация SQLite ---
def init_db():
    conn = sqlite3.connect('transactions.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            payment_id TEXT PRIMARY KEY,
            user_id TEXT,
            username TEXT,
            license_key TEXT,
            timestamp TEXT,
            payment_type TEXT,
            status TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# --- Flask для keep-alive и вебхуков ---
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Valture бот работает!"

@app.route('/yookassa-webhook', methods=['POST'])
def yookassa_webhook():
    try:
        event_json = request.get_json()
        logger.debug(f"YooKassa webhook received: {event_json}")
        
        if not event_json or 'event' not in event_json or 'object' not in event_json:
            logger.error("Invalid webhook payload")
            return jsonify({"status": "error", "message": "Invalid payload"}), 400

        event = event_json['event']
        payment_object = event_json['object']
        payment_id = payment_object['id']
        
        # Проверка, был ли платеж уже обработан
        conn = sqlite3.connect('transactions.db')
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM transactions WHERE payment_id = ?", (payment_id,))
        result = cursor.fetchone()
        if result:
            logger.warning(f"Payment {payment_id} already processed with status: {result[0]}")
            conn.close()
            return jsonify({"status": "ignored", "message": "Payment already processed"}), 200

        if event == 'payment.succeeded':
            metadata = payment_object.get('metadata', {})
            user_id = metadata.get('user_id')
            username = metadata.get('username')
            
            if not user_id or not username:
                logger.error(f"Missing metadata: user_id={user_id}, username={username}")
                conn.close()
                return jsonify({"status": "error", "message": "Missing metadata"}), 400

            try:
                license_key = generate_license()
                sheet_success = append_license_to_sheet(license_key, username)
                
                # Сохраняем транзакцию в базе
                cursor.execute('''
                    INSERT INTO transactions (payment_id, user_id, username, license_key, timestamp, payment_type, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (payment_id, user_id, username, license_key, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 'yookassa', 'succeeded'))
                conn.commit()
                
                bot.send_message(
                    chat_id=user_id,
                    text=(
                        "🎉 *Поздравляем с покупкой!*\n\n"
                        f"Ваш лицензионный ключ:\n`{license_key}`\n\n"
                        f"Скачать приложение Valture:\n[VALTURE.exe]({APP_DOWNLOAD_URL})\n\n"
                        "Сохраните ключ и скачайте приложение! 🚀"
                    ),
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
                logger.info(f"YooKassa payment processed: {license_key} for {username}")
                if user_id in invoices and invoices[user_id]['payment_type'] == 'yookassa':
                    del invoices[user_id]
            except Exception as e:
                logger.error(f"Error processing YooKassa payment {payment_id}: {e}")
                cursor.execute('''
                    INSERT INTO transactions (payment_id, user_id, username, timestamp, payment_type, status)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (payment_id, user_id, username, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 'yookassa', 'failed'))
                conn.commit()
                bot.send_message(
                    chat_id=user_id,
                    text=(
                        "❌ *Ошибка!*\n\n"
                        "Не удалось выдать ключ. Свяжитесь с @s3pt1ck."
                    ),
                    parse_mode="Markdown"
                )
            conn.close()
            return jsonify({"status": "ok"}), 200
        
        elif event == 'payment.canceled':
            logger.warning(f"YooKassa payment canceled: {payment_id}")
            cursor.execute('''
                INSERT INTO transactions (payment_id, user_id, username, timestamp, payment_type, status)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (payment_id, user_id or '', username or '', datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 'yookassa', 'canceled'))
            conn.commit()
            conn.close()
            return jsonify({"status": "ok"}), 200
        
        conn.close()
        return jsonify({"status": "ignored"}), 200
    
    except Exception as e:
        logger.error(f"Error in YooKassa webhook: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# --- Инициализация бота ---
bot = telebot.TeleBot(TOKEN)
invoices = {}
sheet_cache = None

# --- Очистка устаревших invoices ---
def clean_old_invoices():
    current_time = time.time()
    expired = []
    for user_id, invoice in invoices.items():
        if current_time - invoice.get('created_at', current_time) > 1800:  # 30 минут
            expired.append(user_id)
    for user_id in expired:
        del invoices[user_id]
    logger.debug(f"Очищено {len(expired)} устаревших инвойсов")
    # Планируем следующую очистку через 10 минут
    Timer(600, clean_old_invoices).start()

# Запускаем первую очистку
Timer(600, clean_old_invoices).start()

# --- Обработка Google Sheets ---
def setup_google_creds():
    logger.debug("Проверка Google credentials...")
    if not os.path.exists(CREDS_FILE):
        logger.error(f"Файл учетных данных {CREDS_FILE} не найден")
        raise FileNotFoundError(f"Файл {CREDS_FILE} не найден")
    logger.info(f"Используется файл учетных данных: {CREDS_FILE}")

def get_sheet():
    global sheet_cache
    if sheet_cache is None:
        try:
            setup_google_creds()
            creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPE)
            client = gspread.authorize(creds)
            sheet_cache = client.open(SPREADSHEET_NAME).sheet1
            logger.info(f"Подключено к Google Sheet: {SPREADSHEET_NAME}")
        except gspread.exceptions.SpreadsheetNotFound:
            logger.error(f"Google Sheet '{SPREADSHEET_NAME}' не найдена")
            raise
        except Exception as e:
            logger.error(f"Ошибка подключения к Google Sheets: {str(e)}")
            raise
    return sheet_cache

def generate_license(length=32):
    try:
        key = ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))
        logger.info(f"Сгенерирован HWID-ключ: {key}")
        return key
    except Exception as e:
        logger.error(f"Ошибка генерации ключа: {str(e)}")
        raise

def append_license_to_sheet(license_key, username, retries=3, delay=2):
    for attempt in range(1, retries + 1):
        try:
            sheet = get_sheet()
            utc_plus_2 = timezone(timedelta(hours=2))
            now_utc_plus_2 = datetime.now(utc_plus_2)
            now_str = now_utc_plus_2.strftime("%Y-%m-%d %H:%M:%S")
            sheet.append_row([license_key, "", username, now_str])
            logger.info(f"HWID-ключ {license_key} добавлен для {username}")
            return True
        except Exception as e:
            logger.error(f"Попытка {attempt}/{retries} не удалась: {str(e)}")
            if attempt < retries:
                time.sleep(delay)
    logger.error(f"Не удалось добавить ключ {license_key} после {retries} попыток")
    return False

# --- Платежные функции ---
def create_crypto_invoice(amount, asset="TON", description="Valture License"):
    logger.debug(f"Создание инвойса: amount={amount}, asset={asset}")
    if not CRYPTOBOT_API_TOKEN:
        logger.error("CRYPTOBOT_API_TOKEN не задан")
        return None, "CRYPTOBOT_API_TOKEN не задан"
    try:
        payload = {
            "amount": str(amount),
            "asset": asset,
            "description": description,
            "order_id": str(uuid4())
        }
        headers = {
            "Crypto-Pay-API-Token": CRYPTOBOT_API_TOKEN,
            "Content-Type": "application/json"
        }
        response = requests.post(f"{CRYPTO_BOT_API}/createInvoice", json=payload, headers=headers, timeout=10)
        logger.debug(f"HTTP статус: {response.status_code}, Ответ: {response.text}")
        response.raise_for_status()
        data = response.json()
        if data.get("ok"):
            logger.info(f"Инвойс создан: invoice_id={data['result']['invoice_id']}")
            return data["result"], None
        else:
            error_msg = data.get("error", "Неизвестная ошибка")
            logger.error(f"Ошибка API CryptoBot: {error_msg}")
            return None, f"Ошибка API: {error_msg}"
    except Exception as e:
        logger.error(f"Ошибка создания инвойса: {e}")
        return None, f"Ошибка: {str(e)}"

def check_invoice_status(invoice_id):
    logger.debug(f"Проверка инвойса: invoice_id={invoice_id}")
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
            logger.error(f"Ошибка проверки: {data.get('error', 'Неизвестная ошибка')}")
            return None
    except Exception as e:
        logger.error(f"Ошибка проверки инвойса: {e}")
        return None

def create_yookassa_payment(amount, description, user_id, username):
    logger.debug(f"Создание YooKassa платежа: amount={amount}, user_id={user_id}")
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
        logger.error(f"Ошибка создания YooKassa платежа: {e}")
        return None, f"YooKassa ошибка: {str(e)}"

def check_yookassa_payment_status(payment_id):
    logger.debug(f"Проверка YooKassa платежа: payment_id={payment_id}")
    try:
        payment = Payment.find_one(payment_id)
        status = payment.status
        logger.info(f"Статус платежа {payment_id}: {status}")
        return status
    except Exception as e:
        logger.error(f"Ошибка проверки YooKassa платежа: {e}")
        return None

# --- Логика бота ---
@bot.message_handler(commands=['start'])
def welcome(message):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(text="🏠 Главное меню", callback_data='menu_main'))
    bot.send_message(
        message.chat.id,
        (
            "🎮 *Добро пожаловать в Valture!*\n\n"
            "Ваш лучший инструмент для игровой производительности! 🚀\n"
            "Выберите опцию ниже, чтобы начать:"
        ),
        parse_mode="Markdown",
        reply_markup=markup
    )

@bot.message_handler(commands=['test_sheets'])
def test_sheets(message):
    try:
        sheet = get_sheet()
        test_key = "TEST_KEY_" + str(int(time.time()))
        sheet.append_row([test_key, "", "test_user", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
        logger.info(f"Тестовая запись {test_key} добавлена")
        bot.reply_to(message, f"✅ Успешно записан тестовый ключ: {test_key}!")
    except Exception as e:
        logger.error(f"Ошибка при тестировании Google Sheets: {str(e)}")
        bot.reply_to(message, f"❌ Ошибка при тестировании: {str(e)}")

@bot.callback_query_handler(func=lambda call: True)
def button_handler(call):
    data = call.data
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    markup = types.InlineKeyboardMarkup()
    username = call.from_user.username or call.from_user.first_name

    if data == "menu_main":
        markup.add(types.InlineKeyboardButton(text="ℹ️ О Valture", callback_data='menu_about'))
        markup.add(types.InlineKeyboardButton(text="📰 Новости", callback_data='menu_news'))
        markup.add(types.InlineKeyboardButton(text="💳 Купить лицензию", callback_data='menu_pay'))
        markup.add(types.InlineKeyboardButton(text="💰 Купленные лицензии", callback_data='menu_licenses'))
        markup.add(types.InlineKeyboardButton(text="❓ FAQ", callback_data='menu_faq'))
        markup.add(types.InlineKeyboardButton(text="📞 Поддержка", callback_data='menu_support'))
        bot.edit_message_text(
            "🏠 *Главное меню*\n\nВыберите раздел:",
            chat_id=chat_id,
            message_id=message_id,
            parse_mode="Markdown",
            reply_markup=markup
        )

    elif data == "menu_about":
        markup.add(types.InlineKeyboardButton(text="🔙 Назад в главное меню", callback_data='menu_main'))
        bot.edit_message_text(
            (
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
            ),
            chat_id=chat_id,
            message_id=message_id,
            parse_mode="Markdown",
            reply_markup=markup
        )

    elif data == "menu_news":
        markup.add(types.InlineKeyboardButton(text="🔙 Назад в главное меню", callback_data='menu_main'))
        bot.edit_message_text(
            NEWS_TEXT,
            chat_id=chat_id,
            message_id=message_id,
            parse_mode="Markdown",
            reply_markup=markup
        )

    elif data == "menu_licenses":
        try:
            conn = sqlite3.connect('transactions.db')
            cursor = conn.cursor()
            cursor.execute("SELECT license_key, timestamp, payment_type FROM transactions WHERE user_id = ? AND status = 'succeeded'",
                (chat_id,)
            )
            results = cursor.fetchall()
            conn.close()

            markup.add(types.InlineKeyboardButton(text="🔙 Назад в главное меню", callback_data='menu_main'))
            if results:
                response = "🔑 *Ваши покупки:*\n\n"
                for key, timestamp, payment_type in results:
                    response += (
                        f"Ключ: `{key}`\n"
                        f"Дата покупки: {timestamp}\n"
                        f"Тип: {payment_type.capitalize()}\n\n"
                    )
            else:
                response = "У вас нет купленных ключей."
                
            bot.edit_message_text(
                response,
                chat_id=chat_id,
                message_id=message_id,
                parse_mode="Markdown",
                reply_markup=markup
            )

        except Exception as e:
            logger.error(f"Ошибка при получении лицензий: {e}")
            markup.add(types.InlineKeyboardButton(text="🔙 Назад в главное меню", callback_data='menu_main'))
            bot.edit_message_text(
                "❌ Ошибка при загрузке лицензий. Свяжитесь с @s3pt1ck.",
                chat_id=chat_id,
                message_id=message_id,
                parse_mode="Markdown",
                reply_markup=markup
            )

    elif data == "menu_pay":
        markup.add(types.InlineKeyboardButton(text="💸 Оплатить через CryptoBot", callback_data='pay_crypto'))
        markup.add(types.InlineKeyboardButton(text="💳 Оплатить через YooKassa", callback_data='pay_yookassa'))
        markup.add(types.InlineKeyboardButton(text="🔙 Назад в главное меню", callback_data='menu_main'))
        bot.edit_message_text(
            (
                f"💳 Информация о покупке\n\n"
                f"Цена: *{CRYPTO_AMOUNT} TON* или *{YOOKASSA_AMOUNT} RUB* (~$10.7)\n"
                "Выберите способ оплаты:\n"
                "- *CryptoBot*: Оплата через криптовалюту.\n"
                "- *YooKassa*: Оплата картой.\n\n"
                "Ключ и ссылка будут отправлены после оплаты."
            ),
            chat_id=chat_id,
            message_id=message_id,
            parse_mode="Markdown",
            reply_markup=markup
        )

    elif data == "pay_crypto":
        markup.add(types.InlineKeyboardButton(text="✅ Подтвердить оплату", callback_data='pay_crypto_confirm'))
        markup.add(types.InlineKeyboardButton(text="🔙 Назад к способам оплаты", callback_data='menu_pay'))
        bot.edit_message_text(
            (
                f"💸 *Подтверждение оплаты CryptoBot*\n"
                f"Вы собираетесь оплатить *{CRYPTO_AMOUNT} TON* за лицензию Valture.\n"
                "Продолжить оплату?"
            ),
            chat_id=chat_id,
            message_id=message_id,
            parse_mode="Markdown",
            reply_markup=markup
        )

    elif data == "pay_crypto_confirm":
        try:
            invoice, error = create_crypto_invoice(amount=CRYPTO_AMOUNT)
            if not invoice:
                markup.add(types.InlineKeyboardButton(text="🔄 Попробовать снова", callback_data='pay_crypto'))
                markup.add(types.InlineKeyboardButton(text="🔙 Назад к способам оплаты", callback_data='menu_pay'))
                bot.edit_message_text(
                    (
                        "❌ *Ошибка!*\n\n"
                        f"Не удалось создать инвойс: {error or 'Неизвестная ошибка'}.\n"
                        "Попробуйте снова или свяжитесь с @s3pt1ck."
                    ),
                    chat_id=chat_id,
                    message_id=message_id,
                    parse_mode="Markdown",
                    reply_markup=markup
                )
                return

            invoice_id = invoice["invoice_id"]
            pay_url = invoice["pay_url"]
            invoices[chat_id] = {
                'invoice_id': invoice_id,
                'username': username,
                'payment_type': 'crypto',
                'created_at': time.time()
            }
            logger.info(f"Инвойс создан: invoice_id={invoice_id}, pay_url={pay_url}")

            # Сохраняем инвойс в базе
            conn = sqlite3.connect('transactions.db')
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO transactions (payment_id, user_id, username, timestamp, payment_type, status)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (invoice_id, chat_id, username, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 'crypto', 'pending'))
            conn.commit()
            conn.close()

            markup.add(types.InlineKeyboardButton(text=f"Оплатить {CRYPTO_AMOUNT} TON", url=pay_url))
            markup.add(types.InlineKeyboardButton(text="✅ Подтвердить оплату", callback_data='pay_verify'))
            markup.add(types.InlineKeyboardButton(text="🔙 Назад к способам оплаты", callback_data='menu_pay'))
            bot.edit_message_text(
                (
                    f"💸 *Оплатите через CryptoBot*\n\n"
                    f"Нажмите ниже для оплаты *{CRYPTO_AMOUNT} TON*:\n"
                    f"[Оплатить через CryptoBot]({pay_url})\n\n"
                    "После оплаты подтвердите ниже."
                ),
                chat_id=chat_id,
                message_id=message_id,
                parse_mode="Markdown",
                reply_markup=markup,
                disable_web_page_preview=True
            )
        except Exception as e:
            logger.error(f"Ошибка создания инвойса: {e}")
            markup.add(types.InlineKeyboardButton(text="🔄 Попробовать снова", callback_data='pay_crypto'))
            markup.add(types.InlineKeyboardButton(text="🔙 Назад к способам оплаты", callback_data='menu_pay'))
            bot.edit_message_text(
                (
                    "❌ *Ошибка!*\n\n"
                    "Не удалось обработать запрос. Свяжитесь с @s3pt1ck."
                ),
                chat_id=chat_id,
                message_id=message_id,
                parse_mode="Markdown",
                reply_markup=markup
            )

    elif data == "pay_yookassa":
        markup.add(types.InlineKeyboardButton(text="✅ Подтвердить оплату", callback_data='pay_yookassa_confirm'))
        markup.add(types.InlineKeyboardButton(text="🔙 Назад к способам оплаты", callback_data='menu_pay'))
        bot.edit_message_text(
            (
                f"💳 *Подтверждение оплаты YooKassa*\n\n"
                f"Вы собираетесь оплатить *{YOOKASSA_AMOUNT} RUB* за лицензию Valture.\n"
                "Продолжить оплату?"
            ),
            chat_id=chat_id,
            message_id=message_id,
            parse_mode="Markdown",
            reply_markup=markup
        )

    elif data == "pay_yookassa_confirm":
        try:
            payment, error = create_yookassa_payment(
                amount=YOOKASSA_AMOUNT,
                description="Valture License",
                user_id=call.from_user.id,
                username=username
            )
            if not payment:
                markup.add(types.InlineKeyboardButton(text="🔄 Попробовать снова", callback_data='pay_yookassa'))
                markup.add(types.InlineKeyboardButton(text="🔙 Назад к способам оплаты", callback_data='menu_pay'))
                bot.edit_message_text(
                    (
                        "❌ *Ошибка!*\n\n"
                        f"Не удалось создать платеж: {error or 'Неизвестная ошибка'}.\n"
                        "Попробуйте снова или свяжитесь с @s3pt1ck."
                    ),
                    chat_id=chat_id,
                    message_id=message_id,
                    parse_mode="Markdown",
                    reply_markup=markup
                )
                return

            payment_id = payment.id
            confirmation_url = payment.confirmation.confirmation_url
            invoices[chat_id] = {
                'payment_id': payment_id,
                'username': username,
                'payment_type': 'yookassa',
                'created_at': time.time()
            }
            logger.info(f"YooKassa платеж создан: payment_id={payment_id}")

            # Сохраняем платеж в базе
            conn = sqlite3.connect('transactions.db')
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO transactions (payment_id, user_id, username, timestamp, payment_type, status)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (payment_id, chat_id, username, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 'yookassa', 'pending'))
            conn.commit()
            conn.close()

            markup.add(types.InlineKeyboardButton(text=f"Оплатить {YOOKASSA_AMOUNT} RUB", url=confirmation_url))
            markup.add(types.InlineKeyboardButton(text="✅ Подтвердить оплату", callback_data='pay_verify'))
            markup.add(types.InlineKeyboardButton(text="🔙 Назад к способам оплаты", callback_data='menu_pay'))
            bot.edit_message_text(
                (
                    f"💳 *Оплатите через YooKassa*\n\n"
                    f"Нажмите ниже для оплаты *{YOOKASSA_AMOUNT} RUB*:\n"
                    f"[Оплатить через YooKassa]({confirmation_url})\n\n"
                    "После оплаты подтвердите ниже или дождитесь автоматической обработки."
                ),
                chat_id=chat_id,
                message_id=message_id,
                parse_mode="Markdown",
                reply_markup=markup,
                disable_web_page_preview=True
            )
        except Exception as e:
            logger.error(f"Ошибка создания YooKassa платежа: {e}")
            markup.add(types.InlineKeyboardButton(text="🔄 Попробовать снова", callback_data='pay_yookassa'))
            markup.add(types.InlineKeyboardButton(text="🔙 Назад к способам оплаты", callback_data='menu_pay'))
            bot.edit_message_text(
                (
                    "❌ *Ошибка!*\n\n"
                    "Не удалось обработать запрос. Свяжитесь с @s3pt1ck."
                ),
                chat_id=chat_id,
                message_id=message_id,
                parse_mode="Markdown",
                reply_markup=markup
            )

    elif data == "pay_verify":
        if chat_id not in invoices:
            markup.add(types.InlineKeyboardButton(text="🔙 Назад к способам оплаты", callback_data='menu_pay'))
            bot.edit_message_text(
                (
                    "❌ *Ошибка!*\n\n"
                    "Данные об оплате отсутствуют. Начните оплату заново."
                ),
                chat_id=chat_id,
                message_id=message_id,
                parse_mode="Markdown",
                reply_markup=markup
            )
            return

        payment_type = invoices[chat_id]['payment_type']
        username = invoices[chat_id]['username']

        try:
            markup.add(types.InlineKeyboardButton(text="🔙 Назад к способам оплаты", callback_data='menu_pay'))
            bot.edit_message_text(
                "⏳ *Проверка оплаты...*\n\nПожалуйста, подождите.",
                chat_id=chat_id,
                message_id=message_id,
                parse_mode="Markdown",
                reply_markup=markup
            )

            conn = sqlite3.connect('transactions.db')
            cursor = conn.cursor()

            if payment_type == 'crypto':
                invoice_id = invoices[chat_id]['invoice_id']
                status = check_invoice_status(invoice_id)
                if status == "paid":
                    cursor.execute("SELECT license_key FROM transactions WHERE payment_id = ?", (invoice_id,))
                    result = cursor.fetchone()
                    if result and result[0]:
                        logger.warning(f"Invoice {invoice_id} already processed")
                        markup = types.InlineKeyboardMarkup()
                        markup.add(types.InlineKeyboardButton(text="🏠 Назад в главное меню", callback_data='menu_main'))
                        bot.edit_message_text(
                            (
                                "🎉 *Платеж уже обработан!*\n\n"
                                f"HWID-ключ:\n`{result[0]}`\n\n"
                                f"Скачать приложение Valture:\n[VALTURE.exe]({APP_DOWNLOAD_URL})\n\n"
                                "Сохраните ключ и скачайте приложение! 🚀"
                            ),
                            chat_id=chat_id,
                            message_id=message_id,
                            parse_mode="Markdown",
                            reply_markup=markup,
                            disable_web_page_preview=True
                        )
                        conn.close()
                        return

                    hwid_key = generate_license()
                    sheet_success = append_license_to_sheet(hwid_key, username)
                    cursor.execute('''
                        UPDATE transactions SET license_key = ?, status = ? WHERE payment_id = ?
                    ''', (hwid_key, 'succeeded', invoice_id))
                    conn.commit()
                    markup = types.InlineKeyboardMarkup()
                    markup.add(types.InlineKeyboardButton(text="🏠 Назад в главное меню", callback_data='menu_main'))
                    if sheet_success:
                        bot.edit_message_text(
                            (
                                "🎉 *Поздравляем с покупкой!*\n\n"
                                f"HWID-ключ:\n`{hwid_key}`\n\n"
                                f"Скачать приложение Valture:\n[VALTURE.exe]({APP_DOWNLOAD_URL})\n\n"
                                "Сохраните ключ и скачайте приложение! 🚀"
                            ),
                            chat_id=chat_id,
                            message_id=message_id,
                            parse_mode="Markdown",
                            reply_markup=markup,
                            disable_web_page_preview=True
                        )
                    else:
                        bot.edit_message_text(
                            (
                                "🎉 *Поздравляем с покупкой!*\n\n"
                                f"HWID-ключ:\n`{hwid_key}`\n\n"
                                f"Скачать приложение Valture:\n[VALTURE.exe]({APP_DOWNLOAD_URL})\n\n"
                                "Сохраните ключ и скачайте приложение! 🚀\n\n"
                                "⚠️ Не удалось записать ключ в таблицу. Свяжитесь с @s3pt1ck."
                            ),
                            chat_id=chat_id,
                            message_id=message_id,
                            parse_mode="Markdown",
                            reply_markup=markup,
                            disable_web_page_preview=True
                        )
                    logger.info(f"CryptoBot оплата подтверждена: {hwid_key} для {username}")
                    del invoices[chat_id]
                else:
                    markup.add(types.InlineKeyboardButton(text="🔄 Проверить снова", callback_data='pay_verify'))
                    markup.add(types.InlineKeyboardButton(text="🔙 Назад к способам оплаты", callback_data='menu_pay'))
                    bot.edit_message_text(
                        (
                            "⏳ *Оплата еще не подтверждена*\n\n"
                            "Завершите оплату или попробуйте снова. Свяжитесь с @s3pt1ck."
                        ),
                        chat_id=chat_id,
                        message_id=message_id,
                        parse_mode="Markdown",
                        reply_markup=markup
                    )

            elif payment_type == 'yookassa':
                payment_id = invoices[chat_id]['payment_id']
                status = check_yookassa_payment_status(payment_id)
                if status == "succeeded":
                    cursor.execute("SELECT license_key FROM transactions WHERE payment_id = ?", (payment_id,))
                    result = cursor.fetchone()
                    if result and result[0]:
                        logger.warning(f"Payment {payment_id} already processed")
                        markup = types.InlineKeyboardMarkup()
                        markup.add(types.InlineKeyboardButton(text="🏠 Назад в главное меню", callback_data='menu_main'))
                        bot.edit_message_text(
                            (
                                "🎉 *Платеж уже обработан!*\n\n"
                                f"HWID-ключ:\n`{result[0]}`\n\n"
                                f"Скачать приложение Valture:\n[VALTURE.exe]({APP_DOWNLOAD_URL})\n\n"
                                "Сохраните ключ и скачайте приложение! 🚀"
                            ),
                            chat_id=chat_id,
                            message_id=message_id,
                            parse_mode="Markdown",
                            reply_markup=markup,
                            disable_web_page_preview=True
                        )
                        conn.close()
                        return

                    hwid_key = generate_license()
                    sheet_success = append_license_to_sheet(hwid_key, username)
                    cursor.execute('''
                        UPDATE transactions SET license_key = ?, status = ? WHERE payment_id = ?
                    ''', (hwid_key, 'succeeded', payment_id))
                    conn.commit()
                    markup = types.InlineKeyboardMarkup()
                    markup.add(types.InlineKeyboardButton(text="🏠 Назад в главное меню", callback_data='menu_main'))
                    if sheet_success:
                        bot.edit_message_text(
                            (
                                "🎉 *Поздравляем с покупкой!*\n\n"
                                f"HWID-ключ:\n`{hwid_key}`\n\n"
                                f"Скачать приложение Valture:\n[VALTURE.exe]({APP_DOWNLOAD_URL})\n\n"
                                "Сохраните ключ и скачайте приложение! 🚀"
                            ),
                            chat_id=chat_id,
                            message_id=message_id,
                            parse_mode="Markdown",
                            reply_markup=markup,
                            disable_web_page_preview=True
                        )
                    else:
                        bot.edit_message_text(
                            (
                                "🎉 *Поздравляем с покупкой!*\n\n"
                                f"HWID-ключ:\n`{hwid_key}`\n\n"
                                f"Скачать приложение Valture:\n[VALTURE.exe]({APP_DOWNLOAD_URL})\n\n"
                                "Сохраните ключ и скачайте приложение! 🚀\n\n"
                                "⚠️ Не удалось записать ключ в таблицу. Свяжитесь с @s3pt1ck."
                            ),
                            chat_id=chat_id,
                            message_id=message_id,
                            parse_mode="Markdown",
                            reply_markup=markup,
                            disable_web_page_preview=True
                        )
                    logger.info(f"YooKassa оплата подтверждена: {hwid_key} для {username}")
                    del invoices[chat_id]
                else:
                    markup.add(types.InlineKeyboardButton(text="🔄 Проверить снова", callback_data='pay_verify'))
                    markup.add(types.InlineKeyboardButton(text="🔙 Назад к способам оплаты", callback_data='menu_pay'))
                    bot.edit_message_text(
                        (
                            "⏳ *Оплата еще не подтверждена*\n\n"
                            "Завершите оплату или попробуйте снова. Свяжитесь с @s3pt1ck."
                        ),
                        chat_id=chat_id,
                        message_id=message_id,
                        parse_mode="Markdown",
                        reply_markup=markup
                    )

            conn.close()

        except Exception as e:
            logger.error(f"Ошибка проверки оплаты: {e}")
            markup.add(types.InlineKeyboardButton(text="🔄 Проверить снова", callback_data='pay_verify'))
            markup.add(types.InlineKeyboardButton(text="🔙 Назад к способам оплаты", callback_data='menu_pay'))
            bot.edit_message_text(
                (
                    "❌ *Ошибка!*\n\n"
                    "Не удалось проверить оплату. Свяжитесь с @s3pt1ck."
                ),
                chat_id=chat_id,
                message_id=message_id,
                parse_mode="Markdown",
                reply_markup=markup
            )

    elif data == "menu_faq":
        markup.add(types.InlineKeyboardButton(text="🔙 Назад в главное меню", callback_data='menu_main'))
        bot.edit_message_text(
            (
                "❓ *FAQ*\n\n"
                "1. Как получить лицензию?\n"
                "- Используйте 'Купить лицензию' и выберите способ оплаты.\n\n"
                "2. Что делать, если ключ не работает?\n"
                "- Свяжитесь с @s3pt1ck.\n\n"
                "3. Можно ли использовать на нескольких устройствах?\n"
                "- Нет, ключ привязан к одному устройству."
            ),
            chat_id=chat_id,
            message_id=message_id,
            parse_mode="Markdown",
            reply_markup=markup
        )

    elif data == "menu_support":
        markup.add(types.InlineKeyboardButton(text="🔙 Назад в главное меню", callback_data='menu_main'))
        bot.edit_message_text(
            (
                "📞 *Поддержка Valture*\n\n"
                "Если у вас вопросы, пишите: @s3pt1ck"
            ),
            chat_id=chat_id,
            message_id=message_id,
            parse_mode="Markdown",
            reply_markup=markup
        )

    bot.answer_callback_query(call.id)

if __name__ == '__main__':
    Thread(target=run_flask).start()
    logger.info("Бот запущен")
    try:
        bot.polling(non_stop=True)
    except Exception as e:
        logger.error(f"Ошибка в polling: {e}")
        time.sleep(10)
        bot.polling(non_stop=True)
