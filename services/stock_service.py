"""Stock reservation and release helpers (business rules S1, S2, R2, R5).

``stock_available = stock_total - reserved_in_active_orders``.
The counter is maintained as a denormalised column for fast reads.
"""


def check_availability(db, variety_id: int, qty: int) -> bool:
    """Check whether a variety has enough stock to fulfil a request.

    Args:
        db: Active SQLite connection.
        variety_id: Primary key of the tulip variety to check.
        qty: Number of units requested.

    Returns:
        ``True`` if ``stock_available >= qty`` for the active variety,
        ``False`` if the variety is inactive, not found, or has
        insufficient stock.
    """
    row = db.execute(
        'SELECT stock_available FROM tulip_varieties WHERE id = ? AND is_active = 1',
        (variety_id,)
    ).fetchone()
    if row is None:
        return False
    return row['stock_available'] >= qty


def reserve(db, variety_id: int, qty: int) -> None:
    """Decrease ``stock_available`` after an order is created (rule S2).

    Args:
        db: Active SQLite connection.
        variety_id: Primary key of the tulip variety to reserve.
        qty: Number of units to reserve.
    """
    db.execute(
        '''UPDATE tulip_varieties
              SET stock_available = stock_available - ?
            WHERE id = ?''',
        (qty, variety_id)
    )


def release(db, order_id: int) -> None:
    """Return stock for every item in a cancelled order (business rule R5).

    Increments ``stock_available`` for each order item.  Does **not**
    commit — the caller is responsible for the enclosing transaction.

    Args:
        db: Active SQLite connection.
        order_id: Primary key of the cancelled order whose items should be
            released back to available stock.
    """
    items = db.execute(
        'SELECT variety_id, quantity FROM order_items WHERE order_id = ?',
        (order_id,)
    ).fetchall()
    for item in items:
        db.execute(
            '''UPDATE tulip_varieties
                  SET stock_available = stock_available + ?
                WHERE id = ?''',
            (item['quantity'], item['variety_id'])
        )
