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

# --- Настройки ---

TOKEN = os.environ.get("BOT_TOKEN", '7941872387:AAGZayILmna-qHHyQy5V50wDGylo3yFCZ0A')
CRYPTOBOT_API_TOKEN = os.environ.get("CRYPTOBOT_API_TOKEN", '406690:AA0uW0MoZHwZ1CnAvw1zn3lcx7lNKnbT24w')
CREDS_FILE = os.environ.get("CREDS_FILE", "creds.json")
SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME", "Valture_Licenses")
GOOGLE_CREDS_JSON_BASE64 = os.environ.get("GOOGLE_CREDS_JSON_BASE64")
TEST_PAYMENT_AMOUNT = 0.01  # Цена для тестового раздела в TON

SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# CryptoBot API endpoint
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
    """Декодирование base64-креденшлов Google и создание временного файла."""
    logger.debug("Проверка Google credentials...")
    if GOOGLE_CREDS_JSON_BASE64:
        try:
            creds_json = base64.b64decode(GOOGLE_CREDS_JSON_BASE64).decode("utf-8")
            with open(CREDS_FILE, "w") as f:
                f.write(creds_json)
            logger.info("Google credentials успешно декодированы и сохранены во временный файл")
        except Exception as e:
            logger.error(f"Ошибка при декодировании Google credentials: {str(e)}", exc_info=True)
            raise
    elif not os.path.exists(CREDS_FILE):
        logger.error("Файл Google credentials не найден, и GOOGLE_CREDS_JSON_BASE64 не задан")
        raise FileNotFoundError("Файл Google credentials не найден, и GOOGLE_CREDS_JSON_BASE64 не задан")
    else:
        logger.info("Используется существующий файл Google credentials")

def get_sheet():
    """Получение кэшированного объекта Google Sheets."""
    global sheet_cache
    if sheet_cache is None:
        try:
            setup_google_creds()
            creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPE)
            client = gspread.authorize(creds)
            sheet_cache = client.open(SPREADSHEET_NAME).sheet1
            logger.info("Успешно подключено к Google Sheets")
        except Exception as e:
            logger.error(f"Ошибка подключения к Google Sheets: {str(e)}", exc_info=True)
            raise
    return sheet_cache

def generate_license(length=32):
    """Генерация безопасного HWID-ключа."""
    try:
        key = ''.join(secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') for _ in range(length))
        logger.info(f"Сгенерирован HWID-ключ: {key}")
        return key
    except Exception as e:
        logger.error(f"Ошибка при генерации ключа: {str(e)}", exc_info=True)
        raise

def append_license_to_sheet(license_key, username):
    """Добавление HWID-ключа в Google Sheets."""
    try:
        sheet = get_sheet()
        utc_plus_2 = timezone(timedelta(hours=2))
        now_utc_plus_2 = datetime.now(utc_plus_2)
        now_str = now_utc_plus_2.strftime("%Y-%m-%d %H:%M:%S")
        sheet.append_row([license_key, "", username, now_str])
        logger.info(f"HWID-ключ {license_key} добавлен для {username}")
    except Exception as e:
        logger.error(f"Ошибка при добавлении HWID-ключа: {str(e)}", exc_info=True)
        raise

# --- Логика бота ---

@bot.message_handler(commands=['start'])
def welcome(message):
    markup = types.InlineKeyboardMarkup()
    get_button = types.InlineKeyboardButton(text="Оплатить", callback_data=f'get_{TEST_PAYMENT_AMOUNT}')
    markup.add(get_button)
    bot.send_message(message.chat.id, "Добро пожаловать! Нажмите кнопку ниже, чтобы купить данный товар.", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('get_'))
def get_invoice(call):
    chat_id = call.message.chat.id
    amount = call.data.split('get_')[1]
    username = call.from_user.username or call.from_user.first_name
    pay_link, invoice_id = get_pay_link(amount)
    if pay_link and invoice_id:
        invoices[chat_id] = invoice_id 
        logger.info(f"Тестовый инвойс создан для {username}: invoice_id={invoice_id}")
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(text=f"Оплатить {amount} TON", url=pay_link))
        markup.add(types.InlineKeyboardButton(text="Проверить оплату", callback_data=f'check_payment_{invoice_id}'))
        bot.send_message(chat_id, "Перейдите по этой ссылке для оплаты и нажмите 'Проверить оплату'", reply_markup=markup)
    else:
        logger.error("Не удалось создать тестовый счет на оплату")
        bot.answer_callback_query(call.id, 'Ошибка: Не удалось создать счет на оплату.', show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data.startswith('check_payment_'))
