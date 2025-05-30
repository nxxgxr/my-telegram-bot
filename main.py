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
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID", "your_shop_id")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY", "your_secret_key")

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
            payment = data.get('object', {})
            metadata = payment.get('metadata', {})
            username = metadata.get('username')
            chat_id = metadata.get('chat_id')
            amount = payment.get('amount', {}).get('value')
            
            logger.info(f"Платеж успешен для {username}, chat_id: {chat_id}, сумма: {amount} RUB")
            
            # Здесь можно добавить отправку HWID ключа или другую логику
            # Например, сохранить в базу данных факт оплаты
            
        return '', 200
    except Exception as e:
        logger.error(f"Ошибка обработки webhook: {str(e)}", exc_info=True)
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
                "return_url": "https://your-domain.com/"
            },
            "capture": True,
            "description": "Тестовый платеж для Valture",
            "metadata": {
                "username": "test_user",
                "chat_id": "123456789"
            }
        }
        
        logger.info(f"Создаем тестовый платеж с параметрами: {json.dumps(payment_params, indent=2)}")
        
        # Создаем платеж с idempotence_key
        payment = Payment.create(payment_params, idempotence_key=secrets.token_hex(16))
        pay_url = payment.confirmation.confirmation_url
        
        logger.info(f"Платеж создан успешно. Ссылка для оплаты: {pay_url}")
        
        return f"""
        <h1>Тестовый платеж</h1>
        <p>Ссылка для оплаты: <a href="{pay_url}" target="_blank">{pay_url}</a></p>
        <p>Сумма: 1000 RUB</p>
        """
        
    except Exception as e:
        logger.error(f"Ошибка при создании тестового платежа: {str(e)}", exc_info=True)
        return f"""
        <h1>Ошибка</h1>
        <p>{str(e)}</p>
        """

# Telegram бот
def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Купить лицензию", callback_data="buy_license")],
        [InlineKeyboardButton("🆘 Поддержка", url="https://t.me/valture_support_bot")]
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    welcome_text = (
        f"👋 Привет, {user.first_name}!\n\n"
        "Добро пожаловать в Valture - лучший приватный софт!\n\n"
        "Здесь ты можешь приобрести лицензию на наш продукт."
    )
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=get_keyboard()
    )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "buy_license":
        await create_payment(query)

async def create_payment(query):
    try:
        user = query.from_user
        username = user.username or f"{user.first_name} {user.last_name}" or str(user.id)
        chat_id = query.message.chat_id
        
        payment_params = {
            "amount": {
                "value": "1000.00",
                "currency": "RUB"
            },
            "confirmation": {
                "type": "redirect",
                "return_url": "https://your-domain.com/"
            },
            "capture": True,
            "description": f"Лицензия Valture для @{username}",
            "metadata": {
                "username": username,
                "chat_id": chat_id,
                "product": "valture_license"
            }
        }
        
        logger.info(f"Создание платежа для {username} (ID: {chat_id})")
        
        payment = Payment.create(payment_params, idempotence_key=secrets.token_hex(16))
        pay_url = payment.confirmation.confirmation_url
        
        await query.edit_message_text(
            text=f"✅ Для оплаты лицензии перейдите по ссылке:\n\n{pay_url}\n\n"
                 "После успешной оплаты вы получите ваш ключ автоматически.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Перейти к оплате", url=pay_url)],
                [InlineKeyboardButton("🔄 Проверить оплату", callback_data="check_payment")]
            ]),
            disable_web_page_preview=True
        )
        
    except Exception as e:
        logger.error(f"Ошибка при создании платежа: {str(e)}", exc_info=True)
        await query.edit_message_text(
            text=f"❌ Произошла ошибка при создании платежа:\n\n{str(e)}\n\n"
                 "Попробуйте еще раз или обратитесь в поддержку.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🆘 Поддержка", url="https://t.me/valture_support_bot")]
            ])
        )

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

def main():
    if not BOT_TOKEN:
        logger.error("Токен бота не указан!")
        return

    # Создаем и настраиваем приложение бота
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(handle_callback))

    # Запускаем Flask в отдельном потоке
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()

    logger.info("Бот запущен и готов к работе!")
    application.run_polling()

if __name__ == "__main__":
    main()
