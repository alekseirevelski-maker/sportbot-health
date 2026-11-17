# -*- coding: utf-8 -*-
"""Импорт весов тренером: только свои команды и обязательно с подтверждением.

Контекст (аудит весов 17.11, решение владельца 18.11): раскладка группового CSV
по спортсменам была доступна только врачу/админу, а тренер, прислав файл, попадал
в «личную» ветку и заливал чужие замеры на себя. После гарда 18.11 тренер получил
честный отказ, но рабочего пути не стало. Теперь: тренер может импортировать,
(а) только спортсмены своих команд, (б) только после подтверждения таблицы
«профиль → спортсмен» на экране.

Врач/админ сохраняет прежнее поведение: автоматическая раскладка по закрепленным
профилям (он и есть источник истины по составу).
"""
import asyncio
from collections import defaultdict

import pytest
from types import SimpleNamespace

import bot as bot_module
from database import Database

TEAM_A = "Славянка ЧКПЗ-2"
TEAM_B = "ЧБК-Челябинская область"
DOCTOR_ID = 990001
COACH_ID = 990002
PROF_A = "Данные пользователя 1"
PROF_B = "Данные пользователя 2"


def _world(tmp_path):
    db = Database(db_path=str(tmp_path / "coach_import.db"))
    db.register_athlete(700401, "a1", "Своя Тестовая", "U17", TEAM_A)
    db.register_athlete(700402, "b1", "Чужая Тестовая", "U17", TEAM_B)
    a = db.get_athlete_by_telegram_id(700401)["id"]
    b = db.get_athlete_by_telegram_id(700402)["id"]
    db.set_coach_teams(COACH_ID, [TEAM_A])
    db.set_scale_profile(PROF_A, a, DOCTOR_ID)
    db.set_scale_profile(PROF_B, b, DOCTOR_ID)
    return db, a, b


def _bot(db):
    obj = object.__new__(bot_module.SportHealthBot)
    obj.db = db
    obj.rate_limiter = defaultdict(list)
    obj.user_states = {}
    return obj


def _records():
    return [
        {"record_date": "2026-11-18", "device_profile": PROF_A,
         "weight_kg": 66.7, "body_fat_pct": 26.3},
        {"record_date": "2026-11-18", "device_profile": PROF_B,
         "weight_kg": 73.4, "body_fat_pct": 14.7},
    ]


def _ctx():
    """ctx python-telegram-bot: handle_text читает ctx.user_data."""
    return SimpleNamespace(user_data={})


def _doc_update(uid):
    sent = []

    async def reply(t, **kw):
        sent.append(t)

    upd = SimpleNamespace(callback_query=None, effective_user=SimpleNamespace(id=uid),
                          message=SimpleNamespace(text="", reply_text=reply))
    return upd, sent


def _cb_update(uid, data):
    """Фейк callback-запроса: одноразовый answer, журнал edit_message_text."""
    q = SimpleNamespace(data=data, from_user=SimpleNamespace(id=uid, first_name=None),
                        edits=[])

    async def answer(*a, **k):
        pass

    async def edit(text, **kw):
        q.edits.append(text)

    q.answer = answer
    q.edit_message_text = edit
    upd = SimpleNamespace(callback_query=q, effective_user=SimpleNamespace(id=uid),
                          message=None)
    return upd, q


# ---------- 1. чужие команды не пишутся ----------

def test_coach_import_skips_athletes_of_other_teams(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {DOCTOR_ID})
    db, a, b = _world(tmp_path)
    obj = _bot(db)
    upd, _ = _doc_update(COACH_ID)

    saved, skipped, unmapped, rows, foreign = asyncio.run(
        obj._import_scale_records(upd, None, _records()))

    assert saved == 1, f"своих должно быть 1: {saved}"
    assert foreign == [PROF_B], f"чужой профиль должен уйти в foreign: {foreign}"
    assert db.get_body_composition(a, days=999), "свой спортсмен не записан"
    assert db.get_body_composition(b, days=999) == [], "чужой спортсмен записан"
    db.close()


def test_doctor_import_unchanged_saves_all(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {DOCTOR_ID})
    db, a, b = _world(tmp_path)
    obj = _bot(db)
    upd, _ = _doc_update(DOCTOR_ID)

    saved, skipped, unmapped, rows, foreign = asyncio.run(
        obj._import_scale_records(upd, None, _records()))

    assert saved == 2 and foreign == []
    assert db.get_body_composition(b, days=999), "врач обязан раскладывать всю базу"
    db.close()


# ---------- 2. тренер: предпросмотр → подтверждение ----------

