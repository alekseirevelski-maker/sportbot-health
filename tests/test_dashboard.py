# -*- coding: utf-8 -*-
"""Дашборд «🔔 На сегодня» (dashboard.build_today_dashboard + bot.show_today_dashboard).

Блоки экрана: 🔴 красные флаги сегодняшних опросов, 🟡 тренды (3 дня подряд +
цикл), ⚪ слепые зоны (не сдали сегодня / заблокировали бота). Scope: тренер
видит только свои команды — спортсмены чужой команды не появляются ни в тексте,
ни в кнопках. Пустое состояние: «🟢 Тревог нет, N из M сдали опрос».
"""
import asyncio
from collections import defaultdict
from datetime import date, timedelta
from types import SimpleNamespace

from telegram.error import BadRequest

from database import Database
from dashboard import build_today_dashboard

TEAM_A = "ЧБК-Челябинская область"
TEAM_B = "Славянка ЧКПЗ-2"
DOCTOR_ID = 999001
COACH_A_ID = 999002
TODAY = date.today()


# ==================== посев ====================

def _make_db(tmp_path, name="dash.db"):
    return Database(db_path=str(tmp_path / name))


def _insert_past(db, aid, days_back, **fields):
    """Опрос за сегодня-days_back в обход save_survey (который пишет только сегодня)."""
    cols = ", ".join(fields)
    marks = ", ".join("?" * len(fields))
    d = (TODAY - timedelta(days=days_back)).isoformat()
    db.conn.execute(
        f"INSERT INTO daily_wellness (athlete_id, survey_date, {cols}) "
        f"VALUES (?, ?, {marks})",
        (aid, d, *fields.values()),
    )
    db.conn.commit()


def _seed(tmp_path):
    """Сценарий брифа: 4 спортсмена / 2 команды.

    A: Первая Больная — illness_flag + pain_nrs=7 (🔴);
       Вторая Нормальная — сегодня в норме, но готовность <5 три дня подряд (🟡);
    B: Третья Слабая — Hooper ниже critical порога U14 (🔴);
       Четвертая Блок — не сдала + bot_blocked=1 (⚪).
    """
    db = _make_db(tmp_path)
    db.register_athlete(101, "sick", "Первая Больная", "U17", TEAM_A)
    db.register_athlete(102, "ok", "Вторая Нормальная", "U17", TEAM_A)
    db.register_athlete(201, "weak", "Третья Слабая", "U14", TEAM_B)
    db.register_athlete(202, "blocked", "Четвертая Блок", "U17", TEAM_B)
    ids = {tg: db.get_athlete_by_telegram_id(tg)["id"] for tg in (101, 102, 201, 202)}

    db.save_survey(ids[101], {"sleep": 6, "fatigue": 5, "soreness": 5,
                              "illness_flag": "ОРВИ", "pain_nrs": 7,
                              "pain_location": "колено"})
    db.save_survey(ids[102], {"sleep": 7, "fatigue": 6, "soreness": 6,
                              "readiness": 4})
    _insert_past(db, ids[102], 1, readiness=4)
    _insert_past(db, ids[102], 2, readiness=3)
    # U14: critical=16; distress = (8-2)+(8-3)+(8-2) = 17 ≥ 16 → 🔴
    db.save_survey(ids[201], {"sleep": 2, "fatigue": 3, "soreness": 2})
    db.set_bot_blocked(ids[202], 1)
    db.set_coach_teams(COACH_A_ID, [TEAM_A])
    return db, ids


def _bot_obj(db):
    """SportHealthBot без конструктора/токена: только нужные атрибуты."""
    from bot import SportHealthBot
    obj = object.__new__(SportHealthBot)
    obj.db = db
    obj.rate_limiter = defaultdict(list)
    return obj


def _callbacks(buttons):
    return [c for row in buttons for _label, c in row]


class FakeQ:
    def __init__(self, uid, data="dash_today"):
        self.data = data
        self.from_user = SimpleNamespace(id=uid, first_name=None)
        self.answers = []
        self.edits = []

    async def answer(self, *a, **k):
        # Как настоящий Telegram: ответ на callback одноразовый —
        # вторая попытка падает «Query is already answered».
        if self.answers:
            raise BadRequest("Query is already answered")
        self.answers.append((a, k))

    async def edit_message_text(self, text, **kw):
        self.edits.append((text, kw))


