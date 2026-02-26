"""Initial reference data loader for FlowerShop.

Idempotent — safe to run multiple times; existing rows are never
duplicated.

Usage (standalone)::

    python database/seed.py

Can also be called programmatically from :func:`database.db.init_db`.

Notes:
    "Без упаковки" is **not** stored as a wrapping row.  The schema uses
    ``wrapping_id IS NULL`` to represent no wrapping (price = 0).

    ``stock_available`` is set equal to ``stock_total`` on first insert
    because there are no reservations yet.

    System settings use ``INSERT OR REPLACE`` so the correct base address
    is written on every run even if it was manually changed in the DB.
"""

import os
import sqlite3
import sys

# Allow running as a standalone script from any directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402

# ------------------------------------------------------------------
# Seed data
# ------------------------------------------------------------------

# (name, color, purchase_price, sell_price, stock_total)
# Sell price: 50 грн — White flag, Sissi, Barcelona Beauty, Surrender, Lions Glory, City of Madrid
#             55 грн — Cosmic, Python, Akebono, Gaston
VARIETIES: list[tuple] = [
    ('White flag',        'белый',             15.0, 50.0, 300),
    ('Sissi',             'пурпурный',         15.0, 50.0, 300),
    ('Barcelona Beauty',  'оранжевый',         15.0, 50.0, 200),
    ('Surrender',         'белый',             15.0, 50.0, 100),
    ('Lions Glory',       'розовый',           15.0, 50.0, 200),
    ('City of Madrid',    'жёлтый',            15.0, 50.0, 100),
    ('Cosmic',            'тёмно-фиолетовый',  20.0, 55.0, 300),
    ('Python',            'попугайный',        20.0, 55.0, 300),
    ('Akebono',           'розовый',           20.0, 55.0, 100),
    ('Gaston',            'персиковый',        20.0, 55.0, 100),
]

# (name, wrapping_type, price)
# Prices are defaults — admin can update via /pricing.
# "Без упаковки" is intentionally absent: the schema uses wrapping_id = NULL for that case.
# "Выбор флориста" = florist picks any available wrapping (price = 0, resolved at assembly).
WRAPPING: list[tuple] = [
    ('Выбор флориста',        'florist',   0.0),
    # Замшевая
    ('Замшевая молочная',     'замшевая',  40.0),
    ('Замшевая розовая',      'замшевая',  40.0),
    ('Замшевая чёрная',       'замшевая',  40.0),
    ('Замшевая красная',      'замшевая',  40.0),
    # Каффин
    ('Каффин белый',          'каффин',    40.0),
    ('Каффин чёрный',         'каффин',    40.0),
    ('Каффин серый',          'каффин',    40.0),
    ('Каффин розовый',        'каффин',    40.0),
    # Плёнка
    ('Пленка прозрачная матовая', 'пленка', 30.0),
    ('Пленка белая',          'пленка',    30.0),
    ('Пленка чёрная',         'пленка',    30.0),
]

RIBBONS: list[str] = [
    'Выбор флориста',
    'Красная',
    'Белая',
    'Розовая',
    'Золотая',
    'Серебряная',
    'Сиреневая',
    'Чёрная',
]

