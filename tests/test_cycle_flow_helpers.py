# -*- coding: utf-8 -*-
"""Оркестрация якоря и вычисляемых значений опроса (Task 5, чистая часть).

Handler'ы Telegram — тонкие обёртки над apply_anchor/survey_cycle_values,
всё решение «корректировать или записать» живёт здесь и тестится на Database.
"""
from datetime import date, timedelta

from cycle_ui import apply_anchor, survey_cycle_values
from database import Database

TODAY = date(2026, 10, 18)


def _girl(tmp_path):
    db = Database(db_path=str(tmp_path / "flow.db"))
    db.register_athlete(555001, "g", "Соня Соня", "U17", "ЧБК")
    a = db.get_athlete_by_telegram_id(555001)
    db.update_athlete_gender(a["id"], "female", 28)
    return db, a["id"]


def test_apply_anchor_first_time(tmp_path):
    db, aid = _girl(tmp_path)
    res = apply_anchor(db, aid, date(2026, 10, 5), TODAY)
    assert res is not None
    assert res["start"] == date(2026, 10, 5)
    assert res["corrected"] is False
    assert res["cycle_day"] == 14
    assert db.get_last_period_start(aid) == date(2026, 10, 5)


def test_apply_anchor_retro_corrects_phantom(tmp_path):
    """Мистап «Да» 18.10, потом ввела 15.10 — день считается от 15.10, призрак стёрт."""
    db, aid = _girl(tmp_path)
    db.add_cycle_event(aid, TODAY)                       # ложный тап «Да»
    res = apply_anchor(db, aid, date(2026, 10, 15), TODAY)
    assert res["corrected"] is True
    assert res["cycle_day"] == 4
    assert res["start"] == date(2026, 10, 15)
    assert db.get_cycle_events(aid) == [date(2026, 10, 15)]
    assert db.get_last_period_start(aid) == date(2026, 10, 15)


def test_apply_anchor_double_tap_idempotent(tmp_path):
    db, aid = _girl(tmp_path)
    r1 = apply_anchor(db, aid, date(2026, 10, 5), TODAY)
    r2 = apply_anchor(db, aid, date(2026, 10, 5), TODAY)
    assert r1 and r2 and r2["cycle_day"] == r1["cycle_day"]
    assert len(db.get_cycle_events(aid)) == 1


def test_apply_anchor_rejects_future(tmp_path):
    db, aid = _girl(tmp_path)
    assert apply_anchor(db, aid, TODAY + timedelta(days=2), TODAY) is None
    assert db.get_last_period_start(aid) is None


def test_apply_anchor_learns_length(tmp_path):
    """Три якоря с интервалами 26/30 → обучение даёт 28, возвращается learned."""
    db, aid = _girl(tmp_path)
    apply_anchor(db, aid, date(2026, 5, 1), TODAY)
    apply_anchor(db, aid, date(2026, 5, 27), TODAY)
    res = apply_anchor(db, aid, date(2026, 6, 26), TODAY)
    assert res["learned"] == 28
    assert db.get_athlete_by_telegram_id(555001)["cycle_length_learned"] == 1


def test_survey_cycle_values_normal_and_beyond_length(tmp_path):
    db, aid = _girl(tmp_path)
    v = survey_cycle_values(date(2026, 10, 5), 28, TODAY)   # день 14
    assert v["cycle_day"] == 14 and v["cycle_length"] == 28
    assert v["cycle_phase"]                                  # любое валидное имя фазы
    v2 = survey_cycle_values(date(2026, 9, 1), 28, TODAY)   # день 48 > длины
    assert v2["cycle_day"] == 48
    assert v2["cycle_phase"] == "Лютеиновая (задержка)"


def test_manual_length_resets_learned_flag(tmp_path):
    db, aid = _girl(tmp_path)
    for d in (date(2026, 5, 1), date(2026, 5, 27), date(2026, 6, 26)):
        db.add_cycle_event(aid, d)
    assert db.update_cycle_length_from_history(aid) == 28
    assert db.get_athlete_by_telegram_id(555001)["cycle_length_learned"] == 1
    db.update_athlete_gender(aid, "female", 24)  # девушка поправила длину вручную
    assert db.get_athlete_by_telegram_id(555001)["cycle_length_learned"] == 0
