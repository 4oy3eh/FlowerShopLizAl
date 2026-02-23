"""Delivery route management and courier mobile interface.

Contains two Flask Blueprints:
    ``delivery_bp`` — dispatcher/admin views (``/routes``)
    ``courier_bp``  — mobile courier view (``/courier``)

Dispatcher endpoints:
    GET  /routes/                          — route list + generate form
    POST /routes/generate                  — create a route for a time slot
    GET  /routes/<id>                      — route detail / stop list
    POST /routes/<id>/status               — advance route status
    GET  /routes/<id>/print/route-sheet    — print route sheet (new tab)
    GET  /routes/<id>/print/labels         — print delivery labels (new tab)
    GET  /routes/<id>/print/assembly       — print assembly sheet (new tab)

Courier endpoints:
    GET  /courier/<route_id>                           — mobile route view
    POST /courier/<route_id>/stop/<order_id>/delivered — mark stop delivered
    POST /courier/<route_id>/stop/<order_id>/missed    — record failed attempt
    POST /courier/<route_id>/stop/<order_id>/postpone  — defer stop
"""

from flask import Blueprint, flash, redirect, render_template, request

from urllib.parse import quote as url_quote

from database.db import get_db
from services.route_service import generate_google_maps_url, generate_route

delivery_bp = Blueprint('delivery', __name__, url_prefix='/routes')

# Time slots must match the values stored in orders.desired_time
TIME_SLOTS = ['08-10', '10-12', '12-14', '14-16', '16-18', '18-20']

ROUTE_STATUS_LABELS = {
    'planning':    'Планирование',
    'loading':     'Загрузка',
    'in_progress': 'В пути',
    'completed':   'Завершён',
}

ROUTE_STATUS_COLORS = {
    'planning':    'bg-blue-100 text-blue-700',
    'loading':     'bg-amber-100 text-amber-700',
    'in_progress': 'bg-orange-100 text-orange-700',
    'completed':   'bg-green-100 text-green-700',
}

# Next status in the state machine
ROUTE_STATUS_FLOW = {
    'planning':    'loading',
    'loading':     'in_progress',
    'in_progress': 'completed',
}

ROUTE_NEXT_LABEL = {
    'planning':    'Начать загрузку',
    'loading':     'Курьер выехал',
    'in_progress': 'Завершить маршрут',
}

ORDER_STATUS_LABELS = {
    'new':        'Новый',
    'confirmed':  'Подтверждён',
    'assembling': 'Сборка',
    'ready':      'Готов',
    'delivering': 'Доставка',
    'delivered':  'Доставлен',
    'done':       'Завершён',
    'cancelled':  'Отменён',
}

ORDER_STATUS_COLORS = {
    'new':        'bg-blue-100 text-blue-700',
    'confirmed':  'bg-amber-100 text-amber-700',
    'assembling': 'bg-violet-100 text-violet-700',
    'ready':      'bg-green-100 text-green-700',
    'delivering': 'bg-orange-100 text-orange-700',
    'delivered':  'bg-teal-100 text-teal-700',
    'done':       'bg-gray-100 text-gray-600',
    'cancelled':  'bg-red-100 text-red-700',
}

PAYMENT_LABELS = {
    'unpaid':   'Не оплачен',
    'partial':  'Предоплата',
    'paid':     'Оплачен',
    'overpaid': 'Переплата',
}

PAYMENT_COLORS = {
    'unpaid':   'bg-red-100 text-red-700',
    'partial':  'bg-amber-100 text-amber-700',
    'paid':     'bg-green-100 text-green-700',
    'overpaid': 'bg-teal-100 text-teal-700',
}


@delivery_bp.context_processor
def _inject_helpers():
    """Inject route/order/payment display dictionaries into every delivery template."""
    return {
        'ROUTE_STATUS_LABELS': ROUTE_STATUS_LABELS,
        'ROUTE_STATUS_COLORS': ROUTE_STATUS_COLORS,
        'ROUTE_STATUS_FLOW':   ROUTE_STATUS_FLOW,
        'ROUTE_NEXT_LABEL':    ROUTE_NEXT_LABEL,
        'ORDER_STATUS_LABELS': ORDER_STATUS_LABELS,
        'ORDER_STATUS_COLORS': ORDER_STATUS_COLORS,
        'PAYMENT_LABELS':      PAYMENT_LABELS,
        'PAYMENT_COLORS':      PAYMENT_COLORS,
    }


