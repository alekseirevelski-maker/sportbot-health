"""Конфигурация бота мониторинга здоровья спортсменов v2.0."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

BOT_TOKEN = os.environ.get("SPORT_BOT_TOKEN", "")

# ============ НАСТРОЙКИ UI ============
COLORS = {
    "primary": "#1E90FF",
    "success": "#32CD32",
    "warning": "#FFD700",
    "danger": "#FF4444",
    "info": "#00CED1",
    "dark": "#2F4F4F",
    "light": "#F0F8FF",
}

EMOJIS = {
    "basketball": "🏀",
    "sleep": "😴",
    "stress": "😰",
    "fatigue": "😩",
    "pain": "🤕",
    "mood": "😊",
    "heart": "❤️",
    "hrv": "📊",
    "training": "💪",
    "alert": "🚨",
    "warning": "⚠️",
    "success": "✅",
    "chart": "📈",
    "profile": "👤",
    "team": "👥",
    "settings": "⚙️",
    "help": "❓",
    "star": "⭐",
    "trophy": "🏆",
    "fire": "🔥",
    "calendar": "📅",
    "time": "⏰",
}

# Возрастные группы
AGE_GROUPS = {
    "U14": "U-14 (12-14 лет)",
    "U15": "U-15 (13-15 лет)",
    "U16": "U-16 (15-16 лет)",
    "U17": "U-17 (16-17 лет)",
    "U18": "U-18 (17-18 лет)",
    "U19": "U-19 (18-19 лет)",
    "U21": "U-21 (19-21 лет)",
    "Pro": "Профессионал (18+)",
}

# Какие группы используют Simple протокол
SIMPLE_PROTOCOLS = {"U14", "U15", "U16"}

# Шкала Hooper Index (1-7)
HOOPER_MIN = 1
HOOPER_MAX = 7

# Шкала sRPE (1-10)
SRPE_MIN = 1
SRPE_MAX = 10

# Пороги алертов
ALERT_THRESHOLDS = {
    "U14": {"warning": 12, "critical": 16},
    "U15": {"warning": 14, "critical": 18},
    "U16": {"warning": 14, "critical": 18},
    "U17": {"warning": 16, "critical": 22},
    "U18": {"warning": 16, "critical": 22},
    "U19": {"warning": 16, "critical": 22},
    "U21": {"warning": 16, "critical": 22},
    "Pro": {"warning": 16, "critical": 22},
}

# Нормы HRV
HRV_NORMS = {
    "U14": None,
    "U15": None,
    "U16": {"min": 40, "max": 60, "critical_min": 30},
    "U17": {"min": 35, "max": 55, "critical_min": 25},
    "U18": {"min": 35, "max": 55, "critical_min": 25},
    "U19": {"min": 35, "max": 50, "critical_min": 25},
    "U21": {"min": 35, "max": 50, "critical_min": 25},
    "Pro": {"min": 30, "max": 50, "critical_min": 20},
}

# Нормы пульса покоя
HR_NORMS = {
    "U14": {"min": 55, "max": 75},
    "U15": {"min": 50, "max": 70},
    "U16": {"min": 50, "max": 70},
    "U17": {"min": 45, "max": 65},
    "U18": {"min": 45, "max": 65},
    "U19": {"min": 42, "max": 62},
    "U21": {"min": 42, "max": 62},
    "Pro": {"min": 40, "max": 60},
}

# Нормы состава тела (биоимпеданс GARLYN Bodyscan Master)
# Источники: Looney et al. (2024), Iblasi et al. (2025), ESPEN guidelines
# body_fat_pct: скорректировано +3% (Potter et al., 2021) для приближения к DXA
BC_NORMS = {
    "U14": {
        "male": {"body_fat_pct": (10, 22), "body_water_pct": (55, 65), "visceral_fat_max": 10},
        "female": {"body_fat_pct": (18, 30), "body_water_pct": (50, 60), "visceral_fat_max": 10},
    },
    "U15": {
        "male": {"body_fat_pct": (10, 20), "body_water_pct": (55, 65), "visceral_fat_max": 10},
        "female": {"body_fat_pct": (18, 28), "body_water_pct": (50, 60), "visceral_fat_max": 10},
    },
    "U16": {
        "male": {"body_fat_pct": (10, 18), "body_water_pct": (55, 65), "visceral_fat_max": 10},
        "female": {"body_fat_pct": (18, 26), "body_water_pct": (50, 60), "visceral_fat_max": 10},
    },
    "U17": {
        "male": {"body_fat_pct": (10, 18), "body_water_pct": (55, 65), "visceral_fat_max": 10},
        "female": {"body_fat_pct": (18, 26), "body_water_pct": (50, 60), "visceral_fat_max": 10},
    },
    "U18": {
        "male": {"body_fat_pct": (10, 18), "body_water_pct": (55, 65), "visceral_fat_max": 10},
        "female": {"body_fat_pct": (18, 26), "body_water_pct": (50, 60), "visceral_fat_max": 10},
    },
    "U19": {
        "male": {"body_fat_pct": (10, 18), "body_water_pct": (55, 65), "visceral_fat_max": 10},
        "female": {"body_fat_pct": (18, 26), "body_water_pct": (50, 60), "visceral_fat_max": 10},
    },
    "U21": {
        "male": {"body_fat_pct": (10, 18), "body_water_pct": (55, 65), "visceral_fat_max": 10},
        "female": {"body_fat_pct": (18, 26), "body_water_pct": (50, 60), "visceral_fat_max": 10},
    },
    "Pro": {
        "male": {"body_fat_pct": (8, 18), "body_water_pct": (55, 65), "visceral_fat_max": 10},
        "female": {"body_fat_pct": (16, 26), "body_water_pct": (50, 60), "visceral_fat_max": 10},
    },
}

# Пороги красных флагов для состава тела (для врача)
BC_RED_FLAGS = {
    "fat_change_pct": 5.0,      # изменение жира > ±5% за замер → артефакт
    "muscle_loss_kg": 1.5,      # потеря мышц > 1.5 кг → катаболизм
    "water_critical_pct": 52.0,  # вода < 52% → критическое обезвоживание
    "visceral_risk": 16.0,       # висцеральный жир > 16 → metabolic risk
}

# ИМТ: перцентили WHO 2007 (5-й и 95-й) для 10-18 лет по полу.
# У баскетболистов ИМТ слаб (рост/мышцы): <5 перцентиль = скрининг дефицита/роста,
# >95 перцентиль НЕ алертим без кожных складок (ложные «ожирения» у рослых мускулистых).
# Ключ: возраст в годах → {"m": (p5, p95), "f": (p5, p95)}
BMI_PERCENTILES = {
    10: {"m": (14.4, 19.6), "f": (14.2, 20.1)},
    11: {"m": (14.7, 20.4), "f": (14.5, 21.0)},
    12: {"m": (15.0, 21.3), "f": (14.9, 22.1)},
    13: {"m": (15.4, 22.2), "f": (15.4, 23.2)},
    14: {"m": (15.9, 23.1), "f": (15.9, 24.3)},
    15: {"m": (16.4, 24.0), "f": (16.4, 25.2)},
    16: {"m": (17.0, 24.9), "f": (16.8, 25.9)},
    17: {"m": (17.5, 25.7), "f": (17.1, 26.4)},
    18: {"m": (18.0, 26.4), "f": (17.4, 26.9)},
}

# Возраст (лет) для возрастной группы — используется для подбора перцентиля ИМТ
AGE_GROUP_YEARS = {
    "U14": 14, "U15": 15, "U16": 16, "U17": 17, "U18": 18,
    "U19": 19, "U21": 21, "Pro": 25,
}

# Мотивационные сообщения
MOTIVATIONAL_MESSAGES = {
    "streak_3": "🔥 Отлично! Уже 3 дня подряд! Продолжай!",
    "streak_7": "⭐ Неделя без пропусков! Ты профессионал!",
    "streak_14": "🏆 14 дней! Организм скажет спасибо!",
    "streak_30": "💎 Месяц идеального мониторинга! Легенда!",
    "perfect_score": "🎯 Идеальные показатели! Так держать!",
    "improvement": "📈 Заметен прогресс! Дисциплина работает!",
}

# Рекомендации на основе данных
RECOMMENDATIONS = {
    "sleep_low": "😴 Твой сон ниже нормы. Попробуй:\n• Ложиться до 23:00\n• Убрать телефон за час до сна\n• Проветрить комнату",
    "stress_high": "🧘 Стресс повышен. Рекомендую:\n• 5 минут глубокого дыхания\n• Прогулка\n• Теплый душ вечером",
    "fatigue_high": "⚡ Усталость? Стоит:\n• Уменьшить нагрузку\n• Увеличить сон на 1 час\n• Пить больше воды",
    "hrv_low": "📉 HRV ниже нормы — перетренированность:\n• Легкая тренировка или отдых\n• Массаж/растяжка\n• Проверь питание",
    "hr_high": "💓 Пульс повышен. Обрати внимание:\n• Возможно начинается болезнь\n• Снизь интенсивность\n• Пей больше жидкости",
    "mood_low": "😔 Настроение снижено. Попробуй:\n• Поговори с другом/тренером\n• Сделай то, что радует\n• Вспомни достижения",
}

# База данных
DB_PATH = Path(__file__).parent / "data" / "sport_health.db"

# Прокси
PROXY_URL = os.environ.get("PROXY_URL", "http://127.0.0.1:10809")

# Rate limiting
RATE_LIMIT_MESSAGES = int(os.environ.get("RATE_LIMIT_MESSAGES", "30"))
RATE_LIMIT_WINDOW = int(os.environ.get("RATE_LIMIT_WINDOW", "60"))

# Админы (супер-пользователи; врачи/тренеры — в БД)
ADMIN_IDS = {351572247}

# Напоминания по умолчанию (переопределяются из БД при старте)
REMINDER_HOUR_DEFAULT = 8
REMINDER_MINUTE_DEFAULT = 0


# Импорт модуля менструального цикла (с научной базой)
from cycle_medicine import (
    MENSTRUAL_ENABLED_GROUPS,
    get_cycle_phase,
    get_cycle_phase_info,
    PHASES,
    AGE_SPECIFIC,
    CYCLE_LENGTH_NORM_MIN,
    CYCLE_LENGTH_NORM_MAX,
    CYCLE_LENGTH_MEDIAN,
    LUTEAL_PHASE_DAYS,
    MENSTRUATION_TYPICAL_DAYS,
    MENSTRUAL_PHASES,
)

# Для обратной совместимости с bot.py (старый код, импортирующий MENSTRUAL_PHASES, PHASE_REC_BY_AGE)
PHASE_REC_BY_AGE = {k: {"note": v.training_note} for k, v in AGE_SPECIFIC.items()}