# INSERT OR REPLACE — always write the correct values (e.g. full base_address).
SETTINGS: dict[str, str] = {
    'note_price':             '30',
    'delivery_price':         '100',
    'packaging_price':        '120',  # flat rate: charged if any wrapping OR tissue is present
    'prepayment_percent':     '50',
    'base_address':           'Измаил, ул. Миротворча(чилюскина) 36',
    'max_bouquets_per_route': '15',
}


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def seed(db: sqlite3.Connection) -> dict[str, int]:
    """Insert all seed data into an open connection.

    Calls each table-specific seeder in dependency order.  Does **not**
    commit — the caller is responsible for the transaction.

    Args:
        db: Open :class:`sqlite3.Connection`.

    Returns:
        Dict mapping table name to the number of rows inserted.  A count
        of ``0`` means the table was already seeded.
    """
    counts: dict[str, int] = {}
    counts['tulip_varieties']  = _seed_varieties(db)
    counts['wrapping_options'] = _seed_wrapping(db)
    counts['ribbon_colors']    = _seed_ribbons(db)
    counts['system_settings']  = _seed_settings(db)
    return counts


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _seed_varieties(db: sqlite3.Connection) -> int:
    """Insert tulip varieties if they do not already exist.

    Args:
        db: Open :class:`sqlite3.Connection`.

    Returns:
        Number of rows inserted (0 if already seeded).
    """
    inserted = 0
    for name, color, purchase_price, sell_price, stock_total in VARIETIES:
        cur = db.execute(
            """
            INSERT INTO tulip_varieties
                (name, color, purchase_price, current_sell_price,
                 stock_total, stock_available)
            SELECT ?, ?, ?, ?, ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM tulip_varieties WHERE name = ?
            )
            """,
            (name, color, purchase_price, sell_price,
             stock_total, stock_total,   # stock_available == stock_total initially
             name),
        )
        inserted += cur.rowcount
    return inserted


def _seed_wrapping(db: sqlite3.Connection) -> int:
    """Insert wrapping options if they do not already exist.

    Args:
        db: Open :class:`sqlite3.Connection`.

    Returns:
        Number of rows inserted (0 if already seeded).
    """
    inserted = 0
    for name, wrapping_type, price in WRAPPING:
        cur = db.execute(
            """
            INSERT INTO wrapping_options (name, wrapping_type, current_price)
            SELECT ?, ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM wrapping_options WHERE name = ?
            )
            """,
            (name, wrapping_type, price, name),
        )
        inserted += cur.rowcount
    return inserted


def _seed_ribbons(db: sqlite3.Connection) -> int:
    """Insert ribbon colours if they do not already exist.

    Args:
        db: Open :class:`sqlite3.Connection`.

    Returns:
        Number of rows inserted (0 if already seeded).
    """
    inserted = 0
    for name in RIBBONS:
        cur = db.execute(
            """
            INSERT INTO ribbon_colors (name)
            SELECT ?
            WHERE NOT EXISTS (
                SELECT 1 FROM ribbon_colors WHERE name = ?
            )
            """,
            (name, name),
        )
        inserted += cur.rowcount
    return inserted


def _seed_settings(db: sqlite3.Connection) -> int:
    """Insert (or overwrite) system settings.

    Uses ``INSERT OR REPLACE`` to ensure the correct values are always
    present even if they were changed manually in the database.

    Args:
        db: Open :class:`sqlite3.Connection`.

    Returns:
        Number of rows inserted or replaced.
    """
    inserted = 0
    for key, value in SETTINGS.items():
        cur = db.execute(
            'INSERT OR REPLACE INTO system_settings (key, value) VALUES (?, ?)',
            (key, value),
        )
        inserted += cur.rowcount
    return inserted


# ------------------------------------------------------------------
# Standalone entry point
# ------------------------------------------------------------------

def _get_connection() -> sqlite3.Connection:
    """Open a direct SQLite connection for standalone (non-Flask) use.

    Returns:
        :class:`sqlite3.Connection` with ``row_factory`` and foreign-key
        support enabled.
    """
    db = sqlite3.connect(config.DATABASE)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys = ON')
    return db


def _init_schema(db: sqlite3.Connection) -> None:
    """Execute ``schema.sql`` to ensure all tables exist.

    Used by the standalone entry point before seeding, in case the app has
    never been started yet and the tables don't exist.

    Args:
        db: Open :class:`sqlite3.Connection`.
    """
    schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
    with open(schema_path, encoding='utf-8') as f:
        db.executescript(f.read())


if __name__ == '__main__':
    os.makedirs(os.path.dirname(config.DATABASE), exist_ok=True)
    conn = _get_connection()
    try:
        _init_schema(conn)          # ensure tables exist if running before app.py
        counts = seed(conn)
        conn.commit()
        for table, n in counts.items():
            status = f'+{n} inserted' if n > 0 else 'already seeded'
            print(f'  {table}: {status}')
        print('Done.')
    finally:
        conn.close()