# ---------------------------------------------------------------------------
# GET /routes  — list all routes + generate form
# ---------------------------------------------------------------------------

@delivery_bp.route('/', strict_slashes=False)
def route_list():
    """Render the route list page with the "generate new route" form.

    Also shows how many unassigned ready-delivery orders exist per time
    slot so the dispatcher knows which slots are ready to route.

    Returns:
        Rendered ``delivery/index.html`` template.
    """
    db = get_db()

    routes = db.execute(
        """SELECT r.*,
                  (SELECT COUNT(*) FROM orders o
                    WHERE o.route_id = r.id
                      AND o.order_status = 'delivered') AS delivered_count
             FROM delivery_routes r
            ORDER BY r.created_at DESC"""
    ).fetchall()

    # How many unassigned ready-delivery orders exist per time slot
    slot_counts = {}
    for slot in TIME_SLOTS:
        cnt = db.execute(
            """SELECT COUNT(*) FROM orders
                WHERE order_status = 'ready'
                  AND is_pickup   = 0
                  AND route_id    IS NULL
                  AND desired_time = ?""",
            (slot,),
        ).fetchone()[0]
        slot_counts[slot] = cnt

    return render_template(
        'delivery/index.html',
        routes=routes,
        time_slots=TIME_SLOTS,
        slot_counts=slot_counts,
    )


# ---------------------------------------------------------------------------
# POST /routes/generate  — create a new route for a time slot
# ---------------------------------------------------------------------------

@delivery_bp.route('/generate', methods=['POST'])
def generate_route_view():
    """Create a new delivery route for the selected time slot.

    Delegates to :func:`services.route_service.generate_route`.  On success
    redirects to the new route detail page; on failure flashes an error and
    returns to the route list.

    Returns:
        Redirect to ``/routes/<id>`` on success, or to ``/routes`` on error.
    """
    time_slot = request.form.get('time_slot', '').strip()
    if not time_slot:
        flash('Выберите временной слот', 'error')
        return redirect('/routes')

    db = get_db()

    # Read max_bouquets_per_route from system settings (default 15)
    row = db.execute(
        "SELECT value FROM system_settings WHERE key = 'max_bouquets_per_route'"
    ).fetchone()
    max_orders = int(row['value']) if row else 15

    try:
        route_id = generate_route(db, time_slot, max_orders)
        flash('Маршрут сформирован!', 'success')
        return redirect(f'/routes/{route_id}')
    except ValueError as exc:
        flash(str(exc), 'error')
        return redirect('/routes')


# ---------------------------------------------------------------------------
# GET /routes/<id>  — route detail / stop list
# ---------------------------------------------------------------------------

@delivery_bp.route('/<int:route_id>')
def route_detail(route_id):
    """Render the route detail page with its stop list and Google Maps QR code.

    Args:
        route_id: Primary key of the delivery route.

    Returns:
        Rendered ``delivery/detail.html`` template, or a redirect to
        ``/routes`` if the route is not found.
    """
    db = get_db()

    route = db.execute(
        'SELECT * FROM delivery_routes WHERE id = ?', (route_id,)
    ).fetchone()

    if route is None:
        flash('Маршрут не найден', 'error')
        return redirect('/routes')

    stops = db.execute(
        """SELECT o.id, o.order_number, o.route_order,
                  o.recipient_name, o.recipient_phone,
                  o.delivery_address, o.desired_time,
                  o.order_status, o.payment_status,
                  o.total_price, o.paid_amount,
                  o.has_note, o.note_text,
                  w.name AS wrapping_name
             FROM orders o
             LEFT JOIN wrapping_options w ON o.wrapping_id = w.id
            WHERE o.route_id = ?
            ORDER BY o.route_order""",
        (route_id,),
    ).fetchall()

    maps_url = route['google_maps_url'] or ''
    qr_url = (
        'https://api.qrserver.com/v1/create-qr-code/'
        f'?size=200x200&data={url_quote(maps_url, safe="")}'
        if maps_url else ''
    )

    return render_template(
        'delivery/detail.html',
        route=route,
        stops=stops,
        qr_url=qr_url,
    )


