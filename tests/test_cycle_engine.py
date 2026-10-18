# -*- coding: utf-8 -*-
"""cycle_engine: чистая арифметика цикла (день/прогноз/задержка/статус/шаг).

Баг, против которого пишется: корзинный ручной ввод дня цикла давал
разброс 3/8/13/18/23/28 и разорванные фазы. День теперь считается от
якоря-события и никогда не оборачивается сам.
"""
import pytest
from datetime import date, timedelta

from cycle_engine import (
    cycle_day, next_period_eta, delay_days, status,
    cycle_step_for, parse_day_input,
    DELAY_THRESHOLD_DAYS, AMENORRHEA_DAYS, DAY_CORRECTION_MARGIN,
)


# ---------- cycle_day ----------

def test_cycle_day_same_day_is_1():
    assert cycle_day(date(2026, 10, 1), date(2026, 10, 1)) == 1


def test_cycle_day_plus_two():
    assert cycle_day(date(2026, 10, 1), date(2026, 10, 3)) == 3


def test_cycle_day_no_wrapping_past_length():
    # старт 01.09, today 18.10 = 48-й день; цикл 28 — но день НЕ оборачивается
    assert cycle_day(date(2026, 9, 1), date(2026, 10, 18)) == 48


def test_cycle_day_future_start_rejected():
    with pytest.raises(ValueError):
        cycle_day(date(2026, 10, 20), date(2026, 10, 18))


# ---------- next_period_eta / delay_days ----------

def test_next_period_eta():
    assert next_period_eta(date(2026, 10, 1), 28) == date(2026, 10, 29)


def test_delay_days_counts_over_eta():
    # start=01.09, L=28 → eta=29.09; today=01.10 → 2 дня сверх прогноза
    assert delay_days(date(2026, 9, 1), 28, date(2026, 10, 1)) == 2


def test_delay_days_zero_before_eta():
    assert delay_days(date(2026, 9, 1), 28, date(2026, 9, 28)) == 0
    assert delay_days(date(2026, 9, 1), 28, date(2026, 9, 29)) == 0  # ровно на eta


# ---------- status ----------

def test_status_no_anchor():
    assert status(None, 28, date(2026, 10, 18)) == 'no_anchor'

def test_status_ok_within_cycle():
    assert status(date(2026, 10, 1), 28, date(2026, 10, 21)) == 'ok'  # день 21


def test_status_delay_after_seven_overdue():
    # L=28: день 34 → delay=33-28=5 → ок; день 37 → delay 8 → 'delay'
    assert status(date(2026, 9, 1), 28, date(2026, 10, 4)) == 'ok'   # delay 5
    assert status(date(2026, 9, 1), 28, date(2026, 10, 8)) == 'delay'  # delay 9


def test_status_delay_boundary_is_strictly_seven():
    # delay=7 ровно → ещё 'ok'; delay=8 → 'delay' (спека: задержка >7)
    assert status(date(2026, 9, 1), 28, date(2026, 9, 1) + timedelta(days=35)) == 'ok'
    assert status(date(2026, 9, 1), 28, date(2026, 9, 1) + timedelta(days=36)) == 'delay'


def test_status_89_days_not_amenorrhea_yet():
    # пограничный 89-й день — ещё не аменорея, но уже delay (регрессия на off-by-one)
    assert status(date(2026, 7, 1), 28, date(2026, 7, 1) + timedelta(days=89)) == 'delay'


@pytest.mark.parametrize("offset", [AMENORRHEA_DAYS, AMENORRHEA_DAYS + 10])
def test_status_amenorrhea_beats_delay(offset):
    start = date(2026, 7, 1)
    today = start + timedelta(days=offset)  # 90 полных дней без менструации
    assert status(start, 28, today) == 'amenorrhea'


def test_constants_values():
    assert DELAY_THRESHOLD_DAYS == 7
    assert AMENORRHEA_DAYS == 90
    assert DAY_CORRECTION_MARGIN == 30


# ---------- cycle_step_for ----------

@pytest.mark.parametrize("gender,start,mstatus,expected", [
    (None,   None,          None,     'skip'),
    ('male', None,          None,     'skip'),
    ('female', None,        None,     'anchor'),
    ('female', None,        'pre',    'skip'),
    ('female', None,        'skip',   'skip'),
    ('female', date(2026, 10, 1), None, 'daily'),
])
def test_cycle_step_for_matrix(gender, start, mstatus, expected):
    assert cycle_step_for(gender, start, mstatus) == expected


# ---------- parse_day_input ----------

@pytest.mark.parametrize("text,length,expected", [
    ("18", 28, 18),
    ("1", 28, 1),
    ("58", 28, 58),      # 28+30 предельный
    ("59", 28, None),    # > length + margin
    ("0", 28, None),
    ("abc", 28, None),
    ("", 28, None),
    (" 14 ", 28, 14),     # пробелы допустимы
])
def test_parse_day_input(text, length, expected):
    assert parse_day_input(text, length) == expected
