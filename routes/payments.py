"""Payment acceptance and financial summary routes (Flask Blueprint ``payments``).

Endpoints:
    GET  /orders/<id>/payment-form  — HTMX: load payment input form
    POST /payments/add              — HTMX: accept a payment, return updated section
    GET  /orders/<id>/payments      — HTMX: payment history partial (also used to
                                      cancel the payment form)
    GET  /payments                  — full financial dashboard page
"""

from flask import Blueprint, render_template, request

from database.db import get_db
from services.payment_service import record_payment

payments_bp = Blueprint('payments', __name__)

PAYMENT_TYPE_LABELS = {
    'cash':     'Наличные',
    'card':     'Карта',
    'transfer': 'Перевод',
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


@payments_bp.context_processor
def _inject_helpers():
    """Inject payment and order display dictionaries into every payments template."""
    return {
        'PAYMENT_TYPE_LABELS': PAYMENT_TYPE_LABELS,
        'PAYMENT_LABELS':      PAYMENT_LABELS,
        'PAYMENT_COLORS':      PAYMENT_COLORS,
        'STATUS_LABELS':       STATUS_LABELS,
        'STATUS_COLORS':       STATUS_COLORS,
    }


# ---------------------------------------------------------------------------
# Internal DB helpers
# ---------------------------------------------------------------------------

def _fetch_order(db, order_id: int):
    """Fetch a full order row (with customer, wrapping, ribbon names).

    Args:
        db: Active SQLite connection.
        order_id: Primary key of the order.

    Returns:
        ``sqlite3.Row`` with joined customer/wrapping/ribbon data, or
        ``None`` if not found.
    """
    return db.execute(
        '''SELECT o.*,
                  c.name  AS customer_name, c.phone AS customer_phone,
                  w.name  AS wrapping_name,
                  r.name  AS ribbon_name
             FROM orders o
             JOIN customers c             ON o.customer_id     = c.id
             LEFT JOIN wrapping_options w ON o.wrapping_id     = w.id
             JOIN ribbon_colors r         ON o.ribbon_color_id = r.id
            WHERE o.id = ?''',
        (order_id,),
    ).fetchone()


def _fetch_payments(db, order_id: int):
    """Fetch all payment log entries for an order, ordered chronologically.

    Args:
        db: Active SQLite connection.
        order_id: Primary key of the order.

    Returns:
        List of ``sqlite3.Row`` objects from ``payment_log``.
    """
    return db.execute(
        'SELECT * FROM payment_log WHERE order_id = ? ORDER BY created_at',
        (order_id,),
    ).fetchall()


# ---------------------------------------------------------------------------
# GET /orders/<id>/payment-form  — HTMX: load payment form into the section
# ---------------------------------------------------------------------------

@payments_bp.route('/orders/<int:order_id>/payment-form')
def payment_form(order_id):
    """HTMX: load the payment input form into the payment section.

    Args:
        order_id: Primary key of the order.

    Returns:
        Rendered ``payments/_form_section.html`` partial, or HTTP 404.
    """
    db    = get_db()
    order = _fetch_order(db, order_id)
    if order is None:
        return 'Заказ не найден', 404
    return render_template('payments/_form_section.html', order=order)


# ---------------------------------------------------------------------------
# POST /payments/add  — accept payment (HTMX: replaces #payment-section)
# ---------------------------------------------------------------------------

@payments_bp.route('/payments/add', methods=['POST'])
def add_payment():
    """HTMX: accept a payment and return the updated payment section.

    On success returns ``payments/_section.html`` with the refreshed totals
    and history.  On validation error returns ``payments/_form_section.html``
    with an inline error message and HTTP 422 so HTMX replaces the form.

    Returns:
        Rendered ``payments/_section.html`` (HTTP 200) or
        ``payments/_form_section.html`` (HTTP 422).
    """
    order_id     = request.form.get('order_id', type=int)
    payment_type = request.form.get('payment_type', 'cash')
    received_by  = request.form.get('received_by', '').strip()
    notes        = request.form.get('notes', '').strip()

    try:
        amount = float(request.form.get('amount', '0').replace(',', '.'))
    except ValueError:
        amount = 0.0

    db = get_db()
    try:
        record_payment(db, order_id, amount, payment_type, received_by, notes)
    except ValueError as exc:
        # Re-render form with inline error so the user can correct and retry
        order = _fetch_order(db, order_id)
        return render_template(
            'payments/_form_section.html',
            order=order,
            error=str(exc),
        ), 422

    # Success: return updated payment section
    order    = _fetch_order(db, order_id)
    payments = _fetch_payments(db, order_id)
    return render_template('payments/_section.html', order=order, payments=payments)


# ---------------------------------------------------------------------------
# GET /orders/<id>/payments  — payment history (also used by "cancel" in form)
# ---------------------------------------------------------------------------

@payments_bp.route('/orders/<int:order_id>/payments')
def order_payments(order_id):
    """HTMX: render the payment history section for an order.

    Also used as the "cancel" target to dismiss the payment input form and
    return to the read-only summary view.

    Args:
        order_id: Primary key of the order.

    Returns:
        Rendered ``payments/_section.html`` partial, or HTTP 404.
    """
    db    = get_db()
    order = _fetch_order(db, order_id)
    if order is None:
        return 'Заказ не найден', 404
    payments = _fetch_payments(db, order_id)
    return render_template('payments/_section.html', order=order, payments=payments)


# ---------------------------------------------------------------------------
# GET /payments  — financial dashboard
# ---------------------------------------------------------------------------

@payments_bp.route('/payments')
def payments_index():
    """Render the financial dashboard page.

    Aggregates revenue, received, pending, overpayments, cost of goods,
    and profit.  Also shows today's payment breakdown by type and the full
    debtors list.

    Returns:
        Rendered ``payments/index.html`` template.
    """
    db = get_db()

    # ── Main financial aggregation (non-cancelled orders only) ──────────────
    finance = db.execute(
        '''SELECT
               COALESCE(SUM(total_price), 0)  AS revenue,
               COALESCE(SUM(paid_amount), 0)  AS received,
               COALESCE(SUM(CASE WHEN total_price > paid_amount
                               THEN total_price - paid_amount ELSE 0 END), 0) AS pending,
               COALESCE(SUM(overpayment), 0)  AS overpayments
          FROM orders
         WHERE order_status != 'cancelled' '''
    ).fetchone()

    # Cost price = sum of (purchase_price × quantity) across non-cancelled orders
    cost_row = db.execute(
        '''SELECT COALESCE(SUM(tv.purchase_price * oi.quantity), 0) AS cost
             FROM order_items oi
             JOIN orders o           ON oi.order_id  = o.id
             JOIN tulip_varieties tv ON oi.variety_id = tv.id
            WHERE o.order_status != 'cancelled' '''
    ).fetchone()

    cost   = cost_row['cost']
    profit = finance['revenue'] - cost

    # ── Today's payment breakdown ────────────────────────────────────────────
    today_totals = db.execute(
        '''SELECT
               COALESCE(SUM(CASE WHEN payment_type = 'cash'     THEN amount END), 0) AS cash_total,
               COALESCE(SUM(CASE WHEN payment_type = 'card'     THEN amount END), 0) AS card_total,
               COALESCE(SUM(CASE WHEN payment_type = 'transfer' THEN amount END), 0) AS transfer_total,
               COALESCE(SUM(amount), 0) AS grand_total
          FROM payment_log
         WHERE date(created_at) = date('now') '''
    ).fetchone()

    # ── Debtors: not fully paid, not cancelled; nearest delivery date first ────
    debtors = db.execute(
        '''SELECT o.id, o.order_number, o.recipient_name, o.recipient_phone,
                  o.order_status, o.payment_status, o.delivery_date,
                  o.total_price, o.paid_amount,
                  (o.total_price - o.paid_amount) AS debt
             FROM orders o
            WHERE o.payment_status NOT IN ('paid', 'overpaid')
              AND o.order_status   != 'cancelled'
            ORDER BY o.delivery_date ASC, debt DESC'''
    ).fetchall()

    # ── Today's payment log ──────────────────────────────────────────────────
    today_payments = db.execute(
        '''SELECT pl.*, o.order_number, o.recipient_name
             FROM payment_log pl
             JOIN orders o ON pl.order_id = o.id
            WHERE date(pl.created_at) = date('now')
            ORDER BY pl.created_at DESC'''
    ).fetchall()

    return render_template(
        'payments/index.html',
        finance=finance,
        cost=cost,
        profit=profit,
        today_totals=today_totals,
        debtors=debtors,
        today_payments=today_payments,
    )
