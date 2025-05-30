import os
import logging
import secrets
import requests
import base64
import json
from datetime import datetime, timezone, timedelta
from threading import Thread
from uuid import uuid4

from flask import Flask, request, jsonify
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

import gspread
from google.oauth2.service_account import Credentials
from yookassa import Configuration, Payment

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CRYPTOBOT_API_TOKEN = os.environ.get("CRYPTOBOT_API_TOKEN")
YOOKASSA_SHOP_ID = os.environ.get("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.environ.get("YOOKASSA_SECRET_KEY")
CREDS_FILE = os.environ.get("CREDS_FILE", "creds.json")
SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME", "Valture_Licenses")
GOOGLE_CREDS_JSON_BASE64 = os.environ.get("GOOGLE_CREDS_JSON_BASE64")
EXE_FILE_PATH = os.environ.get("EXE_FILE_PATH", "valture_app.exe")
APP_DOWNLOAD_LINK = os.environ.get("APP_DOWNLOAD_LINK", "https://www.dropbox.com/scl/fi/ze5ebd909z2qeaaucn56q/VALTURE.exe?rlkey=ihdzk8voej4oikrdhq0wfzvbb&st=jj5tgroa&dl=1")
SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
ADMIN_IDS = [123456789]

PRICES = {
    "crypto_ton": 4.0,
    "yookassa_rub": 1000.0,
    "usd_equivalent": 12.7
}

CRYPTO_BOT_API = "https://pay.crypt.bot/api"

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан в переменных окружения")
if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET_KEY:
    logging.warning("YooKassa credentials не заданы, оплата через YooKassa не будет работать")
else:
    Configuration.account_id = YOOKASSA_SHOP_ID
    Configuration.secret_key = YOOKASSA_SECRET_KEY
if not CRYPTOBOT_API_TOKEN:
    logging.warning("CRYPTOBOT_API_TOKEN не задан, оплата через CryptoBot не будет работать")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.DEBUG,
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Valture бот работает!"

@app.route('/test-crypto-api')
def test_crypto_api():
    try:
        headers = {"Crypto-Pay-API-Token": CRYPTOBOT_API_TOKEN}
        response = requests.get(f"{CRYPTO_BOT_API}/getMe", headers=headers, timeout=10)
        return f"API Response: {response.json()}"
    except Exception as e:
        return f"Error: {str(e)}"

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
                logger.error(f"Missing metadata in webhook: user_id={user_id}, username={username}")
                return jsonify({"status": "error", "message": "Missing metadata"}), 400

            from application import application
            job_queue = application.job_queue
            job_queue.run_once(
                process_yookassa_payment,
                0,
                context={
                    'payment_id': payment_id,
                    'user_id': int(user_id),
                    'username': username,
                    'chat_id': int(user_id)
                },
                name=f"yookassa_payment_{payment_id}"
            )
            logger.info(f"YooKassa payment succeeded: payment_id={payment_id}, user={username}")
            return jsonify({"status": "ok"}), 200
        
        elif event == 'payment.canceled':
            logger.warning(f"YooKassa payment canceled: payment_id={payment_object['id']}")
            return jsonify({"status": "ok"}), 200
        
        return jsonify({"status": "ignored"}), 200
    
    except Exception as e:
        logger.error(f"Error in YooKassa webhook: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

async def process_yookassa_payment(context: ContextTypes.DEFAULT_TYPE):
    job_context = context.job.context
    payment_id = job_context['payment_id']
    user_id = job_context['user_id']
    username = job_context['username']
    chat_id = job_context['chat_id']

    try:
        payment = Payment.find_one(payment_id)
        if payment.status != 'succeeded':
            logger.warning(f"YooKassa payment {payment_id} not succeeded, status: {payment.status}")
            await context.bot.send_message(
                chat_id=chat_id,
                text="⏳ Оплата ещё не подтверждена.\n\nПопробуйте /query_payment или свяжитесь с @s3pt1ck.",
                parse_mode="Markdown"
            )
            return

        license_key = generate_license()
        append_license_to_sheet(license_key, username)
        text = (
            "🎉 *Поздравляем с покупкой!*\n\n"
            f"📥 Скачайте приложение Valture: [Скачать]({APP_DOWNLOAD_LINK})\n\n"
            "⚠️ Перед запуском проверьте файл антивирусом.\n\n"
            "Ваш HWID-ключ:\n"
            f"`{license_key}`\n\n"
            "Сохраните его в надёжном месте! 🚀"
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
        
        try:
            if os.path.exists(EXE_FILE_PATH):
                file_size = os.path.getsize(EXE_FILE_PATH) / (1024 * 1024)
                if file_size < 50:
                    with open(EXE_FILE_PATH, "rb") as file:
                        await context.bot.send_document(
                            chat_id=chat_id,
                            document=file,
                            filename="Valture.exe",
                            caption="📥 Вот ваше приложение Valture!"
                        )
                    logger.info(f"Файл {EXE_FILE_PATH} отправлен пользователю {username}")
                else:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text="📥 Файл слишком большой для отправки. Используйте ссылку выше.",
                        parse_mode="Markdown"
                    )
                    logger.warning(f"Файл {EXE_FILE_PATH} слишком большой: {file_size} МБ")
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="❌ Не удалось найти приложение на сервере. Используйте ссылку выше или свяжитесь с @s3pt1ck.",
                    parse_mode="Markdown"
                )
                logger.error(f"Файл {EXE_FILE_PATH} не найден")
        except Exception as e:
            logger.error(f"Ошибка при отправке файла {EXE_FILE_PATH}: {e}", exc_info=True)
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Не удалось отправить приложение. Используйте ссылку выше или свяжитесь с @s3pt1ck.",
                parse_mode="Markdown"
            )

        logger.info(f"YooKassa payment processed, HWID key issued: {license_key} для {username}")
    except Exception as e:
        logger.error(f"Ошибка обработки YooKassa платежа {payment_id}: {e}", exc_info=True)
        error_text = (
            "❌ *Произошла ошибка!*\n\n"
            "Не удалось выдать ключ и приложение. Попробуйте /query_payment или обратитесь в поддержку: @s3pt1ck."
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=error_text,
            parse_mode="Markdown"
        )

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

