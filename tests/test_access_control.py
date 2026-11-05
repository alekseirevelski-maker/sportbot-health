# -*- coding: utf-8 -*-
"""Default-deny гейт callback'ов (access_control.required_role_for).

Классификация выведена из ВСЕХ веток callback_handler (bot.py ~1180–1456):
admin/doctor/ban/delete/qview/export_q/чужие-bc-данные → 'full';
командные просмотры, отчёты, карточки, athlete_list/export_csv → 'coach';
собственные flow спортсмена (включая self-reset) → None;
незнакомый callback_data → 'full'.
"""
from access_control import CALLBACK_ROLE_PREFIXES, required_role_for

from database import Database


# === Нижняя граница из брифа (Task 1, Step 1) ===

def test_sensitive_prefixes_require_full_access():
    for d in ("ban_5", "unban_3", "ban_menu", "bc_overview_all", "bc_import_to_7",
              "del_2", "delconfirm_2", "delete_athlete", "admin_manage", "qview_1"):
        assert required_role_for(d) == "full", d


def test_coach_prefixes():
    for d in ("bc_team_ЧБК", "bc_view_9", "dash_today", "coach_team_view",
              "athlete_list", "athlete_page_2", "export_csv"):
        assert required_role_for(d) == "coach", d


def test_athlete_callbacks_free():
    for d in ("main_menu", "do_survey", "my_stats", "chart_7", "bc_my_trends", "consent_accept",
              "reset_account", "confirm_reset"):
        assert required_role_for(d) is None, d


# === Полный перечень чувствительных префиксов из диспетчера ===

FULL_ACCESS_CALLBACKS = (
    # админ-панель и управление ботом
    "admin_report", "admin_manage", "admin_add_athlete", "admin_add_team_ЧБК",
    "admin_add_age_U17", "admin_complaints", "admin_complaints_page_7",
    # блокировки / удаление спортсменов
    "ban_menu", "ban_page_2", "ban_5", "unban_3",
    "delete_athlete", "del_2", "delconfirm_2",
    # отчёты/экспорт только для полного доступа
    "daily_report", "export_q", "pdf_report_menu", "pdf_5",
    # чужие анкеты и списки
    "questionnaire_list", "qview_1",
    # напоминания (рассылка всем спортсменам)
    "reminder_settings", "set_reminder_off", "set_reminder_09:30",
    "send_reminder_now", "send_q_reminder",
    # состав команды (show_team требует _is_full_access)
    "team_list",
    # врачи и тренеры (управление ролями)
    "doctor_menu", "doctor_add", "doctor_del_7",
    "coach_menu", "coach_add", "coach_edit_7", "coach_toggle_7",
    "coach_save_7", "coach_del_7",
    # ответ спортсмену от админа
    "reply_athlete_5",
    # чужие данные весов
    "bc_overview_all", "bc_import_to_7",
)

COACH_CALLBACKS = (
    # список спортсменов и CSV-экспорт (внутри — _is_admin_or_coach + _scoped_athletes)
    "athlete_list", "athlete_page_2", "export_csv",
    # меню и обзоры весов (внутри хендлеров — _is_admin_or_coach)
    "bc_menu", "bc_overview", "bc_profiles", "bc_page_2", "bc_map_3",
    "bc_assign_1_2", "bc_view_9", "bc_team_ЧБК",
    # тренерская панель
    "coach_team_view", "coach_week_summary", "recs_4",
    # командные отчёты (внутри — scope по командам тренера)
    "report_export_menu", "report_team_ЧБК", "report_period_30",
    "report_watches", "report_watch_team_ЧБК", "report_watch_period_14",
    "report_body_comp", "report_bc_period_30",
    # дашборд (Task 4)
    "dash_today",
)

