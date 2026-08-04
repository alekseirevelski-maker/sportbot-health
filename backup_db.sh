#!/bin/bash
BACKUP_DIR="/opt/telegram-bots/sport-health/backups"
mkdir -p "$BACKUP_DIR"
cp /opt/telegram-bots/sport-health/data/sport_health.db "$BACKUP_DIR/sport_health_$(date +%Y%m%d_%H%M%S).db"
find "$BACKUP_DIR" -name "*.db" -mtime +30 -delete
echo "$(date): backup done" >> /opt/telegram-bots/sport-health/backup.log
