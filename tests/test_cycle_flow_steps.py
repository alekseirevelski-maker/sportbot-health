# -*- coding: utf-8 -*-
"""Переходы шагов цикла в опросе (AsyncFake-тесты миксина — то, что чистые
юнит-тесты не видят): после любого ответа шаг обязан двинуться к жалобам."""
import asyncio
from datetime import date, timedelta
from types import SimpleNamespace

from cycle_ui import apply_anchor
from database import Database
from handlers_athlete import AthleteMixin


class _FakeQ:
    def __init__(self, data, user_id=555):
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.edits = []

    async def answer(self, *a, **k):
        pass

    async def edit_message_text(self, text, **kw):
        self.edits.append(text)


def _update(q):
    return SimpleNamespace(callback_query=q,
                           effective_user=SimpleNamespace(id=q.from_user.id),
                           message=None)


class _Bot(AthleteMixin):
    """Заглушка real-бота: mixin-код настоящий, Telegram — фейк."""

    def __init__(self, db, state):
        self.db = db
        self._state = state
        self.complaints_called = 0

    def get_state(self, uid):
        return self._state

    def kb(self, rows):
        return rows

    async def _ask_complaints(self, update, ctx):
        self.complaints_called += 1


def _girl(tmp_path):
    db = Database(db_path=str(tmp_path / "steps.db"))
    db.register_athlete(555, "s", "Шаг Шага", "U17", "ЧБК")
    a = db.get_athlete_by_telegram_id(555)
    db.update_athlete_gender(a["id"], "female", 28)
    return db, a


# ---------- блокера 1: якорь-кнопки ----------

def test_anchor_button_advances_to_complaints(tmp_path):
    db, a = _girl(tmp_path)
    state = {"step": "survey_anchor", "data": {"athlete": a}}
    bot = _Bot(db, state)
    q = _FakeQ("anchor_0")
    asyncio.run(bot.set_cycle_anchor(_update(q), None))
    assert db.get_last_period_start(a["id"]) == date.today()
    assert bot.complaints_called == 1      # иначе весь отвеченный опрос потерян
    assert q.edits                          # эхо «Записано…»


def test_anchor_pre_and_skip_advance(tmp_path):
    db, a = _girl(tmp_path)
    bot = _Bot(db, {"step": "survey_anchor", "data": {"athlete": a}})
    asyncio.run(bot.set_cycle_anchor(_update(_FakeQ("anchor_skip")), None))
    assert bot.complaints_called == 1
    assert db.get_athlete_by_telegram_id(555)["menarche_status"] == "skip"


def test_anchor_wrong_step_does_not_save(tmp_path):
    db, a = _girl(tmp_path)
    bot = _Bot(db, {"step": "main_menu", "data": {"athlete": a}})
    asyncio.run(bot.set_cycle_anchor(_update(_FakeQ("anchor_0")), None))
    assert bot.complaints_called == 0
    assert db.get_last_period_start(a["id"]) is None


# ---------- блокера 2: menarche_status ----------

def test_apply_anchor_clears_menarche_status(tmp_path):
    """Отметила начало после «пока не было» — флаг снимаем, иначе опрос вечно
    skip-ит цикл, а на 90-й день прилетит ложный 🚫 RED-S врачу."""
    db, a = _girl(tmp_path)
    db.set_menarche_status(a["id"], "pre")
    target = date.today() - timedelta(days=3)
    assert apply_anchor(db, a["id"], target, date.today(), 28)
    assert db.get_athlete_by_telegram_id(555)["menarche_status"] is None


# ---------- ежедневные тапы ----------

def test_daily_no_saves_computed_day_and_advances(tmp_path):
    db, a = _girl(tmp_path)
    start = date.today() - timedelta(days=5)
    db.add_cycle_event(a["id"], start, type="anchor")
    state = {"step": "survey_cycle_daily", "data": {"athlete": a}}
    bot = _Bot(db, state)
    asyncio.run(bot.set_cycle_daily(_update(_FakeQ("cyc_no")), None))
    assert state["data"]["cycle_day"] == 6
    assert bot.complaints_called == 1