# ==================== Step 1: чистая функция, врач видит всё ====================

def test_doctor_sees_all_blocks(tmp_path, monkeypatch):
    import bot as bot_module
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {DOCTOR_ID})
    db, ids = _seed(tmp_path)
    obj = _bot_obj(db)

    scoped = obj._scoped_athletes(DOCTOR_ID)
    assert len(scoped) == 4

    text, buttons = build_today_dashboard(db, DOCTOR_ID, scoped, TODAY)

    # 🔴 оба красных — с ФИО и причиной
    assert "Первая Больная" in text
    assert "ОРВИ" in text
    assert "7/10" in text
    assert "Третья Слабая" in text
    # 🟡 тренд-блок: готовность 3 дня подряд < 5
    assert "Вторая Нормальная" in text
    assert "готовность" in text.lower()
    # ⚪ слепые зоны: не сдавшая + заблокировавшая
    assert "не сдали" in text.lower()
    assert "Четвертая Блок" in text
    assert "заблокировали" in text.lower()
    # кнопки bc_view_<id> на каждой красной/жёлтой строке + «Обновить»
    cbs = _callbacks(buttons)
    assert f"bc_view_{ids[101]}" in cbs
    assert f"bc_view_{ids[201]}" in cbs
    assert f"bc_view_{ids[102]}" in cbs
    assert "dash_today" in cbs


def test_coach_sees_only_own_team(tmp_path, monkeypatch):
    """Тренер команды А: спортсмены команды Б НЕ встречаются ни в тексте, ни в кнопках."""
    import bot as bot_module
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", set())
    db, ids = _seed(tmp_path)
    obj = _bot_obj(db)

    scoped = obj._scoped_athletes(COACH_A_ID)
    assert {a["id"] for a in scoped} == {ids[101], ids[102]}

    text, buttons = build_today_dashboard(db, COACH_A_ID, scoped, TODAY)

    assert "Первая Больная" in text
    assert "Вторая Нормальная" in text
    assert "Третья Слабая" not in text
    assert "Четвертая Блок" not in text
    cbs = _callbacks(buttons)
    assert f"bc_view_{ids[101]}" in cbs
    assert f"bc_view_{ids[201]}" not in cbs
    assert f"bc_view_{ids[202]}" not in cbs


def test_empty_state(tmp_path):
    db = _make_db(tmp_path, "empty.db")
    db.register_athlete(301, "clean", "Чистая Кристина", "U17", TEAM_A)
    aid = db.get_athlete_by_telegram_id(301)["id"]
    db.save_survey(aid, {"sleep": 7, "fatigue": 7, "soreness": 7})

    text, buttons = build_today_dashboard(db, DOCTOR_ID, db.get_all_athletes(), TODAY)

    assert "🟢 Тревог нет, 1 из 1 сдали опрос" in text
    assert "dash_today" in _callbacks(buttons)


def test_complaints_truncated_to_60_chars(tmp_path):
    db = _make_db(tmp_path, "clip.db")
    db.register_athlete(302, "chatty", "Болтливая Бэлла", "U17", TEAM_A)
    aid = db.get_athlete_by_telegram_id(302)["id"]
    long = "ж" * 80
    db.save_survey(aid, {"sleep": 6, "fatigue": 6, "soreness": 6, "complaints": long})

    text, _buttons = build_today_dashboard(db, DOCTOR_ID, db.get_all_athletes(), TODAY)

    assert "ж" * 60 in text
    assert "ж" * 61 not in text