ATHLETE_OWN_CALLBACKS = (
    "main_menu", "help_menu", "start_reg",
    "consent_accept", "consent_decline",
    "reg_gender_male", "reg_U17", "team_ЧБК",
    "my_progress", "my_goals", "my_stats", "my_profile", "my_charts", "my_cycle",
    "goal_add_weight", "goal_set_5", "goal_done_3",
    "chart_7", "achievements", "view_today", "update_weight",
    "cancel_survey", "do_survey", "srv_3",
    "reset_account", "confirm_reset",
    "painloc_knee", "paingame_2", "illness_1", "analg_0",
    "train_yes", "train_no",
    "questionnaire", "q_restart", "q_5",
    "watch_data", "watch_garmin",
    "body_comp_menu", "body_comp_history", "scale_upload_start", "bc_my_trends",
    "set_gender", "gender_first_female", "gender_mandatory_male", "gender_female",
    "cycle_len_custom", "cycle_len_28", "cycle_5",
    "anchor_2026-01-01", "cyc_set_anchor", "cyc_daily",
    "complaint_text", "complaint_none",
    "consultation_start", "consult_text", "consult_no", "consult_2026-05-01",
    "sport_football", "club_confirm_1", "club_spb",
)


def test_full_access_inventory():
    for d in FULL_ACCESS_CALLBACKS:
        assert required_role_for(d) == "full", d


def test_coach_inventory():
    for d in COACH_CALLBACKS:
        assert required_role_for(d) == "coach", d


def test_athlete_own_inventory():
    for d in ATHLETE_OWN_CALLBACKS:
        assert required_role_for(d) is None, d


def test_longest_prefix_wins():
    # bc_import_to_ (full) раньше bc_ (coach)
    assert required_role_for("bc_import_to_7") == "full"
    # bc_overview_all (full) раньше bc_overview/bc_ (coach)
    assert required_role_for("bc_overview_all") == "full"
    assert required_role_for("bc_overview") == "coach"
    # bc_my_trends (своё) раньше bc_ (coach)
    assert required_role_for("bc_my_trends") is None
    # team_list (full) раньше team_ (регистрация, None)
    assert required_role_for("team_list") == "full"
    assert required_role_for("team_ЧБК") is None
    # questionnaire_list (full) раньше questionnaire (None)
    assert required_role_for("questionnaire_list") == "full"
    assert required_role_for("questionnaire") is None
    # qview_ (full) раньше q_ (None)
    assert required_role_for("qview_1") == "full"
    assert required_role_for("q_5") is None
    # coach_team_view/coach_week_summary (coach) раньше coach_ (full)
    assert required_role_for("coach_team_view") == "coach"
    assert required_role_for("coach_week_summary") == "coach"
    assert required_role_for("coach_add") == "full"


def test_unknown_callback_defaults_to_full():
    # default-deny: незнакомый callback_data трактуется как самый чувствительный
    assert required_role_for("totally_unknown_xyz") == "full"
    assert required_role_for("") == "full"


def test_prefixes_sorted_longest_first():
    lengths = [len(p) for p, _ in CALLBACK_ROLE_PREFIXES]
    assert lengths == sorted(lengths, reverse=True)


# === Step 8: scope-проверка на уровне БД (как в bc_view_athlete) ===

def test_coach_scope_rejects_other_team_athlete(tmp_path):
    db = Database(db_path=str(tmp_path / "test_scope.db"))
    db.register_athlete(101, "athlete_b", "Безопасный Б", "U17", "КомандаБ")
    athlete_b = db.get_athlete_by_telegram_id(101)
    db.register_athlete(102, "athlete_a", "Атлетов А", "U17", "КомандаА")
    athlete_a = db.get_athlete_by_telegram_id(102)

    db.set_coach_teams(201, ["КомандаА"])  # тренер команды А
    db.set_coach_teams(202, ["КомандаБ"])  # тренер команды Б

    teams_coach_a = set(db.get_coach_teams(201))
    assert db.is_coach(201)
    # тренер команды А НЕ проходит scope для спортсмена команды Б
    assert athlete_b["team"] not in teams_coach_a
    # но проходит для спортсмена своей команды
    assert athlete_a["team"] in teams_coach_a
    # у простого пользователя команд нет
    assert set(db.get_coach_teams(301)) == set()
    assert athlete_b["team"] not in set(db.get_coach_teams(301))
    # тренер команды Б — наоборот
    assert athlete_b["team"] in set(db.get_coach_teams(202))
    assert athlete_a["team"] not in set(db.get_coach_teams(202))
