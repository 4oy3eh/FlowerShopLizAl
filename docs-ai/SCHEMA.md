# SCHEMA.md — Database Schema

## Соглашения
- Все таблицы: `snake_case`
- PK: `id INTEGER PRIMARY KEY AUTOINCREMENT`
- FK: `<table>_id INTEGER REFERENCES <table>(id)`
- Timestamps: `TEXT` в формате ISO 8601 (SQLite не имеет DATETIME)
- Деньги: `REAL` (для ~120 заказов достаточно, не банк)
- Boolean: `INTEGER` (0/1)

---

## Справочники

### tulip_varieties
Сорта тюльпанов. Редактируется админом.

```sql
CREATE TABLE tulip_varieties (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,                    -- "Красный Парад"
    color TEXT NOT NULL,                   -- "красный" (для быстрого фильтра)
    purchase_price REAL NOT NULL,          -- закупочная цена за штуку
    current_sell_price REAL NOT NULL,      -- текущая цена продажи (динамическая!)
    stock_total INTEGER NOT NULL DEFAULT 0,-- закуплено всего
    stock_available INTEGER NOT NULL DEFAULT 0, -- доступно (total - зарезервировано)
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### wrapping_options
Варианты упаковки (бумага).

```sql
CREATE TABLE wrapping_options (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,                    -- "Крафт бумага", "Белая матовая"
    current_price REAL NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
);
```

### ribbon_colors
Цвета лент. Лента входит в стоимость всегда.

```sql
CREATE TABLE ribbon_colors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,                    -- "Красная", "Белая"
    is_active INTEGER NOT NULL DEFAULT 1
);
```

### system_settings
Глобальные настройки (key-value).

```sql
CREATE TABLE system_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Начальные значения:
-- note_price: "30"           (цена записки)
-- delivery_price: "100"      (цена доставки)
-- prepayment_percent: "50"   (% предоплаты)
-- base_address: "Измаил, ..."  (адрес точки выдачи)
-- max_bouquets_per_route: "15"
```

---

## Основные таблицы

### customers
Заказчики. Уникальность по телефону.

```sql
CREATE TABLE customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT NOT NULL UNIQUE,            -- +380XXXXXXXXX
    name TEXT NOT NULL DEFAULT 'Аноним',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    notes TEXT
);
```

### orders
Заказы. Центральная таблица.

```sql
CREATE TABLE orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_number TEXT NOT NULL UNIQUE,     -- "2025-0001" автогенерация

    -- Заказчик
    customer_id INTEGER NOT NULL REFERENCES customers(id),

    -- Получатель
    recipient_name TEXT NOT NULL,
    recipient_phone TEXT NOT NULL,
    delivery_address TEXT,                 -- NULL = самовывоз
    is_pickup INTEGER NOT NULL DEFAULT 0,
    desired_time TEXT,                     -- "08:00-10:00"

    -- Записка
    has_note INTEGER NOT NULL DEFAULT 0,
    note_text TEXT,

    -- Упаковка и лента
    wrapping_id INTEGER REFERENCES wrapping_options(id),  -- NULL = без упаковки
    ribbon_color_id INTEGER NOT NULL REFERENCES ribbon_colors(id),

    -- Цены (ФИКСИРОВАНЫ на момент заказа!)
    flowers_total REAL NOT NULL DEFAULT 0,
    wrapping_price REAL NOT NULL DEFAULT 0,
    note_price REAL NOT NULL DEFAULT 0,
    delivery_price REAL NOT NULL DEFAULT 0,
    total_price REAL NOT NULL DEFAULT 0,

    -- Оплата
    paid_amount REAL NOT NULL DEFAULT 0,
    overpayment REAL NOT NULL DEFAULT 0,
    payment_status TEXT NOT NULL DEFAULT 'unpaid'
        CHECK(payment_status IN ('unpaid', 'partial', 'paid', 'overpaid')),

    -- Статусы
    order_status TEXT NOT NULL DEFAULT 'new'
        CHECK(order_status IN ('new','confirmed','assembling','ready','delivering','delivered','done','cancelled')),

    -- Доставка
    route_id INTEGER REFERENCES delivery_routes(id),
    route_order INTEGER,

    -- Мета
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_by TEXT,
    notes TEXT
);
```

### order_items
Позиции заказа (цветы). Связь many-to-one с orders.

```sql
CREATE TABLE order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    variety_id INTEGER NOT NULL REFERENCES tulip_varieties(id),
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    unit_price REAL NOT NULL,              -- ФИКСИРОВАНА на момент заказа!
    line_total REAL NOT NULL               -- quantity * unit_price
);
```

### payment_log
Лог каждого платежа. Никогда не удаляется.

```sql
CREATE TABLE payment_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    amount REAL NOT NULL CHECK(amount > 0),
    payment_type TEXT NOT NULL DEFAULT 'cash'
        CHECK(payment_type IN ('cash', 'card', 'transfer')),
    received_by TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    notes TEXT
);
```

### delivery_routes
Маршруты курьера.

```sql
CREATE TABLE delivery_routes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    route_number INTEGER NOT NULL,         -- 1, 2, 3... за день
    status TEXT NOT NULL DEFAULT 'planning'
        CHECK(status IN ('planning', 'loading', 'in_progress', 'completed')),
    planned_start TEXT,
    actual_start TEXT,
    actual_end TEXT,
    total_orders INTEGER NOT NULL DEFAULT 0,
    google_maps_url TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### price_change_log
Лог изменений цен для аудита.

```sql
CREATE TABLE price_change_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,             -- 'variety', 'wrapping', 'setting'
    entity_id INTEGER,
    field_name TEXT NOT NULL,              -- 'current_sell_price'
    old_value TEXT,
    new_value TEXT,
    changed_by TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

---

## Индексы

```sql
CREATE INDEX idx_orders_status ON orders(order_status);
CREATE INDEX idx_orders_payment ON orders(payment_status);
CREATE INDEX idx_orders_route ON orders(route_id);
CREATE INDEX idx_orders_customer ON orders(customer_id);
CREATE INDEX idx_order_items_order ON order_items(order_id);
CREATE INDEX idx_payment_log_order ON payment_log(order_id);
CREATE INDEX idx_customers_phone ON customers(phone);
```

---

## Автогенерация номера заказа

```sql
-- Формат: "2025-XXXX"
-- При INSERT: SELECT printf('2025-%04d', COALESCE(MAX(id), 0) + 1) FROM orders;
```
