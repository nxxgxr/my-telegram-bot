import logging
import secrets
import json
from yookassa import Configuration, Payment

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Настройка ЮKassa
YOOKASSA_SHOP_ID = "1095145"
YOOKASSA_SECRET_KEY = "live_Kqe5487dKG7PHL5fLOzBC0-jOWWXfxzrLHS2s0YWVz0"

Configuration.account_id = YOOKASSA_SHOP_ID
Configuration.secret_key = YOOKASSA_SECRET_KEY

def create_test_payment():
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

        # Создаем платеж
        payment = Payment.create(payment_params, idempotence_key=secrets.token_hex(16))
        pay_url = payment.confirmation.confirmation_url
        logger.info(f"Платеж успешно создан, ссылка: {pay_url}")
        return pay_url
    except Exception as e:
        logger.error(f"Ошибка создания платежа: {str(e)}", exc_info=True)
        return None

if __name__ == "__main__":
    url = create_test_payment()
    if url:
        print(f"Ссылка на оплату: {url}")
    else:
        print("Не удалось создать платеж")
