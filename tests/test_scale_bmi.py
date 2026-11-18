# -*- coding: utf-8 -*-
"""ИМТ там, где файл весов его не отдал, — с честным указанием источника.

Контекст: в healthdata.csv колонка «ИМТ» есть, но до 18.11 терялась парсером; в
исторических 54 строках bmi остался NULL. Решение владельца (18.11): ИМТ досчитать
из веса и роста. Обязательное условие — признак происхождения, чтобы расчётный
ИМТ не выглядел как измеренный весами: колонка bmi_source ('file' | 'computed').
"""
import asyncio
from collections import defaultdict
from types import SimpleNamespace

import pytest

import bot as bot_module
from database import Database
from scale_csv_parser import parse_scale_csv

TEAM = "Славянка ЧКПЗ-2"
DOCTOR_ID = 990001
ATHLETE_TG = 700601

CSV_WITH_BMI = ('Date,ИМТ,Вес,Безж. мас. Тела,Масса жира(%),Вода в организме(%),'
                'Мышцы(%),Кости,BMR,AMR\n'
                '"2026-11-18 10:00",23.0,70.45кг,50.7кг,28.0%,52.7%,66.9%,2.9кг,1466kcal,2785kcal\n')
CSV_WITHOUT_BMI = ('Date,Вес,Масса жира(%),Мышцы(%),Кости,BMR\n'
                   '"2026-11-18 10:00",70.45кг,28.0%,66.9%,2.9кг,1466kcal\n')


def _db(tmp_path, height=None):
    db = Database(db_path=str(tmp_path / "bmi.db"))
    db.register_athlete(ATHLETE_TG, "g", "Имт Тестовая", "U17", TEAM)
    aid = db.get_athlete_by_telegram_id(ATHLETE_TG)["id"]
    if height:
        db.save_questionnaire(aid, {"height": height, "weight": 70})
    return db, aid


def _bot(db):
    obj = object.__new__(bot_module.SportHealthBot)
    obj.db = db
    obj.rate_limiter = defaultdict(list)
    obj.user_states = {}
    return obj


def _doc_update(uid):
    sent = []

    async def reply(t, **kw):
        sent.append(t)

    return (SimpleNamespace(callback_query=None, effective_user=SimpleNamespace(id=uid),
                            message=SimpleNamespace(text="", reply_text=reply)), sent)


# ---------- парсер помечает источник ----------

def test_parser_marks_bmi_from_file():
    recs = parse_scale_csv(CSV_WITH_BMI, "healthdata.csv")
    assert recs[0]["bmi"] == pytest.approx(23.0)
    assert recs[0].get("bmi_source") == "file", recs[0]


def test_parser_without_bmi_column_leaves_bmi_empty():
    recs = parse_scale_csv(CSV_WITHOUT_BMI, "garlyn.csv")
    assert recs
    assert recs[0].get("bmi") is None


# ---------- импорт доcчитывает ИМТ и пишет источник ----------

def test_import_computes_bmi_when_file_has_none(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {DOCTOR_ID})
    db, aid = _db(tmp_path)
    obj = _bot(db)
    recs = parse_scale_csv(CSV_WITHOUT_BMI, "garlyn.csv")
    recs[0]["height_cm"] = 175          # рост из файла/анкеты — обычное дело
    upd, _ = _doc_update(DOCTOR_ID)

    saved, *_rest = asyncio.run(obj._import_scale_records(upd, None, recs,
                                                          forced_athlete_id=aid))
    row = db.get_body_composition(aid, days=999)[0]
    assert saved == 1
    assert row["bmi"] == pytest.approx(23.0, abs=0.1), row
    assert row["bmi_source"] == "computed", row
    db.close()


def test_import_keeps_file_bmi_and_marks_it(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {DOCTOR_ID})
    db, aid = _db(tmp_path)
    obj = _bot(db)
    recs = parse_scale_csv(CSV_WITH_BMI, "healthdata.csv")
    upd, _ = _doc_update(DOCTOR_ID)

    asyncio.run(obj._import_scale_records(upd, None, recs, forced_athlete_id=aid))
    row = db.get_body_composition(aid, days=999)[0]
    assert row["bmi"] == pytest.approx(23.0)
    assert row["bmi_source"] == "file", row
    db.close()


def test_import_without_any_height_does_not_invent_bmi(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {DOCTOR_ID})
    db, aid = _db(tmp_path)          # анкеты нет → роста нет
    obj = _bot(db)
    recs = parse_scale_csv(CSV_WITHOUT_BMI, "garlyn.csv")
    upd, _ = _doc_update(DOCTOR_ID)

    asyncio.run(obj._import_scale_records(upd, None, recs, forced_athlete_id=aid))
    row = db.get_body_composition(aid, days=999)[0]
    assert row["bmi"] is None and row["height_cm"] is None, row
    db.close()


def test_anquet_height_is_used_for_bmi(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {DOCTOR_ID})
    db, aid = _db(tmp_path, height=175)
    obj = _bot(db)
    recs = parse_scale_csv(CSV_WITHOUT_BMI, "garlyn.csv")
    upd, _ = _doc_update(DOCTOR_ID)

    asyncio.run(obj._import_scale_records(upd, None, recs, forced_athlete_id=aid))
    row = db.get_body_composition(aid, days=999)[0]
    assert row["height_cm"] == 175, row
    assert row["bmi"] == pytest.approx(23.0, abs=0.1), row
    assert row["bmi_source"] == "computed", row
    db.close()


# ---------- скрипт бэкфилла: dry-run по умолчанию, идемпотентность ----------

def test_backfill_script_dry_run_changes_nothing(tmp_path):
    import importlib.util

    db, aid = _db(tmp_path)
    db.save_body_composition(aid, "2026-10-12", {"weight_kg": 70.0, "height_cm": 175,
                                                 "body_fat_pct": 27.0}, source="csv")
    db.close()
    path = str(tmp_path / "bmi.db")

    spec = importlib.util.spec_from_file_location(
        "backfill_bmi", "scripts/backfill_bmi.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    plan = mod.plan(path, apply_changes=False)
    con = Database(db_path=path)
    row = con.get_body_composition(aid, days=999)[0]
    assert row["bmi"] is None, "dry-run изменил базу"
    assert plan["rows"] == 1, plan
    assert plan["values"] == [round(70.0 / (1.75 ** 2), 1)], plan
    # применённый повтор уже ничего не меняет
    plan2 = mod.plan(path, apply_changes=True)
    con2 = Database(db_path=path)
    row2 = con2.get_body_composition(aid, days=999)[0]
    assert row2["bmi"] == pytest.approx(22.9, abs=0.1), row2
    assert row2["bmi_source"] == "computed", row2
    plan3 = mod.plan(path, apply_changes=True)
    assert plan3["rows"] == 0, "бэкфилл не идемпотентен"
    con.close()
    con2.close()


def test_card_marks_computed_bmi(tmp_path):
    """Расчётный ИМТ на экране обязан отличаться от измеренного весами."""
    from handlers_body_comp import format_bc_metrics
    computed = "\n".join(format_bc_metrics({"weight_kg": 70.0, "height_cm": 175,
                                             "bmi": 22.9, "bmi_source": "computed"}))
    from_file = "\n".join(format_bc_metrics({"weight_kg": 70.0, "bmi": 23.0,
                                              "bmi_source": "file"}))
    assert "ИМТ" in computed and "расчёт" in computed, computed
    assert "ИМТ" in from_file and "расчёт" not in from_file, from_file