# ---------------------------------------------------------------------------
# POST /routes/<id>/status  — advance route to next status
# ---------------------------------------------------------------------------

@delivery_bp.route('/<int:route_id>/status', methods=['POST'])
def advance_route_status(route_id):
    """Advance a route to the next status and record transition timestamps.

    Records ``actual_start`` when moving to ``in_progress`` and
    ``actual_end`` when completing the route.

    Args:
        route_id: Primary key of the delivery route.

    Returns:
        Redirect to the route detail page.
    """
    db = get_db()

    route = db.execute(
        'SELECT status FROM delivery_routes WHERE id = ?', (route_id,)
    ).fetchone()

    if route is None:
        flash('Маршрут не найден', 'error')
        return redirect('/routes')

    nxt = ROUTE_STATUS_FLOW.get(route['status'])
    if nxt is None:
        flash('Маршрут уже завершён', 'error')
        return redirect(f'/routes/{route_id}')

    # Record timestamps at key transitions
    extra = ''
    if nxt == 'in_progress':
        extra = ", actual_start = datetime('now')"
    elif nxt == 'completed':
        extra = ", actual_end = datetime('now')"

    db.execute(
        f"UPDATE delivery_routes SET status = ?{extra} WHERE id = ?",
        (nxt, route_id),
    )
    db.commit()
    return redirect(f'/routes/{route_id}')


# ---------------------------------------------------------------------------
# Print views — open in new tab, window.print() fires automatically
# ---------------------------------------------------------------------------

def _fetch_route_or_404(db, route_id):
    """Fetch a delivery route row by ID.

    Args:
        db: Active SQLite connection.
        route_id: Primary key of the delivery route.

    Returns:
        ``sqlite3.Row`` for the route, or ``None`` if not found.  The
        caller is responsible for checking and returning a 404 response.
    """
    route = db.execute(
        'SELECT * FROM delivery_routes WHERE id = ?', (route_id,)
    ).fetchone()
    return route  # caller checks for None


def _fetch_stops_full(db, route_id):
    """Fetch all stops for a route with ribbon and wrapping display names.

    Used by the route sheet print view where both wrapping and ribbon info
    are needed for assembly reference.

    Args:
        db: Active SQLite connection.
        route_id: Primary key of the delivery route.

    Returns:
        List of ``sqlite3.Row`` objects ordered by ``route_order``.
    """
    return db.execute(
        """SELECT o.id, o.order_number, o.route_order,
                  o.recipient_name, o.recipient_phone,
                  o.delivery_address, o.desired_time,
                  o.order_status, o.payment_status,
                  o.total_price, o.paid_amount,
                  o.has_note,
                  w.name AS wrapping_name,
                  r.name AS ribbon_name
             FROM orders o
             LEFT JOIN wrapping_options w ON o.wrapping_id      = w.id
             JOIN  ribbon_colors r        ON o.ribbon_color_id  = r.id
            WHERE o.route_id = ?
            ORDER BY o.route_order""",
        (route_id,),
    ).fetchall()


@delivery_bp.route('/<int:route_id>/print/route-sheet')
def print_route_sheet(route_id):
    """Render the printable route sheet with Google Maps QR code.

    Args:
        route_id: Primary key of the delivery route.

    Returns:
        Rendered ``print/route_sheet.html`` template, or HTTP 404.
    """
    db    = get_db()
    route = _fetch_route_or_404(db, route_id)
    if route is None:
        return 'Маршрут не найден', 404

    stops    = _fetch_stops_full(db, route_id)
    maps_url = route['google_maps_url'] or ''
    qr_url   = (
        'https://api.qrserver.com/v1/create-qr-code/'
        f'?size=220x220&data={url_quote(maps_url, safe="")}'
        if maps_url else ''
    )
    return render_template('print/route_sheet.html', route=route, stops=stops, qr_url=qr_url)


