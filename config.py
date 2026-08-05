"""Конфигурация бота мониторинга здоровья спортсменов v2.0."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

BOT_TOKEN = os.environ.get("SPORT_BOT_TOKEN", "")

# ============ АДМИНЫ И НАПОМИНАНИЯ (через .env) ============
_ADMIN_FALLBACK = {351572247}


def _parse_admin_ids(raw: str) -> set:
    ids = set()
    for part in (raw or "").split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


ADMIN_IDS = _parse_admin_ids(os.environ.get("ADMIN_IDS", "")) or _ADMIN_FALLBACK

REMINDER_HOUR_DEFAULT = int(os.environ.get("REMINDER_HOUR", "20"))
REMINDER_MINUTE_DEFAULT = int(os.environ.get("REMINDER_MINUTE", "0"))

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
