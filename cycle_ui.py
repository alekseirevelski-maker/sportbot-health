# -*- coding: utf-8 -*-
"""UI-помощники цикла: данные/тексты/валидация без Telegram-объектов.

Handler'ы тонкие: берут эти функции и раскладывают по InlineKeyboardMarkup.
Арифметику дня/задержки не дублируем — всё через cycle_engine.
"""
import re
from datetime import date
from typing import List, Optional, Tuple

from cycle_engine import (
    AMENORRHEA_DAYS, cycle_day, delay_days, next_period_eta,
    status as cycle_status,
)
from cycle_medicine import get_cycle_phase

Button = Tuple[str, str]  # (text, callback_data)

MONTHS_RU = ("янв", "фев", "мар", "апр", "май", "июн",
             "июл", "авг", "сен", "окт", "ноя", "дек")

_DATE_RE = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\s*$")


def parse_date_ru(text: str, today: date) -> Optional[date]:
    """«ДД.ММ» или «ДД.ММ.ГГГГ» → дата. Год без явного указания угадывается:
    если дата ещё не наступила — берём прошлый год. Окно — AMENORRHEA_DAYS
    (дальше 90 дней назад скрывать смысл нет: это уже аменорея). Будущее → None."""
    m = _DATE_RE.match(text or "")
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
    try:
        dt = date(int(y) if y else today.year, mo, d)
    except ValueError:
        return None
    if not y and dt > today:
        dt = dt.replace(year=today.year - 1)
    if dt > today or (today - dt).days > AMENORRHEA_DAYS:
        return None
    return dt


def anchor_text(target: date) -> str:
    """Человекочитаемая дата якоря: «5 окт» (для эха-подтверждения «Записано: …»)."""
    return f"{target.day} {MONTHS_RU[target.month - 1]}"


def anchor_buttons(today: date) -> List[List[Button]]:
    """Одноразовый вопрос «когда начались последние?»: 6 дат-ярлыков + ручной
    ввод + два вежливых отказа. Точность ±1 день не критична — якорь уточняется
    ближайшим событием «начались»."""
    return [
        [("🩸 Сегодня", "anchor_0"), ("Вчера", "anchor_1")],
        [("2 дня назад", "anchor_2"), ("3 дня назад", "anchor_3")],
        [("4 дня назад", "anchor_4"), ("5 дней назад", "anchor_5")],
        [("✏️ Ввести дату (ДД.ММ)", "anchor_custom")],
        [("🌱 Пока не было", "anchor_pre")],
        [("❓ Нет данных", "anchor_skip")],
    ]


def _phase_label(day: int, length: int) -> str:
    """Название фазы для строки/сообщения. За пределами длины фаза честная —
    «лютеиновая (задержка)», а не None: день не оборачивается."""
    key, phase = get_cycle_phase(day, length)
    if key is None:
        return "🌙 Лютеиновая (задержка)"
    if key == "irregular":
        return "🌡 Цикл нерегулярный"
    return f"{phase.emoji} {phase.name_ru}"


def daily_cycle_keyboard(cycle_status_key: str) -> List[List[Button]]:
    """Ежедневный один тап. При задержке/аменорее сверху — кнопка отметить
    пропущенное начало (событие «Да» сегодня не было нажато вовремя)."""
    rows = []
    if cycle_status_key in ("delay", "amenorrhea"):
        rows.append([("🗓 Отметить начало", "cyc_anchor_again")])
    rows.append([("🩸 Да, начались", "cyc_start")])
    rows.append([("Нет", "cyc_no")])
    rows.append([("✏️ Исправить день", "cyc_fix")])
    return rows


def daily_cycle_text(name: str, start: date, length: int, today: date) -> str:
    """Вопрос ежедневного шага цикла: текущее состояние, чтобы «Да/Нет»
    было информированным. При аменорее фазу не врать — её уже нет."""
    day = cycle_day(start, today)
    st = cycle_status(start, length, today)
    eta = next_period_eta(start, length)
    delay = delay_days(start, length, today)
    if st == "amenorrhea":
        head = (f"🌸 *{name}, цикл*\n"
                f"День {day} · без менструации {(today - start).days} дн.\n")
    else:
        head = (f"🌸 *{name}, цикл*\n"
                f"День {day} · {_phase_label(day, length)}\n"
                f"Следующие: ~{eta.strftime('%d.%m')}\n")
    lines = [head]
    if st == "amenorrhea":
        lines.append("🚫 Это признак RED-S. Если начались — отметь «Да», "
                     "если нет — стоит сказать врачу.")
    elif st == "delay":
        lines.append(f"⚠️ Задержка {delay} дн. Если начались — отметь «Да».")
    lines.append("Месячные начались сегодня?")
    return "\n".join(lines)


def status_line(start: Optional[date], length: int, today: date,
                learned: bool = False) -> Optional[str]:
    """Строка цикла для отчётов (врач/тренер/дайджест).
    None — показывать нечего (нет якоря; 'pre'/'skip' сюда не доходят)."""
    st = cycle_status(start, length, today)
    if st == "no_anchor":
        return None
    if st == "amenorrhea":
        return (f"🚫 {(today - start).days} дн. без менструации — "
                "RED-S, нужен взгляд врача")
    day = cycle_day(start, today)
    line = (f"День {day} · {_phase_label(day, length)} · "
            f"след. ~{next_period_eta(start, length).strftime('%d.%m')}")
    delay = delay_days(start, length, today)
    if st == "delay":
        line += f" · ⚠️ задержка {delay} дн."
    line += f" · цикл {length} дн. ({'по истории' if learned else 'вручную'})"
    return line
