"""FlowerShop application entry point.

Creates the Flask application, registers blueprints, initialises the
database schema, and starts the hourly backup scheduler.

Run with::

    python app.py

The app listens on ``0.0.0.0:5000`` so it is reachable from any device on
the local network (or via an ngrok tunnel).
"""

import os
import shutil
import urllib.request
from datetime import datetime, timedelta

from flask import Flask, render_template

from config import DELIVERY_DATES
from database.db import close_db, get_db, init_db, run_migrations


# ---------------------------------------------------------------------------
# Backup helpers
# ---------------------------------------------------------------------------

def _run_backup(db_path: str) -> None:
    """Copy the SQLite database file to the backups directory.

    The destination filename includes a ``YYYYMMDD_HHMM`` timestamp suffix.
    Backup files older than 7 days are automatically deleted.  This
    function does not require a Flask application context and is safe to
    call from a background thread.

    Args:
        db_path: Absolute path to the live ``flower_shop.db`` file.
    """
    backup_dir = os.path.join(os.path.dirname(db_path), 'backups')
    os.makedirs(backup_dir, exist_ok=True)

    dst = os.path.join(backup_dir, f"flower_shop_{datetime.now():%Y%m%d_%H%M}.db")
    try:
        shutil.copy2(db_path, dst)
    except OSError:
        return  # DB file may not exist yet on first start

    # Delete backups older than 7 days
    cutoff = (datetime.now() - timedelta(days=7)).timestamp()
    for fname in os.listdir(backup_dir):
        if fname.startswith('flower_shop_') and fname.endswith('.db'):
            fpath = os.path.join(backup_dir, fname)
            try:
                if os.path.getmtime(fpath) < cutoff:
                    os.remove(fpath)
            except OSError:
                pass


def _self_ping(url: str) -> None:
    """Send a GET request to the app's own URL to prevent idle sleep.

    Used on free-tier cloud hosts (e.g. Render) that spin down after
    ~15 minutes of inactivity.  Called by APScheduler every ~9 min ±2 min.

    Args:
        url: Public URL to ping, e.g. ``https://myapp.onrender.com``.
    """
    try:
        urllib.request.urlopen(url, timeout=10)
    except Exception:
        pass  # ignore errors — next ping will retry


