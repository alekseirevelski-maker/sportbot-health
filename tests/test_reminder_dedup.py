# -*- coding: utf-8 -*-
"""Task 5: баг условия времени напоминания + дедуп ДО цикла рассылки.

1) `_reminder_should_fire_now(now_local, hour, minute)` — старое условие
   `hour >= H and minute >= M` давало False для 21:10 vs 20:30 (минуты 10 < 30),
   из-за чего бот при рестарте после времени напоминания не доотправлял рассылку.
2) `reminder_sent_date` должен записываться В НАЧАЛЕ `_send_daily_reminder`
   (сразу после дедуп-проверки): крах/рестарт посреди цикла не приводит
   к повторной рассылке тем, кто уже получил сообщение.
"""
import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

from database import Database

ADMIN_ID = 999


class Boom(BaseException):
    """Авария посреди рассылки. BaseException — не глотается `except Exception`
    в цикле, как настоящий крах процесса."""


class FakeBot:
    def __init__(self, fail_on_call=None):
        self.attempted = []
        self.sent = []
        self._calls = 0
        self.fail_on_call = fail_on_call

    async def send_message(self, chat_id, text, **kw):
        self._calls += 1
        self.attempted.append(chat_id)
        if self.fail_on_call == self._calls:
            raise Boom("crash mid-loop")
        self.sent.append((chat_id, text))


# ==================== Item 1: чистая функция времени ====================

def test_should_fire_when_time_passed():
    """21:10 vs 20:30 — время прошло → True (старый код давал False: 10 < 30)."""
    from bot import _reminder_should_fire_now
    assert _reminder_should_fire_now(datetime(2026, 11, 6, 21, 10), 20, 30) is True


def test_should_not_fire_before_time():
    from bot import _reminder_should_fire_now
    assert _reminder_should_fire_now(datetime(2026, 11, 6, 20, 10), 20, 30) is False


def test_should_fire_exactly_at_time():
    from bot import _reminder_should_fire_now
    assert _reminder_should_fire_now(datetime(2026, 11, 6, 20, 30), 20, 30) is True


# ==================== Item 2: дедуп до цикла ====================

def _make_obj(db, fake):
    from bot import SportHealthBot
    obj = object.__new__(SportHealthBot)
    obj.db = db
    obj._bot = fake
    return obj


def test_dedup_written_before_send_loop(tmp_path, monkeypatch):
    """Крах на середине цикла → reminder_sent_date УЖЕ записан,
    повторный вызов пропускается (дублей нет)."""
    import bot as bot_module
    import pytz
    monkeypatch.setattr(bot_module, "TESTING", False)
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {ADMIN_ID})
    monkeypatch.setattr(bot_module, "REMINDER_HOUR", 20)

    db = Database(db_path=str(tmp_path / "dedup.db"))
    db.register_athlete(111, "a1", "Первый Спорт", "U17", "ЧБК")
    db.register_athlete(222, "a2", "Второй Спорт", "U17", "ЧБК")

    fake = FakeBot(fail_on_call=2)  # крах на втором спортсмене
    obj = _make_obj(db, fake)
    with pytest.raises(Boom):
        asyncio.run(obj._send_daily_reminder(SimpleNamespace(bot=fake)))

    today = datetime.now(pytz.timezone(bot_module.REMINDER_TZ)).strftime("%Y-%m-%d")
    assert db.get_setting("reminder_sent_date") == today

    # повторный вызов в тот же день — полный пропуск, ни одной отправки
    fake2 = FakeBot()
    obj2 = _make_obj(db, fake2)
    asyncio.run(obj2._send_daily_reminder(SimpleNamespace(bot=fake2)))
    assert fake2.attempted == []
