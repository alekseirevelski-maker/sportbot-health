"""
Парсер данных с умных часов для спортивного бота ЧБК.
Поддерживает реальные форматы популярных брендов.
"""
import json, re, logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# Какой заголовок CSV → внутренний ключ
CSV_HEADER_MAP = {
    # Garmin
    "heart rate": "heart_rate",
    "resting heart rate": "resting_hr",
    "resting_hr": "resting_hr",
    "sleep duration (hours)": "sleep_hours",
    "sleep duration": "sleep_hours",
    "sleep hours": "sleep_hours",
    "steps": "steps",
    "stress level": "stress",
    "stress": "stress",
    "spo2": "spo2",
    # Fitbit
    "resting heart rate": "resting_hr",
    "sleep minutes": "sleep_minutes",
    "sleep (minutes)": "sleep_minutes",
    # Xiaomi / Zepp
    "heart_rate_avg": "heart_rate",
    "heart_rate_rest": "resting_hr",
    "sleep_duration_hours": "sleep_hours",
    "sleep_duration_minutes": "sleep_minutes",
    # Huawei
    "heart rate (bpm)": "heart_rate",
    "resting hr": "resting_hr",
    "sleep (hours)": "sleep_hours",
    # Oura / Whoop
    "hrv": "hrv",
    "spo2 (%)": "spo2",
}

# Русские ключи для TXT
TXT_KEY_MAP = {
    "пульс": "heart_rate",
    "пульс покоя": "resting_hr",
    "сон": "sleep_hours",
    "шаги": "steps",
    "стресс": "stress",
    "насыщение": "spo2",
    "кислород": "spo2",
    "hrv": "hrv",
    "вес": "weight",
}


def parse_watch(content: str, filename: str) -> dict:
    """Основная функция: контент файла + имя → словарь с данными."""
    data = {}
    try:
        if filename.endswith(".json"):
            data = _parse_json(content, data)
        elif filename.endswith(".csv"):
            data = _parse_csv(content, data)
        elif filename.endswith(".txt"):
            data = _parse_txt(content, data)
    except Exception as e:
        logger.error(f"Parse watch error: {e}")
    return data


def _parse_json(content: str, data: dict) -> dict:
    jd = json.loads(content)

    if isinstance(jd, dict):
        # Плоский JSON (Apple Health, простой экспорт)
        hr = jd.get("heart_rate") or jd.get("resting_heart_rate") or jd.get("pulse")
        if hr and isinstance(hr, (int, float)):
            data["💓 Пульс"] = int(hr)

        # Вложенный Samsung Health: {"heart_rate": {"avg": 62, "resting": 58}}
        if isinstance(jd.get("heart_rate"), dict):
            hr_inner = jd["heart_rate"]
            if hr_inner.get("resting"):
                data["💓 Пульс"] = int(hr_inner["resting"])
            elif hr_inner.get("avg"):
                data["💓 Пульс"] = int(hr_inner["avg"])

        # Сон
        sleep = jd.get("sleep_duration") or jd.get("sleep")
        if isinstance(sleep, dict):
            mins = sleep.get("duration") or sleep.get("total_minutes")
            if mins and isinstance(mins, (int, float)):
                data["😴 Сон"] = f"{mins / 60:.1f}ч"
        elif sleep and isinstance(sleep, (int, float)):
            data["😴 Сон"] = f"{sleep}ч" if sleep < 24 else f"{sleep / 60:.1f}ч"

        # Шаги
        steps = jd.get("steps") or jd.get("step_count")
        if steps and isinstance(steps, (int, float)):
            data["🏃 Шаги"] = int(steps)

        # Стресс
        stress = jd.get("stress") or jd.get("stress_level")
        if stress and isinstance(stress, (int, float)):
            data["😰 Стресс"] = int(stress)

        # SpO2
        spo2 = jd.get("spo2") or jd.get("oxygen_saturation")
        if spo2 and isinstance(spo2, (int, float)):
            data["🫁 SpO2"] = f"{spo2}%"

        # HRV
        hrv = jd.get("hrv")
        if hrv and isinstance(hrv, (int, float)):
            data["📊 HRV"] = int(hrv)

        # Apple Health metrics массив
        if "metrics" in jd and isinstance(jd["metrics"], list):
            for m in jd["metrics"]:
                n = m.get("name", "").lower()
                v = m.get("qty", 0)
                if not isinstance(v, (int, float)):
                    continue
                if "heart" in n or "pulse" in n:
                    data["💓 Пульс"] = int(v)
                elif "sleep" in n:
                    data["😴 Сон"] = f"{v / 60:.1f}ч" if v > 60 else f"{v}ч"
                elif "step" in n:
                    data["🏃 Шаги"] = int(v)
                elif "stress" in n:
                    data["😰 Стресс"] = int(v)
                elif "spo2" in n or "oxygen" in n:
                    data["🫁 SpO2"] = f"{v}%"
                elif "hrv" in n:
                    data["📊 HRV"] = int(v)

    return data


