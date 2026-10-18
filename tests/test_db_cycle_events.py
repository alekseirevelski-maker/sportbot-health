# -*- coding: utf-8 -*-
"""cycle_events: события цикла, кэш якоря, menarche_status.

Из этих событий считаются день цикла и обучение длины (Task 3).
"""
from datetime import date

from database import Database


def _db(tmp_path):
    return Database(db_path=str(tmp_path / "cycle_events.db"))


def _athlete(db, tg=888001):
    db.register_athlete(tg, "girl", "Тест Тести", "U17", "ЧБК")
    return db.get_athlete_by_telegram_id(tg)["id"]


def test_add_start_event_sets_anchor(tmp_path):
    db = _db(tmp_path)
    aid = _athlete(db)
    assert db.add_cycle_event(aid, date(2026, 10, 1)) is True
    assert db.get_last_period_start(aid) == date(2026, 10, 1)


def test_double_tap_same_date_idempotent(tmp_path):
    """Review Focus №1: повторный тап «Да» — одна строка, не два «дня 1» подряд."""
    db = _db(tmp_path)
    aid = _athlete(db)
    db.add_cycle_event(aid, date(2026, 10, 1))
    assert db.add_cycle_event(aid, date(2026, 10, 1)) is True  # не ошибка
    n = db.conn.execute(
        "SELECT COUNT(*) FROM cycle_events WHERE athlete_id=? AND event_date='2026-10-01'",
        (aid,)).fetchone()[0]
    assert n == 1


def test_anchor_event_also_moves_cache(tmp_path):
    db = _db(tmp_path)
    aid = _athlete(db)
    db.add_cycle_event(aid, date(2026, 9, 20), type="anchor", source="one_time")
    assert db.get_last_period_start(aid) == date(2026, 9, 20)


def test_cache_never_moves_backwards(tmp_path):
    db = _db(tmp_path)
    aid = _athlete(db)
    db.add_cycle_event(aid, date(2026, 10, 10))
    db.add_cycle_event(aid, date(2026, 9, 1))  # задним числом — событие помним
    assert db.get_last_period_start(aid) == date(2026, 10, 10)
    ev = db.get_cycle_events(aid)
    assert ev == [date(2026, 9, 1), date(2026, 10, 10)]  # по возрастанию, оба


def test_get_cycle_events_types_filter(tmp_path):
    db = _db(tmp_path)
    aid = _athlete(db)
    db.add_cycle_event(aid, date(2026, 10, 1), type="start")
    db.add_cycle_event(aid, date(2026, 10, 15), type="anchor")
    assert db.get_cycle_events(aid, types=("start",)) == [date(2026, 10, 1)]
    assert db.get_cycle_events(aid) == [date(2026, 10, 1), date(2026, 10, 15)]


def test_menarche_status_roundtrip(tmp_path):
    db = _db(tmp_path)
    aid = _athlete(db)
    assert db.set_menarche_status(aid, "pre") is True
    a = db.get_athlete_by_telegram_id(888001)
    assert a["menarche_status"] == "pre"
    db.set_menarche_status(aid, None)
    assert db.get_athlete_by_telegram_id(888001)["menarche_status"] is None
