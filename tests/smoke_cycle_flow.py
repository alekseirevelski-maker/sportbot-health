# -*- coding: utf-8 -*-
"""SMOKE цикла — руками: python tests\\smoke_cycle_flow.py (не pytest-тест).

Прогоняет FLOW-модель на tmp-БД без Telegram: якорь → дни растут → «Да» →
обучение длины → задержка → алерт врачу → escape-статусы.
"""
import os
import sys
import tempfile
from datetime import date, timedelta

if hasattr(sys.stdout, "reconfigure"):  # Windows cp1251 не варит эмодзи
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cycle_engine import cycle_step_for, status  # noqa: E402
from cycle_ui import (  # noqa: E402
    apply_anchor, cycle_alert_lines, daily_cycle_keyboard, parse_date_ru,
    status_line, survey_cycle_values,
)
from database import Database  # noqa: E402

today = date.today()
db = Database(db_path=os.path.join(tempfile.mkdtemp(), "smoke.db"))
db.register_athlete(900001, "smoke", "Дым Дымыч", "U17", "ЧБК")
a = db.get_athlete_by_telegram_id(900001)
aid = a["id"]
db.update_athlete_gender(aid, "female", 28)

print("0) без якоря шаг:", cycle_step_for("female", db.get_last_period_start(aid), None))

# одноразовый якорь: «начались 9 дней назад»
anchor = today - timedelta(days=9)
res = apply_anchor(db, aid, anchor, today, 28, source="one_time_question")
print(f"1) якорь {anchor:%d.%m}: день {res['cycle_day']} · {res['cycle_phase']}")
print("   клавиатура дня:", daily_cycle_keyboard(status(anchor, 28, today)))

# 3 дня «Нет» — счётчики растут, обнуления нет
for i in range(1, 4):
    d = today + timedelta(days=i)
    v = survey_cycle_values(anchor, 28, d)
    print(f"2) +{i} дн.: день {v['cycle_day']} ({v['cycle_phase']})")

# «Да, начались» сегодня = новое событие; длина учитывается после 3-х событий
db.add_cycle_event(aid, today, type="start", source="survey_button")
db.add_cycle_event(aid, today - timedelta(days=26) - timedelta(days=30), type="start")
db.add_cycle_event(aid, today - timedelta(days=26), type="start")
learned = db.update_cycle_length_from_history(aid)
print(f"3) после трёх стартов обученная длина: {learned}")

# задержка: сдигаем «сегодня» на +36 от последнего якоря
last = max(db.get_cycle_events(aid))
late = last + timedelta(days=36)
print(f"4) через 36 дн. статус: {status(last, 28, late)!r} | строка: {status_line(last, 28, late)}")
girl = dict(db.get_athlete_by_telegram_id(900001), last_period_start=last)
for _key, line in cycle_alert_lines([girl], late, {}):
    print("   алерт:", line)

# парсер и escape-статусы
print(f"5) parse '5.10': {parse_date_ru('5.10', today)} | 'abc': {parse_date_ru('abc', today)}")
db.set_menarche_status(aid, "pre")
print("   после 'pre' шаг:", cycle_step_for("female", None, "pre"))
db.correct_cycle_anchor(aid, today - timedelta(days=2))
print("   коррекция задним числом: якорь =", db.get_last_period_start(aid),
      "| шаг =", cycle_step_for("female", db.get_last_period_start(aid),
                                 db.get_athlete_by_telegram_id(900001)["menarche_status"]))
print("SMOKE OK")
