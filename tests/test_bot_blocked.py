# -*- coding: utf-8 -*-
"""Автоотписка заблокировавших бота (bot_blocked).

Прод: 61 ошибка «bot was blocked by the user» — рассылки продолжают долбить
спортсменов, заблокировавших бота. Флаг bot_blocked отключает рассылки таким
спортсменам, уведомляет врача (один раз на переходе 0→1) и сбрасывается,
когда спортсмен снова пишет /start.
"""
import asyncio
from types import SimpleNamespace

from telegram.error import Forbidden

from database import Database


# ==================== DB-часть ====================

def test_bot_blocked_roundtrip(tmp_path):
    from database import Database
    db = Database(db_path=str(tmp_path / "t.db"))
    db.register_athlete(777001, "u", "Тест Тестов", "U17", "ЧБК")
    aid = db.get_athlete_by_telegram_id(777001)["id"]
    assert db.get_bot_blocked(aid) == 0
    db.set_bot_blocked(aid, 1)
    assert db.get_bot_blocked(aid) == 1
    assert db.get_all_athletes()[0].get("bot_blocked") == 1  # поле видно в выдаче


def test_bot_blocked_migration_idempotent(tmp_path):
    """Повторное открытие БД (миграция ALTER-in-try) не ломает данные."""
    path = str(tmp_path / "t2.db")
    db = Database(db_path=path)
    db.register_athlete(777002, "u2", "Второй Тестов", "U17", "ЧБК")
    aid = db.get_athlete_by_telegram_id(777002)["id"]
    db.set_bot_blocked(aid, 1)
    db2 = Database(db_path=path)
    assert db2.get_bot_blocked(aid) == 1


# ==================== Рассылка напоминаний ====================

ADMIN_ID = 999


class FakeBot:
    """Запоминает отправки; для заблокированных chat_id кидает Forbidden."""

    def __init__(self, blocked_chat_ids=()):
        self.blocked = set(blocked_chat_ids)
        self.attempted = []   # все chat_id, куда пробовали отправить
        self.sent = []        # (chat_id, text) — успешно

    async def send_message(self, chat_id, text, **kw):
        self.attempted.append(chat_id)
        if chat_id in self.blocked:
            raise Forbidden("bot was blocked by the user")
        self.sent.append((chat_id, text))

    async def send_photo(self, chat_id, photo=None, caption=None, **kw):
        self.attempted.append(chat_id)
        if chat_id in self.blocked:
            raise Forbidden("bot was blocked by the user")
        self.sent.append((chat_id, caption or ""))


def _make_bot_obj(db, fake):
    """SportHealthBot без конструктора/токена: только нужные атрибуты."""
    from bot import SportHealthBot
    obj = object.__new__(SportHealthBot)
    obj.db = db
    obj._bot = fake  # _send_admin шлёт через self._bot
    return obj


def _seed_two_athletes(db):
    db.register_athlete(111, "blocker", "Блокеров Блок", "U17", "ЧБК")
    db.register_athlete(222, "good", "Хорошева Хоря", "U17", "ЧБК")
    return (db.get_athlete_by_telegram_id(111)["id"],
            db.get_athlete_by_telegram_id(222)["id"])


def _run_reminder(db, fake, monkeypatch):
    import bot as bot_module
    monkeypatch.setattr(bot_module, "TESTING", False)
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {ADMIN_ID})
    obj = _make_bot_obj(db, fake)
    asyncio.run(obj._send_daily_reminder(SimpleNamespace(bot=fake)))
    return obj


def test_reminder_forbidden_sets_flag_and_notifies_admin(tmp_path, monkeypatch):
    """Реальный цикл _send_daily_reminder: Forbidden → флаг только блокировщику,
    второму отправлено, ровно одно уведомление врачу."""
    db = Database(db_path=str(tmp_path / "t3.db"))
    aid_blocker, aid_good = _seed_two_athletes(db)
    fake = FakeBot(blocked_chat_ids={111})

    _run_reminder(db, fake, monkeypatch)

    assert db.get_bot_blocked(aid_blocker) == 1
    assert db.get_bot_blocked(aid_good) == 0
    # второму спортсмену напоминание доставлено
    assert any(cid == 222 for cid, _ in fake.sent)
    # блокировщику ничего не доставлено
    assert not any(cid == 111 for cid, _ in fake.sent)
    # ровно одно уведомление врачу о блокировке
    admin_alerts = [t for cid, t in fake.sent
                    if cid == ADMIN_ID and "заблокировал бота" in t]
    assert len(admin_alerts) == 1
    assert "#%d" % aid_blocker in admin_alerts[0]


