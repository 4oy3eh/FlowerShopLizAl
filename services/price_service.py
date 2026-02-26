"""Price snapshot service for order creation (business rule R1).

All prices are READ from the DB at the moment create_order() is called
and stored in orders / order_items.  Subsequent price changes in
tulip_varieties / wrapping_options / system_settings do NOT affect
already-created orders.
"""


def _snapshot_single_bouquet(db, variety_id_qty_pairs, wrapping_id, tissue, has_note):
    """Compute price breakdown for ONE bouquet (no delivery charge).

    Returns a dict with keys ``items``, ``flowers_total``, ``wrapping_price``,
    ``note_price``.  Delivery is handled at the order level.

    Raises ValueError if any variety or wrapping option is not found.
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

    wrapping_price = 0.0
    tissue_present = bool(tissue) and tissue != 'none'

    if wrapping_id:
        w_row = db.execute(
            'SELECT wrapping_type, current_price FROM wrapping_options WHERE id = ? AND is_active = 1',
            (wrapping_id,)
        ).fetchone()
        if w_row is None:
            raise ValueError(f'Упаковка #{wrapping_id} не найдена')
        if w_row['wrapping_type'] == 'слюда':
            wrapping_price = float(w_row['current_price'])
            tissue_present = False
        else:
            row = db.execute(
                "SELECT value FROM system_settings WHERE key = 'packaging_price'"
            ).fetchone()
            wrapping_price = float(row['value']) if row else 120.0
    elif tissue_present:
        row = db.execute(
            "SELECT value FROM system_settings WHERE key = 'packaging_price'"
        ).fetchone()
        wrapping_price = float(row['value']) if row else 120.0

    note_price = 0.0
    if has_note:
        row = db.execute(
            "SELECT value FROM system_settings WHERE key = 'note_price'"
        ).fetchone()
        if row:
            note_price = float(row['value'])

    return {
        'items':          items,
        'flowers_total':  flowers_total,
        'wrapping_price': wrapping_price,
        'note_price':     note_price,
    }


def snapshot_all_bouquets(db, bouquets_data, is_pickup):
    """Fix all prices at order-creation time for a multi-bouquet order (rule R1).

    Args:
        db: Active SQLite connection.
        bouquets_data: List of dicts, each with:
            ``variety_id_qty_pairs`` — list of ``(variety_id, quantity)`` tuples,
            ``wrapping_id`` — int or None,
            ``tissue`` — tissue string,
            ``has_note`` — bool.
        is_pickup: True if the customer picks up (no delivery charge).

    Returns:
        dict with aggregate ``flowers_total``, ``wrapping_price``, ``note_price``,
        ``delivery_price``, ``total_price``, and ``bouquets`` list where each entry
        mirrors the per-bouquet breakdown from ``_snapshot_single_bouquet``.

    Raises:
        ValueError: If any variety or wrapping option is not found/inactive.
    """
    total_flowers  = 0.0
    total_wrapping = 0.0
    total_note     = 0.0
    bouquet_results = []

    for b in bouquets_data:
        result = _snapshot_single_bouquet(
            db,
            b['variety_id_qty_pairs'],
            b.get('wrapping_id'),
            b.get('tissue', 'florist'),
            b.get('has_note', False),
        )
        bouquet_results.append(result)
        total_flowers  += result['flowers_total']
        total_wrapping += result['wrapping_price']
        total_note     += result['note_price']

    delivery_price = 0.0
    if not is_pickup:
        row = db.execute(
            "SELECT value FROM system_settings WHERE key = 'delivery_price'"
        ).fetchone()
        if row:
            delivery_price = float(row['value'])

    total_price = total_flowers + total_wrapping + total_note + delivery_price

    return {
        'bouquets':       bouquet_results,
        'flowers_total':  total_flowers,
        'wrapping_price': total_wrapping,
        'note_price':     total_note,
        'delivery_price': delivery_price,
        'total_price':    total_price,
    }


def snapshot_prices(db, variety_id_qty_pairs, wrapping_id, tissue, has_note, is_pickup):
    """Fix all prices at order-creation time (business rule R1).

    Reads current prices from the database and assembles a complete price
    breakdown that will be persisted with the order.  After this point,
    changes to the price catalogue have no effect on this order.

    Args:
        db: Active SQLite connection.
        variety_id_qty_pairs: List of ``(variety_id, quantity)`` tuples.
        wrapping_id: Primary key in ``wrapping_options``, or ``None`` for
            no wrapping.
        tissue: Tissue value string — 'florist', 'none', 'white', 'cream',
            'black', or 'pink'.  Anything other than 'none' counts as
            "tissue present" and contributes to the packaging combo price.
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

    # Packaging combo (wrapping + tissue) — flat rate from system_settings.
    # The rule: charge packaging_price (120 грн) when ANY packaging is present.
    # Exception: 'слюда' wrapping_type uses its own current_price and ignores tissue.
    # Wrapping is "present" if a wrapping_id is provided (including "Выбор флориста").
    # Tissue is "present" if tissue is anything other than 'none'.
    # Ribbon is always free (no price column in ribbon_colors).
    wrapping_price = 0.0
    tissue_present = bool(tissue) and tissue != 'none'

    if wrapping_id:
        w_row = db.execute(
            'SELECT wrapping_type, current_price FROM wrapping_options WHERE id = ? AND is_active = 1',
            (wrapping_id,)
        ).fetchone()
        if w_row is None:
            raise ValueError(f'Упаковка #{wrapping_id} не найдена')
        if w_row['wrapping_type'] == 'слюда':
            # Слюда: own price, tissue is not applicable
            wrapping_price = float(w_row['current_price'])
            tissue_present = False
        else:
            # All other wrappings: flat packaging_price
            row = db.execute(
                "SELECT value FROM system_settings WHERE key = 'packaging_price'"
            ).fetchone()
            wrapping_price = float(row['value']) if row else 120.0
    elif tissue_present:
        row = db.execute(
            "SELECT value FROM system_settings WHERE key = 'packaging_price'"
        ).fetchone()
        wrapping_price = float(row['value']) if row else 120.0

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
