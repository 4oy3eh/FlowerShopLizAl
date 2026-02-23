# IMPLEMENTATION_PLAN.md — Пошаговый план реализации

## Принципы работы с Claude Code

1. **Один промпт = одна задача.** Не просить 5 фич за раз.
2. **После каждого промпта** — проверить результат, запустить `python app.py`.
3. **Ссылайся на docs-ai/** — "Реализуй по схеме из docs-ai/SCHEMA.md"
4. **Не переписывать с нуля** — "Добавь к существующему", не "Перепиши всё".
5. **Тесты по желанию** — проект на 2 недели, ручное тестирование ок.

---

## Фаза 1: Фундамент (Промпты 1-4)

### Промпт 1: Инициализация проекта
```
Создай структуру Flask-проекта по описанию из CLAUDE.md.
Создай:
- app.py с Flask app, blueprint registration
- config.py с настройками (DB path, debug mode)
- database/db.py с функциями get_db(), close_db(), init_db()
- database/schema.sql — скопируй ВСЮ схему из docs-ai/SCHEMA.md
- requirements.txt (flask, gunicorn)

НЕ создавай routes пока. Только каркас.
Проверь что `python app.py` запускается без ошибок.
```

### Промпт 2: Seed данные
```
Создай database/seed.py который заполняет начальные данные:
- 6-8 сортов тюльпанов (придумай реалистичные названия и цвета,
  закупочная цена 15-25 грн, продажная 40-70 грн, stock_total по 300-500)
- 4 варианта упаковки (крафт, белая матовая, розовая, без упаковки=0 грн)
- 6 цветов лент
- system_settings: note_price=30, delivery_price=100, prepayment_percent=50,
  base_address="Измаил, ул. Центральная 1", max_bouquets_per_route=15

Seed должен быть идемпотентным (можно запускать повторно).
```

### Промпт 3: Базовый layout + мобильный UI
```
Создай templates/base.html:
- Tailwind CDN
- HTMX CDN
- Mobile-first viewport meta
- Навигация внизу экрана (как мобильное приложение):
  [Заказы] [Новый] [Склад] [Маршруты] [₴]
- Минимальный responsive: на десктопе навигация сверху

Создай route GET / → dashboard с заглушкой.
Проверь в Safari iPhone (или DevTools mobile).
```

### Промпт 4: CRUD справочников (admin)
```
Создай routes/inventory.py с Blueprint:
- GET /inventory — таблица сортов тюльпанов с остатками (stock bar)
  Цветовая индикация: зелёный >50%, жёлтый 20-50%, красный <20%
- POST /inventory/varieties/<id>/update — изменить цену, stock_total
- Отображение: "Что можно предложить" — только сорта с stock_available > 0

Используй HTMX для inline-редактирования цен.
Следуй схеме из docs-ai/SCHEMA.md.
```

---

## Фаза 2: Заказы (Промпты 5-8)

### Промпт 5: Форма нового заказа — шаг 1 (Заказчик + Получатель)
```
Создай routes/orders.py и templates/orders/new.html.

Форма — многошаговая (визуальные шаги, одна страница):
Шаг 1:
- Телефон заказчика (input tel, +380 маска)
- Имя заказчика (text, placeholder "Аноним")
- Телефон получателя (input tel)
- Имя получателя (text, обязательное)
- Переключатель: Самовывоз / Доставка
- Адрес доставки (textarea, показывать только если Доставка)
- Желаемое время (select: слоты 08-10, 10-12, ..., 18-20)

Mobile-first! Большие input'ы, удобно на iPhone.
Валидация телефона: автоформат +380XXXXXXXXX.
HTMX: при вводе телефона заказчика — автоподстановка имени если был.
```

### Промпт 6: Форма нового заказа — шаг 2 (Букет)
```
Добавь в форму заказа шаг 2 — Букет:
- Динамический список сортов: [Select сорт ▼] [Количество] [= Цена]
  Показывать stock_available рядом с каждым сортом!
  Кнопка [+ Добавить сорт]
- Упаковка: select из wrapping_options (включая "Без упаковки")
- Лента: select из ribbon_colors
- Записка: checkbox → если да, textarea для текста
- Авто-расчёт итого (HTMX или JS):
  Цветы: XXX грн
  Упаковка: XXX грн
  Записка: XXX грн
  Доставка: XXX грн
  ИТОГО: XXX грн
  Предоплата (50%): XXX грн

Следуй бизнес-правилам из docs-ai/BUSINESS_RULES.md (R1, R2, V2-V5).
```

### Промпт 7: Создание заказа (backend)
```
Реализуй services/order_service.py:
- create_order(data) — полный flow:
  1. Валидация входных данных (V1-V5 из BUSINESS_RULES.md)
  2. find_or_create_customer по телефону
  3. check_availability для каждого сорта (R2)
  4. snapshot_prices — зафиксировать текущие цены (R1)
  5. Рассчитать total_price
  6. INSERT orders + order_items в транзакции
  7. reserve stock (уменьшить stock_available)
  8. Вернуть order_id

Реализуй services/stock_service.py:
- check_availability(variety_id, qty) → bool
- reserve(variety_id, qty) → уменьшить stock_available
- release(order_id) → вернуть stock_available при отмене

Реализуй services/price_service.py:
- snapshot_prices(varieties, wrapping_id, has_note, is_pickup)
  → dict с зафиксированными ценами

POST /orders/new → вызывает create_order → redirect /orders/<id>
```

### Промпт 8: Список заказов + детали
```
Добавь в routes/orders.py:
- GET /orders — список заказов:
  Фильтры (HTMX): по статусу, по оплате, по дате
  Поиск по телефону/имени
  Таблица: №, Получатель, Адрес, Время, Статус, Оплата, Действия
  Цветовые метки: unpaid=красный, partial=жёлтый, paid=зелёный
  Кнопки быстрых действий (HTMX): изменить статус

- GET /orders/<id> — детали заказа:
  Вся информация
  Кнопки статусов (flow из ARCHITECTURE.md §8)
  Кнопка "Принять оплату"
  Кнопка "Отменить" (если статус позволяет, R6)
  Кнопка "Печать бирки"
  Кнопка "Печать сборочного листа"
```

---

## Фаза 3: Оплата (Промпты 9-11)

### Промпт 9: Система оплат
```
Реализуй services/payment_service.py:
- record_payment(order_id, amount, payment_type, received_by)
  → INSERT payment_log + UPDATE orders.paid_amount (R4)
  → Пересчёт payment_status (P1)
  → Обработка переплаты (P3)

Реализуй routes/payments.py:
- POST /payments/add — принять оплату (HTMX modal)
- GET /orders/<id>/payments — история платежей заказа

В форме оплаты показывать:
  Итого: XXX грн
  Оплачено: XXX грн
  К оплате: XXX грн
  [Сумма платежа] [Тип: нал/карта/перевод] [Принял: имя]
  [Принять оплату]
```

### Промпт 10: Финансовый дашборд
```
Добавь GET /payments — финансовый дашборд:
- Общая выручка (ожидаемая) = SUM(total_price) активных заказов
- Получено = SUM(paid_amount)
- К получению = SUM(total_price - paid_amount) где > 0
- Переплаты = SUM(overpayment)
- Себестоимость = SUM(purchase_price * quantity) по order_items
- Прибыль = Выручка - Себестоимость

- Таблица "Должники": заказы где payment_status != 'paid' и status != 'cancelled'
  Сортировка: сначала самые большие долги

- Таблица "Сегодняшние платежи": payment_log за сегодня
```

### Промпт 11: Динамическое ценообразование
```
Реализуй routes/pricing.py:
- GET /pricing — текущие цены всех сортов + упаковки
  Кнопка "Поднять все на X%"
  Кнопка "Снизить все на X%"
  Inline-редактирование каждой цены
  История изменений (price_change_log)

- POST /pricing/bulk-update — массовое изменение
  Параметры: percent (может быть отрицательным)
  Для каждого сорта: записать в price_change_log, обновить current_sell_price

- POST /pricing/update/<type>/<id> — поштучное изменение

Следуй PR1, PR2 из BUSINESS_RULES.md.
```

---

## Фаза 4: Доставка (Промпты 12-15)

### Промпт 12: Формирование маршрутов
```
Реализуй services/route_service.py:
- generate_route(time_slot, max_orders=15)
  1. Выбрать заказы: status='ready', is_pickup=0, route_id=NULL,
     desired_time=time_slot
  2. Ограничить по max_orders
  3. INSERT delivery_routes
  4. UPDATE orders SET route_id, route_order
  5. Посчитать total_orders
  6. Вернуть route_id

Реализуй routes/delivery.py:
- GET /routes — список маршрутов + кнопка "Сформировать"
- POST /routes/generate — форма: выбрать временной слот → генерация
- GET /routes/<id> — детали маршрута (список стопов)
```

### Промпт 13: Google Maps URL + QR
```
Добавь в route_service.py:
- generate_google_maps_url(route_id)
  Формат: https://www.google.com/maps/dir/BASE/ADDR1/ADDR2/.../BASE
  Сохранить в delivery_routes.google_maps_url

На странице маршрута показать:
- Кликабельная ссылка "Открыть в Google Maps"
- QR-код (использовать API: https://api.qrserver.com/v1/create-qr-code/?data=URL)
  Для печати маршрутного листа
```

### Промпт 14: Печать — маршрутный лист + бирки
```
Создай templates для печати (window.print()):

templates/print/route_sheet.html — маршрутный лист:
- Номер маршрута, дата, временной слот
- Количество букетов
- Таблица стопов:
  №, Адрес, Получатель, Телефон, Описание букета,
  Статус оплаты (ОПЛАЧЕНО ✓ / ДОПЛАТА: XXX грн ⚠️)
- QR-код Google Maps

templates/print/labels.html — бирки на букеты:
- Сетка бирок для печати на А4 (по 8 шт на лист)
- Каждая бирка: №заказа, имя получателя, адрес, маршрут+стоп, время

templates/print/assembly_sheet.html — сборочный лист:
- Для каждого заказа: сорта × количество, упаковка, лента, текст записки

CSS: @media print { навигация скрыта, только контент }
```

### Промпт 15: Мобильный интерфейс курьера
```
Создай GET /courier/<route_id> — мобильный вид для курьера:

Минимальный UI, крупные кнопки:
- Название маршрута + время
- Кнопка "Google Maps" (открывает ссылку)
- Список стопов (карточки):
  Большой номер стопа
  Адрес (крупно)
  Имя получателя + телефон (кликабельный для звонка)
  Описание букета (кратко)
  ДОПЛАТА: XXX грн (если есть, красным, крупно) или ОПЛАЧЕНО ✓ (зелёным)
  Кнопки: [✓ Доставлено] [✗ Не дозвонился] [↺ Перенос]

При нажатии "Доставлено":
- HTMX POST → обновить order_status='delivered'
- Карточка становится серой/зачёркнутой
```

---

## Фаза 5: Полировка (Промпты 16-19)

### Промпт 16: Дашборд
```
Обнови GET / (dashboard):
- Карточки с метриками:
  Всего заказов | Собрано | В доставке | Доставлено
  Выручка (ожид.) | Получено | К получению
- Список "Требует внимания":
  Неоплаченные заказы (красный)
  Заказы без маршрута со статусом 'ready' (жёлтый)
  Низкие остатки (<20%) (оранжевый)
```

### Промпт 17: "Что предложить клиенту"
```
Добавь GET /inventory/available — быстрый вид "Что можно предложить":
- Только сорта с stock_available > 0
- Текущая цена
- Доступно штук
- Примерные варианты: "Моно 25 шт = XXX грн", "Микс 25 шт = ~XXX грн"
- Ближайшие доступные слоты (показать какие времена не перегружены)

Этот экран оператор открывает при звонке клиента.
```

### Промпт 18: Отмена заказа
```
Реализуй POST /orders/<id>/cancel:
- Проверка: статус позволяет отмену (R6)
- order_service.cancel_order():
  1. UPDATE order_status = 'cancelled'
  2. stock_service.release(order_id)
  3. Предоплата НЕ возвращается (R3)
  4. Если заказ был в маршруте — убрать из маршрута

Подтверждение: "Вы уверены? Предоплата не возвращается."
```

### Промпт 19: Бэкапы + ngrok
```
Добавь в app.py:
- APScheduler: бэкап SQLite каждый час (по описанию из ARCHITECTURE.md §11)
- Хранить 7 дней, удалять старые

Создай start.sh:
  #!/bin/bash
  echo "Starting FlowerShop..."
  python database/seed.py
  python app.py &
  ngrok http 5000
  # Вывести ngrok URL для раздачи команде

Инструкция в README.md: как запустить, как раздать ссылку, как добавить на домашний экран iPhone.
```

---

## Фаза 6: Monobank (Промпт 20, опционально)

### Промпт 20: Monobank API
```
Добавь интеграцию с Monobank API:
- Документация: https://api.monobank.ua/docs/
- GET /api/personal/statement — получить выписку
- Polling каждые 60 секунд (не webhook для простоты)
- Сопоставление по сумме: если поступление == остаток по заказу
  → автоматически создать payment_log запись
  → обновить payment_status
- Если сопоставление неоднозначное (несколько заказов с такой суммой)
  → показать в UI для ручного подтверждения

Конфигурация: MONO_TOKEN в config.py (или env var)
```
