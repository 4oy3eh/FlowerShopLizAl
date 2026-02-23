# 🌷 FlowerShop — Система управления сезонной продажей цветов

Веб-приложение для управления заказами, складом, маршрутами курьера и финансами.
Сезонная продажа тюльпанов к 8 марта в г. Измаил (Украина).

## Quick Reference
- **Stack**: Python 3.11+ / Flask / SQLite / HTMX / TailwindCSS (CDN)
- **Language**: UI на русском, код и комментарии на английском
- **DB**: SQLite (один файл `data/flower_shop.db`)
- **Run**: `python app.py` → http://localhost:5000
- **Test**: `pytest tests/`

## Repository Map
```
/app.py                 # Entry point
/config.py              # Settings, paths, constants
/database/
  /schema.sql           # Full DB schema
  /seed.py              # Initial data (varieties, wrapping, ribbons)
  /db.py                # Connection helper, migrations
/routes/
  /orders.py            # CRUD заказов
  /inventory.py         # Склад
  /delivery.py          # Маршруты курьера
  /payments.py          # Оплата и финансы
  /pricing.py           # Динамическое ценообразование
  /api.py               # JSON endpoints для HTMX
/services/
  /order_service.py     # Бизнес-логика заказов
  /stock_service.py     # Резервирование, проверка остатков
  /route_service.py     # Формирование маршрутов, Google Maps URL
  /payment_service.py   # Расчёт оплат, статусы
  /price_service.py     # Фиксация и изменение цен
/templates/             # Jinja2 + HTMX, mobile-first
/static/                # CSS, minimal JS
/docs-ai/               # Архитектура, бизнес-правила, схема БД
/tests/
/data/                  # SQLite file, backups (gitignored)
```

## Key Patterns
- Mobile-first: все формы оптимизированы под iPhone Safari
- HTMX для интерактивности, минимум JS
- Цена ФИКСИРУЕТСЯ в момент создания заказа — копируется из прайса
- Каждый платёж — отдельная запись в `payment_log`
- Flask Blueprints для каждого модуля routes/

## Mistakes to Avoid
- **НЕ МЕНЯТЬ цены в существующих заказах** при изменении прайса
- **НЕ РАЗРЕШАТЬ заказ** если `stock_available < requested_quantity`
- **НЕ УДАЛЯТЬ данные** — только soft-delete через статусы (cancelled)
- **НЕ ИСПОЛЬЗОВАТЬ ORM** — чистый SQL через `sqlite3`, проект слишком мал
- **НЕ СОЗДАВАТЬ отдельные CSS/JS файлы** — Tailwind CDN + inline HTMX
- **НЕ ДЕЛАТЬ авторизацию** — доверенная локальная сеть, 4 пользователя
- **НЕ УСЛОЖНЯТЬ** — это сезонный инструмент на 2 недели, не SaaS

## Do Not Touch
- `/docs-ai/` — reference only, не генерировать автоматически
- `/data/` — только через `db.py`, никогда напрямую

## When Unsure
- Сверяйся с `docs-ai/ARCHITECTURE.md` для бизнес-логики
- Сверяйся с `docs-ai/SCHEMA.md` для структуры БД
- Сверяйся с `docs-ai/BUSINESS_RULES.md` для правил валидации