def test_hr_out_of_individual_corridor_two_days(tmp_path):
    """Пульс вне личного коридора 2 дня подряд → 🔴 (логика _individual_recs)."""
    db = _make_db(tmp_path, "hr.db")
    db.register_athlete(303, "tachy", "Тахикардова Таня", "U17", TEAM_A)
    aid = db.get_athlete_by_telegram_id(303)["id"]
    # история 30 дн.: норма ~50 (нужно ≥5 опросов для baseline)
    for back in range(6, 2, -1):
        _insert_past(db, aid, back, resting_hr=50)
    _insert_past(db, aid, 2, resting_hr=95)
    _insert_past(db, aid, 1, resting_hr=95)
    db.save_survey(aid, {"sleep": 6, "fatigue": 6, "soreness": 6, "hr": 95})

    text, buttons = build_today_dashboard(db, DOCTOR_ID, db.get_all_athletes(), TODAY)

    assert "Тахикардова Таня" in text
    assert "2 дня подряд" in text
    assert f"bc_view_{aid}" in _callbacks(buttons)


def test_cycle_alert_scoped_for_coach(tmp_path, monkeypatch):
    """Цикл-алерты: задержка у команды Б — врач видит, тренер А — нет (решение владельца)."""
    import bot as bot_module
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {DOCTOR_ID})
    db = _make_db(tmp_path, "cycle.db")
    db.register_athlete(401, "cycleb", "Циклева Циклита", "U17", TEAM_B)
    aid = db.get_athlete_by_telegram_id(401)["id"]
    db.conn.execute("UPDATE athletes SET gender = 'female', last_period_start = ?, "
                    "cycle_length_default = 28 WHERE id = ?",
                    ((TODAY - timedelta(days=40)).isoformat(), aid))
    db.conn.commit()
    obj = _bot_obj(db)

    doc_text, _ = build_today_dashboard(
        db, DOCTOR_ID, obj._scoped_athletes(DOCTOR_ID), TODAY)
    assert "Циклева Циклита" in doc_text
    assert "задержка" in doc_text

    db.set_coach_teams(COACH_A_ID, [TEAM_A])
    coach_text, _ = build_today_dashboard(
        db, COACH_A_ID, obj._scoped_athletes(COACH_A_ID), TODAY)
    assert "Циклева Циклита" not in coach_text


# ==================== Step 5-6: хендлер, гейт, кэш, меню ====================

def test_dash_today_denied_for_athlete(tmp_path, monkeypatch):
    """Smoke: спортсмен жмёт dash_today → «⛔ Нет доступа» от гейта Task 1."""
    import bot as bot_module
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", set())
    db, _ids = _seed(tmp_path)
    obj = _bot_obj(db)

    q = FakeQ(101, "dash_today")
    update = SimpleNamespace(callback_query=q, message=None,
                             effective_user=SimpleNamespace(id=101))
    asyncio.run(obj.callback_handler(update, None))

    # Отказной алерт — ПЕРВЫЙ и единственный ответ (FakeQ кидает BadRequest
    # на втором answer, как настоящий Telegram «Query is already answered»).
    assert len(q.answers) == 1
    args, kwargs = q.answers[0]
    assert args == ("⛔ Нет доступа",)
    assert kwargs.get("show_alert") is True
    assert not q.edits


def test_show_today_dashboard_doctor_renders_and_caches(tmp_path, monkeypatch):
    import bot as bot_module
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {DOCTOR_ID})
    db, _ids = _seed(tmp_path)
    obj = _bot_obj(db)

    calls = []
    real_build = bot_module.build_today_dashboard

    def counting_build(*a, **k):
        calls.append(1)
        return real_build(*a, **k)

    monkeypatch.setattr(bot_module, "build_today_dashboard", counting_build)

    q = FakeQ(DOCTOR_ID, "dash_today")
    update = SimpleNamespace(callback_query=q, message=None,
                             effective_user=SimpleNamespace(id=DOCTOR_ID))
    asyncio.run(obj.show_today_dashboard(update, None))
    asyncio.run(obj.show_today_dashboard(update, None))

    assert len(q.edits) == 2
    text, kw = q.edits[0]
    assert "На сегодня" in text
    assert "Первая Больная" in text
    assert kw.get("parse_mode") == "Markdown"
    # 60-с кэш: вторая сборка в ту же минуту не запускает тяжёлый build
    assert len(calls) == 1


