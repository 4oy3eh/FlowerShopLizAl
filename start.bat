@echo off
title FlowerShop Server
cd /d "%~dp0"
echo Запуск FlowerShop на http://localhost:5000
echo Не закрывайте это окно!
echo.
python app.py
pause