def setup_google_creds():
    logger.debug("Проверка Google credentials...")
    if GOOGLE_CREDS_JSON_BASE64:
        try:
            creds_json = base64.b64decode(GOOGLE_CREDS_JSON_BASE64).decode("utf-8")
            with open(CREDS_FILE, "w") as f:
                f.write(creds_json)
            logger.info("Google credentials успешно декодированы и сохранены во временный файл")
        except Exception as e:
            logger.error(f"Ошибка при декодировании Google credentials: {e}", exc_info=True)
            raise
    elif not os.path.exists(CREDS_FILE):
        logger.error("Файл Google credentials не найден, и GOOGLE_CREDS_JSON_BASE64 не задан")
        raise FileNotFoundError("Файл Google credentials не найден")
    else:
        logger.info("Используется существующий файл Google credentials")

sheet_cache = None

def get_sheet():
    global sheet_cache
    if sheet_cache is None:
        try:
            setup_google_creds()
            creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPE)
            client = gspread.authorize(creds)
            sheet_cache = client.open(SPREADSHEET_NAME).sheet1
            logger.info("Успешно подключено к Google Sheets")
        except Exception as e:
            logger.error(f"Ошибка подключения к Google Sheets: {e}", exc_info=True)
            raise
    return sheet_cache

