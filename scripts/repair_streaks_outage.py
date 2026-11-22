#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Пересчёт серий опросов после сбоя сервера (инцидент 21.11.2026).

Почему нужен скрипт, а не миграция: это правка ДАННЫХ, а не схемы. Правила
docs/DEPLOY.md (6–7): dry-run по умолчанию, --live только после свежей
вне-серверной копии БД. Повторный запуск ничего не меняет (идемпотентен).

Что делает: добавляет дни сбоя в реестр bot_settings.outage_dates (если
передан --outage) и пересчитывает survey_streak всех атлетов по правилу
«день сбоя перешагивается, но не прибавляется» (database.compute_streak).
Якорь — последняя дата опроса атлета. Меняет ТОЛЬКО survey_streak;
total_surveys/last_active и сами опросы не трогает. Обычные пропуски
(не сбой) не «лечит».

Запуск на сервере (из /opt/telegram-bots/sport-health):
    python3 scripts/repair_streaks_outage.py --outage 2026-11-21          # dry-run
    python3 scripts/repair_streaks_outage.py --outage 2026-11-21 --live   # применить
"""
import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database import Database  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Пересчёт survey_streak с учётом дней сбоя сервера")
    ap.add_argument("db", nargs="?", default=None,
                    help="путь к БД (по умолчанию data/sport_health.db из config)")
    ap.add_argument("--outage", action="append", default=[], metavar="DATE",
                    help="день сбоя в формате YYYY-MM-DD (можно несколько)")
    ap.add_argument("--live", action="store_true",
                    help="применить изменения (по умолчанию dry-run)")
    args = ap.parse_args()

    try:
        new_days = {date.fromisoformat(s) for s in args.outage}
    except ValueError as e:
        raise SystemExit(f"--outage: ожидался YYYY-MM-DD ({e})")

    db = Database(db_path=args.db) if args.db else Database()
    registry = db.get_outage_dates()
    merged = registry | new_days

    print("Реестр дней сбоя сейчас: %s" % (", ".join(sorted(d.isoformat() for d in registry)) or "пуст"))
    if new_days - registry:
        print("Добавим в реестр: %s" % ", ".join(sorted(d.isoformat() for d in new_days - registry)))

    changes = db.recalc_streaks(dry_run=True, outage_dates=merged)
    print("\nИзменения survey_streak (old → new): %d атлетов" % len(changes))
    for c in sorted(changes, key=lambda c: c["id"]):
        print("  #%-4s %-30s %s → %s" % (c["id"], c["full_name"], c["old"], c["new"]))
    if not changes:
        print("  (нет — все серии уже корректны)")

    if not args.live:
        print("\nDRY-RUN: ничего не записано. Для применения: --live "
              "(только после вне-серверной копии БД, правило 7 docs/DEPLOY.md)")
        return

    if new_days - registry:
        if not db.set_outage_dates(merged):
            raise SystemExit("Не удалось записать реестр outage_dates — пересчёт отменён")
    applied = db.recalc_streaks(dry_run=False, outage_dates=merged)
    print("\nLIVE: реестр обновлён, применено изменений: %d" % len(applied))
    integrity = db.conn.execute("PRAGMA integrity_check").fetchone()[0]
    print("integrity_check: %s" % integrity)
    if integrity != "ok":
        raise SystemExit("integrity_check не ok — проверить БД!")
    again = db.recalc_streaks(dry_run=True)
    print("Повторный dry-run: %d изменений (ожидалось 0)" % len(again))


if __name__ == "__main__":
    main()