#!/bin/bash
# Healthcheck for sport-health bot
SERVICE="sport-health.service"
DB="/opt/telegram-bots/sport-health/data/sport_health.db"
LOG="/opt/telegram-bots/sport-health/healthcheck.log"

if systemctl is-active --quiet "$SERVICE"; then
    # Check if bot is actually polling (not just process alive)
    PID=$(systemctl show -p MainPID "$SERVICE" | cut -d= -f2)
    if [ "$PID" -gt 1 ] && [ -d "/proc/$PID" ]; then
        # Check DB readability via python (доступен всегда, без sqlite3-CLI)
        if python3 -c "import sqlite3,sys; sqlite3.connect('$DB').close()" 2>/dev/null; then
            echo "$(date): OK (PID $PID, DB ok)" >> "$LOG"
            exit 0
        fi
        echo "$(date): DB UNREADABLE - restarting" >> "$LOG"
        systemctl restart "$SERVICE"
        echo "$(date): restart issued (db)" >> "$LOG"
        exit 1
    fi
fi

echo "$(date): DEAD - restarting" >> "$LOG"
systemctl restart "$SERVICE"
echo "$(date): restart issued" >> "$LOG"