def generate_license(length=16):
    try:
        key = ''.join(secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') for _ in range(length))
        logger.info(f"Сгенерирован ключ: {key}")
        return key
    except Exception as e:
        logger.error(f"Ошибка при генерации ключа: {e}", exc_info=True)
        raise

def append_license_to_sheet(license_key, username):
    try:
        sheet = get_sheet()
        utc_plus_2 = timezone(timedelta(hours=2))
        now_utc_plus_2 = datetime.now(utc_plus_2)
        now_str = now_utc_plus_2.strftime("%Y-%m-%d %H:%M:%S")
        sheet.append_row([license_key, "", username, now_str])
        logger.info(f"Лицензия {license_key} добавлена для {username}")
    except Exception as e:
        logger.error(f"Ошибка при добавлении лицензии: {e}", exc_info=True)
        raise

def create_crypto_invoice(amount=4.0, asset="TON", description="Valture License"):
    logger.debug(f"Создание инвойса: amount={amount}, asset={asset}, description={description}")
    if not CRYPTOBOT_API_TOKEN:
        logger.error("CRYPTOBOT_API_TOKEN не задан в переменных окружения")
        return None, "CRYPTOBOT_API_TOKEN не задан"

    try:
        payload = {
            "amount": f"{amount:.8f}",
            "asset": asset,
            "description": description,
            "order_id": secrets.token_hex(16),
        }
        headers = {
            "Crypto-Pay-API-Token": CRYPTOBOT_API_TOKEN,
            "Content-Type": "application/json"
        }
        logger.debug(f"Отправка запроса на {CRYPTO_BOT_API}/createInvoice с payload: {payload}")
        
        response = requests.post(f"{CRYPTO_BOT_API}/createInvoice", json=payload, headers=headers, timeout=10)
        logger.debug(f"HTTP статус: {response.status_code}, Ответ: {response.text}")
        
        response.raise_for_status()
        data = response.json()
        
        if data.get("ok"):
            invoice = data["result"]
            logger.info(f"Инвойс успешно создан: invoice_id={invoice['invoice_id']}, pay_url={invoice.get('pay_url')}")
            return invoice, None
        else:
            error_msg = data.get("error", "Неизвестная ошибка от CryptoBot")
            logger.error(f"Ошибка API CryptoBot: {error_msg}")
            return None, f"Ошибка API: {error_msg}"
            
    except requests.exceptions.HTTPError as http_err:
        logger.error(f"HTTP ошибка при создании инвойса: {http_err}, Ответ: {response.text}", exc_info=True)
        return None, f"HTTP ошибка: {http_err}"
    except requests.exceptions.Timeout:
        logger.error("Тайм-аут при обращении к CryptoBot API", exc_info=True)
        return None, "Тайм-аут запроса к CryptoBot API"
    except requests.exceptions.RequestException as req_err:
        logger.error(f"Сетевая ошибка при создании инвойса: {req_err}", exc_info=True)
        return None, f"Сетевая ошибка: {req_err}"
    except Exception as e:
        logger.error(f"Общая ошибка при создании инвойса: {e}", exc_info=True)
        return None, f"Общая ошибка: {e}"

def check_invoice_status(invoice_id):
    logger.debug(f"Проверка статуса инвойса: invoice_id={invoice_id}")
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
            logger.error(f"Ошибка проверки статуса инвойса: {data.get('error', 'Неизвестная ошибка')}")
            return None
    except requests.exceptions.HTTPError as http_err:
        logger.error(f"HTTP ошибка при проверке инвойса: {http_err}, Ответ: {response.text}", exc_info=True)
        return None
    except requests.exceptions.RequestException as req_err:
        logger.error(f"Сетевая ошибка при проверке инвойса: {req_err}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"Общая ошибка при проверке инвойса: {e}", exc_info=True)
        return None

def create_yookassa_payment(amount, description, user_id, username):
    logger.debug(f"Создание YooKassa платежа: amount={amount}, description={description}, user_id={user_id}")
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
        logger.error(f"Ошибка при создании YooKassa платежа: {e}", exc_info=True)
        return None, f"YooKassa ошибка: {str(e)}"

async def check_yookassa_payment(context: ContextTypes.DEFAULT_TYPE):
    job_context = context.job.context
    payment_id = job_context['payment_id']
    user_id = job_context['user_id']
    username = job_context['username']
    chat_id = job_context['chat_id']
    attempts = job_context.get('attempts', 0)

    try:
        payment = Payment.find_one(payment_id)
        logger.debug(f"Проверка YooKassa платежа {payment_id}, статус: {payment.status}")
        if payment.status == 'succeeded':
            license_key = generate_license()
            append_license_to_sheet(license_key, username)
            text = (
                "🎉 *Поздравляем с покупкой!*\n\n"
                f"📥 Скачайте приложение Valture: [Скачать]({APP_DOWNLOAD_LINK})\n\n"
                "⚠️ Перед запуском проверьте файл антивирусом.\n\n"
                "Ваш HWID-ключ:\n"
                f"`{license_key}`\n\n"
                "Сохраните его в надежном месте! 🚀"
            )
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
            
            try:
                if os.path.exists(EXE_FILE_PATH):
                    file_size = os.path.getsize(EXE_FILE_PATH) / (1024 * 1024)
                    if file_size < 50:
                        with open(EXE_FILE_PATH, "rb") as file:
                            await context.bot.send_document(
                                chat_id=chat_id,
                                document=file,
                                filename="Valture.exe",
                                caption="📥 Вот ваше приложение Valture!"
                            )
                        logger.info(f"Файл {EXE_FILE_PATH} отправлен пользователю {username}")
                    else:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text="📥 Файл слишком большой для отправки. Используйте ссылку выше.",
                            parse_mode="Markdown"
                        )
                        logger.warning(f"Файл {EXE_FILE_PATH} слишком большой: {file_size} МБ")
                else:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text="❌ Не удалось найти приложение на сервере. Используйте ссылку выше или свяжитесь с @s3pt1ck.",
                        parse_mode="Markdown"
                    )
                    logger.error(f"Файл {EXE_FILE_PATH} не найден")
            except Exception as e:
                logger.error(f"Ошибка при отправке файла {EXE_FILE_PATH}: {e}", exc_info=True)
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="❌ Не удалось отправить приложение. Используйте ссылку выше или свяжитесь с @s3pt1ck.",
                    parse_mode="Markdown"
                )

            logger.info(f"YooKassa payment {payment_id} confirmed, HWID key issued: {license_key} для {username}")
            return
        elif payment.status == 'canceled':
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ *Оплата была отменена*\n\nПопробуйте снова или свяжитесь с @s3pt1ck.",
                parse_mode="Markdown"
            )
            logger.info(f"YooKassa payment {payment_id} canceled")
            return
        else:
            attempts += 1
            if attempts < 15:
                context.job_queue.run_once(
                    check_yookassa_payment,
                    12,
                    context={**job_context, 'attempts': attempts},
                    name=f"check_yookassa_{payment_id}_{attempts}"
                )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="⏳ *Оплата еще не подтверждена*\n\nПопробуйте /query_payment позже или свяжитесь с @s3pt1ck.",
                    parse_mode="Markdown"
                )
                logger.warning(f"YooKassa payment {payment_id} not confirmed after 15 attempts")
    except Exception as e:
        logger.error(f"Ошибка при проверке YooKassa платежа {payment_id}: {e}", exc_info=True)
        await context.bot.send_message(
            chat_id=chat_id,
            text="❌ *Ошибка при проверке оплаты*\n\nПопробуйте /query_payment или свяжитесь с @s3pt1ck.",
            parse_mode="Markdown"
        )

