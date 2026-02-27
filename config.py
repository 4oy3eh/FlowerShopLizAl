"""Flask application configuration.

Loaded via ``app.config.from_object('config')``.  All values are consumed
by Flask directly — do not import this module from application code; use
``current_app.config`` instead.

Attributes:
    DATABASE: Absolute path to the SQLite database file.
    DEBUG: Enable Flask debug mode and the Werkzeug auto-reloader.
    SECRET_KEY: Used by Flask to sign session cookies.  Change in
        production (this is a trusted LAN app so the default is
        acceptable for the 2-week campaign).
"""

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# On Render the persistent disk is always mounted at /data — use it directly
# so the DB survives deploys even if DATABASE_PATH env var is not set.
# Locally (no RENDER env) fall back to ./data/flower_shop.db.
if os.environ.get('RENDER'):
    DATABASE = os.environ.get('DATABASE_PATH', '/data/flower_shop.db')
else:
    DATABASE = os.environ.get('DATABASE_PATH') or os.path.join(BASE_DIR, 'data', 'flower_shop.db')
DEBUG = os.environ.get('FLASK_DEBUG', 'true').lower() == 'true'
SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-flower-shop')

# ---------------------------------------------------------------------------
# Seasonal delivery configuration (4–9 March)
# ---------------------------------------------------------------------------

DELIVERY_DATES = [
    {"value": "2025-03-04", "label": "4"},
    {"value": "2025-03-05", "label": "5"},
    {"value": "2025-03-06", "label": "6"},
    {"value": "2025-03-07", "label": "7"},
    {"value": "2025-03-08", "label": "8"},
    {"value": "2025-03-09", "label": "9"},
]
DEFAULT_DELIVERY_DATE = "2025-03-08"

TIME_SLOTS = [
    "08:00-10:00", "10:00-12:00", "12:00-14:00",
    "14:00-16:00", "16:00-18:00", "18:00-20:00", "20:00-22:00",
]
