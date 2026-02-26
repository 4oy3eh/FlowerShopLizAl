"""Order creation and lifecycle management (business logic layer).

:func:`create_order` is the single entry point: it validates all inputs,
fixes prices, checks stock, persists the order, and reserves stock in one
atomic transaction.  All public functions raise :class:`ValueError` with a
user-readable message on any rule violation.
"""

import re
from datetime import datetime

from config import DEFAULT_DELIVERY_DATE
from services.price_service import snapshot_all_bouquets, snapshot_prices
from services.stock_service import check_availability, reserve, release

_PHONE_RE = re.compile(r'^\+380\d{9}$')
_INSTAGRAM_RE = re.compile(r'^@[\w.]{1,30}$')

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
    """Validate a phone number or Instagram handle.

    Accepts either a Ukrainian mobile number (+380XXXXXXXXX) or an
    Instagram handle starting with @ (e.g. @username).

    Raises:
        ValueError: If the value does not match either accepted format.
    """
    phone = phone.strip()
    if phone.startswith('@'):
        if not _INSTAGRAM_RE.match(phone):
            raise ValueError(f'Неверный формат Instagram для {label}: нужен @username')
    elif not _PHONE_RE.match(phone):
        raise ValueError(f'Неверный формат {label}: нужен +380XXXXXXXXX или @instagram')
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
    """Execute the full order-creation flow (supports multiple bouquets per order).

    Validates inputs, snapshots prices (rule R1), checks stock availability
    (rule R2), persists the order, its bouquets and their items, and reserves
    stock (rule S2) — all inside a single transaction.

    Args:
        db: Active SQLite connection.
        data: Form data dict.  Expected keys:

            * ``customer_phone`` — caller's phone (``+380…``); falls back to
              recipient's phone if blank.
            * ``customer_name`` — caller's display name.
            * ``recipient_name`` — bouquet recipient name.
            * ``recipient_phone`` — recipient phone (``+380…``).
            * ``is_pickup`` — ``'1'`` for self-pickup, ``'0'`` for delivery.
            * ``delivery_address`` — required when ``is_pickup == '0'``.
            * ``delivery_date`` — ISO date string.
            * ``desired_time`` — delivery time slot string.
            * ``bouquets`` — list of dicts, one per bouquet:
                  ``variety_ids``    — list of variety ID strings,
                  ``quantities``     — list of quantity strings,
                  ``wrapping_id``    — int string or ``''``,
                  ``ribbon_color_id`` — int string (required),
                  ``tissue``         — tissue value string,
                  ``has_note``       — bool,
                  ``note_text``      — note body string.

    Returns:
        The ``id`` of the newly created order.

    Raises:
        ValueError: On any validation failure or business-rule violation.
    """
    _TISSUE_VALUES = {'florist', 'none', 'white', 'cream', 'black', 'pink'}

    # ── Parse order-level fields ──────────────────────────────────────────
    is_pickup = data.get('is_pickup') in ('1', True, 1)

    recipient_name = (data.get('recipient_name') or '').strip()

    raw_recip_phone = (data.get('recipient_phone') or '').strip()
    if raw_recip_phone:
        recipient_phone = _validate_phone(raw_recip_phone, 'телефона получателя')
    else:
        recipient_phone = ''

    delivery_address = (data.get('delivery_address') or '').strip() or None

    # ── Customer — optional phone, fall back to recipient's ───────────────
    raw_cust_phone = (data.get('customer_phone') or '').strip()
    if raw_cust_phone:
        customer_phone = _validate_phone(raw_cust_phone, 'телефона заказчика')
        customer_name  = (data.get('customer_name') or '').strip() or 'Аноним'
    elif recipient_phone:
        customer_phone = recipient_phone
        customer_name  = recipient_name or 'Аноним'
    else:
        raise ValueError('Укажите телефон заказчика или получателя')

    # ── Parse and validate each bouquet ──────────────────────────────────
    raw_bouquets = data.get('bouquets') or []
    if not raw_bouquets:
        raise ValueError('Добавьте хотя бы один букет')

    parsed_bouquets = []   # [{ribbon_color_id, wrapping_id, tissue, has_note,
                           #   note_text, variety_id_qty_pairs}, …]

    for idx, b in enumerate(raw_bouquets):
        label = f'Букет {idx + 1}'

        raw_ribbon = (b.get('ribbon_color_id') or '').strip()
        if not raw_ribbon:
            raise ValueError(f'{label}: выберите цвет ленты')
        ribbon_color_id = int(raw_ribbon)

        raw_wrapping = b.get('wrapping_id') or None
        wrapping_id  = int(raw_wrapping) if raw_wrapping else None

        tissue = (b.get('tissue') or 'florist').strip()
        if tissue not in _TISSUE_VALUES:
            tissue = 'florist'

        has_note  = bool(b.get('has_note'))
        note_text = (b.get('note_text') or '').strip() or None
        if has_note and not note_text:
            raise ValueError(f'{label}: введите текст записки или снимите галочку')

        variety_id_qty = _parse_items(
            b.get('variety_ids', []),
            b.get('quantities', []),
        )

        parsed_bouquets.append({
            'ribbon_color_id':    ribbon_color_id,
            'wrapping_id':        wrapping_id,
            'tissue':             tissue,
            'has_note':           has_note,
            'note_text':          note_text,
            'variety_id_qty_pairs': variety_id_qty,
        })

    # ── Stock check (R2) — aggregate across ALL bouquets ─────────────────
    qty_by_variety: dict[int, int] = {}
    for b in parsed_bouquets:
        for vid, qty in b['variety_id_qty_pairs']:
            qty_by_variety[vid] = qty_by_variety.get(vid, 0) + qty

    for vid, total_qty in qty_by_variety.items():
        if not check_availability(db, vid, total_qty):
            row = db.execute(
                'SELECT name, stock_available FROM tulip_varieties WHERE id = ?', (vid,)
            ).fetchone()
            name  = row['name']           if row else f'сорт #{vid}'
            avail = row['stock_available'] if row else 0
            raise ValueError(
                f'Недостаточно «{name}»: запрошено {total_qty}, доступно {avail} шт.'
            )

    # ── Price snapshot (R1) ───────────────────────────────────────────────
    bouquets_for_pricing = [
        {
            'variety_id_qty_pairs': b['variety_id_qty_pairs'],
            'wrapping_id':          b['wrapping_id'],
            'tissue':               b['tissue'],
            'has_note':             b['has_note'],
        }
        for b in parsed_bouquets
    ]
    prices = snapshot_all_bouquets(db, bouquets_for_pricing, is_pickup)

    # First bouquet's values stored on the orders row for backward compat
    first = parsed_bouquets[0]

    # ── Persist in one transaction ────────────────────────────────────────
    try:
        customer_id = find_or_create_customer(db, customer_phone, customer_name)

        db.execute(
            '''INSERT INTO orders (
                   order_number,
                   customer_id,
                   recipient_name, recipient_phone,
                   delivery_address, is_pickup, delivery_date, desired_time,
                   has_note, note_text,
                   wrapping_id, ribbon_color_id, tissue,
                   flowers_total, wrapping_price, note_price, delivery_price, total_price,
                   created_by
               ) VALUES (
                   'TEMP', ?,
                   ?, ?,
                   ?, ?, ?, ?,
                   ?, ?,
                   ?, ?, ?,
                   ?, ?, ?, ?, ?,
                   'admin'
               )''',
            (
                customer_id,
                recipient_name,
                recipient_phone,
                delivery_address,
                1 if is_pickup else 0,
                data.get('delivery_date') or DEFAULT_DELIVERY_DATE,
                data.get('desired_time') or None,
                1 if first['has_note'] else 0,
                first['note_text'],
                first['wrapping_id'],
                first['ribbon_color_id'],
                first['tissue'],
                prices['flowers_total'],
                prices['wrapping_price'],
                prices['note_price'],
                prices['delivery_price'],
                prices['total_price'],
            )
        )
        order_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]

        year = datetime.now().year
        order_number = f'{year}-{order_id:04d}'
        db.execute(
            'UPDATE orders SET order_number = ? WHERE id = ?',
            (order_number, order_id)
        )

        # Insert order_bouquets + order_items per bouquet
        for position, (b_parsed, b_prices) in enumerate(
                zip(parsed_bouquets, prices['bouquets']), start=1):

            db.execute(
                '''INSERT INTO order_bouquets
                       (order_id, position, wrapping_id, ribbon_color_id, tissue,
                        has_note, note_text, wrapping_price, note_price)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (
                    order_id, position,
                    b_parsed['wrapping_id'],
                    b_parsed['ribbon_color_id'],
                    b_parsed['tissue'],
                    1 if b_parsed['has_note'] else 0,
                    b_parsed['note_text'],
                    b_prices['wrapping_price'],
                    b_prices['note_price'],
                )
            )
            bouquet_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]

            for item in b_prices['items']:
                db.execute(
                    '''INSERT INTO order_items
                           (order_id, bouquet_id, variety_id, quantity, unit_price, line_total)
                       VALUES (?, ?, ?, ?, ?, ?)''',
                    (order_id, bouquet_id, item['variety_id'], item['quantity'],
                     item['unit_price'], item['line_total'])
                )

        # Reserve stock (S2)
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


def update_recipient(db, order_id: int, data: dict) -> None:
    """Update recipient info and delivery type for an existing order.

    Args:
        db: Active SQLite connection.
        order_id: Primary key of the order to update.
        data: Dict with optional keys: ``recipient_name``, ``recipient_phone``,
            ``delivery_address``, ``is_pickup``.
            When ``is_pickup`` is present the delivery price and total are
            recalculated using the current ``delivery_price`` system setting.

    Raises:
        ValueError: If order is not found or phone format is invalid.
    """
    row = db.execute(
        'SELECT id, is_pickup, flowers_total, wrapping_price, note_price FROM orders WHERE id = ?',
        (order_id,)
    ).fetchone()
    if row is None:
        raise ValueError('Заказ не найден')

    recipient_name = (data.get('recipient_name') or '').strip()

    raw_phone = (data.get('recipient_phone') or '').strip()
    if raw_phone:
        recipient_phone = _validate_phone(raw_phone, 'телефона получателя')
    else:
        recipient_phone = ''

    delivery_address = (data.get('delivery_address') or '').strip() or None

    # Handle delivery type change
    if 'is_pickup' in data:
        is_pickup = data['is_pickup'] in ('1', True, 1)
        if is_pickup:
            delivery_price = 0.0
        else:
            setting = db.execute(
                "SELECT value FROM system_settings WHERE key = 'delivery_price'"
            ).fetchone()
            delivery_price = float(setting['value']) if setting else 0.0
        total_price = (
            float(row['flowers_total'])
            + float(row['wrapping_price'])
            + float(row['note_price'])
            + delivery_price
        )
        db.execute(
            """UPDATE orders
                  SET recipient_name = ?, recipient_phone = ?, delivery_address = ?,
                      is_pickup = ?, delivery_price = ?, total_price = ?,
                      updated_at = datetime('now')
                WHERE id = ?""",
            (recipient_name, recipient_phone, delivery_address,
             1 if is_pickup else 0, delivery_price, total_price, order_id)
        )
    else:
        db.execute(
            """UPDATE orders
                  SET recipient_name = ?, recipient_phone = ?, delivery_address = ?,
                      updated_at = datetime('now')
                WHERE id = ?""",
            (recipient_name, recipient_phone, delivery_address, order_id)
        )
    db.commit()


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


def update_order(db, order_id: int, data: dict) -> None:
    """Full update of an order: bouquets, items, delivery info, dates.

    Allowed only for orders in CANCEL_ALLOWED statuses.  Releases old stock
    reservations, re-validates, re-prices at current rates, replaces all
    bouquet/item rows, and re-reserves new stock — all in one transaction.

    Args:
        db: Active SQLite connection.
        order_id: Primary key of the order to update.
        data: Same structure as :func:`create_order` data dict.

    Raises:
        ValueError: On validation failure, stock shortage, or wrong status.
    """
    _TISSUE_VALUES = {'florist', 'none', 'white', 'cream', 'black', 'pink'}

    row = db.execute(
        'SELECT order_status, customer_id FROM orders WHERE id = ?', (order_id,)
    ).fetchone()
    if row is None:
        raise ValueError('Заказ не найден')
    if row['order_status'] not in CANCEL_ALLOWED:
        raise ValueError('Нельзя редактировать заказ в текущем статусе')

    # ── Parse order-level fields ─────────────────────────────────────────
    is_pickup = data.get('is_pickup') in ('1', True, 1)

    recipient_name = (data.get('recipient_name') or '').strip()

    raw_recip_phone = (data.get('recipient_phone') or '').strip()
    if raw_recip_phone:
        recipient_phone = _validate_phone(raw_recip_phone, 'телефона получателя')
    else:
        recipient_phone = ''

    delivery_address = (data.get('delivery_address') or '').strip() or None
    delivery_date = (data.get('delivery_date') or DEFAULT_DELIVERY_DATE).strip()
    desired_time = (data.get('desired_time') or '').strip() or None

    # ── Customer — update if new phone supplied ──────────────────────────
    raw_cust_phone = (data.get('customer_phone') or '').strip()
    if raw_cust_phone:
        customer_phone = _validate_phone(raw_cust_phone, 'телефона заказчика')
        customer_name  = (data.get('customer_name') or '').strip() or 'Аноним'
        customer_id    = find_or_create_customer(db, customer_phone, customer_name)
    else:
        customer_id = row['customer_id']

    # ── Parse bouquets ───────────────────────────────────────────────────
    raw_bouquets = data.get('bouquets') or []
    if not raw_bouquets:
        raise ValueError('Добавьте хотя бы один букет')

    parsed_bouquets = []
    for idx, b in enumerate(raw_bouquets):
        label = f'Букет {idx + 1}'

        raw_ribbon = (b.get('ribbon_color_id') or '').strip()
        if not raw_ribbon:
            raise ValueError(f'{label}: выберите цвет ленты')
        ribbon_color_id = int(raw_ribbon)

        raw_wrapping = b.get('wrapping_id') or None
        wrapping_id  = int(raw_wrapping) if raw_wrapping else None

        tissue = (b.get('tissue') or 'florist').strip()
        if tissue not in _TISSUE_VALUES:
            tissue = 'florist'

        has_note  = bool(b.get('has_note'))
        note_text = (b.get('note_text') or '').strip() or None
        if has_note and not note_text:
            raise ValueError(f'{label}: введите текст записки или снимите галочку')

        variety_id_qty = _parse_items(
            b.get('variety_ids', []),
            b.get('quantities', []),
        )

        parsed_bouquets.append({
            'ribbon_color_id':      ribbon_color_id,
            'wrapping_id':          wrapping_id,
            'tissue':               tissue,
            'has_note':             has_note,
            'note_text':            note_text,
            'variety_id_qty_pairs': variety_id_qty,
        })

    # Aggregate quantities by variety across all bouquets
    qty_by_variety: dict[int, int] = {}
    for b in parsed_bouquets:
        for vid, qty in b['variety_id_qty_pairs']:
            qty_by_variety[vid] = qty_by_variety.get(vid, 0) + qty

    try:
        # 1. Release old stock BEFORE deleting items (release reads order_items)
        release(db, order_id)

        # 2. Drop old bouquet/item rows
        db.execute('DELETE FROM order_items   WHERE order_id = ?', (order_id,))
        db.execute('DELETE FROM order_bouquets WHERE order_id = ?', (order_id,))

        # 3. Stock check (old stock is now free again)
        for vid, total_qty in qty_by_variety.items():
            if not check_availability(db, vid, total_qty):
                vrow = db.execute(
                    'SELECT name, stock_available FROM tulip_varieties WHERE id = ?', (vid,)
                ).fetchone()
                name  = vrow['name']           if vrow else f'сорт #{vid}'
                avail = vrow['stock_available'] if vrow else 0
                raise ValueError(
                    f'Недостаточно «{name}»: запрошено {total_qty}, доступно {avail} шт.'
                )

        # 4. Price snapshot at current rates
        bouquets_for_pricing = [
            {
                'variety_id_qty_pairs': b['variety_id_qty_pairs'],
                'wrapping_id':          b['wrapping_id'],
                'tissue':               b['tissue'],
                'has_note':             b['has_note'],
            }
            for b in parsed_bouquets
        ]
        prices = snapshot_all_bouquets(db, bouquets_for_pricing, is_pickup)
        first  = parsed_bouquets[0]

        # 5. Update orders row
        db.execute(
            """UPDATE orders
                  SET customer_id = ?,
                      recipient_name = ?, recipient_phone = ?,
                      delivery_address = ?, is_pickup = ?,
                      delivery_date = ?, desired_time = ?,
                      has_note = ?, note_text = ?,
                      wrapping_id = ?, ribbon_color_id = ?, tissue = ?,
                      flowers_total = ?, wrapping_price = ?, note_price = ?,
                      delivery_price = ?, total_price = ?,
                      updated_at = datetime('now')
                WHERE id = ?""",
            (
                customer_id,
                recipient_name, recipient_phone,
                delivery_address, 1 if is_pickup else 0,
                delivery_date, desired_time,
                1 if first['has_note'] else 0, first['note_text'],
                first['wrapping_id'], first['ribbon_color_id'], first['tissue'],
                prices['flowers_total'], prices['wrapping_price'],
                prices['note_price'], prices['delivery_price'], prices['total_price'],
                order_id,
            )
        )

        # 6. Insert new bouquets + items
        for position, (b_parsed, b_prices) in enumerate(
                zip(parsed_bouquets, prices['bouquets']), start=1):

            db.execute(
                '''INSERT INTO order_bouquets
                       (order_id, position, wrapping_id, ribbon_color_id, tissue,
                        has_note, note_text, wrapping_price, note_price)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (
                    order_id, position,
                    b_parsed['wrapping_id'],
                    b_parsed['ribbon_color_id'],
                    b_parsed['tissue'],
                    1 if b_parsed['has_note'] else 0,
                    b_parsed['note_text'],
                    b_prices['wrapping_price'],
                    b_prices['note_price'],
                )
            )
            bouquet_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]

            for item in b_prices['items']:
                db.execute(
                    '''INSERT INTO order_items
                           (order_id, bouquet_id, variety_id, quantity, unit_price, line_total)
                       VALUES (?, ?, ?, ?, ?, ?)''',
                    (order_id, bouquet_id, item['variety_id'], item['quantity'],
                     item['unit_price'], item['line_total'])
                )

        # 7. Reserve new stock
        for vid, total_qty in qty_by_variety.items():
            reserve(db, vid, total_qty)

        db.commit()

    except Exception:
        db.rollback()
        raise
