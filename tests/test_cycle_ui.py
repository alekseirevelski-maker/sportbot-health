# -*- coding: utf-8 -*-
"""cycle_ui: чистые помощники шагов опроса (парсер даты, клавиатуры, строки).

Telegram-объектов здесь нет — только данные/тексты/валидация, всё тестируется.
"""
from datetime import date, timedelta

import pytest

from cycle_ui import (
    parse_date_ru, anchor_buttons, anchor_text,
    daily_cycle_keyboard, daily_cycle_text, status_line,
)

TODAY = date(2026, 10, 18)


# ---------- parse_date_ru ----------

@pytest.mark.parametrize("text,today,expected", [
    ("05.10", TODAY, date(2026, 10, 5)),
    ("5.10",  TODAY, date(2026, 10, 5)),          # одиночная цифра ок
    ("01.08", TODAY, date(2026, 8, 1)),
    ("20.07", TODAY, date(2026, 7, 20)),          # ровно 90 дней назад — последний день окна
    ("19.07", TODAY, None),                       # 91 — за окном
    ("20.12", date(2027, 1, 15), date(2026, 12, 20)),  # будущее → сдвиг года, в окне
    ("25.12", TODAY, None),                       # будущее → сдвиг → вне окна
    ("20.07.2026", TODAY, date(2026, 7, 20)),     # полный формат
    ("15.06.2026", TODAY, None),                  # полный, но дальше 90 дн.
    ("01.01.2099", TODAY, None),                  # явное будущее
    ("18.01", TODAY, None),                       # после сдвига года вне окна (273 дн.)
])
def test_parse_date_ru(text, today, expected):
    assert parse_date_ru(text, today) == expected


@pytest.mark.parametrize("junk", ["abc", "18", "90.01", "00.05", "", "  ", "12.13.12", "5/10"])
def test_parse_date_ru_junk(junk):
    assert parse_date_ru(junk, TODAY) is None


# ---------- anchor_buttons / anchor_text ----------

def _flatten(rows):
    return [b for row in rows for b in row]


def test_anchor_buttons_shape():
    btns = _flatten(anchor_buttons(TODAY))
    assert len(btns) == 9
    cbs = [cb for _, cb in btns]
    assert len(set(cbs)) == 9
    assert all(cb.startswith("anchor_") for cb in cbs)
    for n in range(6):
        assert f"anchor_{n}" in cbs
    for extra in ("anchor_custom", "anchor_pre", "anchor_skip"):
        assert extra in cbs


def test_anchor_text_month_ru():
    assert "5 окт" in anchor_text(date(2026, 10, 5))


# ---------- daily_cycle_keyboard ----------

def _flatten_cb(rows):
    return [cb for row in rows for _, cb in row]


def test_daily_keyboard_ok_three_buttons():
    cbs = _flatten_cb(daily_cycle_keyboard("ok"))
    assert cbs == ["cyc_start", "cyc_no", "cyc_fix"]


@pytest.mark.parametrize("st", ["delay", "amenorrhea"])
def test_daily_keyboard_alerts_add_anchor_button(st):
    cbs = _flatten_cb(daily_cycle_keyboard(st))
    assert cbs[0] == "cyc_anchor_again"
    assert cbs[1:] == ["cyc_start", "cyc_no", "cyc_fix"]


# ---------- daily_cycle_text ----------

def test_daily_cycle_text_ok():
    start = date(2026, 10, 5)
    txt = daily_cycle_text("Анна", start, 28, TODAY)
    assert "День 14" in txt                      # 05.10 → день 1, 18.10 → 14
    assert "~02.11" in txt                       # eta = 05.10 + 28
    assert "⚠️" not in txt


def test_daily_cycle_text_delay():
    start = date(2026, 9, 1)
    txt = daily_cycle_text("Анна", start, 28, TODAY)  # delay = 18.10 − 29.09 = 19
    assert "День 48" in txt
    assert "19" in txt and "⚠️" in txt


def test_daily_cycle_text_amenorrhea():
    start = date(2026, 7, 1)
    txt = daily_cycle_text("Анна", start, 28, TODAY)   # 109 дней
    assert "🚫" in txt
    assert "109" in txt                                # счёт не обрывается
    assert "Лютеиновая" not in txt                     # мелочь №4: фаза при аменорее неверна


def test_daily_cycle_text_no_double_newlines():
    txt = daily_cycle_text("Анна", date(2026, 10, 5), 28, TODAY)
    assert "\n\n\n" not in txt                         # косметика join


# ---------- status_line ----------

def test_status_line_none_without_anchor():
    assert status_line(None, 28, TODAY) is None


def test_status_line_ok():
    start = date(2026, 10, 5)
    line = status_line(start, 28, TODAY)
    assert "День 14" in line
    assert "~02.11" in line
    assert "задержка" not in line
    assert "цикл 28" in line and "(вручную)" in line   # learned=False по умолчанию


def test_status_line_delay_learned():
    start = date(2026, 9, 1)
    line = status_line(start, 28, TODAY, learned=True)
    assert "⚠️ задержка 19 дн." in line
    assert "(по истории)" in line


def test_status_line_amenorrhea():
    start = date(2026, 7, 1)
    line = status_line(start, 28, TODAY)
    assert "🚫" in line and "RED-S" in line


def test_status_line_phase_beyond_length_is_luteal():
    """День 48 при длине 28: get_cycle_phase вернёт None — строка не должна ломаться."""
    line = status_line(date(2026, 9, 1), 28, TODAY)
    assert line is not None and "🌙" in line