async def check_crypto_payment(context: ContextTypes.DEFAULT_TYPE):
    job_context = context.job.context
    invoice_id = job_context['invoice_id']
    user_id = job_context['user_id']
    username = job_context['username']
    chat_id = job_context['chat_id']
    attempts = job_context.get('attempts', 0)

    try:
        status = check_invoice_status(invoice_id)
        logger.debug(f"Проверка CryptoBot инвойса: {invoice_id}, status: {status}")
        if status == 'paid':
            license_key = generate_license()
            append_license_to_sheet(license_key, username)
            text = (
                "🎉 *Поздравляем с покупкой!*\n\n"
                f"📥 Скачайте приложение Valture: [Скачать]({APP_DOWNLOAD_LINK})\n\n"
                "⚠️ Перед запуском проверьте файл антивирусом.\n\n"
                "Ваш HWID-ключ:\n"
                f"`{license_key}`\n\n"
                "Сохраните его в надежном месте! 🚀"
            )
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
            
            try:
                if os.path.exists(EXE_FILE_PATH):
                    file_size = os.path.getsize(EXE_FILE_PATH) / (1024 * 1024)
                    if file_size < 50:
                        with open(EXE_FILE_PATH, "rb") as file:
                            await context.bot.send_document(
                                chat_id=chat_id,
                                document=file,
                                filename="Valture.exe",
                                caption="📥 Вот ваше приложение Valture!"
                            )
                        logger.info(f"Файл {EXE_FILE_PATH} отправлен пользователю {username}")
                    else:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text="📥 Файл слишком большой для отправки. Используйте ссылку выше.",
                            parse_mode="Markup"
                        )
                        logger.warning(f"Файл {EXE_FILE_PATH} слишком большой: {file_size} МБ")
                else:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text="❌ Не удалось найти приложение на сервере. Используйте ссылку выше или свяжитесь с @s3pt1ck.",
                        parse_mode="Markdown"
                    )
                    logger.error(f"Файл {EXE_FILE_PATH} не найден")
            except Exception as e:
                logger.error(f"Ошибка при отправке файла {EXE_FILE_PATH}: {e}", exc_info=True)
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="❌ Не удалось отправить приложение. Используйте ссылку выше или свяжитесь с @s3pt1ck.",
                    parse_mode="Markup"
                )

            logger.info(f"CryptoBot payment {invoice_id} confirmed, HWID key issued: {license_key} для {username}")
            return
        elif status in ['expired', 'failed']:
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ *Оплата была отменена или истекла*\n\nПопробуйте снова или свяжитесь с @s3pt1ck.",
                parse_mode="Markdown"
            )
            logger.warning(f"CryptoBot payment {invoice_id} {status}")
            return
        else:
            attempts += 1
            if attempts < 15:
                context.job_queue.run_once(
                    check_crypto_payment,
                    12,
                    context={**job_context, 'attempts': attempts},
                    name=f"check_crypto_{invoice_id}_{attempts}"
                )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="⏳ *Оплата еще не подтверждена*\n\nПопробуйте /query_payment позже или свяжитесь с @s3pt1ck.",
                    parse_mode="Markdown"
                )
                logger.warning(f"CryptoBot payment {invoice_id} not confirmed after 15 attempts")
    except Exception as e:
        logger.error(f"Ошибка при проверке CryptoBot инвойса {invoice_id}: {e}", exc_info=True)
        await context.bot.send_message(
            chat_id=chat_id,
            text="❌ *Ошибка при проверке оплаты*\n\nПопробуйте /query_payment или свяжитесь с @s3pt1ck.",
            parse_mode="Markdown"
        )

