import os
import logging
import secrets
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from yookassa import Configuration, Payment

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация YooKassa
Configuration.account_id = os.getenv('YOOKASSA_SHOP_ID', 'your_shop_id')
Configuration.secret_key = os.getenv('YOOKASSA_SECRET_KEY', 'your_secret_key')

# Клавиатура
def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Купить лицензию", callback_data="buy_license")],
        [InlineKeyboardButton("🆘 Поддержка", url="https://t.me/valture_support")]
    ])

# Обработчики команд
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"👋 Привет, {user.first_name}!\n\n"
        "Добро пожаловать в Valture!\n"
        "Для покупки лицензии нажмите кнопку ниже:",
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
            "description": "Лицензия Valture",
            "metadata": {
                "user_id": user.id,
                "username": user.username or user.first_name
            }
        }, idempotence_key=secrets.token_hex(16))
        
        pay_url = payment.confirmation.confirmation_url
        
        await query.edit_message_text(
            text=f"🛒 Ссылка для оплаты:\n\n{pay_url}\n\n"
                 "После оплаты вы получите лицензионный ключ автоматически.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Перейти к оплате", url=pay_url)],
                [InlineKeyboardButton("🔄 Проверить оплату", callback_data="check_payment")]
            ]),
            disable_web_page_preview=True
        )
        
    except Exception as e:
        logger.error(f"Ошибка при создании платежа: {str(e)}")
        await query.edit_message_text(
            "❌ Произошла ошибка при создании платежа.\n"
            "Попробуйте позже или обратитесь в поддержку."
        )

# Запуск бота
def main():
    application = Application.builder().token(os.getenv('BOT_TOKEN')).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    logger.info("Бот запущен")
    application.run_polling()

if __name__ == "__main__":
    main()
