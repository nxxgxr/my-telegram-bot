import os
import logging
import secrets
import redis.asyncio as redis
from datetime import datetime, timezone, timedelta
from threading import Thread
import json
import asyncio
from typing import List

from flask import Flask, request
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, Bot
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import gspread
from google.oauth2.service_account import Credentials
from aiolimiter import AsyncLimiter

# --- Настройки ---

BOT_TOKEN = os.environ.get("BOT_TOKEN") or "YOUR_BOT_TOKEN"
CREDS_FILE = "valture-license-bot-account.json"
SPREADSHEET_NAME = "valture"
SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
ADMIN_USERNAME = "@s3pt1ck"  # Уведомления будут отправляться только этому пользователю
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://your-app-name.onrender.com/webhook")

# --- Логирование ---

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Flask для keep-alive и вебхуков ---

app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Valture бот работает!"

@app.route('/webhook', methods=['POST'])
async def webhook():
    update = Update.de_json(request.get_json(), application.bot)
    await application.process_update(update)
    return "OK", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# --- Инициализация Redis и лимитера ---

redis_client = redis.from_url(REDIS_URL, decode_responses=True)
limiter = AsyncLimiter(max_rate=10, time_period=60)  # 10 запросов в минуту на пользователя

# --- Интерактивные элементы ---

def get_keyboard(buttons: List[tuple]) -> InlineKeyboardMarkup:
    """Создание клавиатуры с анимацией кнопок."""
    return InlineKeyboardMarkup([[InlineKeyboardButton(f"{'⚡ ' if i == 0 else '🔥 '}{text}", callback_data=callback)] for i, (text, callback) in enumerate(buttons)])

async def send_progress_message(update: Update, context: ContextTypes.DEFAULT_TYPE, message: str):
    """Отправка анимированного сообщения с прогресс-баром."""
    progress_stages = ["█" * i + "▒" * (10 - i) for i in range(1, 11)]
    msg = await update.message.reply_text(f"{message}\n{progress_stages[0]} ⚡", parse_mode="Markdown")
    for stage in progress_stages[1:]:
        await asyncio.sleep(0.3)
        await msg.edit_text(f"{message}\n{stage} ⚡", parse_mode="Markdown")
    return msg

async def get_admin_chat_id(bot: Bot) -> str:
    """Получение Telegram ID админа по username."""
    cache_key = "admin_chat_id"
    cached = await redis_client.get(cache_key)
    if cached:
        return cached
    try:
        chat = await bot.get_chat(ADMIN_USERNAME)
        chat_id = str(chat.id)
        await redis_client.setex(cache_key, 86400, chat_id)  # Кэшируем на 24 часа
        logger.info(f"Получен chat_id для {ADMIN_USERNAME}: {chat_id}")
        return chat_id
    except Exception as e:
        logger.error(f"Ошибка при получении chat_id для {ADMIN_USERNAME}: {e}")
        raise

async def notify_admin(bot: Bot, user: dict, action: str):
    """Отправка уведомления админу о действиях пользователя."""
    username = user.get('username', user.get('first_name', 'Неизвестный пользователь'))
    if username.startswith('@'):
        username = username[1:]
    try:
        admin_chat_id = await get_admin_chat_id(bot)
        await bot.send_message(
            chat_id=admin_chat_id,
            text=(
                f"🔔 *Действие пользователя* ⚡\n"
                "┌────────────────────────┐\n"
                f"│ Пользователь: @{username}\n"
                f"│ Действие: {action}\n"
                "└────────────────────────┘\n"
                "🎮 *Valture* — отслеживаем активность!"
            ),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления админу: {e}")

# --- Логика Google Sheets ---

async def get_sheet():
    """Получение кэшированного объекта Google Sheets из Redis."""
    cache_key = "google_sheet_client"
    cached = await redis_client.get(cache_key)
    if cached:
        logger.info("Используется кэшированный доступ к Google Sheets")
        return gspread.authorize(Credentials.from_json(json.loads(cached)))
    try:
        creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPE)
        client = gspread.authorize(creds)
        sheet = client.open(SPREADSHEET_NAME).sheet1
        await redis_client.setex(cache_key, 3600, json.dumps(creds.to_json()))
        logger.info("Успешно подключено к Google Sheets")
        return sheet
    except Exception as e:
        logger.error(f"Ошибка подключения к Google Sheets: {e}")
        raise

