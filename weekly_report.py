"""Воскресный персональный отчёт-картинка для спортсмена ЧБК (v3).

v2: информативность — стрелки «неделя к неделе», личная норма (30-дн медиана)
пунктиром и текстом, мини-графики готовности/пульса, блок «Боль и жалобы»,
правильное склонение «1 день / 2 дня / 5 дней».
v2.2: семантика стрелок (▲▼ = направление данных, цвет = хорошо/плохо по шкале),
«Энергия» вместо «Утомление», плейсхолдеры «—» при отсутствии данных.
v3: светлая «медицинская» тема (одобрена владельцем 22.11.2026):
настоящая эмблема ЧБК из опросника (assets/logo_cbk.png) вместо рисованного
кружка, бандл-шрифт Inter (assets/fonts, OFL) — прод и локалка рендерят
одинаково, холст 1080x2060 (крупнее шрифт = читабельнее после JPEG-сжатия
Telegram), кольцо готовности, бары сна по ДАТАМ (пропуск дня = «—» в своём
слоте), норма-чипы инлайн, авто-fit длинного имени, ранг-пилюля.

Данные: trend_7d и trends — списки пар (survey_date, value) из get_trend_data;
legacy-список голых значений тоже поддерживается (позиционно). Средние —
семантика get_athlete_stats (окно >= date('now','-7 days')), подпись «за неделю».
Без эмодзи (PIL их не рисует) — только текст и графика.
"""
import os
from datetime import date, timedelta
from PIL import Image, ImageDraw, ImageFont

# ---- размер и палитра (светлая тема) ----
W, H = 1080, 2060
BG = (247, 248, 250)        # фон страницы
HEADER = (255, 255, 255)    # шапка
CARD = (255, 255, 255)      # карточка
ACCENT = (255, 110, 40)     # оранжевый ЧБК
GREEN = (25, 150, 85)
YELLOW = (190, 130, 0)
RED = (215, 60, 60)
TEXT = (22, 32, 46)         # основной текст
GRAY = (105, 115, 132)      # вторичный текст
LINE = (226, 230, 238)      # контур карточек / разделители
TRACK = (233, 236, 242)     # фон баров / кольцо-трек
CHIP = (240, 242, 247)      # норма-чип
DASH = (150, 160, 178)      # пунктир нормы
GUIDE = (233, 236, 242)     # направляющие трендов
PILL = (255, 237, 229)      # подложка ранг-пилюли

# ---- ассеты: вычисляются при импорте от реального пути модуля ----
# (тесты патчат __file__ позже — шрифты/логотип должны браться из репо)
ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
LOGO_PATH = os.path.join(ASSETS_DIR, "logo_cbk.png")

# ---- шрифты: бандл Inter (OFL, кириллица + ▲▼). Без тихого fallback. ----
_FONT_CACHE = {}


def _font(weight, size):
    key = (weight, size)
    if key not in _FONT_CACHE:
        path = os.path.join(ASSETS_DIR, "fonts", f"Inter-{weight}.ttf")
        if not os.path.exists(path):
            raise FileNotFoundError(f"bundled font missing: {path}")
        _FONT_CACHE[key] = ImageFont.truetype(path, size)
    return _FONT_CACHE[key]


F_TITLE = _font("Bold", 52)        # СПОРТМЕД
F_SUB = _font("Regular", 28)       # подзаголовок шапки
F_NAME = _font("Bold", 60)         # имя (авто-fit вниз до 40)
F_META = _font("Regular", 30)      # возраст · команда · опросы
F_CARD_TITLE = _font("Medium", 30)  # заголовки карточек
F_BIG = _font("Bold", 60)          # число готовности в кольце
F_BODY = _font("Regular", 30)      # дельта-фраза готовности
F_SMALL = _font("Regular", 24)     # пометка о сбое, «/ 10»
F_FOOTER = _font("Regular", 24)    # футер

