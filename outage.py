"""Пометки о сбоях сервера для отчётов и экранов.

День сбоя (bot_settings.outage_dates) — день, когда опросы не собирались
по вине сервера (инцидент 21.11.2026). Серия спортсменов такие дни
перешагивает; отчёты обязаны помечать их, чтобы пропуск не выглядел
как прогул спортсмена.
"""
from datetime import timedelta
from typing import Optional

NOTE_TEMPLATE = "⚠️ Сбой сервера: {days} — опросы не собирались, серии не прерваны"


def outage_note_for_period(outage_dates, start, end) -> Optional[str]:
    """Строка-примечание для отчёта, накрывающего [start, end], или None."""
    days = sorted(d for d in outage_dates if start <= d <= end)
    if not days:
        return None
    return NOTE_TEMPLATE.format(days=", ".join(d.strftime("%d.%m") for d in days))


def recent_outage_note(outage_dates, today, window: int = 3) -> Optional[str]:
    """Примечание для экранов «на сегодня»: сбой сегодня/вчера/позавчера."""
    return outage_note_for_period(outage_dates, today - timedelta(days=window - 1), today)
