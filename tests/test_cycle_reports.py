# -*- coding: utf-8 -*-
"""Цикл в отчётах: строка спортсменки + алерт-строки врачу с дедупом 7 дней."""
from datetime import date, timedelta

from cycle_ui import athlete_cycle_line, cycle_alert_lines

TODAY = date(2026, 10, 18)


def _girl(aid=1, start="2026-10-05", length=28, gender="female", **kw):
    a = {"id": aid, "full_name": f"Тест Тест{aid}", "team": "U17",
         "gender": gender, "last_period_start": start,
         "cycle_length_default": length, "cycle_length_learned": 0}
    a.update(kw)
    return a


# ---------- athlete_cycle_line ----------

def test_athlete_line_computes_from_iso_string():
    line = athlete_cycle_line(_girl(), TODAY)
    assert line and "День 14" in line and "~02.11" in line


def test_athlete_line_none_without_start():
    assert athlete_cycle_line(_girl(start=None), TODAY) is None
    assert athlete_cycle_line(_girl(gender="male"), TODAY) is None


def test_athlete_line_delay_marked():
    line = athlete_cycle_line(_girl(start="2026-09-01"), TODAY)  # задержка 19
    assert "⚠️ задержка 19 дн." in line


# ---------- cycle_alert_lines ----------

def test_alert_delay_line():
    rows = cycle_alert_lines([_girl(start="2026-09-01")], TODAY, {})
    assert len(rows) == 1
    key, line = rows[0]
    assert key == "cycle_alert_1_delay"
    assert "задержка 19 дн." in line and "Тест" in line


def test_alert_amenorrhea_not_doubled():
    rows = cycle_alert_lines([_girl(start="2026-07-01", aid=2)], TODAY, {})
    assert len(rows) == 1                      # аменорея перекрывает задержку
    key, line = rows[0]
    assert key == "cycle_alert_2_amenorrhea"
    assert "🚫" in line and "109" in line


def test_alert_suppressed_within_seven_days():
    rows = cycle_alert_lines([_girl(start="2026-09-01")], TODAY,
                             {"cycle_alert_1_delay": "2026-10-17"})
    assert rows == []


def test_alert_resend_after_seven_days():
    rows = cycle_alert_lines([_girl(start="2026-09-01")], TODAY,
                             {"cycle_alert_1_delay": (TODAY - timedelta(days=7)).isoformat()})
    assert len(rows) == 1                       # ровно 7 дней → можно снова


def test_alert_skips_no_anchor_and_men():
    rows = cycle_alert_lines([_girl(start=None), _girl(aid=3, gender="male",
                                                        start="2026-09-01")], TODAY, {})
    assert rows == []
