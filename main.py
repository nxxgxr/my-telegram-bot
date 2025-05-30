async def pay_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
                "return_url": "https://rabochij-production.up.railway.app/"
            },
            "capture": True,
            "description": "Покупка лицензии Valture",
            "metadata": {
                "username": username,
                "chat_id": str(chat_id)
            }
        }
        logger.debug(f"Параметры платежа: {json.dumps(payment_params, ensure_ascii=False)}")

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
            "❌ Ошибка создания платежа. Проверьте настройки или попробуйте позже.\n"
            "Обратитесь в поддержку: @valture_support_bot"
        )
