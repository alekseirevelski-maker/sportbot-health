# -*- coding: utf-8 -*-
"""Изоляция данных спортсменов в источниках недельного отчёта.

Карточка обязана показывать данные ИМЕННО того спортсмена, для которого
рендерится: средние, тренды и жалобы не должны подмешивать чужие строки.
Проверяем сырые INSERT'ы двух атлетов с разными значениями на одни даты.
"""
from datetime import date, timedelta

from database import Database

TODAY = date.today()
YESTERDAY = (TODAY - timedelta(days=1)).isoformat()


def _db_with_two(tmp_path):
    db = Database(db_path=str(tmp_path / "iso.db"))
    db.register_athlete(910001, "iso1", "Первая Изоляция", "U17", "ЧБК")
    db.register_athlete(910002, "iso2", "Вторая Изоляция", "U17", "ЧБК")
    a1 = db.get_athlete_by_telegram_id(910001)["id"]
    a2 = db.get_athlete_by_telegram_id(910002)["id"]
    for aid, sleep, hr, nrs, loc in ((a1, 7, 55, 0, ""), (a2, 4, 80, 5, "колено")):
        db.conn.execute(
            "INSERT INTO daily_wellness (athlete_id, survey_date, sleep_score, "
            "resting_hr, readiness, pain_nrs, pain_location) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (aid, YESTERDAY, sleep, hr, sleep, nrs, loc))
    db.conn.commit()
    return db, a1, a2


def test_stats_isolated_per_athlete(tmp_path):
    db, a1, a2 = _db_with_two(tmp_path)
    s1, s2 = db.get_athlete_stats(a1, 7), db.get_athlete_stats(a2, 7)
    assert s1["avg_sleep"] == 7.0 and s2["avg_sleep"] == 4.0
    assert s1["avg_hr"] == 55.0 and s2["avg_hr"] == 80.0
    assert s1["total_days"] == 1 and s2["total_days"] == 1


def test_trend_isolated_per_athlete(tmp_path):
    db, a1, a2 = _db_with_two(tmp_path)
    t1 = db.get_trend_data(a1, "sleep", 7)
    t2 = db.get_trend_data(a2, "sleep", 7)
    assert t1 == [(YESTERDAY, 7)]
    assert t2 == [(YESTERDAY, 4)]


def test_pain_isolated_per_athlete(tmp_path):
    db, a1, a2 = _db_with_two(tmp_path)
    assert db.get_week_pain_summary(a1, 7) == []
    p2 = db.get_week_pain_summary(a2, 7)
    assert len(p2) == 1 and p2[0]["pain_location"] == "колено"


def test_prev_window_isolated_per_athlete(tmp_path):
    db, a1, a2 = _db_with_two(tmp_path)
    old = (TODAY - timedelta(days=9)).isoformat()
    db.conn.execute(
        "INSERT INTO daily_wellness (athlete_id, survey_date, sleep_score) VALUES (?, ?, ?)",
        (a2, old, 2))
    db.conn.commit()
    assert db.get_athlete_stats_window(a1, 7, 7)["total_days"] == 0
    assert db.get_athlete_stats_window(a2, 7, 7)["avg_sleep"] == 2.0