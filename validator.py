# -*- coding: utf-8 -*-
"""Валидаторы пользовательских вводов (опрос/анкета). Чистые функции, без состояния."""
import re
from datetime import datetime
from typing import Optional, Tuple, Union


def validate_scale(value: Union[str, int], min_val: int = 1, max_val: int = 10) -> Optional[int]:
    """Валидация шкалы (напр. 1-7 опрос, 1-10 анкета). Вернёт int или None."""
    try:
        val = int(value)
        if min_val <= val <= max_val:
            return val
        return None
    except (ValueError, TypeError):
        return None


def validate_heart_rate(value: Union[str, int]) -> Optional[int]:
    """Валидация пульса покоя (30-220)."""
    try:
        val = int(value)
        if 30 <= val <= 220:
            return val
        return None
    except (ValueError, TypeError):
        return None


def parse_birth_date(value: str) -> Optional[datetime]:
    """Распарсить дату рождения из формата ДД.ММ.ГГГГ (или ГГГГ-ММ-ДД). None если невалидно."""
    value = (value or "").strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def validate_age_ge14(value: str) -> Tuple[bool, str]:
    """Проверка возраста по дате рождения: минимум 14 лет, максимум 99."""
    birth = parse_birth_date(value)
    if birth is None:
        return False, "❌ Неверный формат даты. Введи ДД.ММ.ГГГГ (например 15.03.2008)"
    today = datetime.now().date()
    age = today.year - birth.date().year - ((today.month, today.day) < (birth.date().month, birth.date().day))
    if age < 14:
        return False, f"❌ Регистрация с 14 лет (сейчас {age})"
    if age > 99:
        return False, f"❌ Максимальный возраст 99 лет"
    return True, f"✅ Возраст {age} лет"


def sanitize_text(text: str, keep_nl: bool = False) -> str:
    """Очистка текста от опасных/мусорных символов. Кириллица, цифры, обычные знаки — сохраняются."""
    if not text:
        return ""
    # разрешаем буквы (вкл. кириллицу), цифры, пробелы, переносы (по желанию) и базовую пунктуацию
    pattern = r"[^\w\s\-.,!?@()№/+]" if not keep_nl else r"[^\w\s\-.,!?@()№/+\n]"
    cleaned = re.sub(pattern, "", str(text))
    return cleaned.strip()