def test_reminder_skips_already_blocked(tmp_path, monkeypatch):
    """bot_blocked==1 → пропускаем ДО отправки, без повторных уведомлений."""
    db = Database(db_path=str(tmp_path / "t4.db"))
    aid_blocker, aid_good = _seed_two_athletes(db)
    db.set_bot_blocked(aid_blocker, 1)
    fake = FakeBot(blocked_chat_ids={111})

    _run_reminder(db, fake, monkeypatch)

    assert 111 not in fake.attempted  # даже не пробовали отправить
    assert any(cid == 222 for cid, _ in fake.sent)
    assert not any("заблокировал бота" in t for _, t in fake.sent)


def test_reminder_admin_notified_once_on_transition_only(tmp_path, monkeypatch):
    """Повторный прогон (флаг уже 1, сбросили дедуп) — врач не спамятся."""
    db = Database(db_path=str(tmp_path / "t5.db"))
    aid_blocker, _ = _seed_two_athletes(db)
    fake1 = FakeBot(blocked_chat_ids={111})
    _run_reminder(db, fake1, monkeypatch)
    # снимаем дедуп-метку и временно возвращаем блокировщика в рассылку,
    # но флаг остаётся 1 → пропуск до отправки, уведомлений нет
    db.set_setting("reminder_sent_date", "")
    fake2 = FakeBot(blocked_chat_ids={111})
    _run_reminder(db, fake2, monkeypatch)
    assert not any("заблокировал бота" in t for _, t in fake2.sent)
    assert db.get_bot_blocked(aid_blocker) == 1


# ==================== Сброс флага в /start ====================

def test_cmd_start_resets_flag_and_notifies_admin(tmp_path, monkeypatch):
    import bot as bot_module
    monkeypatch.setattr(bot_module, "TESTING", False)
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {ADMIN_ID})
    db = Database(db_path=str(tmp_path / "t6.db"))
    db.register_athlete(333, "back", "Вернувшаяся Вера", "U17", "ЧБК")
    aid = db.get_athlete_by_telegram_id(333)["id"]
    db.set_bot_blocked(aid, 1)

    fake = FakeBot()
    obj = _make_bot_obj(db, fake)
    menu_shown = []

    async def fake_menu(update, ctx):
        menu_shown.append(True)

    async def noop(*a, **k):
        pass

    obj.show_main_menu = fake_menu
    obj.restore_user_state = noop
    obj._looks_like_name = lambda s: False

    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=333, username="back", first_name=None),
        callback_query=None,
        message=SimpleNamespace(reply_text=noop),
    )
    asyncio.run(obj.cmd_start(update, None))

    assert db.get_bot_blocked(aid) == 0
    assert any(cid == ADMIN_ID and "снова с ботом" in t for cid, t in fake.sent)
    assert menu_shown == [True]


def test_cmd_start_no_notification_when_not_blocked(tmp_path, monkeypatch):
    import bot as bot_module
    monkeypatch.setattr(bot_module, "TESTING", False)
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {ADMIN_ID})
    db = Database(db_path=str(tmp_path / "t7.db"))
    db.register_athlete(444, "normal", "Обычный Олег", "U17", "ЧБК")

    fake = FakeBot()
    obj = _make_bot_obj(db, fake)

    async def fake_menu(update, ctx):
        pass

    async def noop(*a, **k):
        pass

    obj.show_main_menu = fake_menu
    obj.restore_user_state = noop
    obj._looks_like_name = lambda s: False

    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=444, username="normal", first_name=None),
        callback_query=None,
        message=SimpleNamespace(reply_text=noop),
    )
    asyncio.run(obj.cmd_start(update, None))

    assert not any("снова с ботом" in t for _, t in fake.sent)