def test_coach_upload_previews_before_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {DOCTOR_ID})
    db, a, b = _world(tmp_path)
    obj = _bot(db)
    upd, sent = _doc_update(COACH_ID)

    asyncio.run(obj._import_scale_csv(upd, None, _records(), "data20261118101708.csv",
                                      content=b'"date","x"\n"11/18/2026 10:17:08","1"\n'))

    assert db.get_body_composition(a, days=999) == [], \
        "тренерский импорт обязан сначала показать раскладку"
    text = " ".join(sent)
    assert "подтвер" in text.lower(), text[:200]
    assert PROF_A in text and PROF_B in text, text[:200]
    state = obj.user_states.get(COACH_ID) or {}
    assert state.get("step") == "bc_import_review", state
    db.close()


def test_coach_confirm_writes_only_own(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {DOCTOR_ID})
    db, a, b = _world(tmp_path)
    obj = _bot(db)
    upd, _ = _doc_update(COACH_ID)
    asyncio.run(obj._import_scale_csv(upd, None, _records(), "f.csv", content=b"x,y\n1,2\n"))

    cb, q = _cb_update(COACH_ID, "bc_import_confirm")
    asyncio.run(obj.bc_import_confirm(cb, None))

    assert db.get_body_composition(a, days=999), "подтверждённый свой не записан"
    assert db.get_body_composition(b, days=999) == [], "чужой записан вопреки scope"
    row = db.get_body_composition(a, days=999)[0]
    assert row["recorded_by"] == COACH_ID, row
    db.close()


def test_coach_cancel_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {DOCTOR_ID})
    db, a, b = _world(tmp_path)
    obj = _bot(db)
    upd, _ = _doc_update(COACH_ID)
    asyncio.run(obj._import_scale_csv(upd, None, _records(), "f.csv", content=b"x,y\n1,2\n"))

    cb, q = _cb_update(COACH_ID, "bc_import_cancel")
    asyncio.run(obj.bc_import_cancel(cb, None))

    assert db.get_body_composition(a, days=999) == []
    assert (obj.user_states.get(COACH_ID) or {}).get("data", {}) == {} or \
           "pending_scale_records" not in (obj.user_states.get(COACH_ID, {}).get("data") or {})
    db.close()


def test_confirm_without_pending_is_visible_refusal(tmp_path, monkeypatch):
    """После рестарта RAM-state пуст — подтверждение должно объяснить, а не молчать."""
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {DOCTOR_ID})
    db, a, b = _world(tmp_path)
    obj = _bot(db)
    cb, q = _cb_update(COACH_ID, "bc_import_confirm")
    asyncio.run(obj.bc_import_confirm(cb, None))
    assert db.get_body_composition(a, days=999) == []
    assert any("заново" in (t or "") for t in q.edits), q.edits
    db.close()


def test_height_step_finishes_import_after_new_return_shape(tmp_path, monkeypatch):
    """Шаг «введи рост» достраивает импорт — не должен падать на новом контракте.

    Здесь однажды и терялись записи: возврат `_import_scale_records` стал
    пятизначным, а этот вызов остался с четырьмя.
    """
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {DOCTOR_ID})
    db, a, b = _world(tmp_path)
    obj = _bot(db)
    upd, sent = _doc_update(DOCTOR_ID)
    obj.user_states[DOCTOR_ID] = {"step": "bc_height", "data": {
        "bc_pending": [{"record_date": "2026-11-18", "device_profile": PROF_A,
                        "weight_kg": 66.7, "body_fat_pct": 26.3}],
        "bc_height_athlete": a}}

    asyncio.run(obj._bc_height_save(upd, None, DOCTOR_ID, "178"))

    rows = db.get_body_composition(a, days=999)
    assert rows, f"импорт после ввода роста оборвался: {sent}"
    assert rows[0]["recorded_by"] == DOCTOR_ID, rows[0]
    db.close()


# ---------- текст вместо тапа: шаги импорта не должны падать ----------

def _text_update(uid, text):
    sent = []

    async def reply(t, **kw):
        sent.append(t)

    upd = SimpleNamespace(callback_query=None, effective_user=SimpleNamespace(id=uid),
                          message=SimpleNamespace(text=text, reply_text=reply))
    return upd, sent


