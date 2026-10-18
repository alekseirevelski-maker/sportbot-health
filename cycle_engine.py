# -*- coding: utf-8 -*-
"""Цикл: арифметика дня/прогноза/задержки. Чистые функции, без БД и Telegram.

Модель: день цикла — непрерывный счёт от события «месячные начались»
(якорь). Сам себя в 1 не переносит: это делает только новое событие.
"""
from datetime import date, timedelta
from typing import Optional

DELAY_THRESHOLD_DAYS = 7      # задержка: полных дней сверх прогноза
AMENORRHEA_DAYS = 90          # RED-S-флаг: дней без менструации от якоря
DAY_CORRECTION_MARGIN = 30    # ручная корректировка дня: допуск сверх длины


def cycle_day(start: date, today: date) -> int:
    """День цикла: 1 = день начала. Не оборачивается, даже если день > длины."""
    if today < start:
        raise ValueError("today раньше якоря")
    return (today - start).days + 1


def next_period_eta(start: date, length: int) -> date:
    """Прогноз начала следующих месячных."""
    return start + timedelta(days=length)


def delay_days(start: date, length: int, today: date) -> int:
    """Полных дней сверх прогноза (0, если срок ещё не прошёл)."""
    return max(0, (today - next_period_eta(start, length)).days)


def status(start: Optional[date], length: int, today: date) -> str:
    """'no_anchor' | 'amenorrhea' | 'delay' | 'ok' (по убыванию тяжести)."""
    if start is None:
        return "no_anchor"
    if (today - start).days >= AMENORRHEA_DAYS:
        return "amenorrhea"
    if delay_days(start, length, today) > DELAY_THRESHOLD_DAYS:
        return "delay"
    return "ok"


def cycle_step_for(gender: Optional[str], start: Optional[date],
                   menarche_status: Optional[str]) -> str:
    """Какой шаг цикла показать в опросе: 'skip' | 'anchor' | 'daily'."""
    if gender != "female":
        return "skip"
    if menarche_status in ("pre", "skip"):
        return "skip"
    return "daily" if start is not None else "anchor"


def parse_day_input(text: str, length: int) -> Optional[int]:
    """День для ручной корректировки: 1..length+DAY_CORRECTION_MARGIN."""
    try:
        d = int(str(text).strip())
    except (TypeError, ValueError):
        return None
    if 1 <= d <= length + DAY_CORRECTION_MARGIN:
        return d
    return None
