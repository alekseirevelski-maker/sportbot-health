# -*- coding: utf-8 -*-
"""Лесенка «Мой цикл» (Review Focus №4): вычисленные дни не смешиваются
со старыми корзинами — stored видны только до первой якорной даты."""
from datetime import date

from cycle_ui import build_cycle_display

TODAY = date(2026, 10, 18)


def _stored(pairs):
    """[('YYYY-MM-DD', basket_day)] → формат get_cycle_history (DESC)."""
    return [{"survey_date": d, "cycle_day": day} for d, day in pairs]


def test_empty_events_empty_display():
    assert build_cycle_display([], _stored([("2026-10-01", 3)]), TODAY) == []


def test_computed_after_first_anchor_no_baskets():
    events = [date(2026, 9, 1), date(2026, 9, 29)]
    stored = _stored([("2026-10-05", 23), ("2026-10-03", 18), ("2026-10-01", 13)])
    rows = build_cycle_display(events, stored, TODAY)
    by_date = {d: (day, kind) for d, day, kind in rows}
    # 29.09 → якорь: 01.10 = день 3, 03.10 = день 5, 05.10 = день 7 (корзины 13/18/23 — забыты)
    assert by_date["05.10"] == (7, "computed")
    assert by_date["03.10"] == (5, "computed")
    assert by_date["01.10"] == (3, "computed")


def test_stored_before_first_anchor_kept_as_is():
    events = [date(2026, 9, 1), date(2026, 9, 29)]
    stored = _stored([("2026-08-20", 8), ("2026-08-10", 3)])
    rows = build_cycle_display(events, stored, TODAY)
    assert rows == [("10.08", 3, "stored"), ("20.08", 8, "stored")]


def test_anchor_day_itself_is_day_one():
    events = [date(2026, 9, 29)]
    stored = _stored([("2026-09-29", 23)])
    rows = build_cycle_display(events, stored, TODAY)
    assert rows == [("29.09", 1, "computed")]


def test_dates_between_anchors_use_their_segment():
    events = [date(2026, 9, 1), date(2026, 9, 29)]
    stored = _stored([("2026-09-15", 13)])  # между якорями: от 01.09 → день 15
    rows = build_cycle_display(events, stored, TODAY)
    assert rows == [("15.09", 15, "computed")]


def test_sorted_ascending_mixed_sources():
    events = [date(2026, 9, 1)]
    stored = _stored([("2026-10-05", 3), ("2026-08-20", 8)])  # DESC как в БД
    rows = build_cycle_display(events, stored, TODAY)
    assert [r[0] for r in rows] == ["20.08", "05.10"]
    assert rows[0][2] == "stored" and rows[1][2] == "computed"
    assert rows[1][1] == 35  # 01.09 → 05.10 непрерывно