def generate_license(length: int = 32) -> str:
    """Генерация безопасного лицензионного ключа."""
    key = ''.join(secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') for _ in range(length))
    logger.info(f"Сгенерирован ключ: {key}")
    return key

async def append_license_to_sheet(license_key: str, username: str):
    """Добавление лицензии в Google Sheets и уведомление админа."""
    try:
        sheet = await get_sheet()
        utc_plus_2 = timezone(timedelta(hours=2))
        now_str = datetime.now(utc_plus_2).strftime("%Y-%m-%d %H:%M:%S")
        sheet.append_row([license_key, "", username, now_str])
        logger.info(f"Лицензия {license_key} добавлена для {username}")
        # Уведомление админа с ASCII-артом
        bot = Bot(BOT_TOKEN)
        admin_chat_id = await get_admin_chat_id(bot)
        await bot.send_message(
            chat_id=admin_chat_id,
            text=(
                "🔔 *Новая лицензия!* ⚡\n"
                "┌────────────────────────┐\n"
                f"│ Пользователь: {username}\n"
                f"│ Ключ: `{license_key}`\n"
                "└────────────────────────┘\n"
                "🎮 *Valture* — к победам!"
            ),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Ошибка при добавлении лицензии: {e}")
        raise

# --- Обработчики команд ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start."""
    user = update.effective_user
    bot = context.bot
    # Отправка уведомления в фоновом режиме
    asyncio.create_task(notify_admin(bot, user, "Нажал /start"))
    welcome_text = (
        "👋 *Добро2 пожаловать в Valture!* ⚡\n\n"
        "Мы предлагаем профессиональный инструмент для геймеров, "
        "которые стремятся к максимальной производительности и стабильности.\n\n"
        "Выберите действие в меню ниже:"
    )
    await send_progress_message(update, context, welcome_text)
    buttons = [("📋 Открыть меню", "menu_main")]
    await update.message.reply_text(welcome_text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отображение главного меню."""
    query = update.callback_query
    await query.answer()
    bot = context.bot
    # Отправка уведомления в фоновом режиме
    asyncio.create_task(notify_admin(bot, query.from_user, "Открыл главное меню"))
    buttons = [
        ("ℹ️ О приложении", "menu_about"),
        ("📰 Новости", "menu_news"),
        ("💳 Купить лицензию", "menu_pay"),
        ("❓ FAQ", "menu_faq"),
        ("📞 Поддержка", "menu_support"),
        ("📊 Статистика", "menu_stats"),
    ]
    await query.edit_message_text(
        "🏠 *Главное меню* ⚡\n\nВыберите раздел:",
        parse_mode="Markdown",
        reply_markup=get_keyboard(buttons)
    )

async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Информация о приложении."""
    query = update.callback_query
    await query.answer()
    bot = context.bot
    # Отправка уведомления в фоновом режиме
    asyncio.create_task(notify_admin(bot, query.from_user, "Выбрал 'О приложении'"))
    text = (
        "✨ *Valture — Ваш путь к совершенству в играх* ⚡\n\n"
        "Valture — это передовой инструмент, созданный для геймеров, которые не готовы мириться с компромиссами. "
        "Наша миссия — вывести вашу игровую производительность на новый уровень, обеспечив максимальную плавность, "
        "стабильность и отзывчивость системы. С Valture вы получите конкурентное преимущество, о котором всегда мечтали.\n\n"
        "🔥 *Почему выбирают Valture?*\n"
        "🚀 Увеличение FPS на 20–30%\n"
        "🛡️ Стабильный фреймрейт\n"
        "💡 Молниеносная отзывчивость\n"
        "🔋 Оптимизация Windows\n"
        "🛳️ Плавность управления\n"
        "🖥️ Плавность картинки\n\n"
        "_Создано для геймеров, которые ценят качество и стремятся к победе._ 🎮"
    )
    buttons = [("🔙 Назад", "menu_main")]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню оплаты."""
    query = update.callback_query
    await query.answer()
    bot = context.bot
    # Отправка уведомления в фоновом режиме
    asyncio.create_task(notify_admin(bot, query.from_user, "Выбрал 'Купить лицензию'"))
    text = (
        "💳 *Приобретение лицензии Valture* ⚡\n\n"
        "Стоимость: *1000 рублей*\n"
        "После оплаты вы получите уникальный ключ прямо в чат.\n\n"
        "Готовы продолжить?"
    )
    buttons = [("✅ Оплатить", "pay_confirm"), ("🔙 Назад", "menu_main")]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def pay_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение оплаты и выдача ключа с ASCII-артом."""
    query = update.callback_query
    await query.answer()
    bot = context.bot
    # Отправка уведомления в фоновом режиме
    asyncio.create_task(notify_admin(bot, query.from_user, "Подтвердил покупку"))
    async with limiter:
        try:
            license_key = generate_license()
            username = query.from_user.username or query.from_user.full_name
            await append_license_to_sheet(license_key, username)
            text = (
                "🎉 *Поздравляем с покупкой!* ⚡\n\n"
                "Ваш лицензионный ключ:\n"
                f"`{license_key}`\n\n"
                "┌────────────────────┐\n"
                "│    Valture Key     │\n"
                "│   🎮 Activated!   │\n"
                "└────────────────────┘\n"
                "🔐 *Сохрани ключ в надежном месте!*"
            )
            await query.edit_message_text(text, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Ошибка при генерации ключа: {e}")
            await query.edit_message_text(
                "❌ *Ошибка* 😔\n\nНе удалось сгенерировать ключ. Попробуйте позже или обратитесь в поддержку (@s3pt1ck).",
                parse_mode="Markdown"
            )

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню поддержки."""
    query = update.callback_query
    await query.answer()
    bot = context.bot
    # Отправка уведомления в фоновом режиме
    asyncio.create_task(notify_admin(bot, query.from_user, "Выбрал 'Поддержка'"))
    text = (
        "📞 *Поддержка Valture* ⚡\n\n"
        "Возникли вопросы? Свяжитесь с нами:\n"
        "👉 *@s3pt1ck*\n\n"
        "Мы ответим максимально быстро!"
    )
    buttons = [("🔙 Назад", "menu_main")]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def faq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Часто задаваемые вопросы."""
    query = update.callback_query
    await query.answer()
    bot = context.bot
    # Отправка уведомления в фоновом режиме
    asyncio.create_task(notify_admin(bot, query.from_user, "Выбрал 'FAQ'"))
    text = (
        "❓ *FAQ* ⚡\n\n"
        "**1. Как получить лицензию?**\n"
        "Перейдите в раздел 'Купить лицензию' и следуйте инструкциям.\n\n"
        "**2. Что делать, если ключ не работает?**\n"
        "Напишите в поддержку — мы поможем!\n\n"
        "**3. Можно ли использовать ключ на нескольких устройствах?**\n"
        "Нет, ключ привязан к одному устройству."
    )
    buttons = [("🔙 Назад", "menu_main")]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Раздел новостей."""
    query = update.callback_query
    await query.answer()
    bot = context.bot
    # Отправка уведомления в фоновом режиме
    asyncio.create_task(notify_admin(bot, query.from_user, "Выбрал 'Новости'"))
    text = (
        "📰 *Новости Valture* ⚡\n\n"
        "Следите за обновлениями здесь!\n"
        "Пока новых сообщений нет."
    )
    buttons = [("🔙 Назад", "menu_main")]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(buttons))

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отображение статистики лицензий с анимированным графиком."""
    query = update.callback_query
    await query.answer()
    bot = context.bot
    # Отправка уведомления в фоновом режиме
    asyncio.create_task(notify_admin(bot, query.from_user, "Выбрал 'Статистика'"))
    try:
        sheet = await get_sheet()
        licenses = sheet.get_all_values()[1:]  # Пропускаем заголовок
        total_licenses = len(licenses)
        active_users = len(set(row[2] for row in licenses))
        chart_data = {
            "type": "bar",
            "data": {
                "labels": ["Всего лицензий", "Активные пользователи"],
                "datasets": [{
                    "label": "Valture Stats",
                    "data": [total_licenses, active_users],
                    "backgroundColor": ["#36A2EB", "#FF6384"],
                    "borderColor": ["#2A80C1", "#D65673"],
                    "borderWidth": 1
                }]
            },
            "options": {
                "animation": {
                    "duration": 2000,
                    "easing": "easeInOutBounce"
                },
                "scales": {
                    "y": {
                        "beginAtZero": True
                    }
                },
                "plugins": {
                    "legend": {
                        "display": True,
                        "position": "top"
                    }
                }
            }
        }
        text = (
            "📊 *Статистика Valture* ⚡\n\n"
            f"💿 *Всего лицензий:* {total_licenses}\n"
            f"👥 *Активные пользователи:* {active_users}\n\n"
            "🔍 *Посмотри график ниже!*"
        )
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard([("🔙 Назад", "menu_main")]))
        # Встраивание анимированного Chart.js графика
        await query.message.reply_html(
            f"""
            <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
            <canvas id="statsChart" width="400" height="200"></canvas>
            <script>
                new Chart(document.getElementById('statsChart'), {json.dumps(chart_data)});
            </script>
            """
        )
    except Exception as e:
        logger.error(f"Ошибка при получении статистики: {e}")
        await query.edit_message_text(
            "❌ *Ошибка* 😔\n\nНе удалось загрузить статистику. Попробуйте позже или обратитесь в поддержку (@s3pt1ck).",
            parse_mode="Markdown"
        )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий кнопок."""
    query = update.callback_query
    data = query.data
    handlers = {
        "menu_main": main_menu,
        "menu_pay": pay,
        "pay_confirm": pay_confirm,
        "menu_about": about,
        "menu_news": news,
        "menu_faq": faq,
        "menu_support": support,
        "menu_stats": stats
    }
    if data in handlers:
        await handlers[data](update, context)

# --- Запуск бота ---

if __name__ == "__main__":
    # Запуск Flask в отдельном потоке
    Thread(target=run_flask).start()

    # Инициализация приложения
    application = Application.builder().token(BOT_TOKEN).build()

    # Добавление обработчиков
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))

    # Настройка вебхуков
    application.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 8443)),
        url_path="/webhook",
        webhook_url=WEBHOOK_URL
    )
    print("✅ Valture бот запущен с вебхуками!")
