import os
import logging
import secrets
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from yookassa import Configuration, Payment
from flask import Flask
import asyncio

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# YooKassa configuration
Configuration.account_id = os.getenv('YOOKASSA_SHOP_ID')
Configuration.secret_key = os.getenv('YOOKASSA_SECRET_KEY')

# Flask app for Railway health checks
app = Flask(__name__)
@app.route('/')
def home():
    return "Bot is running!"

# Telegram Bot
def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Купить лицензию (1000₽)", callback_data="buy_license")],
        [InlineKeyboardButton("🆘 Поддержка", url="https://t.me/valture_support")]
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"👋 Привет, {user.first_name}!\n\n"
        "Для покупки лицензии нажмите кнопку ниже:",
        reply_markup=get_keyboard()
    )

async def create_payment(query):
    try:
        user = query.from_user
        payment_params = {
            "amount": {"value": "1000.00", "currency": "RUB"},
            "confirmation": {
                "type": "redirect",
                "return_url": f"https://t.me/{(await query.bot.get_me()).username}"
            },
            "description": "Лицензия Valture",
            "metadata": {
                "user_id": user.id,
                "username": user.username or user.first_name
            }
        }
        
        # Debug logging
        logger.info(f"Creating payment for user {user.id}")
        logger.debug(f"Payment params: {payment_params}")
        
        payment = Payment.create(payment_params, idempotence_key=secrets.token_hex(16))
        pay_url = payment.confirmation.confirmation_url
        
        logger.info(f"Payment created successfully: {pay_url}")
        
        await query.edit_message_text(
            text=f"✅ Ссылка для оплаты:\n\n{pay_url}\n\n"
                 "После оплаты лицензия придёт автоматически в этот чат.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Перейти к оплате", url=pay_url)],
                [InlineKeyboardButton("🔄 Проверить оплату", callback_data="check_payment")]
            ]),
            disable_web_page_preview=True
        )
        
    except Exception as e:
        logger.error(f"Payment creation failed: {str(e)}", exc_info=True)
        await query.edit_message_text(
            "❌ Ошибка при создании платежа:\n\n"
            f"Техническая информация: {str(e)}\n\n"
            "Попробуйте ещё раз или обратитесь в поддержку: @valture_support",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🆘 Поддержка", url="https://t.me/valture_support")]
            )
        )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "buy_license":
        await create_payment(query)

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

async def run_bot():
    # Check required environment variables
    required_vars = ['BOT_TOKEN', 'YOOKASSA_SHOP_ID', 'YOOKASSA_SECRET_KEY']
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        return
    
    application = Application.builder().token(os.getenv('BOT_TOKEN')).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    await application.initialize()
    await application.start()
    logger.info("Bot started successfully")
    
    # Keep the bot running
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    # Start Flask in a separate thread
    import threading
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Run the bot
    asyncio.run(run_bot())
