#!/bin/bash
# ---------------------------------------------------------------------------
# start.sh — FlowerShop launcher
# Usage: bash start.sh
# Requires: Python 3.11+, ngrok installed and authenticated
# ---------------------------------------------------------------------------

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "🌷 FlowerShop — запуск..."
echo ""

# 1. Seed reference data (idempotent — safe to run every time)
echo "📦 Загрузка начальных данных..."
python database/seed.py

# 2. Start Flask in the background
echo "🚀 Запуск Flask на порту 5000..."
python app.py &
FLASK_PID=$!

# Give Flask a moment to bind the port
sleep 2

# Check Flask actually started
if ! kill -0 "$FLASK_PID" 2>/dev/null; then
    echo "❌ Flask не запустился. Проверьте ошибки выше."
    exit 1
fi

echo "   Flask запущен (PID $FLASK_PID)"

# 3. Start ngrok tunnel
echo "🌐 Открываем ngrok туннель..."
ngrok http 5000 --log=stdout --log-level=warn > /tmp/ngrok_flower.log 2>&1 &
NGROK_PID=$!

# Wait for ngrok to establish the tunnel
sleep 4

# 4. Extract the public URL from ngrok's local API
NGROK_URL=$(python3 - <<'PYEOF'
import urllib.request, json, sys
try:
    resp = urllib.request.urlopen('http://localhost:4040/api/tunnels', timeout=5)
    data = json.loads(resp.read())
    tunnels = data.get('tunnels', [])
    # Prefer https tunnel
    for t in tunnels:
        if t.get('proto') == 'https':
            print(t['public_url'])
            sys.exit(0)
    if tunnels:
        print(tunnels[0]['public_url'])
except Exception as e:
    pass
PYEOF
)

# 5. Print the result
echo ""
echo "=============================================="
echo "  FlowerShop запущен!"
echo "----------------------------------------------"
echo "  Локально:  http://localhost:5000"
if [ -n "$NGROK_URL" ]; then
    echo "  Команде:   $NGROK_URL"
    echo ""
    echo "  Отправьте эту ссылку команде:"
    echo "  👉 $NGROK_URL"
else
    echo "  Команде:   смотрите http://localhost:4040"
    echo "  (ngrok URL не удалось получить автоматически)"
fi
echo "=============================================="
echo ""
echo "Для остановки нажмите Ctrl+C"
echo ""

# 6. Cleanup on exit
cleanup() {
    echo ""
    echo "Останавливаем сервисы..."
    kill "$FLASK_PID" 2>/dev/null || true
    kill "$NGROK_PID" 2>/dev/null || true
    echo "Готово."
}
trap cleanup INT TERM

# Keep script alive until user presses Ctrl+C
wait "$FLASK_PID"
