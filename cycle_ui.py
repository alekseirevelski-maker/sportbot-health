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


def values_for_day(day: int, length: int) -> dict:
    """Вычисляемые stored-значения для конкретного дня цикла (фаза без обёртки:
    за длиной — честная «лютеиновая (задержка)»)."""
    key, phase = get_cycle_phase(day, length)
    if key is None:
        name = "Лютеиновая (задержка)"
    elif key == "irregular":
        name = "Нерегулярный цикл"
    else:
        name = phase.name_ru
    return {"cycle_day": day, "cycle_length": length, "cycle_phase": name}


def survey_cycle_values(start: date, length: int, today: date) -> dict:
    """Вычисленные значения для сохранения в опрос (stored-совместимость Excel):
    день от якоря, фаза — через values_for_day."""
    return values_for_day(cycle_day(start, today), length)


def apply_anchor(db, athlete_id: int, target: date, today: date,
                 current_length: int = 28, source: str = "anchor") -> Optional[dict]:
    """Сохранить якорь (или ретро-скорректировать) и вернуть состояние цикла.

    target раньше кэша — это коррекция задним числом: correct_cycle_anchor стирает
    призрачные тапы позже. Иначе — обычная запись события. После — обучение длины.
    None, если сохранить не удалось (будущая дата / ошибка БД).
    """
    if target > today:
        return None
    cur = db.get_last_period_start(athlete_id)
    corrected = False
    if cur is not None and target < cur:
        if not db.correct_cycle_anchor(athlete_id, target):
            return None
        corrected = True
    else:
        if not db.add_cycle_event(athlete_id, target, type="anchor", source=source):
            return None
    # дата есть — значит менструация была: снимаем escape-флаг pre/skip,
    # иначе опрос вечно skip-ит цикл, а на 90-й день прилетит ложный 🚫 врачу
    db.set_menarche_status(athlete_id, None)
    learned = db.update_cycle_length_from_history(athlete_id)
    length = learned or current_length or 28
    start = db.get_last_period_start(athlete_id)
    return {"start": start, "length": length, "learned": learned,
            "corrected": corrected, **survey_cycle_values(start, length, today)}


def cycle_saved_text(res: dict) -> str:
    """Эхо-подтверждение после сохранения/коррекции якоря (одноразовый вопрос,
    ручной ввод даты): «сегодня день N · фаза», плюс про персонализацию."""
    line = (f"Сегодня *день {res['cycle_day']}* · "
            f"{_phase_label(res['cycle_day'], res['length'])}.")
    if res.get("learned"):
        line += f"\n\n📅 Цикл персонализирован: ~*{res['learned']} дн.* (по твоим датам)"
    if res.get("corrected"):
        line += "\n\nИсправила дату — счёт переписан от неё."
    return line


def build_cycle_display(events: List[date], stored: List[dict], today: date
                        ) -> List[Tuple[str, Optional[int], str]]:
    """Лесенка «Мой цикл» [(«дд.мм», день, источник)] по возрастанию дат.

    Даты с якоря — вычисляем от ближайшего предшествующего события (без корзин);
    даты до первого события — старые stored-записи как есть ('stored').
    так прошлое не смешивается с вычисляемым настоящим (Review Focus №4).
    """
    if not events:
        return []
    first = min(events)
    out = []
    for e in stored:
        raw = e.get("survey_date")
        try:
            d = date.fromisoformat(str(raw)) if isinstance(raw, str) else raw
        except ValueError:
            continue
        if d > today:  # защитник: опросы «из будущего» не показываем
            continue
        if d < first:
            out.append((d, e.get("cycle_day"), "stored"))
        else:
            anchor = max(x for x in events if x <= d)
            out.append((d, cycle_day(anchor, d), "computed"))
    out.sort(key=lambda t: t[0])
    return [(d.strftime("%d.%m"), day, kind) for d, day, kind in out]


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


def _as_date(v) -> Optional[date]:
    """Колонки БД переживают и ISO-строку, и date — нормализуем в одном месте."""
    if not v:
        return None
    return date.fromisoformat(str(v)) if isinstance(v, str) else v


def athlete_cycle_line(a: dict, today: date) -> Optional[str]:
    """Циклическая строка по строке get_all_athletes (для отчётов), None если нечего."""
    if a.get("gender") != "female":
        return None
    start = _as_date(a.get("last_period_start"))
    if not start:
        return None
    return status_line(start, a.get("cycle_length_default") or 28, today,
                       bool(a.get("cycle_length_learned")))


ALERT_REPEAT_DAYS = 7  # повтор алерта врачу — не чаще раза в неделю на девушку/тип


def cycle_alert_lines(athletes: List[dict], today: date,
                      last_sent: dict) -> List[Tuple[str, str]]:
    """Алерты «задержка» (>7 дн.) и «аменорея» (≥90 дн.) для trend-дайджеста.

    last_sent: {ключ: iso-дата последней отправки} — дедуп ALERT_REPEAT_DAYS.
    Возвращает [(ключ_для_сохранения, строка)]; статус no_anchor/pre/skip не участвуют.
    """
    out = []
    for a in athletes:
        if a.get("gender") != "female":
            continue
        start = _as_date(a.get("last_period_start"))
        if not start:
            continue
        cl = a.get("cycle_length_default") or 28
        st = cycle_status(start, cl, today)
        if st not in ("delay", "amenorrhea"):
            continue
        key = f"cycle_alert_{a['id']}_{st}"
        prev = _as_date(last_sent.get(key))
        if prev is not None and (today - prev).days < ALERT_REPEAT_DAYS:
            continue
        name = a.get("full_name") or f"#{a['id']}"
        team = a.get("team", "?")
        if st == "amenorrhea":
            line = (f"🚫 *{name}* ({team}): {(today - start).days} дн. "
                    "без менструации — RED-S, нужен осмотр")
        else:
            line = (f"⚠️ *{name}* ({team}): задержка {delay_days(start, cl, today)} дн. "
                    f"(цикл {cl} дн.)")
        out.append((key, line))
    return out
