"""Database connection helpers for the Flask request context.

Usage::

    from database.db import get_db

    db = get_db()          # inside a request or app-context

Connections are stored on Flask's ``g`` object so the same connection is
reused within a single request and automatically closed at teardown via
:func:`close_db`.
"""

import os
import sqlite3

from flask import g, current_app

from config import DEFAULT_DELIVERY_DATE


def get_db() -> sqlite3.Connection:
    """Return the SQLite connection for the current request context.

    Creates a new connection on the first call within a request, then
    caches it on ``flask.g``.  Enables ``row_factory = sqlite3.Row`` for
    dict-style column access and turns on foreign-key enforcement.

    Returns:
        Open :class:`sqlite3.Connection` with ``row_factory`` set to
        :class:`sqlite3.Row`.
    """
    if 'db' not in g:
        g.db = sqlite3.connect(
            current_app.config['DATABASE'],
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
    return g.db


def close_db(e=None) -> None:
    """Close the database connection at the end of the request.

    Registered as a teardown handler via
    ``app.teardown_appcontext(close_db)``.

    Args:
        e: Optional exception passed by Flask's teardown mechanism.
            Not used.
    """
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db() -> None:
    """Execute ``schema.sql`` to create all tables (``IF NOT EXISTS``).

    Safe to call on every startup — existing tables and data are never
    dropped.  Must be called inside an active Flask application context.
    """
    db = get_db()
    schema_path = os.path.join(
        os.path.dirname(__file__), 'schema.sql'
    )
    with open(schema_path, encoding='utf-8') as f:
        db.executescript(f.read())


def run_migrations() -> None:
    """Apply incremental schema migrations, safe to re-run on every startup.

    Each migration is guarded by a ``PRAGMA table_info`` check so it is
    idempotent — running it against a database that already has the columns
    is a no-op.  Must be called inside an active Flask application context.

    Migrations applied here:
    - Add ``delivery_date`` (TEXT) to ``orders`` and ``delivery_routes``
    - Add ``tissue`` (TEXT, default 'florist') to ``orders``
    - Add ``wrapping_type`` (TEXT, default 'other') to ``wrapping_options``
    - Deactivate old wrapping options; insert new ones with correct types
    - Insert 'Выбор флориста' ribbon colour if absent
    """
    db = get_db()

    # --- orders.delivery_date ---
    orders_cols = {row[1] for row in db.execute('PRAGMA table_info(orders)')}
    if 'delivery_date' not in orders_cols:
        db.execute(
            f"ALTER TABLE orders "
            f"ADD COLUMN delivery_date TEXT NOT NULL DEFAULT '{DEFAULT_DELIVERY_DATE}'"
        )

    # --- delivery_routes.delivery_date ---
    routes_cols = {row[1] for row in db.execute('PRAGMA table_info(delivery_routes)')}
    if 'delivery_date' not in routes_cols:
        db.execute(
            'ALTER TABLE delivery_routes ADD COLUMN delivery_date TEXT'
        )

    # Index is idempotent via IF NOT EXISTS
    db.execute(
        'CREATE INDEX IF NOT EXISTS idx_orders_delivery_date ON orders(delivery_date)'
    )

    # --- orders.tissue ---
    orders_cols = {row[1] for row in db.execute('PRAGMA table_info(orders)')}
    if 'tissue' not in orders_cols:
        db.execute("ALTER TABLE orders ADD COLUMN tissue TEXT NOT NULL DEFAULT 'florist'")

    # --- wrapping_options.wrapping_type ---
    wrap_cols = {row[1] for row in db.execute('PRAGMA table_info(wrapping_options)')}
    if 'wrapping_type' not in wrap_cols:
        db.execute("ALTER TABLE wrapping_options ADD COLUMN wrapping_type TEXT DEFAULT 'other'")

    # --- Refresh wrapping catalogue (deactivate old, insert new) ---
    # Detect whether the new catalogue is already loaded by checking for
    # the sentinel 'florist' type entry.
    has_florist = db.execute(
        "SELECT 1 FROM wrapping_options WHERE wrapping_type = 'florist' LIMIT 1"
    ).fetchone()
    if not has_florist:
        # Mark every existing wrapping row as inactive
        db.execute("UPDATE wrapping_options SET is_active = 0")
        # Insert new catalogue entries (idempotent via WHERE NOT EXISTS)
        new_wrapping = [
            ('Выбор флориста',            'florist',   0.0),
            ('Замшевая молочная',         'замшевая',  40.0),
            ('Замшевая розовая',          'замшевая',  40.0),
            ('Замшевая чёрная',           'замшевая',  40.0),
            ('Замшевая красная',          'замшевая',  40.0),
            ('Каффин белый',              'каффин',    40.0),
            ('Каффин чёрный',             'каффин',    40.0),
            ('Каффин серый',              'каффин',    40.0),
            ('Каффин розовый',            'каффин',    40.0),
            ('Пленка прозрачная матовая', 'пленка',    30.0),
            ('Пленка белая',              'пленка',    30.0),
            ('Пленка чёрная',             'пленка',    30.0),
        ]
        for name, wtype, price in new_wrapping:
            db.execute(
                """INSERT INTO wrapping_options (name, wrapping_type, current_price)
                   SELECT ?, ?, ?
                   WHERE NOT EXISTS (SELECT 1 FROM wrapping_options WHERE name = ?)""",
                (name, wtype, price, name),
            )

    # --- packaging_price system setting ---
    db.execute(
        "INSERT OR IGNORE INTO system_settings (key, value) VALUES ('packaging_price', '120')"
    )

    # --- order_bouquets table ---
    existing_tables = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    if 'order_bouquets' not in existing_tables:
        db.executescript("""
            CREATE TABLE order_bouquets (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id         INTEGER NOT NULL REFERENCES orders(id),
                position         INTEGER NOT NULL DEFAULT 1,
                wrapping_id      INTEGER REFERENCES wrapping_options(id),
                ribbon_color_id  INTEGER NOT NULL REFERENCES ribbon_colors(id),
                tissue           TEXT NOT NULL DEFAULT 'florist',
                has_note         INTEGER NOT NULL DEFAULT 0,
                note_text        TEXT,
                wrapping_price   REAL NOT NULL DEFAULT 0,
                note_price       REAL NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_order_bouquets_order
                ON order_bouquets(order_id);
        """)

    # --- order_items.bouquet_id ---
    items_cols = {r[1] for r in db.execute('PRAGMA table_info(order_items)')}
    if 'bouquet_id' not in items_cols:
        db.execute(
            'ALTER TABLE order_items ADD COLUMN bouquet_id INTEGER REFERENCES order_bouquets(id)'
        )

    # --- 'Выбор флориста' ribbon colour ---
    has_florist_ribbon = db.execute(
        "SELECT 1 FROM ribbon_colors WHERE name = 'Выбор флориста' LIMIT 1"
    ).fetchone()
    if not has_florist_ribbon:
        # Insert with the lowest id so it appears first; use a temp row then fix id
        # Simpler: just insert normally — the form will put it first via ORDER
        db.execute(
            "INSERT INTO ribbon_colors (name) VALUES ('Выбор флориста')"
        )

    db.commit()
