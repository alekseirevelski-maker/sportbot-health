"""
Парсер CSV-экспорта биоимпедансных весов (GARLYN Bodyscan Master, приложение MovingLife).

Формат CSV экспорта MovingLife фиксированный набор колонок (по типу прогрессивных весов
на 16 профилей): дата/время + метрики состава тела. Заголовки бывают на разных языках
(en/ru/cn или транслит), потому маппинг гибкий: по подстроке.

Возвращает список словарей по датам:
  [{ "record_date": "YYYY-MM-DD", "weight_kg": 74.3, "body_fat_pct": 15.2,
     "muscle_mass_kg": 42.1, "body_water_pct": 58.0, "bone_mass_kg": 2.8,
     "visceral_fat_index": 6, "subcutaneous_fat_pct": 21.0, "lean_mass_kg": 60.1,
     "protein_pct": 17.0, "bmr_kcal": 1750, "amr_kcal": 2400,
     "device_profile": "Профиль 5" }]
Дедупликация по дате: если за один день несколько строк — берём последнюю.
"""
import csv
import io
import re
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Какое имя колонки → внутр. ключ. Совпадение по ПОДСТРОКЕ (нормализованной).
# Порядок важен: проверяем от более специфичных к более общим.
HEADER_MAP = [
    # --- дата / профиль ---
    (("date", "время", "дата", "tim", "data", "время измерен", "measure time", "record time"), "record_datetime"),
    (("профиль", "profile", "user", "member", "учет", "пользовател"), "device_profile"),
    # --- состав тела ---
    (("weight", "вес", "масса"), "weight_kg"),
    (("bmi", "имп"),
     "bmi"),
    (("body fat", "fat %", "fat percent", "% fat", "жира", "жировой", "жир"), "body_fat_pct"),
    (("muscle", "мыш", "мышечной"), "muscle_mass_kg"),
    (("water", "вода", "воды"), "body_water_pct"),
    (("bone", "кост", "костной"), "bone_mass_kg"),
    (("visceral", "висцерал", "висц"), "visceral_fat_index"),
    (("subcutaneous", "подкожн", "skin"), "subcutaneous_fat_pct"),
    (("lean", "безжир", "fat free", "ffm"), "lean_mass_kg"),
    (("protein", "белок", "белк"), "protein_pct"),
    (("bmr", "базал", "метаб"), "bmr_kcal"),
    (("amr", "активн", "метабол"), "amr_kcal"),
    (("height", "рост"), "height_cm"),
    (("impedance",), "impedance_ohm"),
]

# Нормализация заголовка: нижний регистр, без пробелов/знаков препинания/unicode-вариативности
_RE_SPACE = re.compile(r"[\s\u00a0/\\()%:\-—._'`\"«»]+")


def _norm(s: str) -> str:
    if not s:
        return ""
    return _RE_SPACE.sub("", s.lower().strip())


def _parse_num(v):
    if v is None:
        return None
    s = str(v).strip()
    if not s or s in ("-", "—", "None", "null", "nan", "N/A"):
        return None
    s = s.replace(",", ".").rstrip("%").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _parse_bmi_ambiguous(val):
    """bmi может прийти как '25.0'. Отдельная функция на случай строк с единич."""
    return _parse_num(val)


def _map_columns(headers):
    """Вернуть {индекс: наш_ключ} для понятных колонок."""
    col_map = {}
    used = set()
    for i, h in enumerate(headers):
        nh = _norm(h)
        if not nh:
            continue
        best = None
        for pats, key in HEADER_MAP:
            for p in pats:
                pn = _norm(p)
                if pn and (pn in nh or nh in pn):
                    best = key
                    break
            if best:
                break
        if best and best not in used and best not in col_map.values():
            col_map[i] = best
            used.add(best)
    return col_map


def _parse_record_datetime(raw):
    """Разобрать дату/время экспорта в record_date (YYYY-MM-DD). Возвращает (record_date, ts)."""
    if not raw:
        return None, None
    s = str(raw).strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y",
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.date().isoformat(), dt
        except ValueError:
            continue
    # fallback: regex числа-дата
    m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}", None
    m = re.search(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        y = int(y) if len(y) == 4 else 2000 + int(y)
        if y >= 2000:
            return f"{y:04d}-{mo:02d}-{d:02d}", None
    return None, None


def _looks_like_bodycomp(record):
    """Обнаружил ли парсер признаки состава тела (вес/жир/мышцы/вода)."""
    return (record.get("weight_kg") is not None
            or record.get("body_fat_pct") is not None
            or record.get("muscle_mass_kg") is not None
            or record.get("body_water_pct") is not None)


def parse_scale_csv(content: str, filename: str = "") -> list:
    """Парсит CSV биоимпедансных весов. Возвращает список словарей по строкам (без дедупликации дат)."""
    records = []
    if not content or not content.strip():
        return []
    try:
        reader = csv.DictReader(io.StringIO(content))
        if not reader.fieldnames:
            return []
        col_map = _map_columns(reader.fieldnames)

        for row in reader:
            rec = {}
            for idx, key in col_map.items():
                if idx >= len(reader.fieldnames):
                    continue
                fname = reader.fieldnames[idx]
                rec[key] = row.get(fname) if row else None

            out = {}
            dt = rec.get("record_datetime")
            rdate, _ = _parse_record_datetime(dt)
            if rdate:
                out["record_date"] = rdate
            # device_profile: почистить, отрезать пользовательский суффикс вроде ", 24m" нет — оставляем как есть
            if rec.get("device_profile"):
                out["device_profile"] = str(rec["device_profile"]).strip()

            for key in ("weight_kg", "bmi", "body_fat_pct", "muscle_mass_kg", "body_water_pct",
                        "bone_mass_kg", "visceral_fat_index", "subcutaneous_fat_pct",
                        "lean_mass_kg", "protein_pct", "height_cm"):
                v = rec.get(key)
                if v is not None:
                    nv = _parse_num(v)
                    if nv is not None:
                        out[key] = nv
            for key in ("bmr_kcal", "amr_kcal"):
                v = _parse_num(rec.get(key))
                if v is not None:
                    out[key] = int(round(v))

            if _looks_like_bodycomp(out) or "record_date" in out:
                records.append(out)
    except Exception as e:
        logger.error(f"parse_scale_csv: {e}")
    return records


def parse_scale_csv_dated(content: str, filename: str = "") -> list:
    """Парсит и дедуплицирует по дате (за каждый день берём последнюю строку)."""
    records = parse_scale_csv(content, filename)
    by_date = {}
    for r in records:
        d = r.get("record_date")
        if not d:
            continue
        # последняя строка за день выигрывает
        by_date[d] = r
    return list(by_date.values())
