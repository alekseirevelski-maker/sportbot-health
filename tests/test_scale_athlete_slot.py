# -*- coding: utf-8 -*-
"""Спортсмен сам присылает выгрузку с весов — в том числе когда в файле несколько слотов.

Модель владельца (18.11): CSV из MovingLife присылает сам спортсмен на себя; тренер и
врач смотрят данные своей команды. Реальные выгрузки часто содержат несколько слотов
(«Данные пользователя 1/2/3»), поэтому бот обязан один раз спросить «какой слот твой»,
запомнить ответ и больше не спрашивать. Чужие строки не пишутся, чужие цифры не
показываются.
"""
import asyncio
from collections import defaultdict
from types import SimpleNamespace

import bot as bot_module
from database import Database

TEAM = "Славянка ЧКПЗ-2"
DOCTOR_ID = 990001
COACH_ID = 990002
ATHLETE_TG = 700501
OTHER_TG = 700502
SLOT_1 = "Данные пользователя 1"
SLOT_2 = "Данные пользователя 2"
SLOT_3 = "Данные пользователя 3"
RAW = b'"date","x"\n"11/18/2026 12:00:00","1"\n'


def _bot(db):
    obj = object.__new__(bot_module.SportHealthBot)
    obj.db = db
    obj.rate_limiter = defaultdict(list)
    obj.user_states = {}
    return obj


def _callbacks(markup):
    out = []
    for row in getattr(markup, "inline_keyboard", []) or []:
        for btn in row:
            out.append((btn.text, btn.callback_data))
    return out


def _doc_update(uid):
    sent = []

    async def reply(t, **kw):
        sent.append(t)
        # подписи кнопок — тоже то, что видит спортсмен
        markup = kw.get("reply_markup")
        if markup is not None:
            for row in getattr(markup, "inline_keyboard", []) or []:
                for btn in row:
                    sent.append(btn.text)

    upd = SimpleNamespace(callback_query=None, effective_user=SimpleNamespace(id=uid),
                          message=SimpleNamespace(text="", reply_text=reply))
    return upd, sent


def _cb_update(uid, data):
    state = {"edits": [], "marks": []}

    async def answer(*a, **k):
        pass

    async def edit(text, **kw):
        state["edits"].append(text)
        if kw.get("reply_markup") is not None:
            state["marks"].append(kw["reply_markup"])

    sent = []

    async def reply(t_, **kw):
        sent.append(t_)

    q = SimpleNamespace(data=data, from_user=SimpleNamespace(id=uid, first_name=None),
                        answer=answer, edit_message_text=edit, edits=state["edits"],
                        marks=state["marks"],
                        message=SimpleNamespace(text="", reply_text=reply))
    return SimpleNamespace(callback_query=q, effective_user=SimpleNamespace(id=uid),
                           message=None), q


def _world(tmp_path, profiles=None):
    db = Database(db_path=str(tmp_path / "slot.db"))
    db.register_athlete(ATHLETE_TG, "girl", "Слот Тестовая", "U17", TEAM)
    db.register_athlete(OTHER_TG, "girl2", "Вторая Слотная", "U17", TEAM)
    a = db.get_athlete_by_telegram_id(ATHLETE_TG)["id"]
    b = db.get_athlete_by_telegram_id(OTHER_TG)["id"]
    for prof, aid in (profiles or {}).items():
        db.set_scale_profile(prof, aid, DOCTOR_ID)
    return db, a, b


def _multi():
    return [
        {"record_date": "2026-11-18", "device_profile": SLOT_1,
         "weight_kg": 66.7, "body_fat_pct": 26.3},
        {"record_date": "2026-11-18", "device_profile": SLOT_2,
         "weight_kg": 73.4, "body_fat_pct": 14.7},
        {"record_date": "2026-11-10", "device_profile": SLOT_2,
         "weight_kg": 72.9, "body_fat_pct": 15.1},
    ]


def _upload(obj, records=None, uid=ATHLETE_TG, tmp=None):
    upd, sent = _doc_update(uid)
    asyncio.run(obj._import_scale_csv(upd, None, _multi() if records is None else records,
                                      "data20261118120000.csv", content=RAW,
                                      inbox_dir=str(tmp / "inbox") if tmp else None))
    return upd, sent


