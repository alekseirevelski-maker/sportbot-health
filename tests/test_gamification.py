# -*- coding: utf-8 -*-
"""Геймификация серии опросов: ранги выше 60 дней, достижения, мотивация.

Баг, против которого написаны тесты: серия замирала на 60 дней и после
этого «ничего не происходило» (ранг и достижения кончались на 30 днях).
"""
import pytest

from gamification import get_rank, STREAK_ACHIEVEMENTS, get_motivation_key


# ---------- get_rank: новая лестница ----------

@pytest.mark.parametrize("streak,expected", [
    (0,   "🌱 Новичок"),
    (1,   "🌱 Новичок"),
    (2,   "🌱 Новичок"),
    (3,   "⭐ Старт"),
    (6,   "⭐ Старт"),
    (7,   "🥉 Бронза"),
    (13,  "🥉 Бронза"),
    (14,  "🥈 Серебро"),
    (29,  "🥈 Серебро"),
    (30,  "🥇 Золото"),
    (59,  "🥇 Золото"),
    (60,  "💠 Платина"),
    (99,  "💠 Платина"),
    (100, "👑 Легенда"),
    (364, "👑 Легенда"),
    (365, "🏛️ Бессмертный"),
    (1000, "🏛️ Бессмертный"),
])
def test_get_rank_ladder(streak, expected):
    assert get_rank(streak) == expected


# ---------- STREAK_ACHIEVEMENTS: порог 60+ существует ----------

def test_achievements_cover_beyond_60_days():
    thresholds = [th for th, _ in STREAK_ACHIEVEMENTS]
    assert thresholds == sorted(thresholds), "пороги должны быть по возрастанию"
    assert thresholds[0] == 1
    for milestone in (3, 7, 14, 30, 60, 100, 365):
        assert milestone in thresholds, f"нет достижения на {milestone} дней"


def test_achievements_names_unique():
    names = [name for _, name in STREAK_ACHIEVEMENTS]
    assert len(names) == len(set(names))


# ---------- get_motivation_key: сообщение после опроса есть и за 60+ ----------

@pytest.mark.parametrize("streak,expected", [
    (0,  None),
    (2,  None),
    (3,  "streak_3"),
    (6,  "streak_3"),
    (7,  "streak_7"),
    (14, "streak_14"),
    (30, "streak_30"),
    (59, "streak_30"),
    (60, "streak_60"),
    (99, "streak_60"),
    (100, "streak_100"),
    (364, "streak_100"),
    (365, "streak_365"),
])
def test_get_motivation_key(streak, expected):
    assert get_motivation_key(streak) == expected


def test_motivation_keys_exist_in_config():
    from config import MOTIVATIONAL_MESSAGES
    for key in ("streak_3", "streak_7", "streak_14", "streak_30",
                "streak_60", "streak_100", "streak_365"):
        assert key in MOTIVATIONAL_MESSAGES, f"в конфиге нет сообщения {key}"


# ---------- get_rank_plain: для PNG-отчётов (PIL не рисует эмодзи) ----------

@pytest.mark.parametrize("streak,expected", [
    (0, "Новичок"),
    (30, "Золото"),
    (60, "Платина"),
    (100, "Легенда"),
    (365, "Бессмертный"),
])
def test_get_rank_plain_matches_rank_ladder(streak, expected):
    from gamification import get_rank_plain
    assert get_rank_plain(streak) == expected
