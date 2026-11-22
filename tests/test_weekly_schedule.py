# -*- coding: utf-8 -*-
"""Регресс: дни недельных рассылок в PTB run_daily (v20+: 0=вс … 6=сб).

Старые комментарии «0=Mon, 6=Sun» давали PNG по СУББОТАМ и сводку тренерам
по ВОСКРЕСЕНЬЯМ. Неделя 21.11: суббота = сбой сервера, воскресенье = job не
запускается → спортсмены остались без недельного отчёта. Тест пиннит
фактический cron-день через JobQueue._CRON_MAPPING, а не комментарий.
"""
from datetime import datetime
from types import SimpleNamespace

import pytz
from telegram.ext._jobqueue import JobQueue

from bot import SportHealthBot

EKB = pytz.timezone("Asia/Yekaterinburg")


class RecordingJobQueue:
    def __init__(self):
        self.calls = []

    def get_jobs_by_name(self, name):
        return []

    def run_daily(self, callback, time=None, days=None, name=None, **kw):
        self.calls.append({"name": name, "time": time, "days": days, "cb": callback})


def _schedule():
    obj = object.__new__(SportHealthBot)
    jq = RecordingJobQueue()
    obj.job_queue = jq
    obj._schedule_weekly_jobs(SimpleNamespace(job_queue=jq))
    return obj, {c["name"]: c for c in jq.calls}


def test_weekly_png_fires_on_sunday_1800_ekb():
    _, calls = _schedule()
    c = calls["weekly_report"]
    assert tuple(JobQueue._CRON_MAPPING[d] for d in c["days"]) == ("sun",)
    assert c["time"] == EKB.localize(datetime(1970, 1, 4, 18, 0)).timetz()
    assert c["time"].tzinfo.zone == "Asia/Yekaterinburg"


def test_coach_summary_fires_on_monday_0900_ekb():
    _, calls = _schedule()
    c = calls["weekly_coach_summary"]
    assert tuple(JobQueue._CRON_MAPPING[d] for d in c["days"]) == ("mon",)
    assert c["time"] == EKB.localize(datetime(1970, 1, 5, 9, 0)).timetz()
    assert c["time"].tzinfo.zone == "Asia/Yekaterinburg"


def test_callbacks_bound_to_send_methods():
    obj, calls = _schedule()
    assert calls["weekly_report"]["cb"] == obj._send_weekly_reports
    assert calls["weekly_coach_summary"]["cb"] == obj._send_weekly_coach_summary


def test_old_jobs_removed_before_reschedule():
    """Рестарты не плодят дубли: старые job с тем же имени снимаются."""
    obj = object.__new__(SportHealthBot)
    removed = []

    class OldJob:
        def schedule_removal(self):
            removed.append(True)

    class JQ(RecordingJobQueue):
        def get_jobs_by_name(self, name):
            return [OldJob()]

    jq = JQ()
    obj.job_queue = jq
    obj._schedule_weekly_jobs(SimpleNamespace(job_queue=jq))
    assert len(removed) == 2
    assert len(jq.calls) == 2
