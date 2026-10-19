# -*- coding: utf-8 -*-
"""«Написать спортсмену» для врача: к deep-link tg://user?id= добавляем
релей «Через бота». Ссылка не открывается, если у спортсменки скрыт номер
телефона и нет переписки с врачом (случай Цуркану) — бот же пишет ей всегда,
пока она не заблокировала бота."""
import asyncio
from types import SimpleNamespace

from contact_ui import contact_row
from handlers_admin import AdminMixin


class _FakeQ:
    def __init__(self, data, user_id=777):
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.markups = []

    async def answer(self, *a, **k):
        pass

    async def edit_message_text(self, text, **kw):
        self.markups.append(kw.get("reply_markup"))


def _update(q):
    return SimpleNamespace(callback_query=q,
                           effective_user=SimpleNamespace(id=q.from_user.id),
                           message=None)


class _Db:
    def get_athletes_with_complaints(self, days):
        return [{"id": 100, "telegram_id": 5122265515, "full_name": "Цуркану Габриелла",
                 "team": "ЧБК", "survey_date": "2026-10-19", "complaints": "болит колено"}]


class _Bot(AdminMixin):
    """Mixin настоящий, Telegram — фейк; kb возвращает rows как есть."""

    def __init__(self):
        self.db = _Db()

    def _is_full_access(self, uid):
        return True

    def kb(self, rows):
        return rows


# ---------- хелпер ----------

def test_contact_row_has_relay_button():
    row = contact_row({"id": 100, "telegram_id": 5122265515, "full_name": "Цуркану Габриелла"})
    assert len(row) == 2
    url_btn, relay_btn = row
    assert url_btn.url == "tg://user?id=5122265515"
    assert relay_btn.callback_data == "reply_athlete_100"
    assert "Через бота" in relay_btn.text


def test_contact_row_none_without_ids():
    assert contact_row({"id": 1, "telegram_id": None, "full_name": "X"}) is None
    assert contact_row({"telegram_id": 5, "full_name": "X"}) is None
    assert contact_row(None) is None


# ---------- поверхность: страница жалоб ----------

def test_complaints_page_shows_relay_button():
    bot = _Bot()
    q = _FakeQ("admin_complaints_page_7")
    asyncio.run(bot.admin_complaints_page(_update(q), None))
    rows = q.markups[-1]  # фейковый kb пропускает rows как есть
    flat = [b for r in rows for b in r]
    assert any(getattr(b, "callback_data", None) == "reply_athlete_100" for b in flat)
