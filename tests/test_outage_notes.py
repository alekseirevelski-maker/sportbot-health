# -*- coding: utf-8 -*-
"""Пометка «сбой сервера» во всех отчётах (инцидент 21.11.2026).

Отчёты, накрывающие день сбоя, обязаны его помечать: пропуск не должен
выглядеть как прогул спортсмена. Проверяются хелперы outage.py и все
поверхности: дашборд, сводка тренера, недельный PNG, текстовый отчёт, Excel.
"""
import asyncio
import io
from datetime import date, timedelta
from types import SimpleNamespace

from database import Database

TODAY = date.today()
OUTAGE = TODAY - timedelta(days=1)
OUTAGE_ISO = OUTAGE.isoformat()
TEAM = "ЧБК-Челябинская область"


def _make_db(tmp_path, name="notes.db"):
    return Database(db_path=str(tmp_path / name))


def _athlete(db, tg_id, name="Тестовый Спортсмен"):
    db.register_athlete(tg_id, f"user{tg_id}", name, "U17", TEAM)
    return db.get_athlete_by_telegram_id(tg_id)["id"]


def _seed(db, aid, back_start, back_end):
    d = TODAY - timedelta(days=back_start)
    stop = TODAY - timedelta(days=back_end)
    while d <= stop:
        db.conn.execute(
            "INSERT INTO daily_wellness (athlete_id, survey_date) VALUES (?, ?)",
            (aid, d.isoformat()),
        )
        d += timedelta(days=1)
    db.conn.commit()


# ==================== хелперы outage.py ====================

def test_note_lists_outage_days_in_period():
    from outage import outage_note_for_period
    note = outage_note_for_period({OUTAGE}, TODAY - timedelta(days=7), TODAY)
    assert note is not None
    assert OUTAGE.strftime("%d.%m") in note
    assert "сбой" in note.lower()


def test_no_note_when_outage_outside_period():
    from outage import outage_note_for_period
    assert outage_note_for_period({OUTAGE}, TODAY, TODAY + timedelta(days=6)) is None


def test_note_sorts_multiple_days():
    from outage import outage_note_for_period
    note = outage_note_for_period({TODAY, OUTAGE}, TODAY - timedelta(days=7), TODAY)
    assert note.index(OUTAGE.strftime("%d.%m")) < note.index(TODAY.strftime("%d.%m"))


def test_recent_note_window_three_days():
    from outage import recent_outage_note
    assert recent_outage_note({OUTAGE}, TODAY) is not None  # вчера — в окне
    old = TODAY - timedelta(days=4)
    assert recent_outage_note({old}, TODAY) is None  # 4 дня назад — вне окна


# ==================== дашборд «На сегодня» ====================

def test_dashboard_shows_outage_banner(tmp_path):
    from dashboard import build_today_dashboard
    db = _make_db(tmp_path)
    _athlete(db, 901001)
    db.set_outage_dates([OUTAGE])

    text, _buttons = build_today_dashboard(db, 999, db.get_all_athletes(), TODAY)

    assert "сбой" in text.lower()


def test_dashboard_no_banner_without_outage(tmp_path):
    from dashboard import build_today_dashboard
    db = _make_db(tmp_path)
    _athlete(db, 901002)

    text, _buttons = build_today_dashboard(db, 999, db.get_all_athletes(), TODAY)

    assert "сбой" not in text.lower()


# ==================== недельная сводка тренерам ====================

def _bot_obj(db):
    from bot import SportHealthBot
    obj = object.__new__(SportHealthBot)
    obj.db = db
    return obj


def test_coach_summary_notes_outage_and_adjusts_expected(tmp_path):
    db = _make_db(tmp_path)
    _athlete(db, 902001, "Первая Тестовая")
    _athlete(db, 902002, "Вторая Тестовая")
    db.set_outage_dates([OUTAGE])

    text = _bot_obj(db)._coach_summary_text([TEAM])

    assert "сбой" in text.lower()
    # 2 атлета × (7 − 1 день сбоя) = 12 ожидаемых опросов, не 14
    assert "из 12" in text


def test_coach_summary_without_outage_unchanged(tmp_path):
    db = _make_db(tmp_path)
    _athlete(db, 902003, "Первая Тестовая")
    _athlete(db, 902004, "Вторая Тестовая")

    text = _bot_obj(db)._coach_summary_text([TEAM])

    assert "сбой" not in text.lower()
    assert "из 14" in text


# ==================== недельный PNG ====================

def _png_bytes(monkeypatch, tmp_path, note):
    import weekly_report
    monkeypatch.setattr(weekly_report, "__file__", str(tmp_path / "weekly_report.py"))
    path = weekly_report.generate_weekly_report(
        full_name="Тестовый Спортсмен", age_group="U17", team="ЧБК",
        streak=31, rank="Мониторинг-машина", total=40,
        stats={"avg_readiness": 7.0, "avg_sleep": 6.0},
        trend_7d=[6, 5, 6, 5, 6, 5, 6],
        outage_note=note,
    )
    with open(path, "rb") as f:
        return f.read()


def test_weekly_png_renders_outage_note(tmp_path, monkeypatch):
    from outage import outage_note_for_period
    note = outage_note_for_period({OUTAGE}, TODAY - timedelta(days=6), TODAY)
    with_note = _png_bytes(monkeypatch, tmp_path, note)
    without = _png_bytes(monkeypatch, tmp_path, None)
    assert with_note != without, "пометка должна быть видна на картинке"
    from PIL import Image
    img = Image.open(io.BytesIO(with_note))
    assert img.size == (1080, 2060)