@delivery_bp.route('/<int:route_id>/print/labels')
def print_labels(route_id):
    """Render printable delivery labels for all stops in a route.

    Args:
        route_id: Primary key of the delivery route.

    Returns:
        Rendered ``print/labels.html`` template, or HTTP 404.
    """
    db    = get_db()
    route = _fetch_route_or_404(db, route_id)
    if route is None:
        return 'Маршрут не найден', 404

    stops = db.execute(
        """SELECT o.order_number, o.route_order,
                  o.recipient_name, o.recipient_phone,
                  o.delivery_address, o.desired_time,
                  o.total_price, o.paid_amount, o.payment_status
             FROM orders o
            WHERE o.route_id = ?
            ORDER BY o.route_order""",
        (route_id,),
    ).fetchall()
    return render_template('print/labels.html', route=route, stops=stops)


@delivery_bp.route('/<int:route_id>/print/assembly')
def print_assembly_sheet(route_id):
    """Render the printable assembly sheet listing every order in a route.

    Args:
        route_id: Primary key of the delivery route.

    Returns:
        Rendered ``print/assembly_sheet.html`` template, or HTTP 404.
    """
    db    = get_db()
    route = _fetch_route_or_404(db, route_id)
    if route is None:
        return 'Маршрут не найден', 404

    orders = db.execute(
        """SELECT o.id, o.order_number, o.route_order,
                  o.recipient_name, o.desired_time,
                  o.has_note, o.note_text,
                  w.name AS wrapping_name,
                  r.name AS ribbon_name
             FROM orders o
             LEFT JOIN wrapping_options w ON o.wrapping_id     = w.id
             JOIN  ribbon_colors r        ON o.ribbon_color_id = r.id
            WHERE o.route_id = ?
            ORDER BY o.route_order""",
        (route_id,),
    ).fetchall()

    # Fetch flower items for every order in one pass
    order_items = {}
    for order in orders:
        order_items[order['id']] = db.execute(
            """SELECT oi.quantity,
                      tv.name  AS variety_name,
                      tv.color AS variety_color
                 FROM order_items oi
                 JOIN tulip_varieties tv ON oi.variety_id = tv.id
                WHERE oi.order_id = ?
                ORDER BY tv.name""",
            (order['id'],),
        ).fetchall()

    return render_template(
        'print/assembly_sheet.html',
        route=route,
        orders=orders,
        order_items=order_items,
    )


# ===========================================================================
# Courier Blueprint  —  /courier/<route_id>
# Mobile view for the delivery driver; HTMX inline stop updates.
# ===========================================================================

courier_bp = Blueprint('courier', __name__, url_prefix='/courier')

_STOP_SQL = """
    SELECT o.id, o.order_number, o.route_order,
           o.recipient_name, o.recipient_phone,
           o.delivery_address, o.desired_time,
           o.order_status, o.payment_status,
           o.total_price, o.paid_amount,
           o.has_note, o.notes,
           w.name AS wrapping_name,
           r.name AS ribbon_name
      FROM orders o
      LEFT JOIN wrapping_options w ON o.wrapping_id     = w.id
      JOIN  ribbon_colors r        ON o.ribbon_color_id = r.id
"""


def _get_stop(db, order_id: int):
    """Fetch a single stop row with wrapping/ribbon names for the courier view.

    Args:
        db: Active SQLite connection.
        order_id: Primary key of the order (stop).

    Returns:
        ``sqlite3.Row`` for the stop, or ``None`` if not found.
    """
    return db.execute(_STOP_SQL + ' WHERE o.id = ?', (order_id,)).fetchone()


def _get_stops(db, route_id: int):
    """Fetch all stops for a route ordered by position (courier view).

    Args:
        db: Active SQLite connection.
        route_id: Primary key of the delivery route.

    Returns:
        List of ``sqlite3.Row`` objects ordered by ``route_order``.
    """
    return db.execute(
        _STOP_SQL + ' WHERE o.route_id = ? ORDER BY o.route_order',
        (route_id,)
    ).fetchall()