async def query_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    username = update.message.from_user.username or update.message.from_user.full_name
    chat_id = update.message.chat_id
    args = context.args

    payment_type = context.user_data.get("payment_type")
    payment_id = context.user_data.get("payment_id") or context.user_data.get("invoice_id")

    if args and len(args) == 1:
        payment_id = args[0]
        payment_type = "yookassa"
        logger.info(f"Ручной ввод payment_id: {payment_id} пользователем {username}")

    if not payment_type or not payment_id:
        await update.message.reply_text(
            "❌ *Нет данных об оплате*\n\n"
            "Если у вас есть ID платежа, используйте: /query_payment <ID_платежа>\n"
            "Или свяжитесь с @s3pt1ck.",
            parse_mode="Markdown"
        )
        return

    try:
        if payment_type == "yookassa":
            payment = Payment.find_one(payment_id)
            if payment.status == 'succeeded':
                license_key = generate_license()
                append_license_to_sheet(license_key, username)
                text = (
                    "🎉 *Поздравляем с покупкой!*\n\n"
                    f"📥 Скачайте приложение Valture: [Скачать]({APP_DOWNLOAD_LINK})\n\n"
                    "⚠️ Перед запуском проверьте файл антивирусом.\n\n"
                    "Ваш HWID-ключ:\n"
                    f"`{license_key}`\n\n"
                    "Сохраните его в надежном месте! 🚀"
                )
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
                
                try:
                    if os.path.exists(EXE_FILE_PATH):
                        file_size = os.path.getsize(EXE_FILE_PATH) / (1024 * 1024)
                        if file_size < 50:
                            with open(EXE_FILE_PATH, "rb") as file:
                                await context.bot.send_document(
                                    chat_id=chat_id,
                                    document=file,
                                    filename="Valture.exe",
                                    caption="📥 Вот ваше приложение Valture!"
                                )
                            logger.info(f"Файл {EXE_FILE_PATH} отправлен пользователю {username}")
                        else:
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text="📥 Файл слишком большой для отправки. Используйте ссылку выше.",
                                parse_mode="Markdown"
                            )
                            logger.warning(f"Файл {EXE_FILE_PATH} слишком большой: {file_size} МБ")
                    else:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text="❌ Не удалось найти приложение на сервере. Используйте ссылку выше или свяжитесь с @s3pt1ck.",
                            parse_mode="Markdown"
                        )
                        logger.error(f"Файл {EXE_FILE_PATH} не найден")
                except Exception as e:
                    logger.error(f"Ошибка при отправке файла {EXE_FILE_PATH}: {e}", exc_info=True)
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text="❌ Не удалось отправить приложение. Используйте ссылку выше или свяжитесь с @s3pt1ck.",
                        parse_mode="Markdown"
                    )

                logger.info(f"YooKassa payment {payment_id} confirmed via /query_payment, HWID key issued: {license_key}")
                context.user_data.clear()
            else:
                await update.message.reply_text(
                    f"⏳ *Оплата еще не подтверждена*\n\nСтатус: {payment.status}\nПопробуйте /query_payment позже или свяжитесь с @s3pt1ck.",
                    parse_mode="Markdown"
                )
        elif payment_type == "crypto":
            status = check_invoice_status(payment_id)
            if status == 'paid':
                license_key = generate_license()
                append_license_to_sheet(license_key, username)
                text = (
                    "🎉 *Поздравляем с покупкой!*\n\n"
                    f"📥 Скачайте приложение Valture: [Скачать]({APP_DOWNLOAD_LINK})\n\n"
                    "⚠️ Перед запуском проверьте файл антивирусом.\n\n"
                    "Ваш HWID-ключ:\n"
                    f"`{license_key}`

                    "\n\n"
                    "Сохраните его в надежном месте! 🚖"
                )
                await context.bot.send_message(
                    chat_id,
                    text=text,
                    parse_mode="markdown",
                    disable_web_page_preview=True
                )

                try:
                    if os.path.exists(EXE_FILE_PATH):
                        file_size = os.path.getsize(EXE_FILE_PATH) / (1024 * 1024)
                        if file_size < 50:
                            with open(EXE_FILE_PATH, "rb") as file:
                                await context.bot.send_document(
                                    chat_id=chat_id,
                                    document=file,
                                    filename="Valture.exe",
                                    caption="📖 Скачайте ваше приложение Valture!"
                                )
                            logger.info(f"Файл {EXE_FILE_PATH} отправлен пользователю {username}")
                        else:
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text="📖 Файл слишком большой для отправки. Используйте ссылку выше.",
                                parse_mode="Markdown"
                            )
                            logger.warning(f"Файл {EXE_FILE_PATH} слишком большой: {file_size} МБ")
                    else:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text="❌ Не удалось найти приложение на сервере. Используйте ссылку выше или свяжитесь с @s3pt1ck.",
                            parse_mode="Markdown"
                        )
                        logger.error(f"Файл {EXE_FILE_PATH} не найден")
                except Exception as e:
                    logger.error(f"Ошибка при отправке файла {EXE_FILE_PATH}: {e}", exc_info=True)
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text="❌ Не удалось отправить приложение. Используйте ссылку выше или свяжитесь с @s3pt1ck.",
                        parse_mode="Markdown"
                    )

                logger.info(f"CryptoBot payment {payment_id} confirmed via /query_payment, HWID key issued: {license_key}")
                context.user_data.clear()
            else:
                await update.message.reply_text(
                    f"⏳ *Оплата еще не подтверждена*\n\nСтатус: {status or 'неизвестен'}\nПопробуйте /query_payment позже или свяжитесь с @s3pt1ck.",
                    parse_mode="Markdown"
                )
    except Exception as e:
        logger.error(f"Ошибка при обработке /query_payment для {payment_id}: {e}", exc_info=True)
        await update.message.reply_text(
            "❌ *Ошибка при проверке оплаты*\n\nСвяжитесь с @s3pt1ck для поддержки.",
            parse_mode="Markdown"
        )

