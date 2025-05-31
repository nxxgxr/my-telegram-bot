import telebot
from telebot import types
import requests
import os
import secrets
import base64
from datetime import datetime, timezone, timedelta
import gspread
from google.oauth2.service_account import Credentials
import logging
import time

# --- Настройки ---
TOKEN = os.environ.get("BOT_TOKEN", '7941872387:AAGZayILmna-qHHyQy5V50wDGylo3yFCZ0A')
CRYPTOBOT_API_TOKEN = os.environ.get("CRYPTOBOT_API_TOKEN", '406690:AA0uW0MoZHwZ1CnAvw1zn3lcx7lNKnbT24w')
CREDS_FILE = os.environ.get("CREDS_FILE", "creds.json")
SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME", "Valture_Licenses")
GOOGLE_CREDS_JSON_BASE64 = os.environ.get("GOOGLE_CREDS_JSON_BASE64")
TEST_PAYMENT_AMOUNT = 0.01

SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

CRYPTO_BOT_API = "https://pay.crypt.bot/api"

# --- Логирование ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.DEBUG
)
logger = logging.getLogger(__name__)

# --- Инициализация бота ---
bot = telebot.TeleBot(TOKEN)

invoices = {}
sheet_cache = None

# --- Обработка Google Credentials ---
def setup_google_creds():
    logger.debug("Проверка Google credentials...")
    if GOOGLE_CREDS_JSON_BASE64:
        if not GOOGLE_CREDS_JSON_BASE64.strip():
            logger.error("GOOGLE_CREDS_JSON_BASE64 пустая")
            raise ValueError("GOOGLE_CREDS_JSON_BASE64 пустая")
        try:
            # Добавляем padding, если отсутствует
            padded_base64 = GOOGLE_CREDS_JSON_BASE64.strip() + '=' * (-len(GOOGLE_CREDS_JSON_BASE64.strip()) % 4)
            creds_json = base64.b64decode(padded_base64).decode("utf-8")
            with open(CREDS_FILE, "w") as f:
                f.write(creds_json)
            logger.info("Google credentials декодированы и сохранены")
        except base64.binascii.Error as e:
            logger.error(f"Ошибка base64: {str(e)}")
            raise
        except UnicodeDecodeError as e:
            logger.error(f"Ошибка UTF-8: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Неизвестная ошибка декодирования: {str(e)}")
            raise
    elif os.path.exists(CREDS_FILE):
        logger.info(f"Используется файл: {CREDS_FILE}")
    else:
        logger.error("Файл credentials не найден, и GOOGLE_CREDS_JSON_BASE64 не задан")
        raise FileNotFoundError("Файл credentials не найден")

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
            logger.error(f"Sheet '{SPREADSHEET_NAME}' не найдена")
            raise
        except Exception as e:
            logger.error(f"Ошибка подключения к Google Sheets: {str(e)}")
            raise
    return sheet_cache

def generate_license(length=32):
    try:
        key = ''.join(secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') for _ in range(length))
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

# --- Логика бота ---
@bot.message_handler(commands=['start'])
def welcome(message):
    markup = types.InlineKeyboardMarkup()
    get_button = types.InlineKeyboardButton(text="Оплатить", callback_data=f'get_{TEST_PAYMENT_AMOUNT}')
    markup.add(get_button)
    bot.send_message(message.chat.id, "Добро пожаловать! Нажмите кнопку для оплаты.", reply_markup=markup)

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

@bot.callback_query_handler(func=lambda call: call.data.startswith('get_'))
def get_invoice(call):
    chat_id = call.message.chat.id
    amount = call.data.split('get_')[1]
    username = call.from_user.username or call.from_user.first_name
    pay_link, invoice_id = get_pay_link(amount)
    if pay_link and invoice_id:
        invoices[chat_id] = invoice_id 
        logger.info(f"Инвойс создан для {username}: invoice_id={invoice_id}")
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(text=f"Оплатить {amount} TON", url=pay_link))
        markup.add(types.InlineKeyboardButton(text="Проверить оплату", callback_data=f'check_payment_{invoice_id}'))
        bot.send_message(chat_id, "Перейдите по ссылке для оплаты и нажмите 'Проверить'", reply_markup=markup)
    else:
        logger.error("Не удалось создать счет")
        bot.answer_callback_query(call.id, 'Ошибка: Не удалось создать счет.', show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data.startswith('check_payment_'))
