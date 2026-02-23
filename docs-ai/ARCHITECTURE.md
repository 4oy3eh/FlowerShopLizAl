# ARCHITECTURE.md — FlowerShop System

## 1. Обзор проекта

**Что:** Веб-система для сезонной продажи тюльпанов к 8 марта.
**Кто использует:** Оператор (приём заказов), Сборщик (сборка букетов), Курьер (доставка), Админ (цены, финансы).
**Масштаб:** ~120 заказов, ~3000 цветов, 6-8 сортов тюльпанов, 1 курьер на авто, 1 точка выдачи, г. Измаил.
**Срок жизни:** Инструмент на 2-3 недели. Простота > масштабируемость.

## 2. Технический стек

| Компонент | Технология | Почему |
|-----------|-----------|--------|
| Backend | Flask 3.x | Минимален, достаточен для 4 пользователей |
| Database | SQLite | Один файл, нет сервера БД, легко бэкапить |
| Frontend | Jinja2 + HTMX | Формы без SPA, работает в Safari iPhone |
| CSS | Tailwind CDN | Быстрый mobile-first UI без сборки |
| Maps | Google Maps URL | Бесплатно, без API ключа |
| Hosting | Локальный ПК + ngrok | Бесплатно, доступ по ссылке |
| Print | HTML → window.print() | Маршрутные листы и бирки |

## 3. Пользовательские роли (без авторизации)

Авторизации нет — доверенная сеть, 4 человека. Роли реализованы как разные точки входа:

- **`/`** — Дашборд: сводка по заказам, складу, финансам
- **`/orders/new`** — Форма нового заказа (Оператор, с телефона)
- **`/orders`** — Список заказов с фильтрами (Оператор/Админ)
- **`/inventory`** — Склад (Админ)
- **`/routes`** — Маршруты курьера (Админ → Курьер)
- **`/courier/<route_id>`** — Мобильный вид маршрута (Курьер)
- **`/payments`** — Финансы и сверка (Админ)
- **`/pricing`** — Управление ценами (Админ)

## 4. Поток данных (Data Flow)

### 4.1 Создание заказа
```
[Оператор вводит данные в форму с телефона]
       ↓
[POST /orders/new]
       ↓
[order_service.create_order()]
  ├── Валидация телефонов (формат +380XXXXXXXXX)
  ├── Поиск/создание customer по телефону
  ├── stock_service.check_availability() → FAIL если нет цветов
  ├── price_service.snapshot_prices() → ФИКСАЦИЯ текущих цен
  ├── stock_service.reserve() → уменьшение stock_available
  ├── Расчёт total_price
  ├── INSERT orders + order_items
  └── Redirect → /orders/<id>
```

### 4.2 Оплата
```
[Оператор/Курьер нажимает "Принять оплату"]
       ↓
[POST /payments/add]
       ↓
[payment_service.record_payment()]
  ├── INSERT payment_log (amount, type, received_by, timestamp)
  ├── UPDATE orders.paid_amount += amount
  ├── Пересчёт payment_status:
  │   paid_amount == 0        → 'unpaid'
  │   paid_amount < total     → 'partial'
  │   paid_amount >= total    → 'paid'
  │   paid_amount > total     → 'overpaid' + записать overpayment
  └── Response с новым статусом
```

### 4.3 Маршрут курьера
```
[Админ нажимает "Сформировать маршрут"]
       ↓
[POST /routes/generate]
       ↓
[route_service.generate_route()]
  ├── Выбрать заказы: status='ready', is_pickup=false, route_id=NULL
  ├── Фильтр по временному слоту
  ├── Ограничение по вместимости авто (~15-20 букетов)
  ├── Сортировка по адресу (простая — алфавитная, город маленький)
  ├── INSERT delivery_routes
  ├── UPDATE orders SET route_id, route_order
  ├── Генерация Google Maps URL
  └── Redirect → /routes/<id>
```

### 4.4 Отмена заказа
```
[Админ нажимает "Отменить"]
       ↓
[POST /orders/<id>/cancel]
       ↓
[order_service.cancel_order()]
  ├── UPDATE orders.order_status = 'cancelled'
  ├── stock_service.release() → возврат цветов на склад
  ├── Предоплата НЕ возвращается (бизнес-правило)
  └── Response
```