async def check_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in ADMIN_IDS:
        logger.error(f"Несанкционированный доступ к логам: ID {user_id}")
        await update.message.reply_text(
            "⚠️ *Доступ запрещен*\n\nЭта команда доступна только администраторам.",
            parse_mode="Markdown"
        )
        return

    try:
        with open("bot.log", "r") as log_file:
            lines = log_file.readlines()
            payment_logs = [line for line in lines[-100:] if "payment" in line.lower() or "webhook" in line.lower()]
            log_text = "".join(payment_logs[-50:])
            if not log_text:
                log_text = "Нет недавних записей платежных логов."
        await update.message.reply_text(
            f"📜 *Последние логи платежей:\n\n{log_text}",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Ошибка при чтении логов: {e}")
        await update.message.reply_text(
            "❌ *Не удалось получить доступ к логам*\n\nСвяжитесь с @s3pt1ck.",
            parse_mode="Markdown"
        )

def get_keyboard(buttons):
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=callback)] for text, callback in buttons])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🎮 *Добро пожаловать в Valture!*\n\n"
        "Ваш лучший инструмент для игр! 🚖\n\n"
        "Выберите опцию ниже, чтобы начать:"
        
    )
    buttons = [
        ("🏖 Главное меню", "menu_main"),
    ]
    await update.message.reply_text(
        text=text,
        parse_mode="markdown",
        reply_markup=get_keyboard(buttons)

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    buttons = [
        ("ℹ️ О нас", "menu_about"),
        ("🗞 Новости", "news_info"),
        ("💳 Оплатить", "menu_pay"),
        ("❓ FAQ", "menu_faq"),
        ("📞 Поддержка", "menu_support"),
    ]
    text = (
        "🏖️ *Главное меню*\n\n"
        "Выберите раздел:"
    )
    
    await query.edit_message_text(
        text=text,
        parse_mode="markdown",
        reply_markup=get_keyboard(buttons)
    )

async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    text = (
        "✨ *Valture — Ваш путь к совершенству!*\n\n"
        "➖➖➖⬛️➖️⬜️"
        "Valture — это инновационный инструмент для геймеров, которые не готовы идти на компромиссы. "
        "Мы стремимся вывести вашу производительность на новый уровень, обеспечивая максимальную плавность, "
        "стабильность и отзывчивость системы. С Valture вы получите конкурентное преимущество, о котором мечтите!\n\n"
        "🔥 *Почему выбирают нас?*\n\n"
        "- 🚖 *Увеличение FPS на 20–30%*: Оптимизация системы для максимальной производительности.\n"
        "- ✔️ *Стабильный фреймрейт*: Никаких лагов и просадок FPS.\n"
        "- ❓ *Мгновенная реакция*: Минимальное время отклика для мгновенных действий.\n"
        "- 💡 *Оптимизация Windows*: Полная настройка ОС для игр.\n"
        "- 🖐 *Плавное управление*: Улучшенная точность мыши.\n"
        "- 🖥 *Плавная картинка*: Четкая и плавная графика.\n"
        "➖➖\n"
        "_Создано для геймеров, стремящихся к победам._"
    )
    buttons = [
        ("🔙 Назад в меню", "menu_main"),
    ]
    await query.edit_message_text(
        text=text,
        parse_mode="markdown",
        reply_markup=get_keyboard(buttons)
    )

async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "💸 *Покупка лицензии Valture*\n\n"
        f"Стоимость: *{PRICES['crypto_ton']} TON* или *{PRICES['yookassa_rub']} RUB (~${PRICES['usd_equivalent']})*\n"
        "Выберите способ оплаты:\n"
        "- *CryptoBot*: оплата криптовалютой.\n"
        "- *YooKassa*: оплата картой.\n\n"
        "Ключ и приложение будут выданы автоматически после оплаты."
    )
    buttons = [
        ("💸 CryptoBot", "pay_crypto"),
        ("💳 YooKassa", "pay_yookassa"),
        ("🔙 Назад в меню", "menu_main"),
    ]
    await query.edit_message_text(
        text,
        parse_mode="markdown",
        reply_markup=get_keyboard(buttons)
    )

