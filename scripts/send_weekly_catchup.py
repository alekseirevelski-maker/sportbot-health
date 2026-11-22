# -*- coding: utf-8 -*-
"""Разовый догон недельной PNG-рассылки, пропущенной за неделю 21.11
(сб = сбой сервера, вс = job не запускался из-за бага дней run_daily).

По умолчанию dry-run: только список получателей, без рендера, без отправок,
без записей в БД. --live: рендер в data/ + send_photo с подписью как в bot.py.
Forbidden -> skip + лог (пометку blocked сделает штатная рассылка).
Правила 6-7 docs/DEPLOY.md; план 2026-11-22-weekly-schedule-fix.md.
"""
import asyncio
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telegram import Bot
from telegram.error import Forbidden, RetryAfter

from config import BOT_TOKEN
from database import Database
from gamification import get_rank_plain
from outage import outage_note_for_period
from weekly_report import generate_weekly_report

LIVE = "--live" in sys.argv


def first_name(a):
    """Как bot.py:_first_name — приоритет у колонки first_name."""
    fn = a.get("first_name")
    if fn:
        return fn
    full = str(a.get("full_name", ""))
    parts = full.split()
    return parts[-1] if len(parts) > 1 else full


def caption_for(a, note):
    s = a.get("survey_streak", 0)
    word = "день" if s == 1 else (
        "дня" if s % 10 in (2, 3, 4) and s % 100 not in (12, 13, 14) else "дней")
    return (
        f"📊 *Твой недельный отчёт*, {first_name(a)}!\n"
        f"Серия: 🔥 {s} {word}\n\n"
        + (f"{note}\n\n" if note else "")
        + "Увидимся в понедельник! 💪"
    )


async def send_photo_retry(bot, a, path, note):
    for attempt in (1, 2):
        try:
            with open(path, "rb") as f:
                await bot.send_photo(
                    chat_id=a["telegram_id"], photo=f,
                    caption=caption_for(a, note), parse_mode="Markdown")
            return
        except RetryAfter as e:
            if attempt == 2:
                raise
            await asyncio.sleep(e.retry_after + 1)


async def send_all(db, recipients, note):
    sent = failed = blocked = 0
    async with Bot(BOT_TOKEN) as bot:
        for a, stats in recipients:
            try:
                path = await asyncio.to_thread(
                    generate_weekly_report,
                    full_name=a["full_name"],
                    age_group=a.get("age_group", "?"),
                    team=a.get("team", "?"),
                    streak=a.get("survey_streak", 0),
                    rank=get_rank_plain(a.get("survey_streak", 0)),
                    total=a.get("total_surveys", 0),
                    stats=stats,
                    trend_7d=db.get_trend_data(a["id"], "sleep", 7),
                    prev=db.get_athlete_stats_window(a["id"], 7, 7),
                    baseline=db.get_individual_baseline(a["id"], 30),
                    trends={m: db.get_trend_data(a["id"], m, 7)
                            for m in ("readiness", "hr")},
                    pain=db.get_week_pain_summary(a["id"], 7),
                    outage_note=note,
                )
                await send_photo_retry(bot, a, path, note)
                sent += 1
                print(f"sent #{a['id']} {a['full_name']}")
            except Forbidden:
                blocked += 1
                print(f"blocked #{a['id']} — skipped (no DB write)")
            except Exception as e:
                failed += 1
                print(f"FAILED #{a['id']}: {e}")
    print(f"CATCHUP sent={sent} failed={failed} "
          f"blocked_skipped={blocked} of {len(recipients)}")


def main():
    db = Database()
    athletes = db.get_all_athletes()
    note = outage_note_for_period(
        db.get_outage_dates(), date.today() - timedelta(days=6), date.today())
    recipients = []
    for a in athletes:
        if a.get("bot_blocked"):
            continue
        stats = db.get_athlete_stats(a["id"], 7)
        if not stats.get("total_days"):
            continue
        recipients.append((a, stats))
    print(f"recipients={len(recipients)} of {len(athletes)}; outage_note={note!r}")
    for a, stats in recipients:
        print(f"  #{a['id']} {a['full_name']} "
              f"streak={a.get('survey_streak', 0)} days={stats['total_days']}")
    if not LIVE:
        print("DRY-RUN: nothing rendered/sent. Add --live to send.")
        return
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN empty (.env?)")
        sys.exit(1)
    asyncio.run(send_all(db, recipients, note))


if __name__ == "__main__":
    main()
