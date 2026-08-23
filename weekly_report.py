"""Воскресный персональный отчёт-картинка для спортсмена ЧБК (v2.2).

v2: информативность — стрелки «неделя к неделе», личная норма (30-дн медиана)
пунктиром и текстом, мини-графики готовности/пульса, блок «Боль и жалобы»,
правильное склонение «1 день / 2 дня / 5 дней».
v2.2: исправлены наложения блоков (готовность/сон), семантика стрелок
(▲▼ = направление данных, цвет = хорошо/плохо по шкале), «Энергия» вместо
«Утомление», плейсхолдеры «—» при отсутствии данных.

Дизайн: тёмный «спортивный» фон, брендовый оранжевый ЧБК, карточки.
Без эмодзи (PIL их не рисует) — только текст и графика.
"""
import os
from datetime import date
from PIL import Image, ImageDraw, ImageFont

# ---- размер и палитра ----
W, H = 900, 1640
BG = (16, 24, 40)           # тёмно-синий фон
HEADER = (20, 36, 60)       # шапка
CARD = (28, 42, 68)         # карточка
ACCENT = (255, 110, 40)     # оранжевый ЧБК (#FF6600)
ACCENT_DARK = (200, 80, 20)
GREEN = (80, 210, 120)
YELLOW = (255, 200, 60)
RED = (255, 90, 90)
WHITE = (255, 255, 255)
GRAY = (150, 165, 190)
LINE = (50, 70, 105)

# ---- шрифты: DejaVu (кириллица). На Windows для локального теста — arial. ----
def _font(bold, size):
    candidates = []
    if bold:
        candidates = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                      "C:/Windows/Fonts/arialbd.ttf", "arialbd.ttf"]
    else:
        candidates = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                      "C:/Windows/Fonts/arial.ttf", "arial.ttf"]
    for c in candidates:
        try:
            return ImageFont.truetype(c, size)
        except Exception:
            continue
    return ImageFont.load_default()

F_TITLE = _font(True, 46)
F_SUB = _font(False, 26)
F_NAME = _font(True, 58)
F_META = _font(False, 28)
F_CARD_TITLE = _font(True, 34)
F_BIG = _font(True, 64)
F_BODY = _font(False, 30)
F_SMALL = _font(False, 24)
F_FOOTER = _font(False, 26)


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


def _right(draw, text, x_right, y, font, fill):
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    draw.text((x_right - tw, y), text, fill=fill, font=font)


def _card(draw, y0, h):
    draw.rounded_rectangle([55, y0, W - 55, y0 + h], radius=24, fill=CARD,
                           outline=LINE, width=2)


def _score_color(v, invert=False):
    """Цвет по шкале 7=хорошо (вверх = лучше). invert — для пульса (вверх = хуже)."""
    if v is None:
        return GRAY
    if invert:
        return GREEN if v <= 60 else (YELLOW if v <= 70 else RED)
    return GREEN if v >= 6 else (YELLOW if v >= 4 else RED)


def _bar(draw, x0, y, x1, frac, color):
    """Прогресс-бар с закруглением."""
    draw.rounded_rectangle([x0, y, x1, y + 22], radius=11, fill=(45, 60, 90))
    w = int((x1 - x0) * max(0.0, min(1.0, frac)))
    if w > 4:
        draw.rounded_rectangle([x0, y, x0 + w, y + 22], radius=11, fill=color)


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


