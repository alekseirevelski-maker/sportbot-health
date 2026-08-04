@echo off
set http_proxy=http://127.0.0.1:10809
set https_proxy=http://127.0.0.1:10809
cd /d C:\Users\egoro\Desktop\sport-health-bot
python -u bot.py > logs\output.log 2>&1
