# -*- coding: utf-8 -*-
"""Раскладка недельного PNG v3 (светлый редизайн, холст 1080x2060).

Проверяем: холст, бандл-шрифты (не битмап-fallback), настоящая эмблема в шапке,
бары сна по ДАТАМ (пропуск дня = «—» в своём слоте), авто-fit длинного имени,
норма-чипы инлайн, дельта готовности словами. Семантика данных не меняется.
"""
from datetime import date, timedelta

from PIL import Image, ImageDraw, ImageFont

TODAY = date.today()
WINDOW = [(TODAY - timedelta(days=6 - i)).isoformat() for i in range(7)]
SLOT = 880 / 7.0


def _render(monkeypatch, tmp_path, **over):
    import weekly_report
    monkeypatch.setattr(weekly_report, "__file__", str(tmp_path / "weekly_report.py"))
    kw = dict(full_name="Тестовый Спортсмен", age_group="U17", team="ЧБК",
              streak=31, rank="Мониторинг-машина", total=40,
              stats={"avg_readiness": 7.0, "avg_sleep": 6.0},
              trend_7d=[6, 5, 6, 5, 6, 5, 6])
    kw.update(over)
    return weekly_report.generate_weekly_report(**kw)


def _spy_text(monkeypatch):
    calls = []
    orig = ImageDraw.ImageDraw.text

    def spy(self, xy, text, fill=None, font=None, anchor=None, **kw):
        calls.append((str(text), xy[0], xy[1], font))
        return orig(self, xy, text, fill=fill, font=font, anchor=anchor, **kw)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", spy)
    return calls


def test_canvas_size(monkeypatch, tmp_path):
    path = _render(monkeypatch, tmp_path)
    assert Image.open(path).size == (1080, 2060)


def test_fonts_are_bundled_truetype():
    import weekly_report
    for f in (weekly_report.F_TITLE, weekly_report.F_SUB, weekly_report.F_NAME,
              weekly_report.F_META, weekly_report.F_CARD_TITLE, weekly_report.F_BIG,
              weekly_report.F_BODY, weekly_report.F_SMALL, weekly_report.F_FOOTER):
        assert isinstance(f, ImageFont.FreeTypeFont), "шрифт из бандла, не load_default()"
        assert "Inter-" in getattr(f, "path", ""), f"именно бандл Inter: {getattr(f, 'path', None)}"


def test_logo_emblem_in_header(monkeypatch, tmp_path):
    path = _render(monkeypatch, tmp_path)
    crop = Image.open(path).crop((60, 60, 160, 160)).convert("RGB")
    px = crop.tobytes()
    dark = sum(1 for i in range(0, len(px), 3)
               if px[i] < 60 and px[i + 1] < 40 and px[i + 2] < 40)
    assert dark > 200, "в шапке настоящая тёмная эмблема, не пустая плитка"


def test_sleep_bars_mapped_by_date(monkeypatch, tmp_path):
    calls = _spy_text(monkeypatch)
    pairs = [(iso, 6) for iso in WINDOW if iso != WINDOW[4]]
    _render(monkeypatch, tmp_path, trend_7d=pairs)
    # бар центрирован в слоте: x = 100 + i*slot + (slot-bw)/2, текст в x + bw/2
    # → центр слота = 100 + i*slot + slot/2 (геометрия одобренного мокапа)
    center4 = 100 + 4 * SLOT + SLOT / 2
    assert any(t == "—" and y == 1025 and abs(x - center4) < 1 for t, x, y, f in calls), \
        "пропущенный день — «—» в своём слоте по дате"
    assert sum(1 for t, x, y, f in calls if t == "6.0" and y == 952) == 6
    assert any(t == "Сб" for t, x, y, f in calls), "подписи дней недели на месте"


def test_legacy_plain_values_still_render(monkeypatch, tmp_path):
    calls = _spy_text(monkeypatch)
    path = _render(monkeypatch, tmp_path, trend_7d=[6, 5, 6, 5, 6, 5, 6])
    assert Image.open(path).size == (1080, 2060)
    assert sum(1 for t, x, y, f in calls if t in ("6.0", "5.0") and y == 952) == 7


def test_long_name_autofit(monkeypatch, tmp_path):
    calls = _spy_text(monkeypatch)
    name = "Константинопольская Александра-Виктория"
    _render(monkeypatch, tmp_path, full_name=name)
    hits = [(x, f) for t, x, y, f in calls if t == name]
    assert hits, "имя отрендерено"
    x, font = hits[0]
    assert x + font.getbbox(name)[2] <= 1080 - 60, "длинное имя не вылезает за холст"


def test_norm_chip_inline(monkeypatch, tmp_path):
    calls = _spy_text(monkeypatch)
    _render(monkeypatch, tmp_path, baseline={"median": {"sleep": 7.0}})
    assert any(t == "норма 7.0" and y == 1510 for t, x, y, f in calls), \
        "норма чипом инлайн в первой строке средних (y=1510)"


def test_readiness_delta_sentence(monkeypatch, tmp_path):
    calls = _spy_text(monkeypatch)
    _render(monkeypatch, tmp_path, stats={"avg_readiness": 7.4, "avg_sleep": 6.0},
            prev={"avg_readiness": 6.8})
    assert any("▲ +0.6 к прошлой неделе" in t for t, x, y, f in calls)


def test_readiness_no_prev_week(monkeypatch, tmp_path):
    calls = _spy_text(monkeypatch)
    _render(monkeypatch, tmp_path, stats={"avg_readiness": 7.4, "avg_sleep": 6.0})
    assert any("нет данных за прошлую неделю" in t for t, x, y, f in calls)