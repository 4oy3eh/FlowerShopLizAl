"""Delivery route generation service.

Public API:
    :func:`generate_route` — create a route record and assign eligible orders.
    :func:`generate_google_maps_url` — build and persist the round-trip Maps URL.

All public functions raise :class:`ValueError` with a user-readable message
on any rule violation.
"""

from datetime import date
from urllib.parse import quote


def generate_google_maps_url(db, route_id: int) -> str:
    """Build a round-trip Google Maps directions URL for the route and persist it.

    Constructs a URL of the form ``BASE / ADDR1 / ADDR2 / … / BASE`` where
    ``BASE`` is read from ``system_settings.base_address``.  The result is
    written to ``delivery_routes.google_maps_url`` and committed immediately.

    Args:
        db: Active SQLite connection.
        route_id: Primary key of the delivery route.

    Returns:
        The Google Maps directions URL string, or an empty string ``''`` if
        the route has no delivery addresses.
    """
    row = db.execute(
        "SELECT value FROM system_settings WHERE key = 'base_address'"
    ).fetchone()
    base = row['value'].strip() if row else 'Измаил'

    stops = db.execute(
        """SELECT delivery_address FROM orders
            WHERE route_id = ?
              AND delivery_address IS NOT NULL
            ORDER BY route_order""",
        (route_id,),
    ).fetchall()

    addresses = [s['delivery_address'] for s in stops if s['delivery_address']]

    if not addresses:
        return ''

    # Round-trip: BASE → stop1 → stop2 → … → BASE
    all_points = [base] + addresses + [base]
    path = '/'.join(quote(p, safe='') for p in all_points)
    url = f'https://www.google.com/maps/dir/{path}'

    db.execute(
        'UPDATE delivery_routes SET google_maps_url = ? WHERE id = ?',
        (url, route_id),
    )
    db.commit()
    return url


def generate_route(db, time_slot: str, max_orders: int = 15) -> int:
    """Generate a delivery route for the given time slot.

    Selects eligible orders and groups them into a new route record.
    Steps:

    1. Select orders: ``status='ready'``, ``is_pickup=0``,
       ``route_id IS NULL``, ``desired_time = time_slot``, up to
       ``max_orders``.
    2. Calculate a sequential ``route_number`` for today.
    3. INSERT a ``delivery_routes`` record.
    4. UPDATE selected orders with ``route_id`` and ``route_order``.
    5. Generate and persist the Google Maps URL via
       :func:`generate_google_maps_url`.

    Args:
        db: Active SQLite connection.
        time_slot: Delivery time window string, e.g. ``'10-12'``.
        max_orders: Maximum number of orders to include in the route.
            Defaults to ``15``.

    Returns:
        The primary key (``id``) of the newly created
        ``delivery_routes`` record.

    Raises:
        ValueError: If there are no eligible (ready, unrouted) orders
            for the given time slot.
    """
    # 1. Select eligible orders (stable ordering by id)
    orders = db.execute(
        """SELECT id FROM orders
            WHERE order_status = 'ready'
              AND is_pickup     = 0
              AND route_id      IS NULL
              AND desired_time  = ?
            ORDER BY id
            LIMIT ?""",
        (time_slot, max_orders),
    ).fetchall()

    if not orders:
        raise ValueError(
            f'Нет готовых заказов к доставке для временного слота «{time_slot}»'
        )

    # 2. Route number — how many routes were created today + 1
    today = date.today().isoformat()
    existing = db.execute(
        "SELECT COUNT(*) FROM delivery_routes WHERE date(created_at) = ?",
        (today,),
    ).fetchone()[0]
    route_number = existing + 1

    total = len(orders)

    try:
        # 3. INSERT delivery_routes
        db.execute(
            """INSERT INTO delivery_routes
                   (route_number, status, planned_start, total_orders)
               VALUES (?, 'planning', ?, ?)""",
            (route_number, time_slot, total),
        )
        route_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]

        # 4. UPDATE orders — assign route and position
        for position, row in enumerate(orders, start=1):
            db.execute(
                """UPDATE orders
                      SET route_id    = ?,
                          route_order = ?,
                          updated_at  = datetime('now')
                    WHERE id = ?""",
                (route_id, position, row['id']),
            )

        db.commit()

    except Exception:
        db.rollback()
        raise

    # 5. Build and persist Google Maps URL (separate commit, non-critical)
    generate_google_maps_url(db, route_id)

    return route_id