def test_daily_start_writes_event_day_one(tmp_path):
    db, a = _girl(tmp_path)
    start = date.today() - timedelta(days=26)
    db.add_cycle_event(a["id"], start, type="anchor")
    db.add_cycle_event(a["id"], start - timedelta(days=28), type="start")
    state = {"step": "survey_cycle_daily", "data": {"athlete": a}}
    bot = _Bot(db, state)
    asyncio.run(bot.set_cycle_daily(_update(_FakeQ("cyc_start")), None))
    assert db.get_last_period_start(a["id"]) == date.today()
    assert state["data"]["cycle_day"] == 1
    assert bot.complaints_called == 1


def test_daily_fix_and_anchor_again_do_not_advance(tmp_path):
    """RF-переходы: ✏️ и «отметить начало» остаются в шаге ввода, к жалобам не бегут."""
    db, a = _girl(tmp_path)
    db.add_cycle_event(a["id"], date.today() - timedelta(days=30), type="anchor")
    for data, expected_step in (("cyc_fix", "survey_cycle"),
                                ("cyc_anchor_again", "survey_anchor_date")):
        state = {"step": "survey_cycle_daily", "data": {"athlete": a}}
        bot = _Bot(db, state)
        asyncio.run(bot.set_cycle_daily(_update(_FakeQ(data)), None))
        assert state["step"] == expected_step, data
        assert bot.complaints_called == 0, data


# ---------- риск R6: шаг после даты вне опроса ----------

def test_anchor_date_text_outside_survey_clears_step(tmp_path):
    db, a = _girl(tmp_path)
    target = date.today() - timedelta(days=4)
    sent = []

    async def reply(*args, **kw):
        sent.append(args[0] if args else kw.get("text"))

    msg = SimpleNamespace(text=target.strftime("%d.%m"), reply_text=reply)
    upd = SimpleNamespace(callback_query=None,
                          effective_user=SimpleNamespace(id=555), message=msg)
    state = {"step": "survey_anchor_date", "data": {}}  # вне опроса
    bot = _Bot(db, state)
    asyncio.run(bot._anchor_date_text(upd, None))
    assert db.get_last_period_start(a["id"]) == target
    assert bot.complaints_called == 0
    assert state["step"] is None                 # залипший шаг блокировал бы текст


# ---------- ✏️ Исправить день: правка должна переживать следующий опрос ----------

class _FakeMsg:
    def __init__(self, text):
        self.text = text
        self.replies = []

    async def reply_text(self, text, **kw):
        self.replies.append(text)


def _text_update(text):
    return SimpleNamespace(callback_query=None,
                           effective_user=SimpleNamespace(id=555),
                           message=_FakeMsg(text))


def test_manual_cycle_day_moves_anchor_so_next_survey_keeps_it(tmp_path):
    """Ручной день — это утверждение о дате начала. Раньше правилась только
    цифра текущего опроса, и назавтра счёт снова шёл от старого якоря
    (у #106: 16.11 вручную 40 → 17.11 автоматически 42)."""
    db, a = _girl(tmp_path)
    db.add_cycle_event(a["id"], date.today() - timedelta(days=41), type="anchor")
    state = {"step": "survey_cycle", "data": {"athlete": a}}
    bot = _Bot(db, state)
    asyncio.run(bot._manual_cycle_day(_text_update("5"), None, state, "5"))
    assert state["data"]["cycle_day"] == 5
    assert db.get_last_period_start(a["id"]) == date.today() - timedelta(days=4)
    assert bot.complaints_called == 1

    # следующий опрос: «Нет» считает от исправленного якоря, а не от старого (42)
    fresh = db.get_athlete_by_telegram_id(555)
    nxt = {"step": "survey_cycle_daily", "data": {"athlete": fresh}}
    asyncio.run(_Bot(db, nxt).set_cycle_daily(_update(_FakeQ("cyc_no")), None))
    assert nxt["data"]["cycle_day"] == 5


def test_manual_cycle_day_rejects_out_of_range_and_keeps_anchor(tmp_path):
    db, a = _girl(tmp_path)
    anchor = date.today() - timedelta(days=10)
    db.add_cycle_event(a["id"], anchor, type="anchor")
    state = {"step": "survey_cycle", "data": {"athlete": a}}
    bot = _Bot(db, state)
    asyncio.run(bot._manual_cycle_day(_text_update("0"), None, state, "0"))
    assert state["step"] == "survey_cycle"
    assert bot.complaints_called == 0
    assert db.get_last_period_start(a["id"]) == anchor
