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