async def pay_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "💸 *Подтверждение оплаты CryptoBot*\n\n"
        f"Вы собираетесь оплатить *{PRICES['crypto_ton']} TON* за лицензию Valture.\n\n"
        "На странице оплаты вы сможете выбрать любую криптовалюту.\n\n"
        "Продолжить оплату?"
        
    )
    buttons = [
        ("✅ Подтвердить оплату", "pay_crypto_confirm"),
        ("🔙 Назад к способам оплаты", "pay"),
    ]
    await query.edit_message_text(
        text=text,
        parse_mode="markdown",
        reply_markup=get_keyboard(buttons)
    async def pay_crypto_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.full_name

    try:
        logger.debug(f"Создание CryptoBot платежа для пользователя: {username} (ID: {user_id})")
        invoice, error = create_crypto_payment(amount=PRICES['crypto_ton'], asset="TON", description="Valture License")
        if not invoice or "pay_url" not not invoice:
            error_msg = (
                "❌ *Ошибка при создании платежа!*!\n\n"
                f"Не удалось создать инвойс: {error or 'Неизвестная ошибка'}.\n\n"
                "Попробуйте снова или свяжитесь с @s3.pt1ck."
            )
            buttons = [
                ("🔄 Попробовать снова", "pay_crypto"),
                ("🔙 Назад к способам оплаты", "pay"),
            ]
            logger.error(f"Ошибка в pay_crypto: {error}, invoice: {invoice}")
            await query.edit_message_text(
                text=error_msg,
                parse_mode="markdown",
                reply_markup=get_keyboard(buttons)
            )
            return

        invoice_id = invoice["invoice_id"]
        pay_url = invoice.get("pay_url")

        context.user_data["payment_type"] = "crypto"
        context.user_data["invoice_id"] = invoice_id
        context.user_data["username"] = username
        logger.info(f"CryptoBot invoice created: invoice_id={invoice_id}, pay_url={pay_url}")

        text = (
            "💸 *Оплатите через CryptoBot*\n\n"
            f"Нажмите кнопку для оплаты *{PRICES['crypto_ton']} TON* (или эквивалент):\n\n"
            f"[Оплатить через CryptoBot]\n({pay_url})\n\n"
            "Ключ и приложение будут выданы автоматически после оплаты.\n"
            
        )
        buttons = [
            ("🔙 Назад к способам оплаты", "pay"),
        ]
        await query.edit_message_text(
            text=text,
            parse_mode="markdown",
            reply_markup=get_keyboard(buttons),
            disable_web_page_preview=True
        )

        context.job_queue.run_once(
            job=check_crypto_payment,
            when=12,
            context={
                "invoice_id": invoice_id,
                "user_id": user_id,
                "username": username,
                "chat_id": user_id,
                "attempts": 0,
            },
            name=f"check_crypto_{invoice_id}_{1}"
        )

    except Exception as e:
        logger.error(f"Критическая ошибка в pay_crypto_confirm: {e}")
        error_msg = (
            "❌ *Произошла ошибка!*n\n"
            "Не удалось обработать запрос. Попробуйте снова или свяжитесь с @s3pt1ck.\n"
            
        )
        buttons = [
            ("🔄 Попробовать снова", "pay_crypto"),
            ("🔙 Назад к способам оплаты", "pay"),
        ]
        await query.edit_message_text(
            text=error_msg,
            parse_mode="markdown",
            reply_markup=get_keyboard(buttons)
        )

async def pay_yook(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "💳 *Подтверждение оплаты через YooKassa*\n\n"
        f"Вы собираетесь оплатить *{PRICES['yookassa_rub']} RUB* за лицензию Valture.\n\n"
        "Продолжить оплату?"
        
    )
    buttons = [
        ("✅ Подтвердить оплату", "pay_yookassa_confirm"),
        ("🔙 Назад к способам оплаты", "pay"),
    ]
    await query.edit_message_text(
        text=text,
        parse_mode="markdown",
        reply_markup=get_keyboard(buttons)
    )

async def pay_yookassa_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query()
    await query.answer()
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.full_name

    try:
        logger.debug(f"Создание YooKassa платежа для пользователя: {username} (ID: {user_id})")
        payment, error = create_yookassa_payment(
            amount=PRICES['yookassa_rub'],
            description="Valture License",
            user_id=user_id,
            username=username
        )
        if not payment:
            error_msg = (
                "❌ *Ошибка при создании платежа!*n\n"
                f"Не удалось создать платеж: {error or 'Unknown error'}.\n\n"
                "Попробуйте снова или свяжитесь с @s3pt1ck.\n"
            )
            buttons = [
                ("🔄 Попробовать снова", "pay_yook"),
                ("🔙 Назад к способам оплаты", "pay"),
            ]
            logger.error(f"Ошибка в pay_yook: {error}")
            await query.edit_message_text(
                text=error_msg,
                parse_mode="markdown",
                reply_markup=get_keyboard(buttons)
            )
            
            return

        payment_id = payment.id
        confirmation_url = payment.confirmation.confirmation_url

        context.user_data["payment_type"] = "yookassa"
        context.user_data["payment_id"] = payment_id
        context.user_data["username"] = username
        logger.info(f"YooKassa payment created: payment_id={payment_id}, confirmation_url={confirmation_url}")

        text = (
            "💳 *Оплатите через YooKassa*\n\n"
            f"Нажмите на кнопку для оплаты *{PRICES['yookassa_rub']} RUB*:\n"
            f"[Оплатить сейчас]({confirmation_url}\n)\n"
            "Ключ и приложение будут выданы автоматически после оплаты.\n"
            
        )
        buttons = [
            ("🔙 Назад к способам оплаты", "pay"),
        ]
        await query.edit_message_text(
            text=text,
            parse_mode="markdown",
            reply_markup=get_keyboard(buttons),
            disable_web_page=False
        )

        context.job_queue.run_once(
            job=check_yookassa_payment,
            when=12,
            context={
                "payment_id": payment_id,
                "user_id": user_id,
                "username": username,
                "chat_id": user_id,
                "attempts": 0,
            },
            name=f"check_yoo_{payment_id}_{1}"
        )

    except Exception as e:
        logger.error(f"Ошибка в pay_yookassa_confirm: {e}")
        error_msg = (
            "❌ *Произошла ошибка!*n\n"
            "Не удалось обработать запрос. Попробуйте снова или свяжитесь с @s3pt1ck.\n"
            
        )
        buttons = [
            ("🔄 Попробовать снова", "pay_yook"),
            ("🔙 Назад к способам оплаты", "pay"),
        ]
        await query.edit_message_text(
            text=error_msg,
            parse_mode="markdown",
            reply_markup=get_keyboard(buttons)
        )

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "📞 *Техническая поддержка*\n\n"
        "Свяжитесь с нами:\n"
        "👉 @s3pt1ck\n\n"
        "Мы ответим максимально быстро! 😊"
        
    )
    buttons = [
        ("🔙 Назад в меню", "menu_main"),
    ]
    await query.edit_message_text(
        text=text,
        parse_mode="markdown",
        reply_markup=get_keyboard(buttons)
    )

