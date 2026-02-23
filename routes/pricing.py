"""Dynamic pricing management routes (Flask Blueprint ``pricing``).

Implements business rules PR1 (bulk percentage update) and PR2 (individual
price change).  All price changes are recorded in ``price_change_log``.

Endpoints:
    GET  /pricing                         — main pricing page
    GET  /pricing/variety/<id>            — HTMX: display row for a variety
    GET  /pricing/variety/<id>/edit       — HTMX: edit form for a variety
    GET  /pricing/wrapping/<id>           — HTMX: display row for a wrapping option
    GET  /pricing/wrapping/<id>/edit      — HTMX: edit form for a wrapping option
    GET  /pricing/setting/<key>           — HTMX: display row for a system setting
    GET  /pricing/setting/<key>/edit      — HTMX: edit form for a system setting
    POST /pricing/update/<type>/<id>      — save individual price (rule PR2)
    POST /pricing/bulk-update             — percentage change for all varieties (rule PR1)
"""

from flask import Blueprint, flash, redirect, render_template, request

from database.db import get_db

pricing_bp = Blueprint('pricing', __name__)

# Human-readable names for editable system_settings keys
SETTING_LABELS = {
    'note_price':     'Записка',
    'delivery_price': 'Доставка',
}


@pricing_bp.context_processor
def _inject_helpers():
    """Inject ``SETTING_LABELS`` dict into every pricing template."""
    return {'SETTING_LABELS': SETTING_LABELS}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _log_price_change(db, entity_type, entity_id, field_name, old_value, new_value):
    """Insert one audit row into ``price_change_log`` (rules PR1, PR2).

    Args:
        db: Active SQLite connection.
        entity_type: One of ``'variety'``, ``'wrapping'``, or ``'setting'``.
        entity_id: Primary key of the changed entity, or ``None`` for
            settings (which use a string key).
        field_name: Name of the changed field (e.g. ``'current_sell_price'``).
        old_value: Previous value (will be stored as a string).
        new_value: New value (will be stored as a string).
    """
    db.execute(
        '''INSERT INTO price_change_log
               (entity_type, entity_id, field_name, old_value, new_value, changed_by)
           VALUES (?, ?, ?, ?, ?, 'admin')''',
        (entity_type, entity_id, field_name, str(old_value), str(new_value)),
    )


def _parse_price(raw: str) -> float:
    """Parse a price string and validate it.

    Accepts both ``'.'`` and ``','`` as decimal separators.

    Args:
        raw: Raw price string from the form input.

    Returns:
        Parsed price as a ``float``.

    Raises:
        ValueError: If the string cannot be converted to a float, or if
            the resulting value is negative.
    """
    try:
        price = float(raw.replace(',', '.'))
    except (ValueError, AttributeError):
        raise ValueError('Неверный формат цены')
    if price < 0:
        raise ValueError('Цена не может быть отрицательной')
    return price


# ---------------------------------------------------------------------------
# GET /pricing  — main pricing page
# ---------------------------------------------------------------------------

@pricing_bp.route('/pricing')
def pricing_index():
    """Render the main pricing management page.

    Displays all active variety prices, wrapping prices, editable system
    settings, and the 30 most recent price-change log entries.

    Returns:
        Rendered ``pricing/index.html`` template.
    """
    db = get_db()

    varieties = db.execute(
        '''SELECT id, name, color, purchase_price, current_sell_price, stock_available
             FROM tulip_varieties
            WHERE is_active = 1
            ORDER BY name'''
    ).fetchall()

    wrappings = db.execute(
        '''SELECT id, name, current_price
             FROM wrapping_options
            WHERE is_active = 1
            ORDER BY name'''
    ).fetchall()

    settings = db.execute(
        "SELECT key, value FROM system_settings WHERE key IN ('note_price', 'delivery_price')"
    ).fetchall()

    # Resolve entity names for the history log
    recent_changes = db.execute(
        '''SELECT pcl.*,
                  CASE pcl.entity_type
                      WHEN 'variety'  THEN tv.name
                      WHEN 'wrapping' THEN wo.name
                      ELSE pcl.field_name
                  END AS entity_name
             FROM price_change_log pcl
             LEFT JOIN tulip_varieties  tv ON pcl.entity_type = 'variety'  AND pcl.entity_id = tv.id
             LEFT JOIN wrapping_options wo ON pcl.entity_type = 'wrapping' AND pcl.entity_id = wo.id
            ORDER BY pcl.created_at DESC
            LIMIT 30'''
    ).fetchall()

    return render_template(
        'pricing/index.html',
        varieties=varieties,
        wrappings=wrappings,
        settings=settings,
        recent_changes=recent_changes,
    )


