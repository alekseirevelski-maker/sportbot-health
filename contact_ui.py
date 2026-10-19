# -*- coding: utf-8 -*-
"""Кнопки контакта врача со спортсменом.

Deep-link tg://user?id= не открывается, если у спортсмена скрыт номер телефона
и нет с врачом общей переписки (приватность Telegram). Релей «Через бота»
(callback reply_athlete_<id> → _admin_reply_start в bot.py) работает всегда,
пока спортсмен не заблокировал бота.
"""
from telegram import InlineKeyboardButton


def contact_row(athlete):
    """Строка кнопок для врача: 💬 прямая ссылка + 📩 через бота.

    Возвращает None, если id/telegram_id нет — писать всё равно некому.
    """
    if not athlete:
        return None
    athlete_id = athlete.get("id")
    tg_id = athlete.get("telegram_id")
    if not athlete_id or not tg_id:
        return None
    name = athlete.get("full_name", "?")
    return [
        InlineKeyboardButton(f"💬 Написать: {name}", url=f"tg://user?id={tg_id}"),
        InlineKeyboardButton("📩 Через бота", callback_data=f"reply_athlete_{athlete_id}"),
    ]
