# -*- coding: utf-8 -*-
"""Окно просмотра и единицы — одним правилом (итерация 2, решение владельца 18.11).

Проблема, которую закрывает файл:
 * список «кто взвесился» читал 999 дней, а карточка — 30, из-за чего у 24 из 27
   карточка отвечала «нет данных», хотя история в базе была;
 * `muscle_mass_kg` хранит проценты (историческое имя колонки), а `bone_mass_kg`
   в одном формате приезжает килограммами («Кости» → «3.2кг»), в другом — под
   заголовком «Кости(%)». Экран подписывал обоим «кг».

Правило: один каталог окон (BC_WINDOW_DAYS) + таблица спецификации метрик, где
для каждой метрики явно записано, откуда берутся единицы. Для костей единица
берётся из строки (колонка bone_unit, пишется парсером из заголовка файла);
если она не известна — число показывается без «кг» и с пометкой, что единица не
подтверждена. Придумывать значения нельзя: это медицинский экран несовершеннолетних.
"""
import asyncio
from collections import defaultdict
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from telegram.error import BadRequest

import bot as bot_module
from database import Database
from handlers_body_comp import BC_WINDOW_DAYS, format_bc_metrics
from scale_csv_parser import parse_scale_csv

TEAM = "Славянка ЧКПЗ-2"
DOCTOR_ID = 990001
CSV_PERCENT_BONES = (
    '"date","Вес","Масса жира(%)","Мышцы(%)","Кости(%)","BMR"\n'
    '"11/18/2026 10:00:00","66.7кг","26.3%","68.6%","4.1%","1432kcal"\n')
CSV_KG_BONES = (
    'Date,Вес,Масса жира(%),Мышцы(%),Кости,BMR\n'
    '"2026-11-18 10:00","66.7кг","26.3%","68.6%","4.1кг","1432kcal"\n')


class FakeQ:
    def __init__(self, uid, data):
        self.data = data
        self.from_user = SimpleNamespace(id=uid, first_name=None)
        self.answers = []
        self.edits = []

    async def answer(self, *a, **k):
        if self.answers:
            raise BadRequest("Query is already answered")
        self.answers.append(1)

    async def edit_message_text(self, text, **kw):
        self.edits.append(text)


def _bot(db, uid=DOCTOR_ID):
    obj = object.__new__(bot_module.SportHealthBot)
    obj.db = db
    obj.rate_limiter = defaultdict(list)
    obj.user_states = {}
    return obj


def _db_with_measurement(tmp_path, days_back, **fields):
    db = Database(db_path=str(tmp_path / "units.db"))
    db.register_athlete(700301, "u3", "Окно Тестовая", "U17", TEAM)
    aid = db.get_athlete_by_telegram_id(700301)["id"]
    row = {"weight_kg": 66.7, "body_fat_pct": 26.3, "muscle_mass_kg": 68.6,
           "body_water_pct": 54.0, "bmr_kcal": 1432}
    row.update(fields)
    db.save_body_composition(aid, (date.today() - timedelta(days=days_back)).isoformat(),
                             row, source="csv")
    return db, aid


# ---------- единицы: парсер различает кг и % ----------

def test_parser_records_bone_unit_percent_from_header():
    recs = parse_scale_csv(CSV_PERCENT_BONES, "data20261118100000.csv")
    assert recs
    assert recs[0].get("bone_unit") == "%", recs[0]


def test_parser_records_bone_unit_kg_from_header():
    recs = parse_scale_csv(CSV_KG_BONES, "healthdata.csv")
    assert recs
    assert recs[0].get("bone_unit") == "кг", recs[0]


def test_parser_marks_bone_unit_unknown_without_bone_column():
    recs = parse_scale_csv('"date","Вес"\n"11/18/2026 10:00:00","66.7кг"\n', "x.csv")
    assert recs
    assert recs[0].get("bone_unit") is None


# ---------- единицы: экран не врёт ----------

def test_metrics_show_muscle_as_percent():
    text = "\n".join(format_bc_metrics({"weight_kg": 66.7, "muscle_mass_kg": 68.6}))
    assert "Мышцы" in text and "68.6" in text and "%" in text
    assert "кг" not in text.split("Мышцы")[1].split("\n")[0]


def test_metrics_use_recorded_bone_unit():
    kg = "\n".join(format_bc_metrics({"bone_mass_kg": 4.1, "bone_unit": "кг"}))
    pct = "\n".join(format_bc_metrics({"bone_mass_kg": 4.1, "bone_unit": "%"}))
    assert "Кости" in kg and "кг" in kg
    assert "Кости" in pct and "%" in pct and "кг" not in pct


def test_metrics_do_not_claim_kg_for_bone_of_unknown_unit():
    lines = "\n".join(format_bc_metrics({"bone_mass_kg": 4.7}))
    assert "Кости" in lines and "4.7" in lines
    assert "кг" not in lines, lines
    assert "не подтверждена" in lines, lines


def test_metrics_skip_none_and_keep_order():
    """None не печатаем вообще, порядок — из спецификации (вес раньше жира)."""
    lines = format_bc_metrics({"weight_kg": 60.0, "bmi": None, "body_fat_pct": 21.0})
    assert len(lines) == 2, lines
    assert "Вес" in lines[0] and "Жир" in lines[1], lines


# ---------- окно: один каталог вместо четырёх ----------

def test_window_constant_is_long_enough_for_season():
    assert BC_WINDOW_DAYS >= 365


def test_card_shows_measurement_older_than_30_days(tmp_path, monkeypatch):
    """Раньше карточка читала 30 дней: замер 80-дневной давности = «нет данных»."""
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {DOCTOR_ID})
    db, aid = _db_with_measurement(tmp_path, 80, bone_mass_kg=4.1, bone_unit="кг")
    obj = _bot(db)
    q = FakeQ(DOCTOR_ID, "bc_view_%d" % aid)
    upd = SimpleNamespace(callback_query=q, effective_user=SimpleNamespace(id=DOCTOR_ID),
                          message=None)
    asyncio.run(obj.bc_view_athlete(upd, None))
    text = q.edits[-1] if q.edits else ""
    assert "нет данных" not in text, text[:120]
    assert "Состав тела" in text and "66.7" in text
    db.close()
