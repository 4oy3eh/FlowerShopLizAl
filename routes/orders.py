"""Order management routes (Flask Blueprint ``orders``).

URL prefix: ``/orders``

Endpoints:
    GET  /orders/new              — multi-step new-order form
    GET  /orders/customer-lookup  — HTMX: look up customer by phone
    POST /orders/create           — process new-order form submission
    GET  /orders/                 — order list with status / search filters
    GET  /orders/list             — HTMX partial: filtered order card list
    GET  /orders/<id>             — order detail page
    POST /orders/<id>/status      — advance order to the next status
    POST /orders/<id>/cancel      — cancel order (rules R3, R5, R6)
    GET  /orders/<id>/label       — print bouquet label (new tab)
    GET  /orders/<id>/assembly    — print assembly sheet (new tab)
"""

import html

from flask import Blueprint, flash, redirect, render_template, request

from config import DELIVERY_DATES, DEFAULT_DELIVERY_DATE, TIME_SLOTS
from database.db import get_db
from services.order_service import (
    CANCEL_ALLOWED,
    STATUS_FLOW,
    advance_status,
    cancel_order,
    create_order,
    update_order,
    update_recipient,
)

orders_bp = Blueprint('orders', __name__, url_prefix='/orders')

_INPUT_CLS = (
    'w-full rounded-xl border border-gray-300 text-lg px-4 py-3.5 '
    'bg-white focus:outline-none focus:ring-2 focus:ring-pink-400'
)

# ---------------------------------------------------------------------------
# Status / payment display helpers (injected into every orders template)
# ---------------------------------------------------------------------------

STATUS_LABELS = {
    'new':        'Новый',
    'confirmed':  'Подтверждён',
    'assembling': 'Сборка',
    'ready':      'Готов',
    'delivering': 'Доставка',
    'delivered':  'Доставлен',
    'done':       'Завершён',
    'cancelled':  'Отменён',
}

STATUS_COLORS = {
    'new':        'bg-blue-100 text-blue-700',
    'confirmed':  'bg-amber-100 text-amber-700',
    'assembling': 'bg-violet-100 text-violet-700',
    'ready':      'bg-green-100 text-green-700',
    'delivering': 'bg-orange-100 text-orange-700',
    'delivered':  'bg-teal-100 text-teal-700',
    'done':       'bg-gray-100 text-gray-600',
    'cancelled':  'bg-red-100 text-red-700',
}

