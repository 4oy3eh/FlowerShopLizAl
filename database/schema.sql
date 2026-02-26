-- FlowerShop Database Schema
-- Conventions: snake_case tables, INTEGER PK, TEXT timestamps (ISO 8601), REAL money

-- ============================================================
-- Reference tables
-- ============================================================

CREATE TABLE IF NOT EXISTS tulip_varieties (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,                         -- "Красный Парад"
    color TEXT NOT NULL,                        -- "красный" (for quick filter)
    purchase_price REAL NOT NULL,               -- cost price per unit
    current_sell_price REAL NOT NULL,           -- current sell price (dynamic!)
    stock_total INTEGER NOT NULL DEFAULT 0,     -- total purchased
    stock_available INTEGER NOT NULL DEFAULT 0, -- available (total - reserved)
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS wrapping_options (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,                         -- "молочная", "белый"
    wrapping_type TEXT NOT NULL DEFAULT 'other',-- "florist", "замшевая", "каффин", "пленка"
    current_price REAL NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS ribbon_colors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,                         -- "Красная", "Белая"
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS system_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- ============================================================
-- Core tables
-- ============================================================

CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT NOT NULL UNIQUE,                 -- +380XXXXXXXXX
    name TEXT NOT NULL DEFAULT 'Аноним',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    notes TEXT
);

CREATE TABLE IF NOT EXISTS delivery_routes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    route_number INTEGER NOT NULL,              -- 1, 2, 3... per day
    status TEXT NOT NULL DEFAULT 'planning'
        CHECK(status IN ('planning', 'loading', 'in_progress', 'completed')),
    planned_start TEXT,
    actual_start TEXT,
    actual_end TEXT,
    total_orders INTEGER NOT NULL DEFAULT 0,
    google_maps_url TEXT,
    delivery_date TEXT,                                -- "2025-03-04" … "2025-03-09"
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_number TEXT NOT NULL UNIQUE,          -- "2025-0001" auto-generated

    -- Customer
    customer_id INTEGER NOT NULL REFERENCES customers(id),

    -- Recipient
    recipient_name TEXT NOT NULL,
    recipient_phone TEXT NOT NULL,
    delivery_address TEXT,                      -- NULL = pickup
    is_pickup INTEGER NOT NULL DEFAULT 0,
    desired_time TEXT,                          -- "08:00-10:00"

    -- Note card
    has_note INTEGER NOT NULL DEFAULT 0,
    note_text TEXT,

    -- Wrapping, ribbon and tissue
    wrapping_id INTEGER REFERENCES wrapping_options(id),  -- NULL = no wrapping
    ribbon_color_id INTEGER NOT NULL REFERENCES ribbon_colors(id),
    tissue TEXT NOT NULL DEFAULT 'florist',     -- "florist", "none", "white", "cream", "black", "pink"

    -- Prices (FIXED at order creation time!)
    flowers_total REAL NOT NULL DEFAULT 0,
    wrapping_price REAL NOT NULL DEFAULT 0,
    note_price REAL NOT NULL DEFAULT 0,
    delivery_price REAL NOT NULL DEFAULT 0,
    total_price REAL NOT NULL DEFAULT 0,

    -- Payment
    paid_amount REAL NOT NULL DEFAULT 0,
    overpayment REAL NOT NULL DEFAULT 0,
    payment_status TEXT NOT NULL DEFAULT 'unpaid'
        CHECK(payment_status IN ('unpaid', 'partial', 'paid', 'overpaid')),

    -- Order lifecycle
    order_status TEXT NOT NULL DEFAULT 'new'
        CHECK(order_status IN ('new','confirmed','assembling','ready','delivering','delivered','done','cancelled')),

    -- Delivery
    delivery_date TEXT NOT NULL DEFAULT '2025-03-08',  -- "2025-03-04" … "2025-03-09"
    route_id INTEGER REFERENCES delivery_routes(id),
    route_order INTEGER,

    -- Meta
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_by TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    variety_id INTEGER NOT NULL REFERENCES tulip_varieties(id),
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    unit_price REAL NOT NULL,                   -- FIXED at order creation time!
    line_total REAL NOT NULL                    -- quantity * unit_price
);

CREATE TABLE IF NOT EXISTS payment_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    amount REAL NOT NULL CHECK(amount > 0),
    payment_type TEXT NOT NULL DEFAULT 'cash'
        CHECK(payment_type IN ('cash', 'card', 'transfer')),
    received_by TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    notes TEXT
);

CREATE TABLE IF NOT EXISTS price_change_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,                  -- 'variety', 'wrapping', 'setting'
    entity_id INTEGER,
    field_name TEXT NOT NULL,                   -- 'current_sell_price'
    old_value TEXT,
    new_value TEXT,
    changed_by TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================
-- Indexes
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(order_status);
CREATE INDEX IF NOT EXISTS idx_orders_payment ON orders(payment_status);
CREATE INDEX IF NOT EXISTS idx_orders_route ON orders(route_id);
CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_payment_log_order ON payment_log(order_id);
CREATE INDEX IF NOT EXISTS idx_customers_phone ON customers(phone);
-- idx_orders_delivery_date is created in run_migrations() after the column is guaranteed to exist

-- ============================================================
-- Initial system settings
-- ============================================================

INSERT OR IGNORE INTO system_settings (key, value) VALUES
    ('note_price', '30'),
    ('delivery_price', '100'),
    ('prepayment_percent', '50'),
    ('base_address', 'Измаил'),
    ('max_bouquets_per_route', '15');