def test_weekly_png_note_does_not_overlap_pain_block(tmp_path, monkeypatch):
    """Пометка на y=1934 не должна накладываться на блок боли (карточка до y=1910).

    При пометке блок «Боль и жалобы» сжимается: 1 строка жалобы + «и ещё» (y=1858).
    Без пометки: 2 строки + «и ещё» на y=1894. Строки боли левые (draw.text),
    пометка и футер — через _center; шпионим все вызовы ImageDraw.text.
    """
    import weekly_report
    from PIL import ImageDraw
    from outage import outage_note_for_period
    calls = []
    orig_text = ImageDraw.ImageDraw.text

    def spy(self, xy, text, fill=None, font=None, anchor=None, **kw):
        calls.append((str(text), xy[0], xy[1]))
        return orig_text(self, xy, text, fill=fill, font=font, anchor=anchor, **kw)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", spy)
    monkeypatch.setattr(weekly_report, "__file__", str(tmp_path / "weekly_report.py"))
    pain = [{"survey_date": (TODAY - timedelta(days=i)).isoformat(),
             "pain_nrs": 4, "pain_location": "колено"} for i in range(1, 5)]
    note = outage_note_for_period({OUTAGE}, TODAY - timedelta(days=6), TODAY)
    base = dict(full_name="Тестовый Спортсмен", age_group="U17", team="ЧБК",
                streak=31, rank="Мониторинг-машина", total=40,
                stats={"avg_readiness": 7.0, "avg_sleep": 6.0},
                trend_7d=[6, 5, 6, 5, 6, 5, 6])

    weekly_report.generate_weekly_report(pain=pain, outage_note=note, **base)
    pain_rows = [(t, y) for t, x, y in calls if t[:2].isdigit() and "-" in t[:5]]
    assert len(pain_rows) == 1 and pain_rows[0][1] == 1822, pain_rows
    assert any("и ещё" in t and y == 1858 for t, x, y in calls), calls
    assert not any(y >= 1910 and ("и ещё" in t or t[:2].isdigit()) for t, x, y in calls), \
        "строки боли не выходят за карточку (до y=1910)"
    assert any("сбой" in t.lower() and y == 1934 for t, x, y in calls), "пометка на месте"

    calls.clear()
    weekly_report.generate_weekly_report(pain=pain, **base)
    pain_rows = [(t, y) for t, x, y in calls if t[:2].isdigit() and "-" in t[:5]]
    assert len(pain_rows) == 2, pain_rows
    assert any("и ещё" in t and y == 1894 for t, x, y in calls), "без пометки 3 строки"
    assert not any(y == 1934 for t, x, y in calls), "пометки нет — строка не рисуется"


# ==================== текстовый отчёт по атлету ====================

def test_athlete_text_report_marks_outage(tmp_path, monkeypatch):
    import reports
    db = _make_db(tmp_path)
    aid = _athlete(db, 903001)
    _seed(db, aid, 5, 2)
    db.set_outage_dates([OUTAGE])
    monkeypatch.setattr(reports, "Database", lambda: db)
    # код делает Path(__file__).parent — стаб возвращает «файл» внутри tmp_path
    monkeypatch.setattr(reports, "Path", lambda _p: tmp_path / "reports.py")
    (tmp_path / "data").mkdir(exist_ok=True)

    path = reports.ReportGenerator().generate_athlete_report(aid, days=7)

    text = open(path, encoding="utf-8").read()
    assert "сбой" in text.lower()
    assert OUTAGE_ISO in text, "день сбоя отмечен в ежедневных данных"


# ==================== Excel-отчёт ====================

class _FakeQuery:
    def __init__(self, data):
        self.data = data
        self.from_user = SimpleNamespace(id=1)
        self.message = self
        self.edited = None
        self.doc = None
        self.caption = None

    async def answer(self):
        pass

    async def edit_message_text(self, text, **kw):
        self.edited = text

    async def reply_document(self, document, filename=None, caption=None, **kw):
        self.doc = document.getvalue()
        self.caption = caption


def _make_harness(db):
    """ReportsMixin без конструктора бота (паттерн scripts/attic/_gen_report2.py)."""
    from handlers_reports import ReportsMixin

    class Harness(ReportsMixin):
        def _is_admin_or_coach(self, uid):
            return True

        def _is_full_access(self, uid):
            return True

        def _coach_teams(self, uid):
            return []

        def get_state(self, uid):
            return {"data": {"report_team": "all"}}

        def kb(self, rows):
            return None

        def _add_logo_to_ws(self, ws, anchor=None, size=80):
            return None

    h = Harness()
    h.db = db
    return h


def test_xlsx_report_marks_outage_everywhere(tmp_path):
    from openpyxl import load_workbook
    db = _make_db(tmp_path)
    aid = _athlete(db, 904001)
    _seed(db, aid, 5, 2)
    db.save_survey(aid, {"sleep": 6, "fatigue": 5, "soreness": 5})
    db.set_outage_dates([OUTAGE])
    q = _FakeQuery("report_period_7")
    upd = SimpleNamespace(callback_query=q)

    asyncio.run(_make_harness(db).generate_xlsx_report(upd, None))

    assert q.doc, "отчёт должен быть отправлен"
    assert "сбой" in (q.caption or "").lower()
    wb = load_workbook(io.BytesIO(q.doc))
    svodka = " ".join(str(c.value) for row in wb["Сводка"].iter_rows() for c in row if c.value)
    assert "сбой" in svodka.lower()
    by_days = str(wb["По дням"].cell(row=2, column=1).value)
    assert "сбой" in by_days.lower()
    team_ws = wb[TEAM[:31]]
    headers = [str(c.value) for row in team_ws.iter_rows(min_row=1, max_row=12) for c in row if c.value]
    assert any("⚠" in h and OUTAGE.strftime("%m-%d") in h for h in headers), \
        f"колонка дня сбоя в матрице должна быть помечена: {headers}"