def _start_scheduler(db_path: str) -> None:
    """Start the APScheduler background job that backs up the database hourly.

    The scheduler is intentionally skipped in Werkzeug's reloader child
    process (identified by ``WERKZEUG_RUN_MAIN=true``) to prevent two
    backup threads from running simultaneously.  If APScheduler is not
    installed a warning is printed and the function returns silently.

    Args:
        db_path: Absolute path to the live ``flower_shop.db`` file,
            passed through to :func:`_run_backup`.
    """
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        print('[backup] APScheduler not installed — automatic backups disabled.')
        print('[backup] Run: pip install APScheduler')
        return

    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(
        _run_backup,
        trigger='interval',
        hours=1,
        args=[db_path],
        id='db_backup',
        replace_existing=True,
    )

    # Self-ping to keep free-tier cloud hosts (Render etc.) awake.
    # Only activates when RENDER_EXTERNAL_URL is set by the hosting platform.
    render_url = os.environ.get('RENDER_EXTERNAL_URL', '').strip()
    if render_url:
        scheduler.add_job(
            _self_ping,
            trigger='interval',
            minutes=9,
            jitter=120,       # ±2 minutes random drift
            args=[render_url],
            id='self_ping',
            replace_existing=True,
        )
        print(f'[ping] Self-ping enabled → {render_url}')

    scheduler.start()
    print('[backup] Hourly backup scheduler started.')


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_app() -> Flask:
    """Create and configure the Flask application.

    Initialises the database schema (idempotent), registers all blueprints,
    and starts the hourly backup scheduler in the main process.

    Returns:
        Configured :class:`flask.Flask` application instance ready to serve.
    """
    app = Flask(__name__)
    app.config.from_object('config')

    # Ensure data/ directory exists before connecting
    os.makedirs(os.path.dirname(app.config['DATABASE']), exist_ok=True)

    # Close DB connection at the end of each request
    app.teardown_appcontext(close_db)

    # Initialize schema (CREATE IF NOT EXISTS — safe to run every startup)
    # then apply incremental migrations (idempotent, guarded by PRAGMA checks)
    with app.app_context():
        init_db()
        run_migrations()

    # ---------------------------------------------------------------------------
    # Blueprints
    # ---------------------------------------------------------------------------
    from routes.inventory import inventory_bp
    app.register_blueprint(inventory_bp)

    from routes.orders import orders_bp; app.register_blueprint(orders_bp)
    from routes.payments import payments_bp; app.register_blueprint(payments_bp)
    from routes.pricing import pricing_bp; app.register_blueprint(pricing_bp)
    from routes.delivery import delivery_bp, courier_bp
    app.register_blueprint(delivery_bp)
    app.register_blueprint(courier_bp)
    # from routes.api import api_bp; app.register_blueprint(api_bp)

    # ---------------------------------------------------------------------------
    # Hourly backup scheduler
    # Run only in the main process; Werkzeug reloader spawns a child process
    # identified by WERKZEUG_RUN_MAIN=true — we skip the scheduler there to
    # avoid running two backup threads simultaneously.
    # ---------------------------------------------------------------------------
    if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        _start_scheduler(app.config['DATABASE'])

    # ---------------------------------------------------------------------------
    # Dashboard
    # ---------------------------------------------------------------------------

    @app.route('/')
    def dashboard():
        """Render the main dashboard with operational metrics and alerts.

        Displays order counts by status, revenue totals, and three attention
        panels: unpaid orders, ready orders without a route, and low-stock
        varieties (below 20 % remaining).

        Returns:
            Rendered ``dashboard.html`` template.
        """
        db = get_db()

        # --- Order count metrics ---
        orders_total = db.execute(
            "SELECT COUNT(*) FROM orders WHERE order_status != 'cancelled'"
        ).fetchone()[0]
        orders_assembling = db.execute(
            "SELECT COUNT(*) FROM orders WHERE order_status = 'ready'"
        ).fetchone()[0]
        orders_delivering = db.execute(
            "SELECT COUNT(*) FROM orders WHERE order_status = 'delivering'"
        ).fetchone()[0]
        orders_delivered = db.execute(
            "SELECT COUNT(*) FROM orders"
            " WHERE order_status IN ('delivered', 'done')"
        ).fetchone()[0]

        # --- Revenue metrics ---
        revenue_expected = db.execute(
            "SELECT COALESCE(SUM(total_price), 0) FROM orders"
            " WHERE order_status != 'cancelled'"
        ).fetchone()[0]
        revenue_received = db.execute(
            "SELECT COALESCE(SUM(paid_amount), 0) FROM orders"
            " WHERE order_status != 'cancelled'"
        ).fetchone()[0]
        revenue_pending = db.execute(
            "SELECT COALESCE(SUM(total_price - paid_amount), 0) FROM orders"
            " WHERE order_status != 'cancelled'"
            "   AND payment_status != 'paid'"
        ).fetchone()[0]

        # --- Attention: unpaid / partially paid orders ---
        unpaid_orders = db.execute(
            "SELECT id, order_number, recipient_name, total_price, paid_amount"
            " FROM orders"
            " WHERE order_status NOT IN ('cancelled', 'done')"
            "   AND payment_status IN ('unpaid', 'partial')"
            " ORDER BY created_at"
        ).fetchall()

        # --- Attention: ready orders without a route (delivery only) ---
        ready_no_route = db.execute(
            "SELECT id, order_number, recipient_name, desired_time"
            " FROM orders"
            " WHERE order_status = 'ready'"
            "   AND is_pickup = 0"
            "   AND route_id IS NULL"
            " ORDER BY desired_time, created_at"
        ).fetchall()

        # --- Attention: low stock varieties (<20% remaining) ---
        low_stock = db.execute(
            "SELECT name, stock_available, stock_total"
            " FROM tulip_varieties"
            " WHERE is_active = 1"
            "   AND stock_total > 0"
            "   AND CAST(stock_available AS REAL) / stock_total < 0.2"
            " ORDER BY CAST(stock_available AS REAL) / stock_total"
        ).fetchall()

        # --- Per-date breakdown: orders count + stems count ---
        _date_rows = db.execute(
            """SELECT o.delivery_date,
                      COUNT(DISTINCT o.id)          AS orders_count,
                      COALESCE(SUM(oi.quantity), 0) AS stems_count
                 FROM orders o
                 LEFT JOIN order_items oi ON oi.order_id = o.id
                WHERE o.order_status != 'cancelled'
                GROUP BY o.delivery_date"""
        ).fetchall()
        _date_map = {r['delivery_date']: r for r in _date_rows}
        date_breakdown = [
            {
                'label':  d['label'],
                'value':  d['value'],
                'orders': (_date_map[d['value']]['orders_count'] if d['value'] in _date_map else 0),
                'stems':  (_date_map[d['value']]['stems_count']  if d['value'] in _date_map else 0),
            }
            for d in DELIVERY_DATES
        ]

        stats = {
            'orders_total': orders_total,
            'orders_assembling': orders_assembling,
            'orders_delivering': orders_delivering,
            'orders_delivered': orders_delivered,
            'revenue_expected': revenue_expected,
            'revenue_received': revenue_received,
            'revenue_pending': revenue_pending,
            'unpaid_orders': unpaid_orders,
            'ready_no_route': ready_no_route,
            'low_stock': low_stock,
            'date_breakdown': date_breakdown,
        }
        return render_template('dashboard.html', stats=stats)

    return app


if __name__ == '__main__':
    application = create_app()
    application.run(host='0.0.0.0', port=5000)
