# -*- coding: utf-8 -*-
"""Расчёт серии опросов (survey_streak) в Database.save_survey.

Баг (репорт Иванина Макара): серия замирала на 60 днях — пересчёт брал
только последние 60 дат (`LIMIT 60`). Тесты ниже не проходят на старом коде.
"""
from datetime import date, timedelta

from database import Database


def _make_db(tmp_path):
    return Database(db_path=str(tmp_path / "test_streak.db"))


def _seed_consecutive_days(db, athlete_id, days_back_start, days_back_end):
    """Вставить записи за период [today-days_back_start .. today-days_back_end]."""
    d = date.today() - timedelta(days=days_back_start)
    stop = date.today() - timedelta(days=days_back_end)
    while d <= stop:
        db.conn.execute(
            "INSERT INTO daily_wellness (athlete_id, survey_date) VALUES (?, ?)",
            (athlete_id, d.isoformat()),
        )
        d += timedelta(days=1)
    db.conn.commit()


def _athlete_id(db, tg_id=777001):
    db.register_athlete(tg_id, "makar", "Иванин Макар", "U17", "ЧБК")
    return db.get_athlete_by_telegram_id(tg_id)["id"]


def test_streak_can_exceed_60_days(tmp_path):
    db = _make_db(tmp_path)
    aid = _athlete_id(db)
    # 69 пропущенных назад подряд + сегодняшний опрос = 70 дней
    _seed_consecutive_days(db, aid, days_back_start=69, days_back_end=1)

    assert db.save_survey(aid, {"sleep": 5, "stress": 3, "fatigue": 3}) is True

    athlete = db.get_athlete_by_telegram_id(777001)
    assert athlete["survey_streak"] == 70, (
        "серия ограничена 60 — пересчёт берёт только 60 последних дат"
    )


def test_streak_100_days(tmp_path):
    db = _make_db(tmp_path)
    aid = _athlete_id(db, tg_id=777002)
    _seed_consecutive_days(db, aid, days_back_start=99, days_back_end=1)

    db.save_survey(aid, {"sleep": 5})
    assert db.get_athlete_by_telegram_id(777002)["survey_streak"] == 100


def test_streak_resets_after_missed_day(tmp_path):
    """Регрессия: пропуск подрядности по-прежнему обнуляет серию."""
    db = _make_db(tmp_path)
    aid = _athlete_id(db, tg_id=777003)
    _seed_consecutive_days(db, aid, days_back_start=30, days_back_end=2)  # вчера пропуск

    db.save_survey(aid, {"sleep": 5})
    assert db.get_athlete_by_telegram_id(777003)["survey_streak"] == 1


def test_streak_normal_short_run_unchanged(tmp_path):
    """Регрессия: обычные короткие серии считаются как раньше."""
    db = _make_db(tmp_path)
    aid = _athlete_id(db, tg_id=777004)
    _seed_consecutive_days(db, aid, days_back_start=5, days_back_end=1)

    db.save_survey(aid, {"sleep": 5})
    assert db.get_athlete_by_telegram_id(777004)["survey_streak"] == 6
