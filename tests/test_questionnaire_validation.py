# -*- coding: utf-8 -*-
"""Task 5: валидация анкеты — чистые обёртки над validator.py.

`validate_questionnaire_age/birth/phone(text) -> (bool, str)` подключены
в текстовые шаги анкеты q_age / q_birth_date / q_phone вместо ad-hoc проверок.
Минимальный возраст — 14 (единый порог из validator.validate_age_ge14).
"""


# ==================== Возраст ====================

def test_age_below_14_rejected():
    from bot import validate_questionnaire_age
    ok, msg = validate_questionnaire_age("10")
    assert ok is False
    assert msg


def test_age_14_accepted():
    from bot import validate_questionnaire_age
    ok, _ = validate_questionnaire_age("14")
    assert ok is True


def test_age_normal_accepted():
    from bot import validate_questionnaire_age
    ok, _ = validate_questionnaire_age(" 17 ")
    assert ok is True


def test_age_over_99_rejected():
    from bot import validate_questionnaire_age
    ok, _ = validate_questionnaire_age("100")
    assert ok is False


def test_age_garbage_rejected():
    from bot import validate_questionnaire_age
    ok, msg = validate_questionnaire_age("abc")
    assert ok is False
    assert msg


# ==================== Дата рождения ====================

def test_birth_calendar_invalid_rejected():
    from bot import validate_questionnaire_birth
    ok, msg = validate_questionnaire_birth("99.99.9999")
    assert ok is False
    assert msg


def test_birth_valid_accepted():
    from bot import validate_questionnaire_birth
    ok, _ = validate_questionnaire_birth("15.03.2008")
    assert ok is True


def test_birth_garbage_rejected():
    from bot import validate_questionnaire_birth
    ok, _ = validate_questionnaire_birth("abc")
    assert ok is False


def test_birth_underage_rejected():
    """Валидный формат, но возраст < 14 → False (порог validate_age_ge14)."""
    from bot import validate_questionnaire_birth
    ok, _ = validate_questionnaire_birth("01.01.2020")
    assert ok is False


# ==================== Телефон ====================

def test_phone_valid_accepted():
    from bot import validate_questionnaire_phone
    ok, _ = validate_questionnaire_phone("+7 900 123-45-67")
    assert ok is True


def test_phone_parens_accepted():
    from bot import validate_questionnaire_phone
    ok, _ = validate_questionnaire_phone("8 (900) 123-45-67")
    assert ok is True


def test_phone_garbage_rejected():
    from bot import validate_questionnaire_phone
    ok, msg = validate_questionnaire_phone("abc")
    assert ok is False
    assert msg


def test_phone_too_short_rejected():
    from bot import validate_questionnaire_phone
    ok, _ = validate_questionnaire_phone("123")
    assert ok is False


def test_phone_too_long_rejected():
    from bot import validate_questionnaire_phone
    ok, _ = validate_questionnaire_phone("1234567890123456")
    assert ok is False
