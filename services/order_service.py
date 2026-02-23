"""Order creation and lifecycle management (business logic layer).

:func:`create_order` is the single entry point: it validates all inputs,
fixes prices, checks stock, persists the order, and reserves stock in one
atomic transaction.  All public functions raise :class:`ValueError` with a
user-readable message on any rule violation.
"""

import re
from datetime import datetime

from services.price_service import snapshot_prices
from services.stock_service import check_availability, reserve, release

_PHONE_RE = re.compile(r'^\+380\d{9}$')

# ---------------------------------------------------------------------------
# Order status machine
# ---------------------------------------------------------------------------

STATUS_FLOW = {
    'new':        'confirmed',
    'confirmed':  'assembling',
    'assembling': 'ready',
    'ready':      'delivering',
    'delivering': 'delivered',
    'delivered':  'done',
}

CANCEL_ALLOWED = {'new', 'confirmed', 'assembling', 'ready'}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _validate_phone(phone: str, label: str) -> str:
    """Normalise and validate a Ukrainian mobile phone number.

    Args:
        phone: Raw phone string from the form (may have leading/trailing
            whitespace).
        label: Human-readable field label used in the error message (e.g.
            ``'телефона получателя'``).

    Returns:
        The stripped phone string if it matches ``+380XXXXXXXXX``.

    Raises:
        ValueError: If the phone does not match the expected format.
    """
    phone = phone.strip()
    if not _PHONE_RE.match(phone):
        raise ValueError(f'Неверный формат {label}: нужен +380XXXXXXXXX')
    return phone


def _parse_items(variety_ids: list, quantities: list) -> list:
    """Zip and validate variety_id / quantity lists from the form.

    Blank ``<select>`` entries (empty variety IDs) are silently skipped.

    Args:
        variety_ids: List of variety ID strings from ``request.form``.
        quantities: Corresponding list of quantity strings.

    Returns:
        List of ``(variety_id: int, quantity: int)`` tuples with at least
        one entry.

    Raises:
        ValueError: If any quantity is less than 1 (rule V5) or the
            resulting list is empty (rule V2).
    """
    pairs = []
    for vid, qty_str in zip(variety_ids, quantities):
        if not vid:          # blank <select> — user left a row empty
            continue
        try:
            qty = int(qty_str)
        except (ValueError, TypeError):
            qty = 0
        if qty < 1:          # V5
            raise ValueError('Количество должно быть не менее 1')
        pairs.append((int(vid), qty))

    if not pairs:            # V2 / V5
        raise ValueError('Добавьте хотя бы один сорт цветов')
    return pairs