def check_payment(call):
    chat_id = call.message.chat.id
    invoice_id = call.data.split('check_payment_')[1]
    username = call.from_user.username or call.from_user.first_name

    logger.debug(f"Начало проверки тестовой оплаты: chat_id={chat_id}, invoice_id={invoice_id}, username={username}")
    try:
        payment_status = check_payment_status(invoice_id)
        logger.debug(f"Результат проверки тестового инвойса: {payment_status}")
        if payment_status and payment_status.get('ok'):
            if 'items' in payment_status['result']:
                invoice = next((inv for inv in payment_status['result']['items'] if str(inv['invoice_id']) == invoice_id), None)
                if invoice:
                    status = invoice['status']
                    logger.debug(f"Статус тестового инвойса {invoice_id}: {status}")
                    if status == 'paid':
                        try:
                            hwid_key = generate_license()
                            try:
                                append_license_to_sheet(hwid_key, username)
                                logger.info(f"Успешно записан HWID-ключ {hwid_key} в Google Sheets для {username}")
                            except Exception as sheet_error:
                                logger.error(f"Не удалось записать HWID-ключ в Google Sheets: {str(sheet_error)}", exc_info=True)
                                bot.send_message(chat_id, f"Оплата прошла успешно!✅\n\nВаш HWID-ключ:\n`{hwid_key}`\n\nСохраните его в надежном месте! 🚀\n\n⚠️ Внимание: Не удалось записать ключ в таблицу. Свяжитесь с @s3pt1ck.", parse_mode="Markdown")
                            else:
                                bot.send_message(chat_id, f"Оплата прошла успешно!✅\n\nВаш HWID-ключ:\n`{hwid_key}`\n\nСохраните его в надежном месте! 🚀", parse_mode="Markdown")
                            logger.info(f"Тестовая оплата подтверждена, HWID-ключ выдан: {hwid_key} для {username}")
                            if chat_id in invoices:
                                del invoices[chat_id]
                            bot.answer_callback_query(call.id)
                        except Exception as key_error:
                            logger.error(f"Ошибка при генерации HWID-ключа: {str(key_error)}", exc_info=True)
                            bot.answer_callback_query(call.id, 'Ошибка: Не удалось сгенерировать HWID-ключ.', show_alert=True)
                    else:
                        logger.warning(f"Тестовая оплата не подтверждена: invoice_id={invoice_id}, статус: {status}")
                        bot.answer_callback_query(call.id, 'Оплата не найдена❌', show_alert=True)
                else:
                    logger.error(f"Тестовый счет не найден для invoice_id={invoice_id}")
                    bot.answer_callback_query(call.id, 'Счет не найден.', show_alert=True)
            else:
                logger.error(f"Ответ API не содержит ключа 'items': {payment_status}")
                bot.answer_callback_query(call.id, 'Ошибка при получении статуса оплаты.', show_alert=True)
        else:
            logger.error(f"Ошибка API или неверный ответ: {payment_status}")
            bot.answer_callback_query(call.id, 'Ошибка при получении статуса оплаты.', show_alert=True)
    except Exception as e:
        logger.error(f"Критическая ошибка при проверке оплаты: {str(e)}", exc_info=True)
        bot.answer_callback_query(call.id, 'Критическая ошибка при проверке оплаты.', show_alert=True)

def get_pay_link(amount):
    """Создание инвойса через CryptoBot."""
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_API_TOKEN}
    data = {
        "asset": "TON",
        "amount": amount,
        "description": "Valture License"
    }
    try:
        response = requests.post(f'{CRYPTO_BOT_API}/createInvoice', headers=headers, json=data, timeout=10)
        logger.debug(f"Создание тестового инвойса: HTTP статус: {response.status_code}, Ответ: {response.text}")
        if response.ok:
            response_data = response.json()
            logger.info(f"Тестовый инвойс создан: invoice_id={response_data['result']['invoice_id']}")
            return response_data['result']['pay_url'], response_data['result']['invoice_id']
        logger.error(f"Ошибка API при создании тестового инвойса: {response.status_code}, {response.text}")
        return None, None
    except Exception as e:
        logger.error(f"Ошибка при создании тестового инвойса: {e}")
        return None, None

def check_payment_status(invoice_id):
    """Проверка статуса инвойса."""
    headers = {
        "Crypto-Pay-API-Token": CRYPTOBOT_API_TOKEN,
        "Content-Type": "application/json"
    }
    logger.debug(f"Начало проверки тестового инвойса: invoice_id={invoice_id}")
    try:
        response = requests.post(f'{CRYPTO_BOT_API}/getInvoices', headers=headers, json={}, timeout=10)
        logger.debug(f"Проверка тестового инвойса {invoice_id}: HTTP статус: {response.status_code}, Ответ: {response.text}")
        if response.ok:
            return response.json()
        logger.error(f"Ошибка API при проверке тестового инвойса: {response.status_code}, {response.text}")
        return None
    except Exception as e:
        logger.error(f"Ошибка при проверке тестового инвойса: {e}")
        return None

if __name__ == '__main__':
    logger.info("Valture бот запущен")
    bot.polling(non_stop=True)