NEXT_STATUS_LABEL = {
    'new':        'Подтвердить',
    'confirmed':  'Начать сборку',
    'assembling': 'Готов',
    'ready':      'Передать курьеру',
    'delivering': 'Доставлен',
    'delivered':  'Завершить',
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

PAYMENT_TYPE_LABELS = {
    'cash':     'Наличные',
    'card':     'Карта',
    'transfer': 'Перевод',
}


@orders_bp.context_processor
def _inject_status_helpers():
    """Inject order/payment display dictionaries into every orders template."""
    return {
        'STATUS_LABELS':       STATUS_LABELS,
        'STATUS_COLORS':       STATUS_COLORS,
        'NEXT_STATUS_LABEL':   NEXT_STATUS_LABEL,
        'PAYMENT_LABELS':      PAYMENT_LABELS,
        'PAYMENT_COLORS':      PAYMENT_COLORS,
        'PAYMENT_TYPE_LABELS': PAYMENT_TYPE_LABELS,
        'CANCEL_ALLOWED':      CANCEL_ALLOWED,
        'STATUS_FLOW':         STATUS_FLOW,
    }


# ---------------------------------------------------------------------------
# GET /orders/new  — multi-step new order form
# ---------------------------------------------------------------------------

@orders_bp.route('/new')
def new_order():
    """Render the multi-step new order form.

    Loads all active varieties (with available stock), wrapping options,
    ribbon colours, and system settings so the Jinja2 template can build
    the dynamic form.

    Returns:
        Rendered ``orders/new.html`` template.
    """
    db = get_db()

    # Convert sqlite3.Row → plain dict so Jinja2 `tojson` can serialize them
    varieties = [dict(r) for r in db.execute(
        '''SELECT id, name, color, current_sell_price, stock_available
             FROM tulip_varieties
            WHERE is_active = 1 AND stock_available > 0
            ORDER BY name'''
    ).fetchall()]

    wrapping = [dict(r) for r in db.execute(
        '''SELECT id, name, wrapping_type, current_price
             FROM wrapping_options
            WHERE is_active = 1
            ORDER BY CASE wrapping_type
                       WHEN 'florist'  THEN 0
                       WHEN 'замшевая' THEN 1
                       WHEN 'каффин'   THEN 2
                       WHEN 'пленка'   THEN 3
                       ELSE 4 END,
                     name'''
    ).fetchall()]

    ribbons = [dict(r) for r in db.execute(
        '''SELECT id, name FROM ribbon_colors WHERE is_active = 1
           ORDER BY CASE WHEN name = 'Выбор флориста' THEN 0 ELSE 1 END, name'''
    ).fetchall()]

    settings = {row['key']: row['value']
                for row in db.execute('SELECT key, value FROM system_settings').fetchall()}

    tissue_options = [
        ('florist', 'Выбор флориста'),
        ('none',    'Без тишью'),
        ('white',   'Белая'),
        ('cream',   'Молочная'),
        ('black',   'Чёрная'),
        ('pink',    'Розовая'),
    ]

    return render_template(
        'orders/new.html',
        time_slots=TIME_SLOTS,
        delivery_dates=DELIVERY_DATES,
        default_delivery_date=DEFAULT_DELIVERY_DATE,
        varieties=varieties,
        wrapping=wrapping,
        ribbons=ribbons,
        tissue_options=tissue_options,
        settings=settings,
    )


# ---------------------------------------------------------------------------
# GET /orders/customer-lookup  — HTMX: look up customer by phone
#
# Returns innerHTML for #customer-lookup-result plus an OOB swap that
# pre-fills the customer name input when the customer is already known.
# ---------------------------------------------------------------------------

@orders_bp.route('/customer-lookup')
def customer_lookup():
    """HTMX endpoint: look up an existing customer by phone number.

    Query params:
        customer_phone: Full Ukrainian phone number (``+380XXXXXXXXX``).

    Returns:
        HTML fragment: a "returning customer" hint with an OOB input swap
        when the customer is found, or a "new customer" hint otherwise.
        Returns an empty string if the phone is too short to query.
    """
    phone = request.args.get('customer_phone', '').strip()

    # Require at least a minimal contact value before querying
    if phone.startswith('@'):
        if len(phone) < 3:   # @ab minimum
            return ''
    elif len(phone) < 13:    # full +380XXXXXXXXX
        return ''

    db = get_db()
    customer = db.execute(
        'SELECT name FROM customers WHERE phone = ?', (phone,)
    ).fetchone()

    if customer:
        name = customer['name'] or ''
        hint = '<span class="text-green-600 text-sm font-medium">✓ Постоянный клиент</span>'
        safe_name = html.escape(name, quote=True)
        oob = (
            f'<input id="customer-name-input" name="customer_name" type="text" '
            f'value="{safe_name}" placeholder="Аноним" '
            f'class="{_INPUT_CLS}" hx-swap-oob="true">'
        )
        return f'{hint}{oob}'

    return '<span class="text-gray-400 text-sm">Новый клиент</span>'


# ---------------------------------------------------------------------------
# POST /orders/create  — process the new-order form (all 3 steps)
# ---------------------------------------------------------------------------

@orders_bp.route('/create', methods=['POST'])
def create_order_route():
    """Process the new-order form submission.

    Collects all three form steps, delegates to
    :func:`services.order_service.create_order`, then redirects to the new
    order detail page on success or back to the form with a flash message on
    validation failure.

    Returns:
        Redirect to ``/orders/<id>`` on success, or to ``/orders/new`` on
        error.
    """
    # Parse multi-bouquet form data
    try:
        bouquet_count = max(1, int(request.form.get('bouquet_count', '1') or '1'))
    except (ValueError, TypeError):
        bouquet_count = 1

    bouquets = []
    for i in range(bouquet_count):
        bouquets.append({
            'variety_ids':     request.form.getlist(f'variety_id_{i}[]'),
            'quantities':      request.form.getlist(f'quantity_{i}[]'),
            'wrapping_id':     request.form.get(f'b_wrapping_{i}', '').strip() or None,
            'ribbon_color_id': request.form.get(f'b_ribbon_{i}', '').strip(),
            'tissue':          request.form.get(f'b_tissue_{i}', 'florist').strip(),
            'has_note':        bool(request.form.get(f'b_has_note_{i}')),
            'note_text':       (request.form.get(f'b_note_{i}') or '').strip(),
        })

    data = {
        'customer_phone':   request.form.get('customer_phone', '').strip(),
        'customer_name':    request.form.get('customer_name', '').strip(),
        'recipient_name':   request.form.get('recipient_name', '').strip(),
        'recipient_phone':  request.form.get('recipient_phone', '').strip(),
        'is_pickup':        request.form.get('is_pickup', '0'),
        'delivery_address': request.form.get('delivery_address', '').strip(),
        'delivery_date':    request.form.get('delivery_date', DEFAULT_DELIVERY_DATE).strip(),
        'desired_time':     request.form.get('desired_time', '').strip(),
        'bouquets':         bouquets,
    }

    try:
        order_id = create_order(get_db(), data)
        flash('Заказ создан!', 'success')
        return redirect(f'/orders/{order_id}')
    except ValueError as exc:
        flash(str(exc), 'error')
        return redirect('/orders/new')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _date_counts(db) -> dict:
    """Return a dict of {delivery_date: order_count} for non-cancelled orders."""
    rows = db.execute(
        "SELECT delivery_date, COUNT(*) AS cnt FROM orders "
        "WHERE order_status != 'cancelled' "
        "GROUP BY delivery_date"
    ).fetchall()
    return {row['delivery_date']: row['cnt'] for row in rows}


def _fetch_orders(db, status_filter='', q='', date_filter=''):
    """Build and execute the order list query with optional filters.

    Args:
        db: Active SQLite connection.
        status_filter: If non-empty, restrict results to this
            ``order_status`` value.
        q: Free-text search term matched against ``order_number``,
            ``recipient_name``, and ``recipient_phone``.
        date_filter: If non-empty, restrict results to this
            ``delivery_date`` value (e.g. ``'2025-03-08'``).

    Returns:
        List of ``sqlite3.Row`` objects ordered by ``created_at DESC``.
    """
    where = ['1=1']
    params = []

    if status_filter:
        where.append('o.order_status = ?')
        params.append(status_filter)

    if date_filter:
        where.append('o.delivery_date = ?')
        params.append(date_filter)

    if q:
        like = f'%{q}%'
        where.append(
            '(o.order_number LIKE ? OR o.recipient_name LIKE ? OR o.recipient_phone LIKE ?)'
        )
        params.extend([like, like, like])

    sql = f'''
        SELECT o.*,
               c.name  AS customer_name, c.phone AS customer_phone,
               w.name  AS wrapping_name,
               r.name  AS ribbon_name
          FROM orders o
          JOIN customers c       ON o.customer_id      = c.id
          LEFT JOIN wrapping_options w ON o.wrapping_id = w.id
          JOIN ribbon_colors r   ON o.ribbon_color_id  = r.id
         WHERE {' AND '.join(where)}
         ORDER BY o.created_at DESC
    '''
    return db.execute(sql, params).fetchall()


# ---------------------------------------------------------------------------
# GET /orders  — order list with filters
# ---------------------------------------------------------------------------

@orders_bp.route('/', strict_slashes=False)
def order_list():
    """Render the full order list page with status/search filter controls.

    Returns:
        Rendered ``orders/index.html`` template.
    """
    status_filter = request.args.get('status', '').strip()
    date_filter   = request.args.get('date', '').strip()
    q             = request.args.get('q', '').strip()
    db            = get_db()
    date_counts   = _date_counts(db)
    orders        = _fetch_orders(db, status_filter, q, date_filter)
    return render_template(
        'orders/index.html',
        orders=orders,
        status_filter=status_filter,
        date_filter=date_filter,
        date_counts=date_counts,
        total_count=sum(date_counts.values()),
        delivery_dates=DELIVERY_DATES,
        q=q,
        STATUS_LABELS=STATUS_LABELS,
    )


# ---------------------------------------------------------------------------
# GET /orders/list  — HTMX partial: re-render card list on filter change
# ---------------------------------------------------------------------------

@orders_bp.route('/list')
def order_list_partial():
    """HTMX partial: re-render the order card list after a filter change.

    Returns:
        Rendered ``orders/_list.html`` partial template.
    """
    status_filter = request.args.get('status', '').strip()
    date_filter   = request.args.get('date', '').strip()
    q             = request.args.get('q', '').strip()
    orders        = _fetch_orders(get_db(), status_filter, q, date_filter)
    return render_template('orders/_list.html', orders=orders)


# ---------------------------------------------------------------------------
# GET /orders/<id>  — order detail
# ---------------------------------------------------------------------------

@orders_bp.route('/<int:order_id>')
def order_detail(order_id):
    """Render the order detail page.

    Args:
        order_id: Primary key of the order.

    Returns:
        Rendered ``orders/detail.html`` template, or a redirect to
        ``/orders`` with a flash error if the order is not found.
    """
    db  = get_db()

    order = db.execute(
        '''SELECT o.*,
                  c.name  AS customer_name, c.phone AS customer_phone,
                  w.name  AS wrapping_name,
                  r.name  AS ribbon_name
             FROM orders o
             JOIN customers c            ON o.customer_id     = c.id
             LEFT JOIN wrapping_options w ON o.wrapping_id    = w.id
             JOIN ribbon_colors r        ON o.ribbon_color_id = r.id
            WHERE o.id = ?''',
        (order_id,)
    ).fetchone()

    if order is None:
        flash('Заказ не найден', 'error')
        return redirect('/orders')

    # Load bouquets (new-style multi-bouquet orders)
    raw_bouquets = db.execute(
        '''SELECT ob.*, wo.name AS wrapping_name, rc.name AS ribbon_name
             FROM order_bouquets ob
             LEFT JOIN wrapping_options wo ON ob.wrapping_id    = wo.id
             LEFT JOIN ribbon_colors    rc ON ob.ribbon_color_id = rc.id
            WHERE ob.order_id = ?
            ORDER BY ob.position''',
        (order_id,)
    ).fetchall()

    bouquets = []
    for b in raw_bouquets:
        bd = dict(b)
        bd['items'] = db.execute(
            '''SELECT oi.*, tv.name AS variety_name, tv.color AS variety_color
                 FROM order_items oi
                 JOIN tulip_varieties tv ON oi.variety_id = tv.id
                WHERE oi.bouquet_id = ?
                ORDER BY oi.id''',
            (b['id'],)
        ).fetchall()
        bouquets.append(bd)

    # Fallback items for old orders (no order_bouquets rows)
    items = db.execute(
        '''SELECT oi.*, tv.name AS variety_name, tv.color AS variety_color
             FROM order_items oi
             JOIN tulip_varieties tv ON oi.variety_id = tv.id
            WHERE oi.order_id = ? AND (oi.bouquet_id IS NULL)
            ORDER BY oi.id''',
        (order_id,)
    ).fetchall()

    payments = db.execute(
        'SELECT * FROM payment_log WHERE order_id = ? ORDER BY created_at',
        (order_id,)
    ).fetchall()

    return render_template(
        'orders/detail.html',
        order=order,
        items=items,
        bouquets=bouquets,
        payments=payments,
    )


# ---------------------------------------------------------------------------
# POST /orders/<id>/status  — advance order to next status
# ---------------------------------------------------------------------------

@orders_bp.route('/<int:order_id>/status', methods=['POST'])
def advance_order_status(order_id):
    """Advance an order to the next status in the state machine.

    Args:
        order_id: Primary key of the order to advance.

    Returns:
        Redirect to the order detail page.
    """
    try:
        advance_status(get_db(), order_id)
    except ValueError as exc:
        flash(str(exc), 'error')
    return redirect(f'/orders/{order_id}')


# ---------------------------------------------------------------------------
# POST /orders/<id>/cancel  — cancel order (R3, R5, R6)
# ---------------------------------------------------------------------------

@orders_bp.route('/<int:order_id>/cancel', methods=['POST'])
def cancel_order_route(order_id):
    """Cancel an order and release its reserved stock (rules R3, R5, R6).

    Args:
        order_id: Primary key of the order to cancel.

    Returns:
        Redirect to the order detail page.
    """
    try:
        cancel_order(get_db(), order_id)
        flash('Заказ отменён. Остатки возвращены на склад.', 'warning')
    except ValueError as exc:
        flash(str(exc), 'error')
    return redirect(f'/orders/{order_id}')


# ---------------------------------------------------------------------------
# POST /orders/<id>/recipient  — update recipient info for an order
# ---------------------------------------------------------------------------

@orders_bp.route('/<int:order_id>/recipient', methods=['POST'])
def update_order_recipient(order_id):
    """Save or update recipient info (name, phone, address) for an existing order.

    Accepts an optional ``next`` form field to redirect back to the calling
    page (e.g. ``/orders/recipients``); falls back to the order detail page.

    Args:
        order_id: Primary key of the order to update.

    Returns:
        Redirect to ``next`` URL or ``/orders/<id>``.
    """
    data = {
        'recipient_name':   request.form.get('recipient_name', '').strip(),
        'recipient_phone':  request.form.get('recipient_phone', '').strip(),
        'delivery_address': request.form.get('delivery_address', '').strip(),
        'is_pickup':        request.form.get('is_pickup', '0'),
    }
    next_url = request.form.get('next') or f'/orders/{order_id}'
    try:
        update_recipient(get_db(), order_id, data)
        flash('Получатель сохранён', 'success')
    except ValueError as exc:
        flash(str(exc), 'error')
    return redirect(next_url)


# ---------------------------------------------------------------------------
# GET /orders/recipients  — list of orders without recipient info
# ---------------------------------------------------------------------------

@orders_bp.route('/recipients')
def recipients_list():
    """Page for filling in recipient details for orders that are missing them.

    Returns:
        Rendered ``orders/recipients.html`` template.
    """
    db = get_db()
    orders = db.execute(
        """SELECT o.*, c.name AS customer_name, c.phone AS customer_phone
             FROM orders o
             JOIN customers c ON o.customer_id = c.id
            WHERE o.order_status NOT IN ('cancelled', 'done')
              AND (o.recipient_name = '' OR o.recipient_phone = '')
            ORDER BY o.delivery_date, o.created_at"""
    ).fetchall()
    return render_template('orders/recipients.html', orders=orders)


# ---------------------------------------------------------------------------
# GET /orders/<id>/label  — print bouquet label (opens in new tab)
# ---------------------------------------------------------------------------

@orders_bp.route('/<int:order_id>/label')
def print_label(order_id):
    """Render a printable bouquet label for the order.

    Intended to be opened in a new browser tab; the template auto-triggers
    ``window.print()``.

    Args:
        order_id: Primary key of the order.

    Returns:
        Rendered ``orders/print_label.html`` template, or a 404 response.
    """
    db    = get_db()
    order = db.execute(
        '''SELECT o.*, r.name AS ribbon_name, w.name AS wrapping_name
             FROM orders o
             LEFT JOIN wrapping_options w ON o.wrapping_id    = w.id
             JOIN ribbon_colors r         ON o.ribbon_color_id = r.id
            WHERE o.id = ?''',
        (order_id,)
    ).fetchone()
    if order is None:
        return 'Заказ не найден', 404
    return render_template('orders/print_label.html', order=order)


# ---------------------------------------------------------------------------
# GET /orders/<id>/assembly  — print assembly sheet (opens in new tab)
# ---------------------------------------------------------------------------

@orders_bp.route('/<int:order_id>/assembly')
def print_assembly(order_id):
    """Render a printable assembly sheet for a single order.

    Intended to be opened in a new browser tab; the template auto-triggers
    ``window.print()``.

    Args:
        order_id: Primary key of the order.

    Returns:
        Rendered ``orders/print_assembly.html`` template, or a 404 response.
    """
    db    = get_db()
    order = db.execute(
        '''SELECT o.*, r.name AS ribbon_name, w.name AS wrapping_name
             FROM orders o
             LEFT JOIN wrapping_options w ON o.wrapping_id    = w.id
             JOIN ribbon_colors r         ON o.ribbon_color_id = r.id
            WHERE o.id = ?''',
        (order_id,)
    ).fetchone()
    if order is None:
        return 'Заказ не найден', 404

    # Load bouquets (new-style multi-bouquet orders)
    raw_bouquets = db.execute(
        '''SELECT ob.*, wo.name AS wrapping_name, rc.name AS ribbon_name
             FROM order_bouquets ob
             LEFT JOIN wrapping_options wo ON ob.wrapping_id     = wo.id
             LEFT JOIN ribbon_colors    rc ON ob.ribbon_color_id = rc.id
            WHERE ob.order_id = ?
            ORDER BY ob.position''',
        (order_id,)
    ).fetchall()

    bouquets = []
    for b in raw_bouquets:
        bd = dict(b)
        bd['items'] = db.execute(
            '''SELECT oi.quantity, tv.name AS variety_name, tv.color AS variety_color
                 FROM order_items oi
                 JOIN tulip_varieties tv ON oi.variety_id = tv.id
                WHERE oi.bouquet_id = ?
                ORDER BY tv.name''',
            (b['id'],)
        ).fetchall()
        bouquets.append(bd)

    # Fallback for old orders
    items = db.execute(
        '''SELECT oi.quantity, tv.name AS variety_name, tv.color AS variety_color
             FROM order_items oi
             JOIN tulip_varieties tv ON oi.variety_id = tv.id
            WHERE oi.order_id = ? AND (oi.bouquet_id IS NULL)
            ORDER BY tv.name''',
        (order_id,)
    ).fetchall()

    return render_template('orders/print_assembly.html', order=order, items=items,
                           bouquets=bouquets)


# ---------------------------------------------------------------------------
# GET /orders/<id>/edit  — full order edit form
# ---------------------------------------------------------------------------

@orders_bp.route('/<int:order_id>/edit')
def edit_order(order_id):
    """Render the full order edit form (allowed for CANCEL_ALLOWED statuses).

    Loads varieties with effective stock (adds back current order's reserved
    quantities so the user sees the correct available amount while editing).

    Args:
        order_id: Primary key of the order to edit.

    Returns:
        Rendered ``orders/edit.html`` template, or redirect on error.
    """
    db = get_db()

    order = db.execute(
        '''SELECT o.*,
                  c.name  AS customer_name, c.phone AS customer_phone,
                  w.name  AS wrapping_name,
                  r.name  AS ribbon_name
             FROM orders o
             JOIN customers c            ON o.customer_id     = c.id
             LEFT JOIN wrapping_options w ON o.wrapping_id    = w.id
             JOIN ribbon_colors r        ON o.ribbon_color_id = r.id
            WHERE o.id = ?''',
        (order_id,)
    ).fetchone()

    if order is None:
        flash('Заказ не найден', 'error')
        return redirect('/orders')

    if order['order_status'] not in CANCEL_ALLOWED:
        flash('Заказ нельзя редактировать в текущем статусе', 'error')
        return redirect(f'/orders/{order_id}')

    # Current order's reserved quantities (to show correct available stock)
    order_qty = {}
    for r in db.execute(
        'SELECT variety_id, SUM(quantity) AS qty FROM order_items WHERE order_id = ? GROUP BY variety_id',
        (order_id,)
    ).fetchall():
        order_qty[r['variety_id']] = r['qty']

    # Varieties: add back reserved qty so the form shows realistic availability
    varieties = []
    for r in db.execute(
        '''SELECT id, name, color, current_sell_price, stock_available
             FROM tulip_varieties
            WHERE is_active = 1
            ORDER BY name'''
    ).fetchall():
        vd = dict(r)
        vd['stock_available'] = vd['stock_available'] + order_qty.get(vd['id'], 0)
        varieties.append(vd)

    wrapping = [dict(r) for r in db.execute(
        '''SELECT id, name, wrapping_type, current_price
             FROM wrapping_options
            WHERE is_active = 1
            ORDER BY CASE wrapping_type
                       WHEN 'florist'  THEN 0
                       WHEN 'замшевая' THEN 1
                       WHEN 'каффин'   THEN 2
                       WHEN 'пленка'   THEN 3
                       ELSE 4 END, name'''
    ).fetchall()]

    ribbons = [dict(r) for r in db.execute(
        '''SELECT id, name FROM ribbon_colors WHERE is_active = 1
           ORDER BY CASE WHEN name = \'Выбор флориста\' THEN 0 ELSE 1 END, name'''
    ).fetchall()]

    settings = {row['key']: row['value']
                for row in db.execute('SELECT key, value FROM system_settings').fetchall()}

    tissue_options = [
        ('florist', 'Выбор флориста'),
        ('none',    'Без тишью'),
        ('white',   'Белая'),
        ('cream',   'Молочная'),
        ('black',   'Чёрная'),
        ('pink',    'Розовая'),
    ]

    # Load bouquets with items
    raw_bouquets = db.execute(
        '''SELECT ob.*, wo.name AS wrapping_name, rc.name AS ribbon_name
             FROM order_bouquets ob
             LEFT JOIN wrapping_options wo ON ob.wrapping_id     = wo.id
             LEFT JOIN ribbon_colors    rc ON ob.ribbon_color_id = rc.id
            WHERE ob.order_id = ?
            ORDER BY ob.position''',
        (order_id,)
    ).fetchall()

    bouquets = []
    for b in raw_bouquets:
        bd = dict(b)
        bd['items'] = db.execute(
            '''SELECT oi.*, tv.name AS variety_name
                 FROM order_items oi
                 JOIN tulip_varieties tv ON oi.variety_id = tv.id
                WHERE oi.bouquet_id = ?
                ORDER BY oi.id''',
            (b['id'],)
        ).fetchall()
        bouquets.append(bd)

    # Legacy fallback: no order_bouquets rows
    if not bouquets:
        items = db.execute(
            '''SELECT oi.*, tv.name AS variety_name
                 FROM order_items oi
                 JOIN tulip_varieties tv ON oi.variety_id = tv.id
                WHERE oi.order_id = ? AND oi.bouquet_id IS NULL
                ORDER BY oi.id''',
            (order_id,)
        ).fetchall()
        if items:
            bouquets = [{
                'id':              None,
                'position':        1,
                'wrapping_id':     order['wrapping_id'],
                'ribbon_color_id': order['ribbon_color_id'],
                'tissue':          order['tissue'],
                'has_note':        order['has_note'],
                'note_text':       order['note_text'],
                'wrapping_name':   order['wrapping_name'],
                'ribbon_name':     order['ribbon_name'],
                'items':           items,
            }]

    return render_template(
        'orders/edit.html',
        order=order,
        bouquets=bouquets,
        varieties=varieties,
        wrapping=wrapping,
        ribbons=ribbons,
        settings=settings,
        tissue_options=tissue_options,
        delivery_dates=DELIVERY_DATES,
        time_slots=TIME_SLOTS,
    )


# ---------------------------------------------------------------------------
# POST /orders/<id>/update  — save full order edit
# ---------------------------------------------------------------------------

@orders_bp.route('/<int:order_id>/update', methods=['POST'])
def update_order_route(order_id):
    """Process the full order edit form submission.

    Args:
        order_id: Primary key of the order to update.

    Returns:
        Redirect to order detail on success, or back to edit form on error.
    """
    try:
        bouquet_count = max(1, int(request.form.get('bouquet_count', '1') or '1'))
    except (ValueError, TypeError):
        bouquet_count = 1

    bouquets = []
    for i in range(bouquet_count):
        bouquets.append({
            'variety_ids':     request.form.getlist(f'variety_id_{i}[]'),
            'quantities':      request.form.getlist(f'quantity_{i}[]'),
            'wrapping_id':     request.form.get(f'b_wrapping_{i}', '').strip() or None,
            'ribbon_color_id': request.form.get(f'b_ribbon_{i}', '').strip(),
            'tissue':          request.form.get(f'b_tissue_{i}', 'florist').strip(),
            'has_note':        bool(request.form.get(f'b_has_note_{i}')),
            'note_text':       (request.form.get(f'b_note_{i}') or '').strip(),
        })

    data = {
        'customer_phone':   request.form.get('customer_phone', '').strip(),
        'customer_name':    request.form.get('customer_name', '').strip(),
        'recipient_name':   request.form.get('recipient_name', '').strip(),
        'recipient_phone':  request.form.get('recipient_phone', '').strip(),
        'is_pickup':        request.form.get('is_pickup', '0'),
        'delivery_address': request.form.get('delivery_address', '').strip(),
        'delivery_date':    request.form.get('delivery_date', DEFAULT_DELIVERY_DATE).strip(),
        'desired_time':     request.form.get('desired_time', '').strip(),
        'bouquets':         bouquets,
    }

    try:
        update_order(get_db(), order_id, data)
        flash('Заказ обновлён!', 'success')
        return redirect(f'/orders/{order_id}')
    except ValueError as exc:
        flash(str(exc), 'error')
        return redirect(f'/orders/{order_id}/edit')