async def faq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "❓ *Часто задаваемые вопросы*\n\n"
        "🔹 **Как получить лицензию?**\n"
        "Перейдите в 'Оплатить' и выберите способ оплаты.\n\n"
        "🔹 **Что делать, если ключ не работает?**n\n"
        "Напишите в поддержку @s3pt1ck.\n\n"
        "🔹 **Можно ли использовать ключ на нескольких устройствах?**n\n"
        "Нет, ключ привязан к одному устройству.\n"
        
    )
    buttons = [
        ("🔙 Назад в меню", "menu_main"),
    ]
    await query.edit_message_text(
        text=text,
        parse_mode="markdown",
        reply_markup=get_keyboard(buttons)
    )

async def news_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    text = (
        "🗞 *Новости Valture*\n\n"
        "Следите за нашими обновлениями!\n\n"
        "На данный момент новостей нет.\n\n"
        
    )
    
    buttons = [
        ("🔙 Назад в меню", "menu_main"),
    ]
    await query.edit_message_text(
        text=text,
        parse_mode="markdown",
        reply_markup=get_keyboard(buttons)
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query()
    data = query.data
    
    if data == "menu_main":
        await main_menu(update, context)
    elif data == "pay":
        await pay(update, context)
    elif data == "pay_crypto":
        await pay_crypto(update, context)
    elif data == "pay_crypto_confirm":
        await pay_crypto_confirm(update, context)
    elif data == "pay_yook":
        await pay_yook(update, context)
    elif data == "pay_yookassa_confirm":
        await pay_yookassa_confirm(update, context)
    elif data == "menu_support":
        await support(update, context)
    elif data == "menu_faq":
        await faq(update, context)
    elif data == "menu_data":
        await about(update, context)
    elif data == "news_info":
        await news_info(update, context)
    else:
        await query.answer("Неизвестная команда!")

async def main():
    try:
        application = Application.builder().token(BOT_TOKEN).build()

        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("query_payment", query_payment))
        application.add_handler(CommandHandler("check_logs", check_logs))
        application.add_handler(CallbackQueryHandler(button_handler))

        flask_thread = Thread(target=run_flask)
        flask_thread.start()

        logger.info("Starting bot...")
        await application.run_polling()
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        raise

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
</xaiagram>       