def _parse_csv(content: str, data: dict) -> dict:
    lines = content.strip().split("\n")
    if len(lines) < 2:
        return data

    headers_raw = lines[0].split(",")
    # Чистим заголовки
    headers = [h.strip().lower().strip('"').strip("'") for h in headers_raw]

    # Карта: номер колонки → наш ключ
    col_map = {}
    for i, h in enumerate(headers):
        if h in CSV_HEADER_MAP:
            col_map[i] = CSV_HEADER_MAP[h]
            continue
        # Попробуем частичное совпадение
        for pattern, key in CSV_HEADER_MAP.items():
            if pattern in h or h in pattern:
                col_map[i] = key
                break

    if not col_map:
        return data

    # Берём первую строку с данными
    for line in lines[1:]:
        vals = line.split(",")
        if len(vals) < 2:
            continue
        for ci, key in col_map.items():
            if ci >= len(vals):
                continue
            raw = vals[ci].strip().strip('"').strip("'")
            if not raw:
                continue
            try:
                val = float(raw) if "." in raw else int(raw)
            except ValueError:
                val = raw

            if key == "resting_hr":
                data["💓 Пульс"] = int(val)
            elif key == "heart_rate":
                data["💓 Пульс"] = int(val)
            elif key == "sleep_hours":
                data["😴 Сон"] = f"{float(val):.1f}ч"
            elif key == "sleep_minutes":
                mins = int(val)
                data["😴 Сон"] = f"{mins / 60:.1f}ч"
            elif key == "steps":
                data["🏃 Шаги"] = int(val)
            elif key == "stress":
                data["😰 Стресс"] = int(val)
            elif key == "spo2":
                data["🫁 SpO2"] = f"{int(val)}%"
            elif key == "hrv":
                data["📊 HRV"] = int(val)
        # Берём только первую строку данных
        break

    return data


def _parse_txt(content: str, data: dict) -> dict:
    for line in content.split("\n"):
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip().lower()
        v = v.strip()

        key = TXT_KEY_MAP.get(k)
        if not key:
            # Частичное совпадение
            for pattern, mapped in TXT_KEY_MAP.items():
                if pattern in k:
                    key = mapped
                    break
        if not key:
            continue

        try:
            val = float(v.replace(",", ".")) if "." in v else int(v)
        except ValueError:
            val = v

        if key in ("resting_hr", "heart_rate"):
            data["💓 Пульс"] = int(val)
        elif key == "sleep_hours":
            data["😴 Сон"] = f"{float(val):.1f}ч"
        elif key == "steps":
            data["🏃 Шаги"] = int(val)
        elif key == "stress":
            data["😰 Стресс"] = int(val)
        elif key == "spo2":
            data["🫁 SpO2"] = f"{int(val)}%"
        elif key == "hrv":
            data["📊 HRV"] = int(val)

    return data