# ---------- 1. файл с несколькими слотами: спросить, а не отказывать ----------

def test_multi_slot_file_asks_which_slot_is_mine(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {DOCTOR_ID})
    db, a, b = _world(tmp_path)
    obj = _bot(db)
    _upload(obj, tmp=tmp_path)

    assert db.get_body_composition(a, days=999) == [], "записал до ответа про слот"
    st = obj.user_states[ATHLETE_TG]
    assert st["step"] == "bc_slot_pick", st
    assert st["data"]["bc_slot_profiles"] == [SLOT_1, SLOT_2], st
    db.close()


def test_slot_question_names_slots_but_not_foreign_numbers(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {DOCTOR_ID})
    db, a, b = _world(tmp_path)
    obj = _bot(db)
    _upd, sent = _upload(obj, tmp=tmp_path)
    text = " ".join(sent)

    assert SLOT_1 in text and SLOT_2 in text, \
        f"спортсмену не показали варианты слотов: {text}"
    assert "66.7" not in text and "73.4" not in text and "26.3" not in text, \
        f"спортсмену показали чужие замеры: {text}"
    db.close()


# ---------- 2. ответил — пишет только своё и запоминает ----------

def test_chosen_slot_imports_only_own_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {DOCTOR_ID})
    db, a, b = _world(tmp_path)
    obj = _bot(db)
    _upload(obj, tmp=tmp_path)

    cb, q = _cb_update(ATHLETE_TG, "bc_slot_1")     # второй слот в списке = SLOT_2
    asyncio.run(obj.bc_slot_choose(cb, None))

    mine = db.get_body_composition(a, days=999)
    assert {r["record_date"] for r in mine} == {"2026-11-18", "2026-11-10"}, mine
    assert all(r["device_profile"] == SLOT_2 for r in mine), mine
    assert all(r["recorded_by"] == ATHLETE_TG for r in mine), mine
    assert db.get_scale_slot(a) == SLOT_2, "слот не запомнен"
    assert db.get_body_composition(b, days=999) == []
    assert (obj.user_states.get(ATHLETE_TG) or {}).get("step") != "bc_slot_pick"
    db.close()


def test_next_upload_uses_saved_slot_without_asking(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {DOCTOR_ID})
    db, a, b = _world(tmp_path)
    obj = _bot(db)
    _upload(obj, tmp=tmp_path)
    cb, q = _cb_update(ATHLETE_TG, "bc_slot_1")
    asyncio.run(obj.bc_slot_choose(cb, None))

    obj.user_states.pop(ATHLETE_TG, None)
    _upd, sent = _upload(obj, records=[
        {"record_date": "2026-11-25", "device_profile": SLOT_2,
         "weight_kg": 73.1, "body_fat_pct": 14.9},
        {"record_date": "2026-11-25", "device_profile": SLOT_1,
         "weight_kg": 66.1, "body_fat_pct": 26.9}], tmp=tmp_path)

    assert (obj.user_states.get(ATHLETE_TG) or {}).get("step") != "bc_slot_pick", \
        "спросил слот повторно"
    assert len(db.get_body_composition(a, days=999)) == 3
    assert all(r["device_profile"] == SLOT_2 for r in db.get_body_composition(a, days=999))
    db.close()


# ---------- 3. слот уже занят другим спортсменом ----------

def test_slot_bound_to_other_athlete_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {DOCTOR_ID})
    db, a, b = _world(tmp_path)
    db.set_scale_profile(SLOT_2, b, DOCTOR_ID)
    obj = _bot(db)
    _upload(obj, tmp=tmp_path)

    cb, q = _cb_update(ATHLETE_TG, "bc_slot_1")
    asyncio.run(obj.bc_slot_choose(cb, None))

    assert db.get_body_composition(a, days=999) == [], "записался в чужой слот"
    assert db.get_scale_slot(a) is None
    assert any("друг" in (t or "").lower() for t in q.edits), q.edits
    db.close()