def _sparkline(draw, values, x0, y0, x1, y1, color=ACCENT, norm=None):
    clean = [v for v in values if v is not None]
    if not clean:
        return
    mn, mx = min(clean), max(clean)
    rng = (mx - mn) or 1
    pts = []
    for i, v in enumerate(values[-7:]):
        if v is None:
            continue
        t = (i + 0.5) / 7.0
        px = x0 + (x1 - x0) * t
        normv = (v - mn) / rng
        py = y1 - (y1 - y0) * normv
        pts.append((px, py))
    # направляющие
    for gx in range(x0, x1 + 1, (x1 - x0) // 6):
        draw.line([(gx, y0), (gx, y1)], fill=(40, 55, 85), width=2)
    # личная норма — пунктир
    if norm is not None:
        ny = y1 - (y1 - y0) * ((norm - mn) / rng)
        ny = max(y0, min(y1, ny))
        _dashed_line(draw, x0, ny, x1, (120, 140, 175))
    if len(pts) >= 2:
        draw.line(pts, fill=color, width=6)
    for px, py in pts:
        draw.ellipse([px - 7, py - 7, px + 7, py + 7], fill=WHITE,
                     outline=color, width=4)


def _avg_row(draw, y, label, val, invert, norm_val, prev_val):
    """Строка средних: label | норма (под) | стрелка | точка | значение."""
    col = _score_color(val, invert)
    val_s = f"{val:.1f}" if isinstance(val, (int, float)) else "—"
    draw.text((110, y), label, fill=WHITE, font=F_BODY)
    if norm_val is not None:
        norm_s = (f"норма ~{int(round(norm_val))}" if invert
                  else f"норма {norm_val:.1f}")
        draw.text((110, y + 34), norm_s, fill=GRAY, font=F_SMALL)
    else:
        draw.text((110, y + 34), "—", fill=GRAY, font=F_SMALL)
    arrow_s, arrow_c = _delta_text(val, prev_val, invert)
    if arrow_s:
        draw.text((W - 370, y), arrow_s, fill=arrow_c, font=F_BODY)
    draw.ellipse([W - 260, y + 14, W - 240, y + 34], fill=col)
    draw.text((W - 210, y), val_s, fill=col, font=F_BODY)


def _pain_card(draw, pain):
    """Блок «Боль и жалобы» (компактно, максимум 3 строки)."""
    _card(draw, 1420, 125)
    _center(draw, "БОЛЬ И ЖАЛОБЫ", 1443, F_CARD_TITLE, ACCENT)
    if not pain:
        _center(draw, "За неделю жалоб не было — отлично!", 1487, F_BODY, GREEN)
        return
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
        _center(draw, line, 1481 + i * 32, F_SMALL, col)
    if len(pain) > 3:
        rest = len(pain) - 3
        _center(draw, f"…и ещё {rest} {_plural(rest, 'день', 'дня', 'дней')} с жалобами",
                1481 + 2 * 32, F_SMALL, GRAY)


def generate_weekly_report(full_name: str, age_group: str, team: str,
                           streak: int, rank: str, total: int,
                           stats: dict, trend_7d: list,
                           prev: dict = None, baseline: dict = None,
                           trends: dict = None, pain: list = None) -> str:
    prev = prev or {}
    baseline = baseline or {}
    bl_median = baseline.get("median") or {}
    median_hr = baseline.get("median_hr")
    trends = trends or {}

    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # ===== шапка =====
    draw.rectangle([0, 0, W, 190], fill=HEADER)
    draw.line([0, 190, W, 190], fill=ACCENT, width=6)
    cx, cy, r = 90, 95, 42
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=ACCENT)
    bbox = draw.textbbox((0, 0), "ЧБК", font=F_TITLE)
    tw = bbox[2] - bbox[0]
    draw.text((cx - tw // 2, cy - 26), "ЧБК", fill=WHITE, font=F_TITLE)
    draw.text((150, 55), "СПОРТМЕД", fill=WHITE, font=F_TITLE)
    draw.text((152, 120), "Недельный отчёт", fill=GRAY, font=F_SUB)

    # ===== имя =====
    _center(draw, full_name, 225, F_NAME, WHITE)
    meta = f"{age_group}  |  {team}  |  {total} {_plural(total, 'опрос', 'опроса', 'опросов')}"
    _center(draw, meta, 300, F_META, GRAY)

    # ===== карточка серии =====
    _card(draw, 340, 115)
    _center(draw, "СЕРИЯ", 363, F_CARD_TITLE, ACCENT)
    _center(draw, f"{streak} {_plural(streak, 'день', 'дня', 'дней')}  |  {rank}",
            408, F_BIG if streak < 100 else F_BODY, YELLOW)

    # ===== готовность за неделю =====
    readiness = stats.get("avg_readiness")
    has_r = isinstance(readiness, (int, float))
    r_c = (GREEN if readiness >= 7 else (YELLOW if readiness >= 5 else RED)) if has_r else GRAY
    _card(draw, 465, 175)
    _center(draw, "Готовность за неделю", 488, F_CARD_TITLE, GRAY)
    _bar(draw, 130, 555, W - 130, (readiness if has_r else 0) / 10.0, r_c)
    val_s = f"{readiness:.1f} / 10" if has_r else "—"
    bbox = draw.textbbox((0, 0), val_s, font=F_BIG)
    tw = bbox[2] - bbox[0]
    vx = (W - tw) // 2
    draw.text((vx, 590), val_s, fill=r_c, font=F_BIG)
    arrow_s, arrow_c = _delta_text(readiness, prev.get("avg_readiness"))
    if arrow_s:
        draw.text((vx + tw + 22, 610), arrow_s, fill=arrow_c, font=F_SUB)

    # ===== график сна + личная норма =====
    norm_sleep = bl_median.get("sleep")
    if trend_7d:
        _card(draw, 660, 215)
        _center(draw, "Качество сна — 7 дней", 683, F_CARD_TITLE, GRAY)
        if norm_sleep is not None:
            _right(draw, f"норма {norm_sleep:.1f}", W - 130, 685, F_SMALL, GRAY)
        _sparkline(draw, trend_7d, 130, 725, W - 130, 825, norm=norm_sleep)

    # ===== тренды: готовность и пульс =====
    trend_r = trends.get("readiness") or []
    trend_hr = trends.get("hr") or []
    if trend_r or trend_hr:
        _card(draw, 895, 225)
        _center(draw, "Тренд недели", 918, F_CARD_TITLE, GRAY)
        if trend_r:
            draw.text((100, 960), "Готовность", fill=GRAY, font=F_SMALL)
            _sparkline(draw, trend_r, 100, 975, 420, 1065, color=GREEN)
        if trend_hr:
            _right(draw, "Пульс покоя", 800, 960, F_SMALL, GRAY)
            _sparkline(draw, trend_hr, 480, 975, 800, 1065, color=YELLOW)

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
    _card(draw, 1140, 290)
    _center(draw, "Средние за неделю", 1163, F_CARD_TITLE, GRAY)
    y = 1210
    for label, val, invert, norm_val, prev_val in rows:
        _avg_row(draw, y, label, val, invert, norm_val, prev_val)
        y += 52

    # ===== боль и жалобы =====
    _pain_card(draw, pain or [])

    # ===== футер =====
    draw.line([0, 1585, W, 1585], fill=LINE, width=2)
    _center(draw, "Береги восстановление — оно решает результат!", 1610,
            F_FOOTER, GRAY)

    out_dir = os.path.join(os.path.dirname(__file__), "data")
    os.makedirs(out_dir, exist_ok=True)
    safe_name = "".join(c if c.isalnum() else "_" for c in full_name)
    out_path = os.path.join(out_dir, f"weekly_{safe_name}_{date.today()}.png")
    img.save(out_path, "PNG")
    return out_path


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    from database import Database
    db = Database()
    a = db.get_athlete_by_id(int(sys.argv[1])) if len(sys.argv) > 1 else None
    if not a:
        print("Usage: python weekly_report.py <athlete_id>")
        sys.exit(1)
    stats = db.get_athlete_stats(a["id"], 7)
    prev = db.get_athlete_stats_window(a["id"], 7, 7)
    baseline = db.get_individual_baseline(a["id"], 30)
    trends = {
        "readiness": [v for _, v in db.get_trend_data(a["id"], "readiness", 7)],
        "hr": [v for _, v in db.get_trend_data(a["id"], "hr", 7)],
    }
    pain = db.get_week_pain_summary(a["id"], 7)
    path = generate_weekly_report(
        a["full_name"], a["age_group"], a.get("team", "?"),
        a.get("survey_streak", 0), "Новичок", a.get("total_surveys", 0),
        stats, [v for _, v in db.get_trend_data(a["id"], "sleep", 7)],
        prev=prev, baseline=baseline, trends=trends, pain=pain)
    print(path)
