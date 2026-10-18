# -*- coding: utf-8 -*-
"""Обучение длины цикла: источник — события cycle_events, не baskets из daily_wellness.

Правила прежние: минимум 3 события (2 интервала), окно 15-45 дн., медиана,
запись только если медиана в норме 21-35.
"""
from datetime import date, timedelta

from cycle_engine import cycle_day, status
from database import Database


def _girl(tmp_path):
    db = Database(db_path=str(tmp_path / "len.db"))
    db.register_athlete(777001, "g", "Лена Лена", "U17", "ЧБК")
    a = db.get_athlete_by_telegram_id(777001)
    db.update_athlete_gender(a["id"], "female", 25)  # prior длины = 25
    return db, a["id"]


def _events(db, aid, gaps, first=date(2026, 5, 1)):
    """События: первое на first, дальше по цепочке интервалов gaps."""
    d = first
    db.add_cycle_event(aid, d, type="start", source="test")
    for g in gaps:
        d = d + timedelta(days=g)
        db.add_cycle_event(aid, d, type="start", source="test")
    return d


def test_median_two_intervals_writes(tmp_path):
    """3 события с интервалами 26 и 30 → медиана 28 → cycle_length_default 25→28."""
    db, aid = _girl(tmp_path)
    _events(db, aid, [26, 30])
    assert db.update_cycle_length_from_history(aid) == 28
    assert db.get_athlete_by_telegram_id(777001)["cycle_length_default"] == 28


def test_two_events_not_enough(tmp_path):
    db, aid = _girl(tmp_path)
    _events(db, aid, [28])
    assert db.update_cycle_length_from_history(aid) is None
    assert db.get_athlete_by_telegram_id(777001)["cycle_length_default"] == 25


def test_gap_outside_window_filtered(tmp_path):
    """Интервал 10 дн. отсекается окном 15-45; остаются 26 и 30 → медиана 28."""
    db, aid = _girl(tmp_path)
    _events(db, aid, [26, 10, 30])
    assert db.update_cycle_length_from_history(aid) == 28


def test_median_out_of_norm_not_written(tmp_path):
    """Медиана 40 (окно пропускает до 45) вне нормы 21-35 → None, prior цел."""
    db, aid = _girl(tmp_path)
    _events(db, aid, [40, 40])
    assert db.update_cycle_length_from_history(aid) is None
    assert db.get_athlete_by_telegram_id(777001)["cycle_length_default"] == 25


def test_old_basket_source_no_longer_used(tmp_path):
    """Старый источник (daily_wellness cycle_day=1) больше не читается: пусто без событий."""
    db, aid = _girl(tmp_path)
    today = date.today().isoformat()
    for i in range(3):  # три «дня 1» в старой таблице — раньше этого хватало
        d = (date.today() - timedelta(days=30 * (i + 1))).isoformat()
        db.conn.execute(
            "INSERT INTO daily_wellness (athlete_id, survey_date, cycle_day) VALUES (?, ?, 1)",
            (aid, d))
    db.conn.commit()
    assert db.update_cycle_length_from_history(aid) is None


def test_review_focus_5_length_change_does_not_jump_day_or_false_delay(tmp_path):
    """RF№5: после обучения медианы=32 день считается от якоря (не прыгает),
    и ложной задержки нет: на 33-й день при новой длине 32 задержка 1 → 'ok',
    тогда как со старым prior 25 тот же день дал бы ложную 'delay'."""
    db, aid = _girl(tmp_path)
    start = date(2026, 6, 1)
    _events(db, aid, [30, 34], first=start)  # медиана [30,34] = 32
    assert db.update_cycle_length_from_history(aid) == 32
    day40 = start + timedelta(days=40)
    assert cycle_day(start, day40) == 41  # день зависит только от якоря, не от длины
    d33 = start + timedelta(days=33)
    assert status(start, 32, d33) == "ok"      # delay = 1 ≤ 7
    assert status(start, 25, d33) == "delay"   # старый prior дал бы ложную тревогу
