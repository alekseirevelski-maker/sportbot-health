# -*- coding: utf-8 -*-
"""День сбоя сервера не прерывает серию опросов (инцидент 21.11.2026).

Сервер потерял сеть утром 21.11 и лежал до 22:34 ЕКБ; опросы прошли только
3 спортсмена. Алгоритм save_survey считал 21.11 обычным пропуском и сбрасывал
серию в 1 каждому, кто ответил 22-го. Решение владельца: день сбоя
перешагивается (не обрывает серию), но в счётчик не идёт — серия = число
реальных ответов. Реестр дней сбоя — bot_settings.outage_dates.
"""
from datetime import date, timedelta

from database import Database, compute_streak

TODAY = date.today()
OUTAGE = TODAY - timedelta(days=1)  # «вчера» — день сбоя в сценариях
TEAM = "ЧБК-Челябинская область"


def _make_db(tmp_path, name="outage.db"):
    return Database(db_path=str(tmp_path / name))


def _athlete(db, tg_id, name="Тестовый Спортсмен"):
    db.register_athlete(tg_id, f"user{tg_id}", name, "U17", TEAM)
    return db.get_athlete_by_telegram_id(tg_id)["id"]


def _seed(db, aid, back_start, back_end):
    """Опросы за [today-back_start .. today-back_end] в обход save_survey."""
    d = TODAY - timedelta(days=back_start)
    stop = TODAY - timedelta(days=back_end)
    while d <= stop:
        db.conn.execute(
            "INSERT INTO daily_wellness (athlete_id, survey_date) VALUES (?, ?)",
            (aid, d.isoformat()),
        )
        d += timedelta(days=1)
    db.conn.commit()


def test_outage_day_bridges_gap(tmp_path):
    """Сценарий 22.11: 30 дней подряд до 20-го, 21-го сбой, ответ сегодня → 31."""
    db = _make_db(tmp_path)
    aid = _athlete(db, 900001)
    _seed(db, aid, 31, 2)
    db.set_outage_dates([OUTAGE])

    db.save_survey(aid, {"sleep": 5})

    assert db.get_athlete_by_telegram_id(900001)["survey_streak"] == 31


def test_real_survey_on_outage_day_counts(tmp_path):
    """Те трое, кто ответил 21-го: их опрос — обычный день серии (2+сегодня=3)."""
    db = _make_db(tmp_path)
    aid = _athlete(db, 900002)
    _seed(db, aid, 2, 1)  # позавчера + вчера (реальный опрос в день сбоя)
    db.set_outage_dates([OUTAGE])

    db.save_survey(aid, {"sleep": 5})

    assert db.get_athlete_by_telegram_id(900002)["survey_streak"] == 3


def test_regular_gap_still_breaks_streak(tmp_path):
    """Пропуск НЕ в день сбоя по-прежнему обрывает серию."""
    db = _make_db(tmp_path)
    aid = _athlete(db, 900003)
    _seed(db, aid, 31, 3)  # пропуск позавчера — не сбой
    db.set_outage_dates([OUTAGE])

    db.save_survey(aid, {"sleep": 5})

    assert db.get_athlete_by_telegram_id(900003)["survey_streak"] == 1


def test_several_outage_days_bridged(tmp_path):
    db = _make_db(tmp_path)
    aid = _athlete(db, 900004)
    _seed(db, aid, 31, 4)  # 28 дней до today-4
    db.set_outage_dates([OUTAGE, TODAY - timedelta(days=2), TODAY - timedelta(days=3)])

    db.save_survey(aid, {"sleep": 5})

    assert db.get_athlete_by_telegram_id(900004)["survey_streak"] == 29


def test_outage_dates_roundtrip_and_default(tmp_path):
    db = _make_db(tmp_path)
    assert db.get_outage_dates() == set()
    db.set_outage_dates([OUTAGE])
    assert db.get_outage_dates() == {OUTAGE}


def test_garbage_outage_setting_failsafe(tmp_path):
    """Битое значение не роняет save_survey и не «лечит» пропуски."""
    db = _make_db(tmp_path)
    aid = _athlete(db, 900005)
    _seed(db, aid, 5, 2)
    db.set_setting("outage_dates", "не json")
    assert db.get_outage_dates() == set()

    db.save_survey(aid, {"sleep": 5})

    assert db.get_athlete_by_telegram_id(900005)["survey_streak"] == 1


def test_compute_streak_anchor_at_last_survey():
    """Пересчёт после сбоя: якорь — последняя дата опроса, не сегодня."""
    dates = [TODAY - timedelta(days=3), TODAY - timedelta(days=2)]
    assert compute_streak(dates, TODAY - timedelta(days=2), {OUTAGE}) == 2


def test_compute_streak_outage_not_counted():
    dates = [TODAY - timedelta(days=2), TODAY]
    assert compute_streak(dates, TODAY, {OUTAGE}) == 2  # не 3: сбой не плюсует


def test_recalc_repairs_broken_streak(tmp_path):
    """Сценарий Калетина: ответил сегодня ДО фикса — серия сломалась в 1."""
    db = _make_db(tmp_path)
    aid = _athlete(db, 900006, "Калетин Тестовый")
    _seed(db, aid, 31, 2)
    db.save_survey(aid, {"sleep": 5})  # без реестра сбоя → серия 1
    assert db.get_athlete_by_telegram_id(900006)["survey_streak"] == 1
    total_before = db.get_athlete_by_telegram_id(900006)["total_surveys"]

    db.set_outage_dates([OUTAGE])
    changes = db.recalc_streaks(dry_run=True)
    assert db.get_athlete_by_telegram_id(900006)["survey_streak"] == 1, "dry-run не пишет"
    assert [(c["id"], c["old"], c["new"]) for c in changes] == [(aid, 1, 31)]

    db.recalc_streaks(dry_run=False)
    a = db.get_athlete_by_telegram_id(900006)
    assert a["survey_streak"] == 31
    assert a["total_surveys"] == total_before, "total_surveys не трогаем"
    assert db.recalc_streaks(dry_run=False) == [], "идемпотентно"


def test_recalc_keeps_unrelated_gaps(tmp_path):
    """Обычный пропуск (не сбой) пересчёт не «лечит»."""
    db = _make_db(tmp_path)
    aid = _athlete(db, 900007)
    _seed(db, aid, 31, 3)
    db.save_survey(aid, {"sleep": 5})
    db.set_outage_dates([OUTAGE])

    assert db.recalc_streaks(dry_run=False) == []
    assert db.get_athlete_by_telegram_id(900007)["survey_streak"] == 1


def test_recalc_accepts_explicit_outage_without_writing_registry(tmp_path):
    """Dry-run скрипта с --outage: считаем с переданными датами, реестр не пишем."""
    db = _make_db(tmp_path)
    aid = _athlete(db, 900008)
    _seed(db, aid, 31, 2)
    db.save_survey(aid, {"sleep": 5})  # серия сломалась в 1

    changes = db.recalc_streaks(dry_run=True, outage_dates={OUTAGE})

    assert [(c["id"], c["old"], c["new"]) for c in changes] == [(aid, 1, 31)]
    assert db.get_outage_dates() == set(), "реестр не изменён"
    assert db.get_athlete_by_telegram_id(900008)["survey_streak"] == 1
