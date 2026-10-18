# -*- coding: utf-8 -*-
"""cycle_events: события цикла, кэш якоря, menarche_status.

Из этих событий считаются день цикла и обучение длины (Task 3).
"""
from datetime import date, timedelta

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


# ---------- ревю «брата» 2026-10-18: блокеры/риски ----------

def test_future_event_rejected(tmp_path):
    """Блокер: будущая дата не должна задвигать кэш в будущее (иначе cycle_day
    бросает ValueError и валит весь отчёт по девушкам)."""
    from datetime import date as _d
    db = _db(tmp_path)
    aid = _athlete(db)
    assert db.add_cycle_event(aid, _d.today() + timedelta(days=3)) is False
    assert db.get_last_period_start(aid) is None


def test_correct_anchor_removes_phantoms(tmp_path):
    """Блокер: коррекция задним числом — истина в новой дате.
    Тап «Да» 18.10 (ошибочный), коррекция на 15.10: кэш=15.10, призрака 18.10 нет."""
    db = _db(tmp_path)
    aid = _athlete(db)
    db.add_cycle_event(aid, date(2026, 10, 18))          # ложный тап
    db.add_cycle_event(aid, date(2026, 9, 12))           # реальный предыдущий старт
    assert db.correct_cycle_anchor(aid, date(2026, 10, 15)) is True
    assert db.get_last_period_start(aid) == date(2026, 10, 15)
    assert db.get_cycle_events(aid) == [date(2026, 9, 12), date(2026, 10, 15)]


def test_correct_anchor_future_rejected(tmp_path):
    db = _db(tmp_path)
    aid = _athlete(db)
    db.add_cycle_event(aid, date(2026, 10, 10))
    from datetime import date as _d
    assert db.correct_cycle_anchor(aid, _d.today() + timedelta(days=1)) is False
    assert db.get_last_period_start(aid) == date(2026, 10, 10)  # как было


def test_start_supersedes_same_day_anchor(tmp_path):
    """Риск: якорь «Сегодня» + тап «Да» в тот же день — одно событие, не лесенка-дубль."""
    db = _db(tmp_path)
    aid = _athlete(db)
    db.add_cycle_event(aid, date(2026, 10, 10), type="anchor")
    db.add_cycle_event(aid, date(2026, 10, 10), type="start")   # пересиливает
    assert db.get_cycle_events(aid) == [date(2026, 10, 10)]
    db.add_cycle_event(aid, date(2026, 10, 10), type="anchor")  # обратный порядок — игнор
    assert db.get_cycle_events(aid) == [date(2026, 10, 10)]


def test_get_cycle_events_empty_types_guard(tmp_path):
    """Риск: types=() не должен ронять SQL IN ()."""
    db = _db(tmp_path)
    aid = _athlete(db)
    db.add_cycle_event(aid, date(2026, 10, 1))
    assert db.get_cycle_events(aid, types=()) == []


def test_length_learned_flag(tmp_path):
    """Мелочь №5: у длины должен быть источник — флаг после обучения, 0 до/при ручной установке."""
    db = _db(tmp_path)
    aid = _athlete(db)
    assert db.get_athlete_by_telegram_id(888001)["cycle_length_learned"] == 0
    for d in (date(2026, 5, 1), date(2026, 5, 27), date(2026, 6, 26)):
        db.add_cycle_event(aid, d)
    assert db.update_cycle_length_from_history(aid) == 28
    assert db.get_athlete_by_telegram_id(888001)["cycle_length_learned"] == 1
