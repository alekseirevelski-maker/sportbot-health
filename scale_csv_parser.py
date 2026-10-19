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
ВНИМАНИЕ: muscle_mass_kg хранит ПРОЦЕНТ мышечной массы (% от массы тела), как его
показывает MovingLife («Мышцы(%)»), а НЕ килограммы. Имя ключа оставлено исторически.
Дедупликация по дате: если за один день несколько строк — берём последнюю.
"""
import csv
import io
import re
import logging
from datetime import date, datetime

logger = logging.getLogger(__name__)

# Какое имя колонки → внутр. ключ. Совпадение по ПОДСТРОКЕ (нормализованной).
# Порядок важен: проверяем от более специфичных к более общим.
HEADER_MAP = [
    # --- дата / профиль ---
    (("date", "время", "дата", "tim", "data", "время измерен", "measure time", "record time"), "record_datetime"),
    (("профиль", "profile", "user", "member", "учет", "пользовател"), "device_profile"),
    # --- состав тела (порядок важен: более специфичные ПЕРЕД общими) ---
    (("body fat", "fat %", "fat percent", "% fat", "жира", "жировой", "жир", "масса жира"), "body_fat_pct"),
    (("weight", "вес"), "weight_kg"),
    (("bmi", "имп"), "bmi"),
    (("lean", "безжир", "fat free", "ffm", "безжирн"), "lean_mass_kg"),
    (("muscle", "мыш", "мышечной"), "muscle_mass_kg"),
    (("water", "вода", "воды"), "body_water_pct"),
    (("bone", "кост", "костной"), "bone_mass_kg"),
    (("visceral", "висцерал", "висц"), "visceral_fat_index"),
    (("subcutaneous", "подкожн", "skin"), "subcutaneous_fat_pct"),
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
    for unit in ("кг", "кг.", "kg", "ккал", "kcal"):
        if s.lower().endswith(unit):
            s = s[:-len(unit)].strip()
            break
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
            # уже занятый показатель пропускаем и ищем дальше: иначе «Подкожный жир»
            # и «Индекс висцерального жира» матчились на общий шаблон «жир», упирались
            # в занятый body_fat_pct и молча терялись
            if key in used:
                continue
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


# Форматы дат в экспортах MovingLife / GARLYN (проверено на реальных файлах):
#   healthdata.csv -> "2026/10/12 16:33"     (YYYY/MM/DD ЧЧ:ММ, без секунд)
#   data<N>.csv    -> "10/12/2026 16:33:00"  (MM/DD/YYYY ЧЧ:ММ:СС)
# Раньше в списке не было форм без секунд и с секундами — обе уходили в regex-fallback
# и превращались в мусор ("2026/9/16 19:30" -> 2016-09-26, "09/16/2026" -> 2026-16-09).
_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d",
    "%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y.%m.%d",
    "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y",
    "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y",
)

# Весы идут по своему часовому поясу (Челябинск, UTC+5), поэтому дата замера может
# опережать серверные сутки (UTC) — допускаем +1 день. Всё, что дальше в будущем,
# означает перепутанные день/месяц и отбраковывается.
_FUTURE_TOLERANCE_DAYS = 1


def _ok_date(d):
    """Дата правдоподобна: год в разумных рамках и не из будущего."""
    if d is None:
        return False
    if not (2000 <= d.year <= date.today().year + 1):
        return False
    return (d - date.today()).days <= _FUTURE_TOLERANCE_DAYS


def _pick_date(y, first, second, order):
    """Собрать дату из года и двух чисел. order='md' — (число1=месяц, число2=день),
    order='dm' — наоборот. Пробуем заданный порядок, затем обратный; берём первый
    правдоподобный (проверка «не из будущего» отсекает перепутанные день/месяц)."""
    variants = ((first, second), (second, first)) if order == "md" else ((second, first), (first, second))
    for mo, dd in variants:
        try:
            cand = date(y, mo, dd)
        except ValueError:
            continue
        if _ok_date(cand):
            return cand.isoformat()
    return None


def _parse_record_datetime(raw):
    """Разобрать дату/время экспорта в record_date (YYYY-MM-DD). Возвращает (record_date, ts)."""
    if not raw:
        return None, None
    s = str(raw).strip().strip('"').strip()
    if not s:
        return None, None

    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
        except ValueError:
            continue
        if _ok_date(dt.date()):
            return dt.date().isoformat(), dt
        # формат подошёл, но дата неправдоподобна (например 10/12/2026 = Dec 10 —
        # это перепутанные день/месяц) — пробуем остальные форматы и fallback

    # Fallback 1: год первым. Часть экспортов отдаёт YYYY/DD/MM (год-день-месяц).
    # (?<!\d)/(?!\d) не дают поймать подстроку внутри года ("26/9/16" из "2026/9/16").
    m = re.search(r"(?<!\d)(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)", s)
    if m:
        res = _pick_date(int(m.group(1)), int(m.group(2)), int(m.group(3)), order="md")
        if res:
            return res, None

    # Fallback 2: год последним. Слэш у GARLYN = MM/DD/YYYY, точки = ДД.ММ.ГГГГ.
    m = re.search(r"(?<!\d)(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})(?!\d)", s)
    if m:
        y_raw = m.group(3)
        y = int(y_raw) if len(y_raw) == 4 else 2000 + int(y_raw)
        order = "dm" if m.group(0)[len(m.group(1))] == "." else "md"
        res = _pick_date(y, int(m.group(1)), int(m.group(2)), order=order)
        if res:
            return res, None

    logger.warning(f"scale date: не удалось разобрать дату {raw!r} — запись ляжет на сегодня")
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

        # Определяем оригинальный заголовок колонки мышц (до нормализации)
        # чтобы понять единицы: "Мышцы(%)" vs "Мышцы(кг)" vs просто "Мышцы"
        muscle_raw_header = ""
        for idx, key in col_map.items():
            if key == "muscle_mass_kg" and idx < len(reader.fieldnames):
                muscle_raw_header = reader.fieldnames[idx].lower()
                break

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

            # Мышцы в экспорте MovingLife (GARLYN Bodyscan Master) — ВСЕГДА процент:
            # колонка «Мышцы(%)», и приложение показывает «Мышечная масса (%)»
            # (норма у мужчин ~70-86%, у женщин ниже). НЕ переводим в кг — храним %,
            # иначе получаются абсурдные «мышцы 89 кг» при весе 68 кг.
            # (имя колонки БД muscle_mass_kg осталось исторически).
            if "muscle_mass_kg" in out:
                muscle_val = out["muscle_mass_kg"]
                if muscle_val > 100:
                    # валидного процента >100 не бывает — мусор, не сохраняем
                    out["muscle_mass_kg"] = None
                elif "%" not in muscle_raw_header and ("кг" in muscle_raw_header or "kg" in muscle_raw_header):
                    # заголовок с явной единицей массы (не-%) — нормализуем в % от веса
                    w = out.get("weight_kg")
                    if w and 0 < muscle_val <= w:
                        out["muscle_mass_kg"] = round(muscle_val / w * 100, 2)
                    else:
                        out["muscle_mass_kg"] = None

            if _looks_like_bodycomp(out) or "record_date" in out:
                records.append(out)
    except Exception as e:
        logger.error(f"parse_scale_csv: {e}")
    return records


def parse_scale_csv_dated(content: str, filename: str = "") -> list:
    """Парсит и дедуплицирует по (дата, профиль) — за каждый день для каждого профиля
    берём последнюю строку. (Уникальность в БД — UNIQUE(athlete_id, record_date), но разные
    профили/спортсмены взвешиваются в один день, поэтому группировка по одной дате НЕ годится.)"""
    records = parse_scale_csv(content, filename)
    by_key = {}
    for r in records:
        d = r.get("record_date")
        if not d:
            continue
        prof = r.get("device_profile") or ""
        key = (d, prof)
        # последняя строка за (дата, профиль) выигрывает
        by_key[key] = r
    return list(by_key.values())