F_LABEL = _font("Medium", 24)      # СЕРИЯ ОПРОСОВ / ГОТОВНОСТЬ / БОЛЬ И ЖАЛОБЫ
F_STREAK = _font("Bold", 54)       # значение серии
F_RANK = _font("Medium", 28)       # ранг-пилюля
F_ROW = _font("Medium", 28)        # названия показателей в средних
F_CHIP = _font("Regular", 22)      # норма-чип
F_ARROW = _font("Regular", 26)     # стрелка дельты в средних
F_VAL = _font("Bold", 32)          # значение в средних
F_PAIN = _font("Regular", 26)      # строки жалоб
F_DAY = _font("Regular", 22)       # дни недели / значения баров / «—»
F_NORM = _font("Regular", 26)      # «норма X.X» у сна, «средняя за неделю»
F_HINT = _font("Regular", 22)      # «шкала 0–10, выше — лучше»
F_NOTE_SMALL = _font("Regular", 20)  # длинная пометка о сбое
F_GOOD = _font("Regular", 28)      # «жалоб не было — отлично!»
F_TREND = _font("Regular", 24)     # подписи и последние значения трендов


def _plural(n, one, few, many):
    """Склонение: 1 день / 2 дня / 5 дней."""
    n = abs(int(n or 0))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return few
    return many