# ---------------------------------------------------------------------------
# HTMX: variety row display / edit
# ---------------------------------------------------------------------------

@pricing_bp.route('/pricing/variety/<int:variety_id>')
def variety_row(variety_id):
    """HTMX partial: render the display row for one tulip variety.

    Args:
        variety_id: Primary key of the variety.

    Returns:
        Rendered ``pricing/_variety_row.html`` partial, or HTTP 404.
    """
    row = get_db().execute(
        '''SELECT id, name, color, purchase_price, current_sell_price, stock_available
             FROM tulip_varieties WHERE id = ?''',
        (variety_id,),
    ).fetchone()
    if row is None:
        return '', 404
    return render_template('pricing/_variety_row.html', v=row)


@pricing_bp.route('/pricing/variety/<int:variety_id>/edit')
def variety_edit_form(variety_id):
    """HTMX partial: render the inline edit form for one tulip variety.

    Args:
        variety_id: Primary key of the variety.

    Returns:
        Rendered ``pricing/_variety_edit.html`` partial, or HTTP 404.
    """
    row = get_db().execute(
        '''SELECT id, name, color, purchase_price, current_sell_price
             FROM tulip_varieties WHERE id = ?''',
        (variety_id,),
    ).fetchone()
    if row is None:
        return '', 404
    return render_template('pricing/_variety_edit.html', v=row)


# ---------------------------------------------------------------------------
# HTMX: wrapping row display / edit
# ---------------------------------------------------------------------------

@pricing_bp.route('/pricing/wrapping/<int:wrapping_id>')
def wrapping_row(wrapping_id):
    """HTMX partial: render the display row for one wrapping option.

    Args:
        wrapping_id: Primary key of the wrapping option.

    Returns:
        Rendered ``pricing/_wrapping_row.html`` partial, or HTTP 404.
    """
    row = get_db().execute(
        'SELECT id, name, current_price FROM wrapping_options WHERE id = ?',
        (wrapping_id,),
    ).fetchone()
    if row is None:
        return '', 404
    return render_template('pricing/_wrapping_row.html', w=row)


@pricing_bp.route('/pricing/wrapping/<int:wrapping_id>/edit')
def wrapping_edit_form(wrapping_id):
    """HTMX partial: render the inline edit form for one wrapping option.

    Args:
        wrapping_id: Primary key of the wrapping option.

    Returns:
        Rendered ``pricing/_wrapping_edit.html`` partial, or HTTP 404.
    """
    row = get_db().execute(
        'SELECT id, name, current_price FROM wrapping_options WHERE id = ?',
        (wrapping_id,),
    ).fetchone()
    if row is None:
        return '', 404
    return render_template('pricing/_wrapping_edit.html', w=row)


# ---------------------------------------------------------------------------
# HTMX: setting row display / edit
# ---------------------------------------------------------------------------

@pricing_bp.route('/pricing/setting/<key>')
def setting_row(key):
    """HTMX partial: render the display row for one system setting.

    Args:
        key: Setting key in ``system_settings`` (e.g. ``'note_price'``).

    Returns:
        Rendered ``pricing/_setting_row.html`` partial, or HTTP 404.
    """
    row = get_db().execute(
        'SELECT key, value FROM system_settings WHERE key = ?', (key,)
    ).fetchone()
    if row is None:
        return '', 404
    return render_template('pricing/_setting_row.html', s=row)


@pricing_bp.route('/pricing/setting/<key>/edit')
def setting_edit_form(key):
    """HTMX partial: render the inline edit form for one system setting.

    Args:
        key: Setting key in ``system_settings`` (e.g. ``'delivery_price'``).

    Returns:
        Rendered ``pricing/_setting_edit.html`` partial, or HTTP 404.
    """
    row = get_db().execute(
        'SELECT key, value FROM system_settings WHERE key = ?', (key,)
    ).fetchone()
    if row is None:
        return '', 404
    return render_template('pricing/_setting_edit.html', s=row)


# ---------------------------------------------------------------------------
# POST /pricing/update/<type>/<id>  — individual price change (PR2)
# ---------------------------------------------------------------------------