def check_payment(call):
    chat_id = call.message.chat.id
    invoice_id = call.data.split('check_payment_')[1]
    username = call.from_user.username or call.from_user.first_name
    logger.debug(f"Проверка оплаты: chat_id={chat_id}, invoice_id={invoice_id}, username={username}")
    try:
        payment_status = check_payment_status(invoice_id)
        if payment_status and payment_status.get('ok'):
            if 'items' in payment_status['result']:
                invoice = next((inv for inv in payment_status['result']['items'] if str(inv['invoice_id']) == invoice_id), None)
                if invoice:
                    status = invoice['status']
                    logger.debug(f"Статус инвойса {invoice_id}: {status}")
                    if status == 'paid':
                        try:
                            hwid_key = generate_license()
                            sheet_success = append_license_to_sheet(hwid_key, username)
                            if sheet_success:
                                bot.send_message(chat_id, f"Оплата прошла!✅\n\nHWID-ключ:\n`{hwid_key}`\n\nСохраните его!🚀", parse_mode="Markdown")
                            else:
                                bot.send_message(chat_id, f"Оплата прошла!✅\n\nHWID-ключ:\n`{hwid_key}`\n\nСохраните его!🚀\n\n⚠️ Не удалось записать ключ в таблицу. Свяжитесь с @s3pt1ck.", parse_mode="Markdown")
                            logger.info(f"Оплата подтверждена, ключ выдан: {hwid_key} для {username}")
                            if chat_id in invoices:
                                del invoices[chat_id]
                            bot.answer_callback_query(call.id)
                        except Exception as key_error:
                            logger.error(f"Ошибка генерации ключа: {str(key_error)}")
                            bot.answer_callback_query(call.id, 'Ошибка: Не удалось сгенерировать ключ.', show_alert=True)
                    else:
                        logger.warning(f"Оплата не подтверждена: invoice_id={invoice_id}, статус: {status}")
                        bot.answer_callback_query(call.id, 'Оплата не найдена❌', show_alert=True)
                else:
                    logger.error(f"Счет не найден: invoice_id={invoice_id}")
                    bot.answer_callback_query(call.id, 'Счет не найден.', show_alert=True)
            else:
                logger.error(f"Ответ API без 'items': {payment_status}")
                bot.answer_callback_query(call.id, 'Ошибка статуса оплаты.', show_alert=True)
        else:
            logger.error(f"Ошибка API: {payment_status}")
            bot.answer_callback_query(call.id, 'Ошибка статуса оплаты.', show_alert=True)
    except Exception as e:
        logger.error(f"Критическая ошибка проверки оплаты: {str(e)}")
        bot.answer_callback_query(call.id, 'Критическая ошибка.', show_alert=True)

def get_pay_link(amount):
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_API_TOKEN}
    data = {
        "asset": "TON",
        "amount": amount,
        "description": "Valture License"
    }
    try:
        response = requests.post(f'{CRYPTO_BOT_API}/createInvoice', headers=headers, json=data, timeout=10)
        logger.debug(f"Создание инвойса: HTTP {response.status_code}, Ответ: {response.text}")
        if response.ok:
            response_data = response.json()
            logger.info(f"Инвойс создан: invoice_id={response_data['result']['invoice_id']}")
            return response_data['result']['pay_url'], response_data['result']['invoice_id']
        logger.error(f"Ошибка API: {response.status_code}, {response.text}")
        return None, None
    except Exception as e:
        logger.error(f"Ошибка создания инвойса: {e}")
        return None, None

def check_payment_status(invoice_id):
    headers = {
        "Crypto-Pay-API-Token": CRYPTOBOT_API_TOKEN,
        "Content-Type": "application/json"
    }
    logger.debug(f"Проверка инвойса: invoice_id={invoice_id}")
    try:
        response = requests.post(f'{CRYPTO_BOT_API}/getInvoices', headers=headers, json={}, timeout=10)
        logger.debug(f"Проверка инвойса {invoice_id}: HTTP {response.status_code}, Ответ: {response.text}")
        if response.ok:
            return response.json()
        logger.error(f"Ошибка API: {response.status_code}, {response.text}")
        return None
    except Exception as e:
        logger.error(f"Ошибка проверки инвойса: {e}")
        return None

if __name__ == '__main__':
    logger.info("Бот запущен")
    bot.polling(non_stop=True)
```