def find_or_create_customer(db, phone: str, name: str) -> int:
    """Look up a customer by phone number; create a new record if not found.

    Args:
        db: Active SQLite connection.
        phone: Normalised Ukrainian phone number (``+380XXXXXXXXX``).
        name: Customer display name.  Falls back to ``'Аноним'`` if empty.

    Returns:
        The ``id`` of the existing or newly inserted ``customers`` row.
    """
    row = db.execute(
        'SELECT id FROM customers WHERE phone = ?', (phone,)
    ).fetchone()
    if row:
        return row['id']

    db.execute(
        'INSERT INTO customers (phone, name) VALUES (?, ?)',
        (phone, name or 'Аноним')
    )
    return db.execute('SELECT last_insert_rowid()').fetchone()[0]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_order(db, data: dict) -> int:
    """Execute the full order-creation flow.

    Validates inputs, snapshots prices (rule R1), checks stock availability
    (rule R2), persists the order and its items, and reserves stock (rule
    S2) — all inside a single transaction.

    Args:
        db: Active SQLite connection.
        data: Form data dict (values are strings or lists as received from
            ``request.form``).  Expected keys:

            * ``customer_phone`` — caller's phone (``+380…``); falls back to
              recipient's phone if blank.
            * ``customer_name`` — caller's display name.
            * ``recipient_name`` — bouquet recipient name (required).
            * ``recipient_phone`` — recipient phone (``+380…``, required).
            * ``is_pickup`` — ``'1'`` for self-pickup, ``'0'`` for delivery.
            * ``delivery_address`` — required when ``is_pickup == '0'``.
            * ``desired_time`` — delivery time slot string, e.g. ``'10-12'``.
            * ``has_note`` — ``'1'`` if a greeting note is requested.
            * ``note_text`` — note body; required when ``has_note == '1'``.
            * ``wrapping_id`` — int string or ``''`` for no wrapping.
            * ``ribbon_color_id`` — int string (required).
            * ``variety_ids`` — list of variety ID strings.
            * ``quantities`` — list of quantity strings (parallel to
              ``variety_ids``).

    Returns:
        The ``id`` of the newly created order.

    Raises:
        ValueError: On any validation failure (rules V1–V5) or business-rule
            violation (rules R1, R2).
    """
    # ── Parse raw form values ─────────────────────────────────────────────
    is_pickup = data.get('is_pickup') in ('1', True, 1)
    has_note  = bool(data.get('has_note'))

    raw_wrapping_id    = data.get('wrapping_id') or None
    wrapping_id        = int(raw_wrapping_id) if raw_wrapping_id else None

    raw_ribbon_id      = data.get('ribbon_color_id') or ''
    if not raw_ribbon_id:                          # V2
        raise ValueError('Выберите цвет ленты')
    ribbon_color_id    = int(raw_ribbon_id)

    recipient_name     = (data.get('recipient_name') or '').strip()
    if not recipient_name:                         # V2
        raise ValueError('Укажите имя получателя')

    recipient_phone    = _validate_phone(          # V1 / V2
        data.get('recipient_phone', ''), 'телефона получателя'
    )

    delivery_address   = (data.get('delivery_address') or '').strip() or None

    if not is_pickup and not delivery_address:     # V3
        raise ValueError('Укажите адрес доставки')

    note_text = (data.get('note_text') or '').strip() or None
    if has_note and not note_text:                 # V4
        raise ValueError('Введите текст записки или снимите галочку «Записка»')

    # ── Parse items list ──────────────────────────────────────────────────
    variety_id_qty = _parse_items(
        data.get('variety_ids', []),
        data.get('quantities', []),
    )

    # ── Customer — optional phone, fall back to recipient's ───────────────
    raw_cust_phone = (data.get('customer_phone') or '').strip()
    if raw_cust_phone:
        customer_phone = _validate_phone(raw_cust_phone, 'телефона заказчика')  # V1
        customer_name  = (data.get('customer_name') or '').strip() or 'Аноним'
    else:
        # Customer phone not provided → use recipient as the customer record
        customer_phone = recipient_phone
        customer_name  = recipient_name

    # ── Stock check (R2) — before any writes ─────────────────────────────
    # Aggregate requested quantities per variety_id
    qty_by_variety: dict[int, int] = {}
    for vid, qty in variety_id_qty:
        qty_by_variety[vid] = qty_by_variety.get(vid, 0) + qty

    for vid, total_qty in qty_by_variety.items():
        if not check_availability(db, vid, total_qty):
            row = db.execute(
                'SELECT name, stock_available FROM tulip_varieties WHERE id = ?', (vid,)
            ).fetchone()
            name  = row['name']          if row else f'сорт #{vid}'
            avail = row['stock_available'] if row else 0
            raise ValueError(
                f'Недостаточно «{name}»: запрошено {total_qty}, доступно {avail} шт.'
            )

    # ── Price snapshot (R1) ───────────────────────────────────────────────
    prices = snapshot_prices(db, variety_id_qty, wrapping_id, has_note, is_pickup)

    # ── Persist in one transaction ────────────────────────────────────────
    try:
        # 1. find_or_create customer
        customer_id = find_or_create_customer(db, customer_phone, customer_name)

        # 2. INSERT order with a temporary order_number (updated below)
        db.execute(
            '''INSERT INTO orders (
                   order_number,
                   customer_id,
                   recipient_name, recipient_phone,
                   delivery_address, is_pickup, desired_time,
                   has_note, note_text,
                   wrapping_id, ribbon_color_id,
                   flowers_total, wrapping_price, note_price, delivery_price, total_price,
                   created_by
               ) VALUES (
                   'TEMP', ?,
                   ?, ?,
                   ?, ?, ?,
                   ?, ?,
                   ?, ?,
                   ?, ?, ?, ?, ?,
                   'admin'
               )''',
            (
                customer_id,
                recipient_name,
                recipient_phone,
                delivery_address,
                1 if is_pickup else 0,
                data.get('desired_time') or None,
                1 if has_note else 0,
                note_text,
                wrapping_id,
                ribbon_color_id,
                prices['flowers_total'],
                prices['wrapping_price'],
                prices['note_price'],
                prices['delivery_price'],
                prices['total_price'],
            )
        )
        order_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]

        # 3. Fix order_number using the auto-increment id
        year = datetime.now().year
        order_number = f'{year}-{order_id:04d}'
        db.execute(
            'UPDATE orders SET order_number = ? WHERE id = ?',
            (order_number, order_id)
        )

        # 4. INSERT order_items
        for item in prices['items']:
            db.execute(
                '''INSERT INTO order_items
                       (order_id, variety_id, quantity, unit_price, line_total)
                   VALUES (?, ?, ?, ?, ?)''',
                (order_id, item['variety_id'], item['quantity'],
                 item['unit_price'], item['line_total'])
            )

        # 5. Reserve stock (S2)
        for vid, total_qty in qty_by_variety.items():
            reserve(db, vid, total_qty)

        db.commit()
        return order_id

    except Exception:
        db.rollback()
        raise


