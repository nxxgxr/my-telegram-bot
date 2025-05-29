import os
import logging
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# --- Настройки ---
BOT_TOKEN = os.environ.get("BOT_TOKEN") or "YOUR_BOT_TOKEN"
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# =============================================
# 1. Анимированные кнопки с hover-эффектом
# =============================================
async def show_animated_buttons(update: Update):
    buttons = [
        [
            InlineKeyboardButton("🔥 НАВЕДИ НА МЕНЯ", callback_data="hover_me"),
            InlineKeyboardButton("✨ МИГАЕТ", callback_data="blink_me")
        ],
        [InlineKeyboardButton("🚀 3D-ТЕКСТ", callback_data="3d_text")],
        [InlineKeyboardButton("⏱️ ТАЙМЕР", callback_data="countdown")]
    ]
    
    await update.message.reply_text(
        "🎮 *ВЫБЕРИ ЭФФЕКТ:*",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown"
    )

# =============================================
# 2. Прогресс-бар с неоновым эффектом
# =============================================
async def show_neon_progress(update: Update):
    message = await update.message.reply_text("⚡ ЗАГРУЗКА ДАННЫХ...")
    
    for i in range(1, 11):
        progress = i * 10
        bar = "█" * i + "░" * (10 - i)
        neon = "✨" * (i % 3)  # Мерцающий эффект
        
        await message.edit_text(
            f"🔮 *Прогресс: {progress}%*\n\n"
            f"`[{bar}]` {neon}\n\n"
            "▰▰▰▰▰▰▰▰▰▰",
            parse_mode="Markdown"
        )
        await asyncio.sleep(0.3)
    
    await message.edit_text("✅ ЗАГРУЗКА ЗАВЕРШЕНА!")

# =============================================
# 3. 3D-текст с псевдографикой
# =============================================
async def show_3d_effect(update: Update):
    text = """
    ``` 
    ██╗   ██╗ █████╗ ██╗     ████████╗ ██████╗ 
    ██║   ██║██╔══██╗██║     ╚══██╔══╝██╔═══██╗
    ██║   ██║███████║██║        ██║   ██║   ██║
    ╚██╗ ██╔╝██╔══██║██║        ██║   ██║   ██║
     ╚████╔╝ ██║  ██║███████╗   ██║   ╚██████╔╝
      ╚═══╝  ╚═╝  ╚═╝╚══════╝   ╚═╝    ╚═════╝ 
    ```
    """
    await update.message.reply_text(text, parse_mode="Markdown")

# =============================================
# 4. Таймер с анимацией
# =============================================
async def start_countdown(update: Update):
    message = await update.message.reply_text("⏳ *Запуск таймера...*", parse_mode="Markdown")
    
    for i in range(5, 0, -1):
        animation = "💥 " * i + "🔹 " * (5 - i)
        await message.edit_text(
            f"⏳ *До старта: {i} сек.*\n\n"
            f"{animation}\n\n"
            "▬▬▬▬▬▬▬▬▬▬▬",
            parse_mode="Markdown"
        )
        await asyncio.sleep(1)
    
    await message.edit_text("🚀 *ПУСК!* 🎇", parse_mode="Markdown")

# =============================================
# Обработчики команд
# =============================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот с крутыми визуальными эффектами.\n"
        "Используй /effects чтобы увидеть магию!"
    )

async def effects(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_animated_buttons(update)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "hover_me":
        await query.edit_message_text("🔥 Ты навел на кнопку! Вот твой эффект:")
        await show_neon_progress(update)
    elif query.data == "blink_me":
        await query.edit_message_text("✨ Мигающая кнопка активирована!")
        await asyncio.sleep(1)
        await query.edit_message_text("✨ *Ты активировал мигание!*", parse_mode="Markdown")
    elif query.data == "3d_text":
        await show_3d_effect(update)
    elif query.data == "countdown":
        await start_countdown(update)

# =============================================
# Запуск бота
# =============================================
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("effects", effects))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    app.run_polling()

if __name__ == "__main__":
    main()
