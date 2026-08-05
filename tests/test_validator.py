# -*- coding: utf-8 -*-
"""Тесты валидаторов validator.py."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from validator import (
    validate_scale, validate_heart_rate,
    parse_birth_date, validate_age_ge14, sanitize_text,
)


def test_scale():
    assert validate_scale(5) == 5
    assert validate_scale("3") == 3
    assert validate_scale(0) is None
    assert validate_scale(11) is None
    assert validate_scale("abc") is None


def test_heart_rate():
    assert validate_heart_rate(60) == 60
    assert validate_heart_rate("120") == 120
    assert validate_heart_rate(25) is None
    assert validate_heart_rate(230) is None


def test_parse_birth_date():
    assert parse_birth_date("15.03.2008") is not None
    assert parse_birth_date("2008-03-15") is not None
    assert parse_birth_date("неверно") is None


def test_age_ge14():
    valid, _ = validate_age_ge14("01.01.2000")
    assert valid is True
    valid, _ = validate_age_ge14("01.01.2015")
    assert valid is False  # <14 лет
    valid, _ = validate_age_ge14("мусор")
    assert valid is False


def test_sanitize():
    assert sanitize_text("Привет, мир!") == "Привет, мир!"
    assert "<script>" not in sanitize_text("<script>alert(1)</script>")
    assert sanitize_text("") == ""