def _append_note(db, order_id: int, marker: str) -> None:
    """Append a time-stamped marker to ``orders.notes`` without overwriting.

    Entries are separated by ``' | '``.  Does not commit.

    Args:
        db: Active SQLite connection.
        order_id: Primary key of the order to annotate.
        marker: Short label prepended to the current time (e.g.
            ``'Не дозвонился'`` → ``'Не дозвонился 14:32'``).
    """
    from datetime import datetime
    entry = f'{marker} {datetime.now().strftime("%H:%M")}'
    db.execute(
        """UPDATE orders
              SET notes      = CASE
                                 WHEN notes IS NULL OR notes = '' THEN ?
                                 ELSE notes || ' | ' || ?
                               END,
                  updated_at = datetime('now')
            WHERE id = ?""",
        (entry, entry, order_id),
    )


# ---------------------------------------------------------------------------
# GET /courier/<route_id>  — courier mobile view
# ---------------------------------------------------------------------------

@courier_bp.route('/<int:route_id>')
def courier_view(route_id):
    """Render the courier mobile view for a route.

    Args:
        route_id: Primary key of the delivery route.

    Returns:
        Rendered ``courier/route.html`` template, or a redirect to
        ``/routes`` if the route is not found.
    """
    db    = get_db()
    route = db.execute(
        'SELECT * FROM delivery_routes WHERE id = ?', (route_id,)
    ).fetchone()
    if route is None:
        flash('Маршрут не найден', 'error')
        return redirect('/routes')

    stops           = _get_stops(db, route_id)
    delivered_count = sum(1 for s in stops if s['order_status'] == 'delivered')

    return render_template(
        'courier/route.html',
        route=route,
        stops=stops,
        route_id=route_id,
        delivered_count=delivered_count,
    )


# ---------------------------------------------------------------------------
# POST /courier/<route_id>/stop/<order_id>/delivered
# ---------------------------------------------------------------------------

@courier_bp.route('/<int:route_id>/stop/<int:order_id>/delivered', methods=['POST'])
def courier_delivered(route_id, order_id):
    """HTMX: mark a stop as delivered and return the updated stop partial.

    Args:
        route_id: Primary key of the delivery route.
        order_id: Primary key of the order (stop).

    Returns:
        Rendered ``courier/_stop.html`` partial.
    """
    db = get_db()
    db.execute(
        """UPDATE orders
              SET order_status = 'delivered',
                  updated_at   = datetime('now')
            WHERE id = ?""",
        (order_id,),
    )
    db.commit()
    stop = _get_stop(db, order_id)
    return render_template('courier/_stop.html', stop=stop, route_id=route_id)


# ---------------------------------------------------------------------------
# POST /courier/<route_id>/stop/<order_id>/missed  — couldn't reach recipient
# ---------------------------------------------------------------------------

@courier_bp.route('/<int:route_id>/stop/<int:order_id>/missed', methods=['POST'])
def courier_missed(route_id, order_id):
    """HTMX: record a failed delivery attempt and return the stop partial.

    Appends a time-stamped "Не дозвонился" note to ``orders.notes``.

    Args:
        route_id: Primary key of the delivery route.
        order_id: Primary key of the order (stop).

    Returns:
        Rendered ``courier/_stop.html`` partial.
    """
    db = get_db()
    _append_note(db, order_id, 'Не дозвонился')
    db.commit()
    stop = _get_stop(db, order_id)
    return render_template('courier/_stop.html', stop=stop, route_id=route_id)


# ---------------------------------------------------------------------------
# POST /courier/<route_id>/stop/<order_id>/postpone  — defer stop
# ---------------------------------------------------------------------------

@courier_bp.route('/<int:route_id>/stop/<int:order_id>/postpone', methods=['POST'])
def courier_postpone(route_id, order_id):
    """HTMX: defer a delivery stop and return the updated stop partial.

    Appends a time-stamped "Перенос" note to ``orders.notes``.

    Args:
        route_id: Primary key of the delivery route.
        order_id: Primary key of the order (stop).

    Returns:
        Rendered ``courier/_stop.html`` partial.
    """
    db = get_db()
    _append_note(db, order_id, 'Перенос')
    db.commit()
    stop = _get_stop(db, order_id)
    return render_template('courier/_stop.html', stop=stop, route_id=route_id)
