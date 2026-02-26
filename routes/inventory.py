"""Inventory management routes (Flask Blueprint ``inventory``).

URL prefix: ``/inventory``

Endpoints:
    GET  /inventory/                       — full inventory page
    GET  /inventory/available              — "what can we offer" quick view
    GET  /inventory/varieties/<id>/row     — HTMX: display row partial
    GET  /inventory/varieties/<id>/edit    — HTMX: edit form partial
    POST /inventory/varieties/<id>/update  — save variety changes
"""

from flask import Blueprint, abort, render_template, request

from config import DELIVERY_DATES, TIME_SLOTS
from database.db import get_db

inventory_bp = Blueprint('inventory', __name__, url_prefix='/inventory')

_BOUQUET_SIZES = [11, 25, 51]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_variety_or_404(variety_id: int):
    """Fetch a tulip variety row by ID, or abort with HTTP 404.

    Args:
        variety_id: Primary key in ``tulip_varieties``.

    Returns:
        ``sqlite3.Row`` for the requested variety.

    Raises:
        werkzeug.exceptions.NotFound: If no row with that ID exists.
    """
    row = get_db().execute(
        'SELECT * FROM tulip_varieties WHERE id = ?', (variety_id,)
    ).fetchone()
    if row is None:
        abort(404)
    return row


def _reserved_qty(variety_id: int) -> int:
    """Count stems currently reserved in active orders.

    Active means any status that is neither ``'cancelled'`` nor ``'done'``.

    Args:
        variety_id: Primary key in ``tulip_varieties``.

    Returns:
        Total quantity reserved across all active order items.
    """
    row = get_db().execute(
        """SELECT COALESCE(SUM(oi.quantity), 0)
             FROM order_items oi
             JOIN orders o ON o.id = oi.order_id
            WHERE oi.variety_id = ?
              AND o.order_status NOT IN ('cancelled', 'done')""",
        (variety_id,),
    ).fetchone()
    return row[0]


# ---------------------------------------------------------------------------
# GET /inventory  — full page
# ---------------------------------------------------------------------------

@inventory_bp.route('/', strict_slashes=False)
def index():
    """Render the full inventory management page.

    Displays all active varieties with their stock totals, available counts,
    and reserved quantities.

    Returns:
        Rendered ``inventory/index.html`` template.
    """
    db = get_db()
    varieties = db.execute(
        'SELECT * FROM tulip_varieties WHERE is_active = 1 ORDER BY name'
    ).fetchall()

    total_stock     = sum(v['stock_total']     for v in varieties)
    total_available = sum(v['stock_available'] for v in varieties)
    total_reserved  = total_stock - total_available

    # "Что можно предложить" — sorted by available qty desc
    available = sorted(
        [v for v in varieties if v['stock_available'] > 0],
        key=lambda v: v['stock_available'],
        reverse=True,
    )

    return render_template(
        'inventory/index.html',
        varieties=varieties,
        available=available,
        total_stock=total_stock,
        total_available=total_available,
        total_reserved=total_reserved,
    )


# ---------------------------------------------------------------------------
# GET /inventory/available  — "what can we offer" quick view for operator
# ---------------------------------------------------------------------------

