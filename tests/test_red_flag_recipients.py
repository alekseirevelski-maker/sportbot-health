# -*- coding: utf-8 -*-
"""Red-flag алерт опроса уходит тренеру команды (в дополнение к врачу).

Владелец: тренер должен видеть red-flag сразу после опроса спортсмена своей
команды. Обычные опросы без флагов тренеру НЕ пушатся отдельным алертом
(существующий coach-notify отправляет сводку каждого опроса — не трогаем).
"""
from database import Database
from handlers_survey import red_flag_recipients


def test_red_flag_recipients(tmp_path):
    db = Database(db_path=str(tmp_path / "t.db"))
    athlete_tg = 777001
    trainer_id = 555001
    other_trainer_id = 555002
    doctor_id = 4242

    db.register_athlete(athlete_tg, "u", "Спорт Спорт", "U17", "ЧБК")
    db.set_coach_teams(trainer_id, ["ЧБК"])
    db.set_coach_teams(other_trainer_id, ["СДЮШОР-2"])

    rec = red_flag_recipients(db, "ЧБК", {doctor_id}, sender_id=athlete_tg)

    assert trainer_id in rec        # тренер команды получает red-flag
    assert doctor_id in rec         # врач (full_access) получает red-flag
    assert athlete_tg not in rec    # отправитель исключён
    assert other_trainer_id not in rec  # тренер другой команды — нет


def test_red_flag_recipients_sender_coach_excluded(tmp_path):
    """Если опрос отправил сам тренер своей команды — не дублируем ему."""
    db = Database(db_path=str(tmp_path / "t2.db"))
    trainer_id = 555001
    doctor_id = 4242
    db.set_coach_teams(trainer_id, ["ЧБК"])

    rec = red_flag_recipients(db, "ЧБК", {doctor_id}, sender_id=trainer_id)

    assert rec == [doctor_id]


def test_red_flag_recipients_no_team(tmp_path):
    """Без команды — только врачи, без падений."""
    db = Database(db_path=str(tmp_path / "t3.db"))
    doctor_id = 4242

    rec = red_flag_recipients(db, None, {doctor_id}, sender_id=777003)

    assert rec == [doctor_id]
