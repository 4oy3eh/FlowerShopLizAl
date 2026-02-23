"""Payment recording business logic (business rules R4, P1, P3).

All payments must flow through :func:`record_payment` — never update
``paid_amount`` directly on the orders table.
"""


def record_payment(
    db,
    order_id: int,
    amount: float,
    payment_type: str,
    received_by: str = '',
    notes: str = '',
) -> dict:
    """Record a single payment against an order.

    Executes the following steps inside one transaction:

    1. Validate inputs.
    2. INSERT a row into ``payment_log`` (rule R4).
    3. UPDATE ``orders.paid_amount += amount``.
    4. Recalculate ``payment_status`` (rule P1).
    5. Recalculate ``overpayment`` amount (rule P3).
    6. Commit.

    Args:
        db: Active SQLite connection.
        order_id: Primary key of the order being paid.
        amount: Payment amount in UAH.  Must be greater than zero.
        payment_type: One of ``'cash'``, ``'card'``, or ``'transfer'``.
        received_by: Name of the team member who accepted the payment.
            Optional.
        notes: Free-form notes to attach to the payment log entry.
            Optional.

    Returns:
        dict: Updated payment state with keys ``paid_amount``,
            ``total_price``, ``payment_status``, and ``overpayment``.

    Raises:
        ValueError: If ``amount <= 0``, ``payment_type`` is invalid, the
            order is not found, or the order is already cancelled.
    """
    if amount <= 0:
        raise ValueError('Сумма платежа должна быть больше 0')

    if payment_type not in ('cash', 'card', 'transfer'):
        raise ValueError('Неверный тип оплаты')

    order = db.execute(
        'SELECT id, total_price, paid_amount, order_status FROM orders WHERE id = ?',
        (order_id,),
    ).fetchone()

    if order is None:
        raise ValueError('Заказ не найден')

    if order['order_status'] == 'cancelled':
        raise ValueError('Нельзя принять оплату за отменённый заказ')

    new_paid = order['paid_amount'] + amount
    total = order['total_price']

    # P1: recalculate payment status
    if new_paid <= 0:
        status = 'unpaid'
    elif new_paid < total:
        status = 'partial'
    elif new_paid == total:
        status = 'paid'
    else:
        status = 'overpaid'

    # P3: record overpayment amount
    overpayment = max(0.0, new_paid - total)

    try:
        # R4: every payment gets its own log row
        db.execute(
            '''INSERT INTO payment_log (order_id, amount, payment_type, received_by, notes)
               VALUES (?, ?, ?, ?, ?)''',
            (order_id, amount, payment_type, received_by or None, notes or None),
        )
        db.execute(
            '''UPDATE orders
                  SET paid_amount    = ?,
                      payment_status = ?,
                      overpayment    = ?,
                      updated_at     = datetime('now')
                WHERE id = ?''',
            (new_paid, status, overpayment, order_id),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        'paid_amount':    new_paid,
        'total_price':    total,
        'payment_status': status,
        'overpayment':    overpayment,
    }
