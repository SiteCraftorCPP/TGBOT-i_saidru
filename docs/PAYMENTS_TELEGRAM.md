# Платежи в Telegram + ЮKassa (по официальной схеме Bot Payments)

Ссылки Telegram (источник правды для клиента бота):

- [Payments / обзор](https://core.telegram.org/bots/payments)
- [Список валют и ограничения сумм (`currencies.json`)](https://core.telegram.org/bots/payments/currencies.json)
- Отправка счёта клиентами API: объект **счёт (invoice)** + **`LabeledPrice`**: поле **`amount`** — целое число в **минимальных единицах** валюты (см. `exp` в `currencies.json`).

Для **RUB** в `currencies.json` указано **`"exp": 2`** — значит сумма счёта задаётся в **копейках**: **100 ₽ → `100 × 100 = 10000`**. В этом репозитории это делает функция `rub_amount_to_telegram_minor_units` в `app/services/telegram_invoice_finalize.py`.

У **RUB** также есть нижний порог: поле **`min_amount`** (в тех же минорных единицах). В коде закреплена константа `TELEGRAM_RUB_MIN_AMOUNT_MINOR` (= значению Telegram на момент правки примерно **8773**, т.е. **не менее ~87,73 ₽** как сумма счёта; для целых рублей в `.env` разумный минимум **88**). Если сделать дешевле, Telegram может пропустить `pre-checkout`, но **провайдер (ЮKassa) отрежет платёж** — клиент видит ошибку вида «на стороне платёжной системы».

Цепочка в боте (как в документации):

1. **Бот отправляет счёт** (`send_invoice` / `answer_invoice` в aiogram): `currency="RUB"`, `provider_token` из BotFather, `payload` короткая строка, `prices`.
2. **Пользователь нажимает «Оплатить»** → Telegram шлёт боту **`pre_checkout_query`**. Бот обязан ответить `answerPreCheckoutQuery(ok=True)` (у нас после проверки суммы и пользователя из БД).
3. После успешной оплаты клиент Telegram присылает **`successful_payment`**; бот фиксирует `paid` в таблице `payments` и открывает документ / продлевает подписку.

Частые причины фразы **«Не удалось провести транзакцию … на стороне платёжной системы»** (уже после шага счёта / у провайдера):

| Проверка |
|----------|
| В BotFather указан токен **LIVE** (`…:LIVE:…`), а оплата картой/кошельком ограничена ЮKassa или банком (лимиты, блокировки, недоступен способ). |
| Магазин или договор в ЮKassa в статусе, при котором нельзя принять канал Telegram. |
| Сумма **ниже телеграмовского минимума для RUB** или не те минорные единицы. |
| Копировать токен из BotFather **целиком** (одна строка в `.env` без пробелов вокруг `=`). После каждого изменения `.env` — перезапуск процесса бота (`systemctl restart …`). |

Переменные окружения в этом проекте:

- **`PAYMENTS_ENABLED=true`** — иначе бот принудительно не ведёт в оплату.
- **`TELEGRAM_PAYMENT_PROVIDER_TOKEN`** — токен провайдера из BotFather (ЮKassa / ЮMoney после привязки магазина).
- Цены в рублях целые: **`DOCUMENT_PRICE_RUB`**, **`SUBSCRIPTION_PRICE_RUB`** (для счёта в Telegram не занижайте ниже порога Telegram для RUB, см. выше).

Сценарий **только браузера (ЮKassa redirect)** задаётся отдельно: **`YOOKASSA_SHOP_ID`**, **`YOOKASSA_SECRET_KEY`**, **`YOOKASSA_RETURN_URL`**, webhook — см. `.env.example` и код `YooKassaClient`.