@pytest.mark.parametrize("step", ["bc_import_review", "bc_import_pick", "wait_scale_csv"])
def test_text_in_import_steps_answers_without_crash(tmp_path, monkeypatch, step):
    """Тренер набрал текст, пока ждал подтверждения/выбора — ответ должен быть
    текстом же. Раньше эти ветки звали q.edit_message_text, а в handle_text
    объекта q нет: NameError и заглушка «Произошла ошибка»."""
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {DOCTOR_ID})
    db, a, b = _world(tmp_path)
    obj = _bot(db)
    obj.user_states[COACH_ID] = {"step": step, "data": {}}
    upd, sent = _text_update(COACH_ID, "178")

    asyncio.run(obj.handle_text(upd, _ctx()))

    assert sent, f"шаг {step}: ответа нет"
    assert not any("Произошла ошибка" in t for t in sent), sent
    assert any(("кноп" in t.lower()) or ("файл" in t.lower()) or ("подтвер" in t.lower())
               for t in sent), sent
    db.close()


def test_review_step_keeps_pending_records(tmp_path, monkeypatch):
    """Текст не должен съедать раскладку, которую тренер ещё не подтвердил."""
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {DOCTOR_ID})
    db, a, b = _world(tmp_path)
    obj = _bot(db)
    upd, sent = _doc_update(COACH_ID)
    asyncio.run(obj._import_scale_csv(upd, None, _records(), "f.csv",
                                      content=b'"date","x"\n"11/18/2026 10:00:00","1"\n'))
    before = obj.user_states[COACH_ID]["data"]["pending_scale_records"]

    tupd, tsent = _text_update(COACH_ID, "когда взвесим ещё?")
    asyncio.run(obj.handle_text(tupd, _ctx()))

    assert obj.user_states[COACH_ID]["data"]["pending_scale_records"] == before
    assert db.get_body_composition(a, days=999) == []
    db.close()


def test_profile_names_match_by_number_not_by_spelling(tmp_path, monkeypatch):
    """Экспорт MovingLife подписывает слот «Данные пользователя 12», экран
    «Профили весов» создаёт ключ «Профиль 12». Строгое равенство не совпадало
    никогда — поэтому автораскладка не работала, а тренерский предпросмотр
    показывал пустоту. Сопоставляем по номеру слота.

    Важно: это не «слепая» запись — тренер всё равно видит таблицу
    «слот → спортсмен» и подтверждает её кнопкой.
    """
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {DOCTOR_ID})
    db = Database(db_path=str(tmp_path / "slots.db"))
    db.register_athlete(700401, "a1", "Своя Тестовая", "U17", TEAM_A)
    db.register_athlete(700402, "b1", "Чужая Тестовая", "U17", TEAM_B)
    a = db.get_athlete_by_telegram_id(700401)["id"]
    b = db.get_athlete_by_telegram_id(700402)["id"]
    db.set_coach_teams(COACH_ID, [TEAM_A])
    # закреплены ТОЛЬКО имена с экрана «Профили весов» — как в реальном проде
    db.set_scale_profile("Профиль 1", a, DOCTOR_ID)
    db.set_scale_profile("Профиль 2", b, DOCTOR_ID)
    assert db.get_scale_profiles() == {"Профиль 1": a, "Профиль 2": b}
    obj = _bot(db)
    upd, _ = _doc_update(COACH_ID)
    records = [
        {"record_date": "2026-11-18", "device_profile": "Данные пользователя 1",
         "weight_kg": 66.7, "body_fat_pct": 26.3},
        {"record_date": "2026-11-18", "device_profile": "Данные пользователя 2",
         "weight_kg": 73.4, "body_fat_pct": 14.7},
    ]

    saved, skipped, unmapped, rows, foreign = asyncio.run(
        obj._import_scale_records(upd, None, records, preview_only=True))

    assert saved == 1, f"свой слот не распознан: saved={saved}, unmapped={unmapped}"
    assert foreign, "чужой слот не помечен как чужой"
    assert any(PROF_A in r for r in rows), rows
    db.close()


def test_slot_number_and_lookup_rules():
    """Номер слота — только отдельное число; совпадение по номеру — только однозначное."""
    from handlers_body_comp import scale_profile_lookup, slot_number
    assert slot_number("Данные пользователя 12") == 12
    assert slot_number("Профиль 3") == 3
    assert slot_number("Профиль") == 0
    assert slot_number("GARLYN 2026") == 0, "год — не номер слота"

    mapping = {"Профиль 1": 11, "Профиль 2": 22}
    assert scale_profile_lookup(mapping, "Профиль 2") == 22               # точное имя
    assert scale_profile_lookup(mapping, "Данные пользователя 1") == 11   # по номеру
    assert scale_profile_lookup(mapping, "Данные пользователя 9") is None
    ambiguous = {"Профиль 5": 55, "Слот 5": 66}
    assert scale_profile_lookup(ambiguous, "Данные пользователя 5") is None