## 5. Google Maps URL

Формирование ссылки для курьера без API ключа:

```python
def generate_google_maps_url(base_address: str, delivery_addresses: list[str]) -> str:
    """
    Генерирует URL для Google Maps с маршрутом.
    base_address: адрес точки выдачи (старт и финиш)
    delivery_addresses: список адресов доставки в порядке маршрута
    """
    base = "https://www.google.com/maps/dir/"
    points = [base_address] + delivery_addresses + [base_address]
    encoded = [urllib.parse.quote(addr) for addr in points]
    return base + "/".join(encoded)
```

## 6. Ценообразование

### Формула цены заказа:
```
total = SUM(цветок_unit_price × quantity)  # из order_items
      + wrapping_price                      # 0 если без упаковки
      + note_price                          # 0 если без записки
      + delivery_price                      # 0 если самовывоз
```

### Динамическое ценообразование:
- `tulip_varieties.current_sell_price` — ТЕКУЩАЯ цена (меняется админом)
- При создании заказа цена КОПИРУЕТСЯ → `order_items.unit_price`
- Изменение текущей цены НЕ влияет на существующие заказы
- Админ может: поднять все цены на X%, снизить на X%, изменить поштучно
- Все изменения логируются в `price_change_log`

## 7. Управление складом

```
stock_available = stock_total - SUM(quantity из order_items
                                    WHERE order.status NOT IN ('cancelled', 'done'))
```

При создании заказа:
1. `stock_service.check_availability(variety_id, qty)` → bool
2. Если FALSE → показать что доступно, предложить замену
3. Если TRUE → `stock_service.reserve(variety_id, qty)`

При отмене заказа:
- `stock_service.release(order_id)` → возврат всех позиций на склад

## 8. Статусы заказа

```
new → confirmed → assembling → ready → delivering → delivered → done
  ↘
   cancelled (на любом этапе до 'delivering')
```

| Статус | Кто меняет | Когда |
|--------|-----------|-------|
| new | Система | Заказ создан |
| confirmed | Оператор | Предоплата получена |
| assembling | Сборщик | Начали собирать букет |
| ready | Сборщик | Букет готов, бирка приклеена |
| delivering | Курьер | Букет загружен в авто |
| delivered | Курьер | Букет отдан получателю |
| done | Система/Админ | Полная оплата получена |
| cancelled | Админ | Отмена на любом этапе до delivering |

## 9. Печатные материалы

### 9.1 Бирка на букет (HTML → print)
```
┌────────────────────────┐
│ #0042                  │
│ → Анна                 │
│ ул. Пушкина 15, кв 4   │
│ Маршрут #3, стоп 1     │
│ 10:00-12:00            │
│ ДОПЛАТА: 350 грн ⚠️    │
└────────────────────────┘
```

### 9.2 Маршрутный лист (HTML → print)
Содержит: номер маршрута, временной слот, список адресов с деталями заказов,
статус оплаты каждого, Google Maps QR-код.

### 9.3 Сборочный лист (HTML → print)
Содержит: номер заказа, список цветов по сортам и количеству,
тип упаковки, цвет ленты, текст записки.

## 10. Мобильный интерфейс

Приоритет — iPhone Safari. Ключевые экраны:

**Форма заказа (шаги):**
1. Заказчик: телефон → автоподстановка, имя/аноним
2. Получатель: имя, телефон, самовывоз/доставка, адрес, время
3. Букет: сорта + количество, упаковка, лента, записка
4. Итого: автокалькуляция, кнопка создания

**Курьер:**
- Список стопов текущего маршрута
- Кнопка "Доставлено" / "Не дозвонился"
- Ссылка Google Maps
- Сумма доплаты крупным шрифтом

## 11. Бэкапы

```python
# Каждый час копировать SQLite файл
import shutil
from datetime import datetime

def backup_db():
    src = "data/flower_shop.db"
    dst = f"data/backups/flower_shop_{datetime.now():%Y%m%d_%H%M}.db"
    shutil.copy2(src, dst)
```

Хранить бэкапы 7 дней, затем удалять.

## 12. Будущее (Phase 6, опционально)

- Monobank API интеграция для автоматической валидации поступлений
- Webhook от Mono → автоматическое подтверждение оплаты по сумме/комментарию
