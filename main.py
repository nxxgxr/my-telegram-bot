import os
import logging
import secrets
import json
from flask import Flask, request
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from yookassa import Configuration, Payment
from threading import Thread

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Переменные окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID", "1095145")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY", "live_Kqe5487dKG7PHL5fLOzBC0-jOWWXfxzrLHS2s0YWVz0")

# Настройка ЮKassa
Configuration.account_id = YOOKASSA_SHOP_ID
Configuration.secret_key = YOOKASSA_SECRET_KEY

# Flask приложение
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Valture бот работает!"

@app.route('/yookassa-webhook', methods=['POST'])
def yookassa_webhook():
    logger.info(f"Получен запрос на /yookassa-webhook: {request.get_data(as_text=True)}")
    try:
        data = json.loads(request.get_data())
        event = data.get('event')
        logger.info(f"Событие от YooKassa: {event}")
        if event == 'payment.succeeded':
            username = data.get('object', {}).get('metadata', {}).get('username')
            chat_id = data.get('object', {}).get('metadata', {}).get('chat_id')
            logger.info(f"Платеж успешен для {username}, chat_id: {chat_id}")
            # Здесь можно добавить отправку HWID ключа
        return '', 200
    except Exception as e:
        logger.error(f"Ошибка обработки webhook: {str(e)}")
        return '', 400

@app.route('/test-payment')
def test_payment():
    try:
        payment_params = {
            "amount": {
                "value": "1000.00",
                "currency": "RUB"
            },
            "confirmation": {
                "type": "redirect",
                "return_url": "https://rabochij-production.up.railway.app/"
            },
            "capture": True,
            "description": "Тестовый платеж для Valture",
            "metadata": {
                "username": "test_user",
                "chat_id": "123456789"
            }
        }
        logger.info(f"Параметры платежа: {json.dumps(payment_params, ensure_ascii=False)}")
        payment = Payment.create(payment_params, idempotence_key=secrets.token_hex(16))
        pay_url = payment.confirmation.confirmation_url
        logger.info(f"Платеж успешно создан, ссылка: {pay_url}")
        return f"Ссылка на оплату: {pay_url}"
    except Exception as e:
        logger.error(f"Ошибка создания тестового платежа: {str(e)}")
        return f"Ошибка создания платежа: {str(e)}"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# Telegram функции
def get_keyboard(buttons):
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=callback)] for text, callback in buttons])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "👋 Добро пожаловать в Valture!\n\nВыберите действие:"
    await update.message.reply_text(text, reply_markup=get_keyboard([("💳 Купить лицензию", "pay")]))

async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        amount_value = "1000.00"
        user = query.from_user
        username = user.username or str(user.id)
        chat_id = query.message.chat_id

        logger.info(f"Создаем платеж для {username} на сумму {amount_value}")
        payment_params = {
            "amount": {
                "value": amount_value,
                "currency": "RUB"
            },
            "confirmation": {
                "type": "redirect",
                "return_url": "https://rabochij-production.up.railway/"
            },
            "capture": True,
            "description": "Покупка лицензии для Valture",
            "metadata": {
                "username": username,
                "chat_id": str(chat_id)
            }
        }
        logger.debug(f"Payment params: {json.dumps(payment_params, ensure_ascii=False)}")

        payment = Payment.create(payment_params, idempotence_key=secrets.token_hex(16))
        pay_url = payment.confirmation.confirmation_url
        logger.info(f"Платеж успешно создан, ссылка: {pay_url}")

        await query.edit_message_text(
            f"Перейдите по ссылке для оплаты:\n{pay_url}",
            disable_web_page_preview=True
        )
    except Exception as e:
        logger.error(f"Ошибка создания платежа: {str(e)}", exc_info=True)
        await query.edit_message_text(
            "❌ Ошибка создания платежа. Попробуйте позже.\n"
            "ОбAfrican Contact: @valture_support_bot"
        )

async def callback_handler(update: Update, context: ContextTypes):
    query = update.callback_query
    if query.data == "pay":
        await pay(update, context)
    else:
        await query.answer("Неизвестная команда", show_alert=True)

def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не указан!")
        return

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(callback_handler))

    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()

    logger.info("Starting bot...")
    application.run_polling()

if __name__ == "__main__":
    main()
</xai_application>