@pricing_bp.route('/pricing/update/<entity_type>/<id_or_key>', methods=['POST'])
def update_price(entity_type, id_or_key):
    """Save an individual price change (business rule PR2).

    Logs the change to ``price_change_log`` and returns the updated display
    row partial so HTMX can swap it in-place.

    Args:
        entity_type: One of ``'variety'``, ``'wrapping'``, or ``'setting'``.
        id_or_key: Integer ID string for varieties/wrapping, or the setting
            key string for system settings.

    Returns:
        Rendered display row partial on success, or an error string with
        HTTP 400/404/422.
    """
    db = get_db()

    try:
        new_price = _parse_price(request.form.get('new_price', ''))
    except ValueError as exc:
        return str(exc), 422

    if entity_type == 'variety':
        entity_id = int(id_or_key)
        row = db.execute(
            '''SELECT id, name, color, purchase_price, current_sell_price, stock_available
                 FROM tulip_varieties WHERE id = ?''',
            (entity_id,),
        ).fetchone()
        if row is None:
            return 'Сорт не найден', 404

        _log_price_change(db, 'variety', entity_id,
                          'current_sell_price', row['current_sell_price'], new_price)
        db.execute(
            'UPDATE tulip_varieties SET current_sell_price = ? WHERE id = ?',
            (new_price, entity_id),
        )
        db.commit()

        updated = db.execute(
            '''SELECT id, name, color, purchase_price, current_sell_price, stock_available
                 FROM tulip_varieties WHERE id = ?''',
            (entity_id,),
        ).fetchone()
        return render_template('pricing/_variety_row.html', v=updated)

    elif entity_type == 'wrapping':
        entity_id = int(id_or_key)
        row = db.execute(
            'SELECT id, name, current_price FROM wrapping_options WHERE id = ?',
            (entity_id,),
        ).fetchone()
        if row is None:
            return 'Упаковка не найдена', 404

        _log_price_change(db, 'wrapping', entity_id,
                          'current_price', row['current_price'], new_price)
        db.execute(
            'UPDATE wrapping_options SET current_price = ? WHERE id = ?',
            (new_price, entity_id),
        )
        db.commit()

        updated = db.execute(
            'SELECT id, name, current_price FROM wrapping_options WHERE id = ?',
            (entity_id,),
        ).fetchone()
        return render_template('pricing/_wrapping_row.html', w=updated)

    elif entity_type == 'setting':
        key = id_or_key
        row = db.execute(
            'SELECT key, value FROM system_settings WHERE key = ?', (key,)
        ).fetchone()
        if row is None:
            return 'Настройка не найдена', 404

        _log_price_change(db, 'setting', None, key, row['value'], new_price)
        db.execute(
            'UPDATE system_settings SET value = ? WHERE key = ?',
            (str(int(new_price)), key),
        )
        db.commit()

        updated = db.execute(
            'SELECT key, value FROM system_settings WHERE key = ?', (key,)
        ).fetchone()
        return render_template('pricing/_setting_row.html', s=updated)

    return 'Неверный тип сущности', 400


# ---------------------------------------------------------------------------
# POST /pricing/bulk-update  — percentage change for all varieties (PR1)
# ---------------------------------------------------------------------------

@pricing_bp.route('/pricing/bulk-update', methods=['POST'])
def bulk_update():
    """Apply a percentage price change to all active varieties (rule PR1).

    Form params:
        percent: Percentage to apply (0.1 – 200).
        direction: ``'up'`` to raise prices, ``'down'`` to lower them.

    Returns:
        Redirect to ``/pricing`` with a flash message indicating success
        or error.
    """
    db = get_db()

    try:
        percent = float(request.form.get('percent', '0').replace(',', '.'))
        if percent <= 0 or percent > 200:
            raise ValueError('Введите процент от 0.1 до 200')
    except ValueError as exc:
        flash(str(exc), 'error')
        return redirect('/pricing')

    direction = request.form.get('direction', 'up')
    multiplier = (1 + percent / 100) if direction == 'up' else (1 - percent / 100)
    verb = 'подняты' if direction == 'up' else 'снижены'

    varieties = db.execute(
        'SELECT id, current_sell_price FROM tulip_varieties WHERE is_active = 1'
    ).fetchall()

    if not varieties:
        flash('Нет активных сортов', 'error')
        return redirect('/pricing')

    try:
        for v in varieties:
            old_price = v['current_sell_price']
            # Round to nearest whole UAH, minimum 1 ₴
            new_price = max(1, round(old_price * multiplier))
            _log_price_change(db, 'variety', v['id'],
                              'current_sell_price', old_price, new_price)
            db.execute(
                'UPDATE tulip_varieties SET current_sell_price = ? WHERE id = ?',
                (new_price, v['id']),
            )
        db.commit()
    except Exception:
        db.rollback()
        flash('Ошибка при обновлении цен', 'error')
        return redirect('/pricing')

    flash(f'Цены {verb} на {percent:g}% ({len(varieties)} сортов)', 'success')
    return redirect('/pricing')
