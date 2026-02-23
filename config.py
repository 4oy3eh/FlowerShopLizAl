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

DATABASE = os.path.join(BASE_DIR, 'data', 'flower_shop.db')
DEBUG = True
SECRET_KEY = 'dev-secret-flower-shop'
