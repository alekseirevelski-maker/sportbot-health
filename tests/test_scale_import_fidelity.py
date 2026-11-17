# -*- coding: utf-8 -*-
"""Что не должно теряться при импорте весов и кого нельзя «заливать» на спортсмена.

Фикстуры CSV_A (healthdata.csv) и CSV_B (data<ts>.csv) — реальные шапки экспортов
MovingLife/GARLYN, снятые из bot.log (см. tests/test_scale_dates.py). Тут ловятся
три тихие потери и одна дыра в правах:

1. ИМТ («ИМТ» против маски «имп») и безжировая масса («Безж. мас. Тела» против «безжир»)
   не маппились → в проде NULL в 54/54 записях.
2. «Cкелетные мыщцы(%)» (опечатка «щ» в самом файле весов) терялась совсем.
3. Автор замера recorded_by не писался никогда: вызывающий кладёт его в data-словарь,
   а сохранение читает отдельный аргумент.
4. Спортсмен, приславший «групповой» CSV (несколько профилей в файле), получал чужие
   замеры на себя.
"""
import asyncio
from collections import defaultdict
from types import SimpleNamespace

import pytest

import bot as bot_module
from database import Database
from scale_csv_parser import distinct_device_profiles, parse_scale_csv_dated
from tests.test_scale_dates import CSV_A, CSV_B

TEAM = "Славянка ЧКПЗ-2"
DOCTOR_ID = 990001
ATHLETE_TG = 700001


def _bot_obj(db):
    obj = object.__new__(bot_module.SportHealthBot)
    obj.db = db
    obj.rate_limiter = defaultdict(list)
    obj.user_states = {}
    return obj


def _msg_update(uid, text=""):
    sent = []

    async def reply(t, **kw):
        sent.append(t)

    msg = SimpleNamespace(text=text, reply_text=reply)
    upd = SimpleNamespace(callback_query=None, effective_user=SimpleNamespace(id=uid),
                          message=msg)
    return upd, sent


def _athlete(db, tg, name):
    db.register_athlete(tg, "u" + str(tg), name, "U17", TEAM)
    return db.get_athlete_by_telegram_id(tg)["id"]


# ---------- 1. ИМТ и безжировая масса ----------

def test_csv_a_healthdata_keeps_bmi_and_lean():
    recs = parse_scale_csv_dated(CSV_A, "healthdata.csv")
    assert recs, "пустой разбор"
    assert all(r.get("bmi") is not None for r in recs), \
        f"ИМТ потерян: {[r.get('bmi') for r in recs]}"
    assert all(r.get("lean_mass_kg") is not None for r in recs), \
        f"безжировая масса потеряна: {[r.get('lean_mass_kg') for r in recs]}"
    row = next(r for r in recs if r.get("record_date") == "2026-10-12")
    assert row["bmi"] == pytest.approx(23.4, abs=0.05)
    assert row["lean_mass_kg"] == pytest.approx(57.2, abs=0.05)


def test_csv_b_datacsv_keeps_bmi_and_lean():
    recs = parse_scale_csv_dated(CSV_B, "data20261012164158.csv")
    assert all(r.get("bmi") is not None for r in recs), "ИМТ потерян (формат B)"
    assert all(r.get("lean_mass_kg") is not None for r in recs), \
        "безжировая потеряна (формат B)"


# ---------- 2. Скелетная мышечная масса ----------

def test_csv_b_keeps_skeletal_muscle_percent():
    recs = parse_scale_csv_dated(CSV_B, "data20261012164158.csv")
    assert all(r.get("skeletal_muscle_pct") is not None for r in recs), \
        f"скелетные мышцы потеряны: {[r.get('skeletal_muscle_pct') for r in recs]}"


# ---------- 3. Автор замера ----------

def test_import_scale_records_stores_author(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {DOCTOR_ID})
    db = Database(db_path=str(tmp_path / "scale.db"))
    aid = _athlete(db, ATHLETE_TG, "Первая Тестовая")

    recs = parse_scale_csv_dated(CSV_A, "healthdata.csv")
    assert recs
    # в healthdata.csv колонки профиля нет → раскладка вручную (forced_athlete_id)
    upd, _ = _msg_update(DOCTOR_ID)
    obj = _bot_obj(db)
    saved, skipped, unmapped, rows, foreign = asyncio.run(
        obj._import_scale_records(upd, None, recs, forced_athlete_id=aid))
    assert saved == len(recs)
    stored = db.get_body_composition(aid, days=999)
    assert stored, "записи не легли"
    assert all(x.get("recorded_by") == DOCTOR_ID for x in stored), \
        f"recorded_by не пишется: {[x.get('recorded_by') for x in stored]}"
    db.close()


# ---------- 4. Групповой файл ≠ на спортсмена ----------

def test_distinct_device_profiles_helper():
    recs = [{"record_date": "2026-11-17", "device_profile": "Данные пользователя 1"},
            {"record_date": "2026-11-17", "device_profile": "Данные пользователя 2"},
            {"record_date": "2026-11-16", "device_profile": "Данные пользователя 1"}]
    assert sorted(distinct_device_profiles(recs)) == [
        "Данные пользователя 1", "Данные пользователя 2"]


def test_athlete_own_upload_of_group_file_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {DOCTOR_ID})
    db = Database(db_path=str(tmp_path / "group.db"))
    aid = _athlete(db, ATHLETE_TG, "Вторая Тестовая")
    obj = _bot_obj(db)

    records = [
        {"record_date": "2026-11-17", "device_profile": "Данные пользователя 1",
         "weight_kg": 68.3, "body_fat_pct": 27.6},
        {"record_date": "2026-11-17", "device_profile": "Данные пользователя 2",
         "weight_kg": 73.4, "body_fat_pct": 14.7},
    ]
    upd, sent = _msg_update(ATHLETE_TG)
    asyncio.run(obj._import_scale_csv(upd, None, records, "data20261117201445.csv"))

    assert db.get_body_composition(aid, days=999) == [], \
        "групповой файл залили на спортсмена без разбора профилей"
    assert any("профил" in (t or "").lower() for t in sent), \
        f"спортсмен не получил объяснения: {sent}"
    db.close()