def _center(draw, text, y, font, fill):
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    draw.text(((W - tw) // 2, y), text, fill=fill, font=font)


def _card(draw, y0, h):
    draw.rounded_rectangle([60, y0, W - 60, y0 + h], radius=28, fill=CARD,
                           outline=LINE, width=2)


def _score_color(v, invert=False):
    """Цвет по шкале 7=хорошо (вверх = лучше). invert — для пульса (вверх = хуже)."""
    if v is None:
        return GRAY
    if invert:
        return GREEN if v <= 60 else (YELLOW if v <= 70 else RED)
    return GREEN if v >= 6 else (YELLOW if v >= 4 else RED)


def _dashed_line(draw, x0, y, x1, color, dash=14, gap=9, width=3):
    x = x0
    while x < x1:
        draw.line([(x, y), (min(x + dash, x1), y)], fill=color, width=width)
        x += dash + gap


def _delta_text(cur, prev_val, invert=False):
    """Сравнение с прошлой неделей: (текст стрелки, цвет).
    Стрелка — направление данных (▲ рост / ▼ падение), цвет — хорошо/плохо
    по шкале метрики (invert — пульс: падение = хорошо)."""
    if cur is None or prev_val is None:
        return "—", GRAY
    d = cur - prev_val
    if abs(d) < 0.15:
        return "—", GRAY
    arrow = "▲" if d > 0 else "▼"
    good = (d > 0) if not invert else (d < 0)
    return f"{arrow} {d:+.1f}", GREEN if good else RED


def _delta_sentence(cur, prev_val, invert=False):
    """Дельта готовности словами: «▲ +0.6 к прошлой неделе»."""
    if cur is None or prev_val is None:
        return "нет данных за прошлую неделю", GRAY
    d = cur - prev_val
    if abs(d) < 0.15:
        return "без изменений к прошлой неделе", GRAY
    arrow = "▲" if d > 0 else "▼"
    good = (d > 0) if not invert else (d < 0)
    return f"{arrow} {d:+.1f} к прошлой неделе", GREEN if good else RED


def _dated_values(items, window):
    """Пары (survey_date, value) → 7 слотов окна today-6..today по ДАТАМ.

    Пропущенный день = None в своём слоте (рисуется «—»). Legacy-список
    голых значений (без дат) раскладывается позиционно — обратная совместимость.
    """
    items = list(items or [])
    if items and isinstance(items[0], (tuple, list)) and len(items[0]) == 2:
        by_date = {str(d): v for d, v in items}
        return [by_date.get(iso) for iso in window]
    return (items + [None] * 7)[:7]


def _sparkline(draw, vals, x0, x1, y0, y1, color):
    """Мини-график по 7 слотам дат: пропуски не соединяются линией."""
    clean = [v for v in vals if v is not None]
    if not clean:
        return
    mn, mx = min(clean), max(clean)
    rng = (mx - mn) or 1
    pts = []
    for i, v in enumerate(vals):
        if v is None:
            continue
        px = x0 + (x1 - x0) * (i + 0.5) / 7.0
        py = y1 - (y1 - y0) * (v - mn) / rng
        pts.append((px, py))
    for i in range(7):
        gx = x0 + (x1 - x0) * (i + 0.5) / 7.0
        draw.line([(gx, y0), (gx, y1)], fill=GUIDE, width=2)
    if len(pts) >= 2:
        draw.line(pts, fill=color, width=5)
    for px, py in pts:
        draw.ellipse([px - 6, py - 6, px + 6, py + 6], fill=CARD,
                     outline=color, width=4)


def _avg_row(draw, y, label, val, invert, norm_val, prev_val):
    """Строка средних: label | норма-чип инлайн | стрелка | точка | значение."""
    draw.text((100, y), label, fill=TEXT, font=F_ROW)
    lb = draw.textbbox((0, 0), label, font=F_ROW)
    lw = lb[2] - lb[0]
    ns = (f"норма ~{int(round(norm_val))}" if invert
          else f"норма {norm_val:.1f}") if norm_val is not None else "норма —"
    nb = draw.textbbox((0, 0), ns, font=F_CHIP)
    nw = nb[2] - nb[0]
    cx0 = 100 + lw + 16
    draw.rounded_rectangle([cx0, y - 2, cx0 + nw + 32, y + 34], radius=17, fill=CHIP)
    draw.text((cx0 + 16, y + 4), ns, fill=GRAY, font=F_CHIP)
    arrow_s, arrow_c = _delta_text(val, prev_val, invert)
    draw.text((W - 320, y + 6), arrow_s, fill=arrow_c, font=F_ARROW, anchor="rm")
    col = _score_color(val, invert)
    draw.ellipse([W - 260, y + 8, W - 240, y + 28], fill=col)
    val_s = f"{val:.1f}" if isinstance(val, (int, float)) else "—"
    draw.text((W - 100, y + 2), val_s, fill=col, font=F_VAL, anchor="rm")


def _pain_card(draw, pain, compact=False):
    """Блок «Боль и жалобы».

    compact=True (есть пометка о сбое на y=1934): карточка 150px и максимум
    1 строка жалобы + «и ещё» (y=1858), чтобы не накладываться на пометку.
    Без пометки: карточка 190px, 2 строки + «и ещё» (y=1894).
    """
    _card(draw, 1760, 150 if compact else 190)
    draw.text((100, 1784), "БОЛЬ И ЖАЛОБЫ", fill=ACCENT, font=F_LABEL)
    if not pain:
        draw.text((100, 1826), "За неделю жалоб не было — отлично!", fill=GREEN, font=F_GOOD)
        return
    if compact:
        detail = pain[:1] if len(pain) > 2 else pain[:2]
    else:
        detail = pain[:2] if len(pain) > 3 else pain[:3]
    for i, p in enumerate(detail):
        d = str(p.get("survey_date", ""))[5:]
        parts = []
        nrs = p.get("pain_nrs") or 0
        if nrs > 0:
            loc = p.get("pain_location") or ""
            parts.append(f"{loc} {int(nrs)}/10" if loc else f"{int(nrs)}/10")
            if p.get("pain_on_game"):
                parts.append("на игре")
        if p.get("illness_flag"):
            parts.append(str(p["illness_flag"]))
        if p.get("analgesics"):
            parts.append("обезболивающие")
        line = f"{d}: " + ", ".join(parts)
        col = RED if (p.get("analgesics") or nrs >= 5) else YELLOW
        draw.text((100, 1822 + i * 36), line, fill=col, font=F_PAIN)
    rest = len(pain) - len(detail)
    if rest > 0:
        draw.text((100, 1822 + len(detail) * 36),
                  f"…и ещё {rest} {_plural(rest, 'день', 'дня', 'дней')} с жалобами",
                  fill=GRAY, font=F_PAIN)


def generate_weekly_report(full_name: str, age_group: str, team: str,
                           streak: int, rank: str, total: int,
                           stats: dict, trend_7d: list,
                           prev: dict = None, baseline: dict = None,
                           trends: dict = None, pain: list = None,
                           outage_note: str = None) -> str:
    prev = prev or {}
    baseline = baseline or {}
    bl_median = baseline.get("median") or {}
    median_hr = baseline.get("median_hr")
    trends = trends or {}

    today = date.today()
    d0 = today - timedelta(days=6)
    window = [(d0 + timedelta(days=i)).isoformat() for i in range(7)]

    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # ===== шапка: настоящая эмблема ЧБК из опросника =====
    draw.rectangle([0, 0, W, 220], fill=HEADER)
    draw.line([0, 220, W, 220], fill=ACCENT, width=6)
    logo = Image.open(LOGO_PATH).convert("RGBA")  # нет файла — FileNotFoundError
    sm = logo.resize((100, 100), Image.LANCZOS)
    img.paste(sm, (60, 60), sm)
    draw.text((190, 64), "СПОРТМЕД", fill=TEXT, font=F_TITLE)
    draw.text((192, 134), f"Недельный отчёт · {d0:%d.%m}–{today:%d.%m}",
              fill=GRAY, font=F_SUB)

    # ===== имя (авто-fit: длинные ФИО не вылезают за холст) + мета =====
    size = 60
    name_font = _font("Bold", size)
    while size > 40:
        bb = draw.textbbox((0, 0), full_name, font=name_font)
        if bb[2] <= W - 120:
            break
        size -= 2
        name_font = _font("Bold", size)
    draw.text((60, 256), full_name, fill=TEXT, font=name_font)
    meta = f"{age_group}  ·  {team}  ·  {total} {_plural(total, 'опрос', 'опроса', 'опросов')}"
    draw.text((60, 336), meta, fill=GRAY, font=F_META)

    # ===== серия + ранг-пилюля =====
    _card(draw, 400, 150)
    draw.text((100, 424), "СЕРИЯ ОПРОСОВ", fill=GRAY, font=F_LABEL)
    draw.text((100, 460), f"{streak} {_plural(streak, 'день', 'дня', 'дней')}",
              fill=YELLOW, font=F_STREAK)
    bb = draw.textbbox((0, 0), rank, font=F_RANK)
    tw = bb[2] - bb[0]
    px0 = W - 100 - tw - 48
    draw.rounded_rectangle([px0, 448, W - 100, 508], radius=30, fill=PILL,
                           outline=ACCENT, width=2)
    draw.text((px0 + 24, 462), rank, fill=ACCENT, font=F_RANK)

    # ===== готовность: кольцо прогресса =====
    readiness = stats.get("avg_readiness")
    has_r = isinstance(readiness, (int, float))
    r_c = (GREEN if readiness >= 7 else (YELLOW if readiness >= 5 else RED)) if has_r else GRAY
    _card(draw, 570, 280)
    draw.text((100, 594), "ГОТОВНОСТЬ ЗА НЕДЕЛЮ", fill=GRAY, font=F_LABEL)
    cx, cy, r = 240, 730, 95
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=TRACK, width=20)
    if has_r:
        draw.arc([cx - r, cy - r, cx + r, cy + r], start=-90,
                 end=-90 + 360 * max(0.0, min(1.0, readiness / 10.0)), fill=r_c, width=20)
        draw.text((cx, cy - 10), f"{readiness:.1f}", fill=TEXT, font=F_BIG, anchor="mm")
    else:
        draw.text((cx, cy - 10), "—", fill=GRAY, font=F_BIG, anchor="mm")
    draw.text((cx, cy + 40), "/ 10", fill=GRAY, font=F_SMALL, anchor="mm")
    draw.text((420, 660), "средняя за неделю", fill=GRAY, font=F_NORM)
    dtext, dcol = _delta_sentence(readiness, prev.get("avg_readiness"))
    draw.text((420, 706), dtext, fill=dcol, font=F_BODY)
    draw.text((420, 756), "шкала 0–10, выше — лучше", fill=GRAY, font=F_HINT)

    # ===== сон: бары по ДАТАМ, значения одной строкой, пунктир нормы =====
    norm_sleep = bl_median.get("sleep")
    if trend_7d:
        _card(draw, 870, 270)
        draw.text((100, 894), "Качество сна — 7 дней", fill=TEXT, font=F_CARD_TITLE)
        if norm_sleep is not None:
            draw.text((W - 100, 900), f"норма {norm_sleep:.1f}", fill=GRAY,
                      font=F_NORM, anchor="rm")
        top, bot = 960, 1090
        slot = 880 / 7.0
        bw = 72
        vals = _dated_values(trend_7d, window)
        for i in range(7):
            x = 100 + i * slot + (slot - bw) / 2
            draw.rounded_rectangle([x, top, x + bw, bot], radius=10, fill=TRACK)
        if norm_sleep is not None:
            ny = bot - norm_sleep / 10.0 * (bot - top)
            _dashed_line(draw, 100, ny, 980, DASH)
        for i, v in enumerate(vals):
            x = 100 + i * slot + (slot - bw) / 2
            wd = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"][(d0 + timedelta(days=i)).weekday()]
            draw.text((x + bw / 2, 1100), wd, fill=GRAY, font=F_DAY, anchor="mt")
            if isinstance(v, (int, float)):
                h = max(6, v / 10.0 * (bot - top))
                draw.rounded_rectangle([x, bot - h, x + bw, bot], radius=10,
                                       fill=_score_color(v))
                draw.text((x + bw / 2, 952), f"{v:.1f}", fill=TEXT, font=F_DAY, anchor="mb")
            else:
                draw.text((x + bw / 2, (top + bot) / 2), "—", fill=GRAY,
                          font=F_DAY, anchor="mm")

    # ===== тренды: готовность и пульс (подписи над графиками) =====
    trend_r = _dated_values(trends.get("readiness"), window)
    trend_hr = _dated_values(trends.get("hr"), window)
    if any(v is not None for v in trend_r) or any(v is not None for v in trend_hr):
        _card(draw, 1160, 250)
        draw.text((100, 1184), "Тренд недели", fill=TEXT, font=F_CARD_TITLE)
        if any(v is not None for v in trend_r):
            draw.text((100, 1230), "Готовность", fill=GRAY, font=F_TREND)
            lr = [v for v in trend_r if v is not None]
            draw.text((520, 1230), f"{lr[-1]:.1f}", fill=GREEN, font=F_TREND, anchor="rm")
            _sparkline(draw, trend_r, 100, 520, 1280, 1380, GREEN)
        if any(v is not None for v in trend_hr):
            draw.text((560, 1230), "Пульс покоя", fill=GRAY, font=F_TREND)
            lh = [v for v in trend_hr if v is not None]
            draw.text((980, 1230), f"{lh[-1]:.0f}", fill=YELLOW, font=F_TREND, anchor="rm")
            _sparkline(draw, trend_hr, 560, 980, 1280, 1380, YELLOW)

    # ===== средние показатели =====
    rows = [
        ("Сон", stats.get("avg_sleep"), False, bl_median.get("sleep"),
         prev.get("avg_sleep")),
        ("Энергия", stats.get("avg_fatigue"), False, bl_median.get("fatigue"),
         prev.get("avg_fatigue")),
        ("Боль (7=нет)", stats.get("avg_soreness"), False, bl_median.get("soreness"),
         prev.get("avg_soreness")),
        ("Пульс покоя", stats.get("avg_hr"), True, median_hr,
         prev.get("avg_hr")),
    ]
    _card(draw, 1430, 310)
    draw.text((100, 1454), "Средние за неделю", fill=TEXT, font=F_CARD_TITLE)
    y = 1506
    for label, val, invert, norm_val, prev_val in rows:
        _avg_row(draw, y, label, val, invert, norm_val, prev_val)
        y += 62

    # ===== боль и жалобы =====
    _pain_card(draw, pain or [], compact=bool(outage_note))

    # ===== пометка о сбое сервера (над футером; без эмодзи — PIL их не рисует) =====
    if outage_note:
        note = outage_note.replace("⚠️", "").strip()
        font = F_SMALL
        bbox = draw.textbbox((0, 0), note, font=font)
        if bbox[2] - bbox[0] > W - 120:
            font = F_NOTE_SMALL
        _center(draw, note, 1934, font, YELLOW)

    # ===== футер =====
    draw.line([0, 2005, W, 2005], fill=LINE, width=2)
    _center(draw, "Береги восстановление — оно решает результат!", 2020,
            F_FOOTER, GRAY)

    out_dir = os.path.join(os.path.dirname(__file__), "data")
    os.makedirs(out_dir, exist_ok=True)
    safe_name = "".join(c if c.isalnum() else "_" for c in full_name)
    out_path = os.path.join(out_dir, f"weekly_{safe_name}_{date.today()}.png")
    img.save(out_path, "PNG")
    return out_path


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from database import Database
    from gamification import get_rank_plain
    db = Database()
    a = db.get_athlete_by_id(int(sys.argv[1])) if len(sys.argv) > 1 else None
    if not a:
        print("Usage: python weekly_report.py <athlete_id>")
        sys.exit(1)
    stats = db.get_athlete_stats(a["id"], 7)
    prev = db.get_athlete_stats_window(a["id"], 7, 7)
    baseline = db.get_individual_baseline(a["id"], 30)
    trends = {m: db.get_trend_data(a["id"], m, 7) for m in ("readiness", "hr")}
    pain = db.get_week_pain_summary(a["id"], 7)
    path = generate_weekly_report(
        a["full_name"], a["age_group"], a.get("team", "?"),
        a.get("survey_streak", 0), get_rank_plain(a.get("survey_streak", 0)),
        a.get("total_surveys", 0),
        stats, db.get_trend_data(a["id"], "sleep", 7),
        prev=prev, baseline=baseline, trends=trends, pain=pain)
    print(path)