@inventory_bp.route('/available')
def available():
    """Render the "what can we offer" quick-view page for operators.

    Calculates mono bouquet options per variety, weighted-average mix
    bouquet prices, and current time-slot occupancy for delivery orders.

    Returns:
        Rendered ``inventory/available.html`` template.
    """
    db = get_db()

    varieties = db.execute(
        'SELECT * FROM tulip_varieties'
        ' WHERE is_active = 1 AND stock_available > 0'
        ' ORDER BY stock_available DESC'
    ).fetchall()

    settings_rows = db.execute('SELECT key, value FROM system_settings').fetchall()
    settings = {r['key']: r['value'] for r in settings_rows}
    delivery_price = int(float(settings.get('delivery_price', 100)))
    max_per_route  = int(settings.get('max_bouquets_per_route', 15))

    # Mono bouquet options: per variety × per standard size (only if enough stock)
    mono_opts = []
    for v in varieties:
        sizes = [
            {'size': s, 'price': s * v['current_sell_price']}
            for s in _BOUQUET_SIZES
            if v['stock_available'] >= s
        ]
        if sizes:
            mono_opts.append({'variety': v, 'sizes': sizes})

    # Mix bouquet options: weighted-average price, need ≥2 varieties
    total_avail = sum(v['stock_available'] for v in varieties)
    if len(varieties) >= 2 and total_avail > 0:
        avg_price = sum(v['current_sell_price'] * v['stock_available'] for v in varieties) / total_avail
        mix_opts = [
            {'size': s, 'price': round(s * avg_price)}
            for s in _BOUQUET_SIZES
            if total_avail >= s
        ]
    else:
        mix_opts = []

    # Reserved stems grouped by delivery date (active orders only)
    _rbd_rows = db.execute(
        """SELECT o.delivery_date,
                  COALESCE(SUM(oi.quantity), 0) AS stems
             FROM order_items oi
             JOIN orders o ON o.id = oi.order_id
            WHERE o.order_status NOT IN ('cancelled', 'done')
            GROUP BY o.delivery_date"""
    ).fetchall()
    _rbd_map = {r['delivery_date']: r['stems'] for r in _rbd_rows}
    reserved_by_date = [
        {'label': d['label'], 'stems': _rbd_map[d['value']]}
        for d in DELIVERY_DATES
        if _rbd_map.get(d['value'], 0) > 0
    ]

    # Time slot occupancy for delivery orders
    slot_rows = db.execute(
        "SELECT desired_time, COUNT(*) AS cnt FROM orders"
        " WHERE order_status NOT IN ('cancelled', 'done')"
        "   AND is_pickup = 0 AND desired_time IS NOT NULL"
        " GROUP BY desired_time"
    ).fetchall()
    slot_counts = {r['desired_time']: r['cnt'] for r in slot_rows}

    slots = []
    for t in TIME_SLOTS:
        count = slot_counts.get(t, 0)
        free  = max_per_route - count
        pct   = min(count * 100 // max_per_route, 100) if max_per_route > 0 else 100
        slots.append({'time': t, 'count': count, 'max': max_per_route, 'free': free, 'pct': pct})

    return render_template(
        'inventory/available.html',
        varieties=varieties,
        mono_opts=mono_opts,
        mix_opts=mix_opts,
        slots=slots,
        delivery_price=delivery_price,
        total_avail=total_avail,
        reserved_by_date=reserved_by_date,
    )


# ---------------------------------------------------------------------------
# GET /inventory/varieties/<id>/row  — display partial (used by HTMX cancel)
# ---------------------------------------------------------------------------

@inventory_bp.route('/varieties/<int:variety_id>/row')
def variety_row(variety_id):
    """HTMX partial: render the display row for one variety.

    Used to restore the read-only row after an edit is cancelled.

    Args:
        variety_id: Primary key of the variety.

    Returns:
        Rendered ``inventory/_row.html`` partial, or HTTP 404.
    """
    v = _get_variety_or_404(variety_id)
    return render_template('inventory/_row.html', v=v)


# ---------------------------------------------------------------------------
# GET /inventory/varieties/<id>/edit  — edit form partial
# ---------------------------------------------------------------------------

@inventory_bp.route('/varieties/<int:variety_id>/edit')
def edit_row(variety_id):
    """HTMX partial: render the inline edit form for one variety.

    Also fetches the current reserved quantity to display the minimum
    allowed stock total.

    Args:
        variety_id: Primary key of the variety.

    Returns:
        Rendered ``inventory/_edit_row.html`` partial, or HTTP 404.
    """
    v = _get_variety_or_404(variety_id)
    reserved = _reserved_qty(variety_id)
    return render_template('inventory/_edit_row.html', v=v, reserved=reserved)


# ---------------------------------------------------------------------------
# POST /inventory/varieties/<id>/update  — save, return updated display partial
# ---------------------------------------------------------------------------

@inventory_bp.route('/varieties/<int:variety_id>/update', methods=['POST'])
def update_variety(variety_id):
    """Save price and stock-total changes for a variety.

    Validates that the new total is not below the currently reserved
    quantity.  Logs price/stock changes to ``price_change_log``.  Returns
    the updated display row partial on success, or the edit form with an
    inline error on validation failure.

    Args:
        variety_id: Primary key of the variety to update.

    Returns:
        Rendered ``inventory/_row.html`` on success, or
        ``inventory/_edit_row.html`` (HTTP 200) on validation error so
        HTMX swaps the content in-place.
    """
    db  = get_db()
    v   = _get_variety_or_404(variety_id)

    # ── Parse input ──────────────────────────────────────────────────────
    error = None
    new_price = None
    new_total = None
    try:
        new_price = float(request.form['current_sell_price'])
        new_total = int(request.form['stock_total'])
    except (ValueError, KeyError):
        error = 'Введите числовые значения'

    # ── Validate ─────────────────────────────────────────────────────────
    if error is None:
        reserved = _reserved_qty(variety_id)

        if new_price <= 0:
            error = 'Цена должна быть больше 0'
        elif new_total < 0:
            error = 'Количество не может быть отрицательным'
        elif new_total < reserved:
            error = (
                f'Нельзя: {reserved} шт. уже зарезервировано в заказах — '
                f'минимум {reserved} шт.'
            )

    if error:
        # Return edit form with error (HTTP 200 so HTMX swaps the content)
        reserved = _reserved_qty(variety_id)
        return render_template(
            'inventory/_edit_row.html', v=v, reserved=reserved, error=error
        )

    # ── Persist ──────────────────────────────────────────────────────────
    new_available = new_total - reserved   # reserved calculated above

    if new_price != v['current_sell_price']:
        db.execute(
            """INSERT INTO price_change_log
                    (entity_type, entity_id, field_name, old_value, new_value, changed_by)
               VALUES ('variety', ?, 'current_sell_price', ?, ?, 'admin')""",
            (variety_id, str(v['current_sell_price']), str(new_price)),
        )

    if new_total != v['stock_total']:
        db.execute(
            """INSERT INTO price_change_log
                    (entity_type, entity_id, field_name, old_value, new_value, changed_by)
               VALUES ('variety', ?, 'stock_total', ?, ?, 'admin')""",
            (variety_id, str(v['stock_total']), str(new_total)),
        )

    db.execute(
        """UPDATE tulip_varieties
              SET current_sell_price = ?,
                  stock_total        = ?,
                  stock_available    = ?
            WHERE id = ?""",
        (new_price, new_total, new_available, variety_id),
    )
    db.commit()

    updated = db.execute(
        'SELECT * FROM tulip_varieties WHERE id = ?', (variety_id,)
    ).fetchone()
    return render_template('inventory/_row.html', v=updated)
