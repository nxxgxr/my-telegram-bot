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
from threading import Thread
from uuid import uuid4
from yookassa import Configuration, Payment

# --- Настройки ---
TOKEN = os.environ.get("BOT_TOKEN", '7941872387:AAGZayILmna-qHHyQy5V50wDGylo3yFCZ0A')
CRYPTOBOT_API_TOKEN = os.environ.get("CRYPTOBOT_API_TOKEN", '406690:AA0uW0MoZHwZ1CnAvw1zn3lcx7lNKnbT24w')
YOOKASSA_SHOP_ID = os.environ.get("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.environ.get("YOOKASSA_SECRET_KEY")
CREDS_FILE = os.environ.get("CREDS_FILE", "valture-license-bot-account.json")
SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME", "Valture_Licenses")
TEST_PAYMENT_AMOUNT = 0.01  # TON for CryptoBot
YOOKASSA_AMOUNT = 1000.0  # RUB for YooKassa

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
        
        if event == 'payment.succeeded':
            payment_id = payment_object['id']
            metadata = payment_object.get('metadata', {})
            user_id = metadata.get('user_id')
            username = metadata.get('username')
            
            if not user_id or not username:
                logger.error(f"Missing metadata: user_id={user_id}, username={username}")
                return jsonify({"status": "error", "message": "Missing metadata"}), 400

            # Process payment synchronously for simplicity
            try:
                license_key = generate_license()
                append_license_to_sheet(license_key, username)
                bot.send_message(
                    chat_id=user_id,
                    text=(
                        "🎉 *Поздравляем с покупкой!*\n\n"
                        f"Ваш лицензионный ключ:\n`{license_key}`\n\n"
                        "Сохраните его! 🚀"
                    ),
                    parse_mode="Markdown"
                )
                logger.info(f"YooKassa payment processed: {license_key} for {username}")
            except Exception as e:
                logger.error(f"Error processing YooKassa payment {payment_id}: {e}")
                bot.send_message(
                    chat_id=user_id,
                    text=(
                        "❌ *Ошибка!*\n\n"
                        "Не удалось выдать ключ. Свяжитесь с @s3pt1ck."
                    ),
                    parse_mode="Markdown"
                )
            return jsonify({"status": "ok"}), 200
        
        elif event == 'payment.canceled':
            logger.warning(f"YooKassa payment canceled: {payment_object['id']}")
            return jsonify({"status": "ok"}), 200
        
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

# --- Обработка Google Credentials ---
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
        bot.reply_to(message, f"Успешно записан тестовый ключ {test_key}!")
        logger.info(f"Тестовая запись {test_key} добавлена")
    except Exception as e:
        error_msg = f"Ошибка при тестировании Google Sheets: {str(e)}"
        bot.reply_to(message, error_msg)
        logger.error(error_msg)

@bot.callback_query_handler(func=lambda call: True)
def button_handler(call):
    data = call.data
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    username = call.from_user.username or call.from_user.first_name

    if data == "menu_main":
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(text="ℹ️ О Valture", callback_data='menu_about'))
        markup.add(types.InlineKeyboardButton(text="💳 Купить лицензию", callback_data='menu_pay'))
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
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(text="🔙 Назад в главное меню", callback_data='menu_main'))
        bot.edit_message_text(
            (
                "✨ *Valture — Ваш путь к совершенству в играх*\n\n"
                "Valture — передовой инструмент для геймеров.\n"
                "🔥 *Почему выбирают Valture?*\n"
                "🚀 +20–30% FPS\n"
                "🛡️ Стабильный фреймрейт\n"
                "💡 Молниеносная отзывчивость\n"
                "🔋 Оптимизация Windows\n"
                "🖥️ Плавность картинки\n"
                "_Создано для победы._"
            ),
            chat_id=chat_id,
            message_id=message_id,
            parse_mode="Markdown",
            reply_markup=markup
        )

    elif data == "menu_pay":
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(text="💸 Оплатить через CryptoBot", callback_data='pay_crypto'))
        markup.add(types.InlineKeyboardButton(text="💳 Оплатить через YooKassa", callback_data='pay_yookassa'))
        markup.add(types.InlineKeyboardButton(text="🔙 Назад в главное меню", callback_data='menu_main'))
        bot.edit_message_text(
            (
                "💳 *Покупка лицензии Valture*\n\n"
                "Цена: *4 TON* или *1000 RUB (~$12.7)*\n"
                "Выберите способ оплаты:\n"
                "- *CryptoBot*: Оплата через криптовалюту.\n"
                "- *YooKassa*: Оплата картой.\n\n"
                "Ключ будет отправлен после оплаты."
            ),
            chat_id=chat_id,
            message_id=message_id,
            parse_mode="Markdown",
            reply_markup=markup
        )

    elif data == "pay_crypto":
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(text="✅ Подтвердить оплату", callback_data='pay_crypto_confirm'))
        markup.add(types.InlineKeyboardButton(text="🔙 Назад к способам оплаты", callback_data='menu_pay'))
        bot.edit_message_text(
            (
                "💸 *Подтверждение оплаты CryptoBot*\n\n"
                "Вы собираетесь оплатить *4 TON* за лицензию Valture.\n"
                "Продолжить оплату?"
            ),
            chat_id=chat_id,
            message_id=message_id,
            parse_mode="Markdown",
            reply_markup=markup
        )

    elif data == "pay_crypto_confirm":
        try:
            invoice, error = create_crypto_invoice(amount=TEST_PAYMENT_AMOUNT)
            if not invoice:
                markup = types.InlineKeyboardMarkup()
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
            invoices[chat_id] = {'invoice_id': invoice_id, 'username': username, 'payment_type': 'crypto'}
            logger.info(f"Инвойс создан: invoice_id={invoice_id}, pay_url={pay_url}")

            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton(text="Оплатить 4 TON", url=pay_url))
            markup.add(types.InlineKeyboardButton(text="✅ Подтвердить оплату", callback_data='pay_verify'))
            markup.add(types.InlineKeyboardButton(text="🔙 Назад к способам оплаты", callback_data='menu_pay'))
            bot.edit_message_text(
                (
                    "💸 *Оплатите через CryptoBot*\n\n"
                    "Нажмите ниже для оплаты *4 TON*:\n"
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
            markup = types.InlineKeyboardMarkup()
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
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(text="✅ Подтвердить оплату", callback_data='pay_yookassa_confirm'))
        markup.add(types.InlineKeyboardButton(text="🔙 Назад к способам оплаты", callback_data='menu_pay'))
        bot.edit_message_text(
            (
                "💳 *Подтверждение оплаты YooKassa*\n\n"
                "Вы собираетесь оплатить *1000 RUB* за лицензию Valture.\n"
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
                markup = types.InlineKeyboardMarkup()
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
            invoices[chat_id] = {'payment_id': payment_id, 'username': username, 'payment_type': 'yookassa'}
            logger.info(f"YooKassa платеж создан: payment_id={payment_id}")

            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton(text="Оплатить 1000 RUB", url=confirmation_url))
            markup.add(types.InlineKeyboardButton(text="🔙 Назад к способам оплаты", callback_data='menu_pay'))
            bot.edit_message_text(
                (
                    "💳 *Оплатите через YooKassa*\n\n"
                    "Нажмите ниже для оплаты *1000 RUB*:\n"
                    f"[Оплатить через YooKassa]({confirmation_url})\n\n"
                    "Ключ будет отправлен автоматически после оплаты."
                ),
                chat_id=chat_id,
                message_id=message_id,
                parse_mode="Markdown",
                reply_markup=markup,
                disable_web_page_preview=True
            )
        except Exception as e:
            logger.error(f"Ошибка создания YooKassa платежа: {e}")
            markup = types.InlineKeyboardMarkup()
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
        if chat_id not in invoices or invoices[chat_id]['payment_type'] != 'crypto':
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton(text="🔙 Назад к способам оплаты", callback_data='menu_pay'))
            bot.edit_message_text(
                (
                    "❌ *Ошибка!*\n\n"
                    "Эта кнопка только для CryptoBot. YooKassa подтверждается автоматически."
                ),
                chat_id=chat_id,
                message_id=message_id,
                parse_mode="Markdown",
                reply_markup=markup
            )
            return

        invoice_id = invoices[chat_id]['invoice_id']
        username = invoices[chat_id]['username']

        try:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton(text="🔙 Назад к способам оплаты", callback_data='menu_pay'))
            bot.edit_message_text(
                "⏳ *Проверка оплаты...*\n\nПожалуйста, подождите.",
                chat_id=chat_id,
                message_id=message_id,
                parse_mode="Markdown",
                reply_markup=markup
            )

            status = check_invoice_status(invoice_id)
            if status == "paid":
                hwid_key = generate_license()
                sheet_success = append_license_to_sheet(hwid_key, username)
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton(text="🏠 Назад в главное меню", callback_data='menu_main'))
                if sheet_success:
                    bot.edit_message_text(
                        (
                            "🎉 *Поздравляем с покупкой!*\n\n"
                            f"HWID-ключ:\n`{hwid_key}`\n\nСохраните его! 🚀"
                        ),
                        chat_id=chat_id,
                        message_id=message_id,
                        parse_mode="Markdown",
                        reply_markup=markup
                    )
                else:
                    bot.edit_message_text(
                        (
                            "🎉 *Поздравляем с покупкой!*\n\n"
                            f"HWID-ключ:\n`{hwid_key}`\n\nСохраните его! 🚀\n\n"
                            "⚠️ Не удалось записать ключ в таблицу. Свяжитесь с @s3pt1ck."
                        ),
                        chat_id=chat_id,
                        message_id=message_id,
                        parse_mode="Markdown",
                        reply_markup=markup
                    )
                logger.info(f"Оплата подтверждена: {hwid_key} для {username}")
                del invoices[chat_id]
            else:
                markup = types.InlineKeyboardMarkup()
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
        except Exception as e:
            logger.error(f"Ошибка проверки оплаты: {e}")
            markup = types.InlineKeyboardMarkup()
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
        markup = types.InlineKeyboardMarkup()
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
        markup = types.InlineKeyboardMarkup()
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
    bot.polling(non_stop=True)