def advance_status(db, order_id: int) -> str:
    """Move an order to the next status in the state machine.

    Args:
        db: Active SQLite connection.
        order_id: Primary key of the order to advance.

    Returns:
        The new status string (e.g. ``'confirmed'``, ``'assembling'``).

    Raises:
        ValueError: If the order is not found or its current status has
            no successor in :data:`STATUS_FLOW`.
    """
    row = db.execute(
        'SELECT order_status FROM orders WHERE id = ?', (order_id,)
    ).fetchone()
    if row is None:
        raise ValueError('Заказ не найден')

    current = row['order_status']
    nxt = STATUS_FLOW.get(current)
    if nxt is None:
        raise ValueError(f'Статус «{current}» нельзя продвинуть дальше')

    db.execute(
        "UPDATE orders SET order_status = ?, updated_at = datetime('now') WHERE id = ?",
        (nxt, order_id)
    )
    db.commit()
    return nxt


def cancel_order(db, order_id: int) -> None:
    """Cancel an order and release its reserved stock (business rule R5).

    Pre-payments are **not** refunded (rule R3).  If the order was already
    assigned to a delivery route, it is removed and the route's
    ``total_orders`` counter is decremented.

    Args:
        db: Active SQLite connection.
        order_id: Primary key of the order to cancel.

    Raises:
        ValueError: If the order is not found, or its status is not in
            :data:`CANCEL_ALLOWED` (rule R6 — cannot cancel after the
            order has been handed to the courier).
    """
    row = db.execute(
        'SELECT order_status, route_id FROM orders WHERE id = ?', (order_id,)
    ).fetchone()
    if row is None:
        raise ValueError('Заказ не найден')
    if row['order_status'] not in CANCEL_ALLOWED:
        raise ValueError('Нельзя отменить заказ после передачи курьеру')

    try:
        db.execute(
            "UPDATE orders"
            " SET order_status = 'cancelled', route_id = NULL, route_order = NULL,"
            "     updated_at = datetime('now')"
            " WHERE id = ?",
            (order_id,)
        )
        # Remove from route if the order was assigned to one
        if row['route_id'] is not None:
            db.execute(
                "UPDATE delivery_routes"
                " SET total_orders = MAX(0, total_orders - 1)"
                " WHERE id = ?",
                (row['route_id'],)
            )
        release(db, order_id)
        db.commit()
    except Exception:
        db.rollback()
        raise
