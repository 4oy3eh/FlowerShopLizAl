"""Price snapshot service for order creation (business rule R1).

All prices are READ from the DB at the moment create_order() is called
and stored in orders / order_items.  Subsequent price changes in
tulip_varieties / wrapping_options / system_settings do NOT affect
already-created orders.
"""


def snapshot_prices(db, variety_id_qty_pairs, wrapping_id, has_note, is_pickup):
    """Fix all prices at order-creation time (business rule R1).

    Reads current prices from the database and assembles a complete price
    breakdown that will be persisted with the order.  After this point,
    changes to the price catalogue have no effect on this order.

    Args:
        db: Active SQLite connection.
        variety_id_qty_pairs: List of ``(variety_id, quantity)`` tuples.
        wrapping_id: Primary key in ``wrapping_options``, or ``None`` for
            no wrapping.
        has_note: ``True`` if the order includes a greeting note.
        is_pickup: ``True`` if the customer picks up the order (no delivery
            charge applied).

    Returns:
        dict: Price breakdown with keys ``items``, ``flowers_total``,
            ``wrapping_price``, ``note_price``, ``delivery_price``, and
            ``total_price``.  ``items`` is a list of dicts with keys
            ``variety_id``, ``quantity``, ``unit_price``, ``line_total``.

    Raises:
        ValueError: If a variety or wrapping option is not found or is
            inactive.
    """
    items = []
    flowers_total = 0.0

    for variety_id, quantity in variety_id_qty_pairs:
        row = db.execute(
            'SELECT name, current_sell_price FROM tulip_varieties WHERE id = ? AND is_active = 1',
            (variety_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f'Сорт #{variety_id} не найден или неактивен')

        unit_price = row['current_sell_price']
        line_total = unit_price * quantity
        items.append({
            'variety_id': variety_id,
            'quantity':   quantity,
            'unit_price': unit_price,
            'line_total': line_total,
        })
        flowers_total += line_total

    # Wrapping — snapshot current price (NULL → 0)
    wrapping_price = 0.0
    if wrapping_id:
        row = db.execute(
            'SELECT current_price FROM wrapping_options WHERE id = ? AND is_active = 1',
            (wrapping_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f'Упаковка #{wrapping_id} не найдена')
        wrapping_price = row['current_price']

    # Note — price from system_settings
    note_price = 0.0
    if has_note:
        row = db.execute(
            "SELECT value FROM system_settings WHERE key = 'note_price'"
        ).fetchone()
        if row:
            note_price = float(row['value'])

    # Delivery — price from system_settings (pickup → 0)
    delivery_price = 0.0
    if not is_pickup:
        row = db.execute(
            "SELECT value FROM system_settings WHERE key = 'delivery_price'"
        ).fetchone()
        if row:
            delivery_price = float(row['value'])

    total_price = flowers_total + wrapping_price + note_price + delivery_price

    return {
        'items':          items,
        'flowers_total':  flowers_total,
        'wrapping_price': wrapping_price,
        'note_price':     note_price,
        'delivery_price': delivery_price,
        'total_price':    total_price,
    }