def test_main_menu_coach_sees_dash_button_first_row(tmp_path, monkeypatch):
    import bot as bot_module
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", set())
    db, _ids = _seed(tmp_path)
    obj = _bot_obj(db)

    q = FakeQ(COACH_A_ID, "main_menu")
    update = SimpleNamespace(callback_query=q, message=None,
                             effective_user=SimpleNamespace(id=COACH_A_ID,
                                                            first_name=None))
    asyncio.run(obj.show_main_menu(update, None))

    first = q.edits[0][1]["reply_markup"].inline_keyboard[0][0]
    assert first.callback_data == "dash_today"
    assert "На сегодня" in first.text


def test_main_menu_athlete_does_not_see_dash_button(tmp_path, monkeypatch):
    import bot as bot_module
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", set())
    db, ids = _seed(tmp_path)
    db.update_athlete_gender(ids[102], "female")
    obj = _bot_obj(db)

    q = FakeQ(102, "main_menu")
    update = SimpleNamespace(callback_query=q, message=None,
                             effective_user=SimpleNamespace(id=102, first_name=None))
    asyncio.run(obj.show_main_menu(update, None))

    markup = q.edits[0][1]["reply_markup"]
    all_cbs = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "dash_today" not in all_cbs


# ==================== Fix round 1: BadRequest не глотается, жалобы экранируются ====================

def test_markdown_complaints_escaped_in_text(tmp_path):
    """Свободный ввод с Markdown-спецсимволами экранируется — экран не роняется."""
    db = _make_db(tmp_path, "esc.db")
    db.register_athlete(304, "markdown", "Маркдаунова Марка", "U17", TEAM_A)
    aid = db.get_athlete_by_telegram_id(304)["id"]
    db.save_survey(aid, {"sleep": 6, "fatigue": 6, "soreness": 6,
                         "complaints": "*болит* _колено_ [ссылка](t.me)"})

    text, _buttons = build_today_dashboard(db, DOCTOR_ID, db.get_all_athletes(), TODAY)

    assert "\\*болит\\*" in text
    assert "\\_колено\\_" in text
    assert "\\[ссылка](t.me)" in text  # «]» не экранируем — не спецсимвол entity


class FlakyQ(FakeQ):
    """edit_message_text кидает BadRequest по сценарию (parse / not_modified)."""

    def __init__(self, uid, fail_kind):
        super().__init__(uid)
        self.fail_kind = fail_kind
        self.calls = []

    async def edit_message_text(self, text, **kw):
        self.calls.append(kw)
        if self.fail_kind == "parse" and kw.get("parse_mode"):
            raise BadRequest("Message can't be parsed: Can't parse entities")
        if self.fail_kind == "not_modified" and len(self.calls) == 1:
            raise BadRequest("Message is not modified")
        self.edits.append((text, kw))


def _run_handler(obj, q):
    update = SimpleNamespace(callback_query=q, message=None,
                             effective_user=SimpleNamespace(id=DOCTOR_ID))
    asyncio.run(obj.show_today_dashboard(update, None))


def test_handler_parse_badrequest_falls_back_without_parse_mode(tmp_path, monkeypatch, caplog):
    """«Can't parse entities» → НЕ глотается: warning в логе + resend без parse_mode."""
    import bot as bot_module
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {DOCTOR_ID})
    db, _ids = _seed(tmp_path)
    obj = _bot_obj(db)

    q = FlakyQ(DOCTOR_ID, "parse")
    _run_handler(obj, q)

    # первая попытка с parse_mode упала, fallback доставил тот же текст без него
    assert len(q.edits) == 1
    text, kw = q.edits[0]
    assert "parse_mode" not in kw
    assert "Первая Больная" in text
    # ошибка видна в логе (без PHI — только user_id)
    assert any("Dashboard render failed" in r.getMessage()
               and str(DOCTOR_ID) in r.getMessage()
               for r in caplog.records)


def test_handler_not_modified_badrequest_stays_silent(tmp_path, monkeypatch, caplog):
    """«Message is not modified» (Обновить в пределах кэша) — штатно, без fallback/лога."""
    import bot as bot_module
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {DOCTOR_ID})
    db, _ids = _seed(tmp_path)
    obj = _bot_obj(db)

    q = FlakyQ(DOCTOR_ID, "not_modified")
    _run_handler(obj, q)

    assert len(q.calls) == 1        # повторной отправки не было
    assert not q.edits
    assert not any("Dashboard render failed" in r.getMessage() for r in caplog.records)
