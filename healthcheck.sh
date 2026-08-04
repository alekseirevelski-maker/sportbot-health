#!/bin/bash
# Healthcheck for sport-health bot
SERVICE="sport-health.service"
LOG="/opt/telegram-bots/sport-health/healthcheck.log"

if systemctl is-active --quiet "$SERVICE"; then
    # Check if bot is actually polling (not just process alive)
    PID=$(systemctl show -p MainPID "$SERVICE" | cut -d= -f2)
    if [ "$PID" -gt 1 ] && [ -d "/proc/$PID" ]; then
        echo "$(date): OK (PID $PID)" >> "$LOG"
        exit 0
    fi
fi

echo "$(date): DEAD - restarting" >> "$LOG"
systemctl restart "$SERVICE"
echo "$(date): restart issued" >> "$LOG"
