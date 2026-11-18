# -*- coding: utf-8 -*-
"""Excel-отчёт «последний замер каждого» — режим без фильтра по дате.

Зачем: регулярного графика взвешиваний нет (решение владельца 18.11, динамика
смотрим по запросу). Кнопка «Последний замер» вёл на get_bc_period(days=1), то
есть «за последние сутки» — при замере месяц назад отчёт приходил пустым
(«❌ Нет данных состава тела за выбранный период»), хотя данные в базе есть.
"""
from datetime import date, timedelta

import pytest

from database import Database
from handlers_reports import bc_report_rows

TEAM_A = "ЧБК-Челябинская область"
TEAM_B = "Славянка ЧКПЗ-2"


def _seed(tmp_path):
    db = Database(db_path=str(tmp_path / "bc_report.db"))
    db.register_athlete(800001, "a1", "Давний Тестовый", "U18", TEAM_A)
    db.register_athlete(800002, "a2", "Свежая Тестовая", "U17", TEAM_B)
    a = db.get_athlete_by_telegram_id(800001)["id"]
    b = db.get_athlete_by_telegram_id(800002)["id"]
    old = (date.today() - timedelta(days=400)).isoformat()
    mid = (date.today() - timedelta(days=40)).isoformat()
    fresh = date.today().isoformat()
    db.save_body_composition(a, old, {"weight_kg": 81.0, "body_fat_pct": 12.0}, source="csv")
    db.save_body_composition(a, mid, {"weight_kg": 82.0, "body_fat_pct": 12.5}, source="csv")
    db.save_body_composition(b, fresh, {"weight_kg": 60.0, "body_fat_pct": 20.0}, source="csv")
    db.set_coach_teams(800003, [TEAM_A])
    db.register_athlete(800003, "coach", "Тренер Тестовый", "U18", TEAM_A)
    return db, a, b


def test_get_bc_latest_all_returns_one_row_per_athlete_at_any_age(tmp_path):
    db, a, b = _seed(tmp_path)
    rows = db.get_bc_latest_all()
    by_athlete = {}
    for r in rows:
        by_athlete.setdefault(r["athlete_id"], []).append(r)

    assert set(by_athlete) == {a, b}, by_athlete.keys()
    assert all(len(v) == 1 for v in by_athlete.values()), "должна быть одна строка на спортсмена"
    assert by_athlete[a][0]["record_date"] == (date.today() - timedelta(days=40)).isoformat()
    assert by_athlete[a][0]["weight_kg"] == pytest.approx(82.0)
    db.close()


def test_get_bc_latest_all_hides_coach_profiles_and_filters_team(tmp_path):
    db, a, b = _seed(tmp_path)
    teams = {r["team"] for r in db.get_bc_latest_all()}
    names = {r["full_name"] for r in db.get_bc_latest_all()}
    assert "Тренер Тестовый" not in names, names
    assert teams == {TEAM_A, TEAM_B}
    only_b = db.get_bc_latest_all(team=TEAM_B)
    assert [r["team"] for r in only_b] == [TEAM_B], only_b
    db.close()


def test_report_source_switches_on_zero_days(tmp_path):
    db, a, b = _seed(tmp_path)

    latest = bc_report_rows(db, 0, None)
    assert len(latest) == 2, latest           # по последнему замеру каждого
    assert {r["weight_kg"] for r in latest} == {82.0, 60.0}

    last_day = bc_report_rows(db, 1, None)
    assert [r["weight_kg"] for r in last_day] == [60.0], "фильтр «за сутки» сломался"

    month = bc_report_rows(db, 90, None)
    assert len(month) == 2, month
    db.close()