# ---------- 4. одиночный слот и файл без слотов ----------

def test_single_slot_file_imports_and_remembers(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {DOCTOR_ID})
    db, a, b = _world(tmp_path)
    obj = _bot(db)
    _upload(obj, records=[{"record_date": "2026-11-18", "device_profile": SLOT_3,
                          "weight_kg": 61.0, "body_fat_pct": 20.0}], tmp=tmp_path)

    assert len(db.get_body_composition(a, days=999)) == 1
    assert db.get_scale_slot(a) == SLOT_3
    db.close()


def test_file_without_slots_still_imports_on_self(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {DOCTOR_ID})
    db, a, b = _world(tmp_path)
    obj = _bot(db)
    _upload(obj, records=[{"record_date": "2026-11-18", "weight_kg": 60.0,
                          "body_fat_pct": 21.0},
                          {"record_date": "2026-11-11", "weight_kg": 60.5,
                          "body_fat_pct": 21.4}], tmp=tmp_path)

    assert len(db.get_body_composition(a, days=999)) == 2
    assert db.get_scale_slot(a) is None
    db.close()


def test_slot_choice_without_pending_asks_new_file(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {DOCTOR_ID})
    db, a, b = _world(tmp_path)
    obj = _bot(db)
    obj.user_states[ATHLETE_TG] = {"step": "bc_slot_pick", "data": {}}
    cb, q = _cb_update(ATHLETE_TG, "bc_slot_0")
    asyncio.run(obj.bc_slot_choose(cb, None))

    assert db.get_body_composition(a, days=999) == []
    assert any("заново" in (t or "").lower() for t in q.edits), q.edits
    db.close()


# ---------- 5. тренер видит свои команды, врач — всех ----------

def test_coach_panel_offers_scales_screen(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", set())
    db = Database(db_path=str(tmp_path / "coach.db"))
    db.set_coach_teams(COACH_ID, [TEAM])
    obj = _bot(db)
    cb, q = _cb_update(COACH_ID, "main_menu")
    asyncio.run(obj.show_main_menu(cb, None))

    cbs = [c for _t, c in _callbacks(q.marks[-1])]
    assert "bc_menu" in cbs, cbs
    db.close()


def test_coach_scales_menu_scoped_to_own_teams(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", set())
    db = Database(db_path=str(tmp_path / "coach2.db"))
    db.set_coach_teams(COACH_ID, [TEAM])
    db.register_athlete(700503, "g3", "Видимая Тестовая", "U17", TEAM)
    db.register_athlete(700504, "b4", "Чужая Видимая", "U17", "Курчатов")
    obj = _bot(db)
    cb, q = _cb_update(COACH_ID, "bc_menu")
    asyncio.run(obj.scale_import_menu(cb, None))

    cbs = [c for _t, c in _callbacks(q.marks[-1])]
    assert "bc_overview_all" not in cbs, f"тренеру обещан экран всей базы: {cbs}"
    assert "bc_overview" in cbs, cbs
    db.close()


# ---------- экран загрузки: обещание «жду файл» должно работать ----------

def test_upload_screen_arms_the_waiting_step(tmp_path, monkeypatch):
    """«⚖️ Состав тела» → «📤 Загрузить данные» обещает «Жду твой файл».

    Раньше шаг не выставлялся, и если спортсмен вместо файла печатал текст, бот
    молчал (ветка wait_scale_csv была мертва).
    """
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {DOCTOR_ID})
    db, a, b = _world(tmp_path)
    obj = _bot(db)
    cb, q = _cb_update(ATHLETE_TG, "scale_upload_start")
    asyncio.run(obj.scale_upload_start(cb, None))

    assert obj.get_state(ATHLETE_TG)["step"] == "wait_scale_csv"
    assert "Жду" in (q.edits[-1] or ""), q.edits

    tupd, tsent = _doc_update(ATHLETE_TG)
    tctx = SimpleNamespace(user_data={})
    tupd.message.text = "а я не знаю какой мой профиль"
    asyncio.run(obj.handle_text(tupd, tctx))
    assert any("файл" in t.lower() for t in tsent), tsent
    db.close()
