# -*- coding: utf-8 -*-
"""Default-deny гейт callback'ов.

Единая точка классификации callback_data по ролям BEFORE роутинга в
`SportHealthBot.callback_handler` (bot.py). Точечные проверки внутри
хендлеров сохраняются (defense in depth) — этот модуль их НЕ заменяет.

Роли:
- `'full'`  — полный доступ: админы (ADMIN_TELEGRAM_IDS) или врачи
              (`self._is_full_access(user_id)`);
- `'coach'` — тренер или полный доступ (`self._is_admin_or_coach(user_id)`);
- `None`    — собственный flow спортсмена, без ограничений.

Правила:
- Матчинг: `data == prefix` или `data.startswith(prefix)`, самые длинные
  префиксы раньше коротких (`bc_import_to_` раньше `bc_`).
- Классификация выведена из ВСЕХ веток callback_handler (bot.py ~1180–1456).
- Сомнительный/неизвестный callback_data → `'full'` (default-deny по духу
  спеки): неизвестная кнопка не должна открывать ничего.
"""
from typing import Optional

# (префикс, роль). Порядок в _TABLE логический (по группам); наружу
# экспортируется CALLBACK_ROLE_PREFIXES, отсортированный по убыванию длины.
_TABLE: list[tuple[str, Optional[str]]] = [
    # ============ FULL: администрирование и чужие данные ============
    # admin_report, admin_manage, admin_add_athlete, admin_add_team_*,
    # admin_add_age_*, admin_complaints, admin_complaints_page_*
    ("admin_", "full"),
    # список спортсменов с анкетами (show_questionnaire_list: _is_full_access)
    ("questionnaire_list", "full"),
    # просмотр чужой анкеты
    ("qview_", "full"),
    # блокировки: ban_menu, ban_page_*, ban_<id>
    ("ban_", "full"),
    ("unban_", "full"),
    # удаление спортсменов
    ("delete_athlete", "full"),
    ("delconfirm_", "full"),
    ("del_", "full"),
    # ежедневный админ-отчёт
    ("daily_report", "full"),
    # напоминания: настройка и рассылка всем спортсменам
    ("reminder_settings", "full"),
    ("set_reminder_", "full"),
    ("send_reminder_now", "full"),
    ("send_q_reminder", "full"),
    # экспорт анкет в Excel (внутри — _is_admin_or_coach + _scoped_athletes,
    # но полный список анкет — чувствительнее CSV-выгрузки; export_csv — ниже, coach)
    ("export_q", "full"),
    # PDF-отчёты по спортсменам: pdf_report_menu, pdf_<id>
    ("pdf_", "full"),
    # состав команды (show_team: _is_full_access); НЕ путать с team_ (регистрация)
    ("team_list", "full"),
    # управление врачами: doctor_menu, doctor_add, doctor_del_*
    ("doctor_", "full"),
    # ответ спортсмену от админа/врача
    ("reply_athlete_", "full"),
    # чужие данные весов: плоский список всех команд и импорт CSV на спортсмена
    ("bc_overview_all", "full"),
    ("bc_import_to_", "full"),
    # управление тренерами: coach_menu, coach_add, coach_edit_*, coach_toggle_*,
    # coach_save_*, coach_del_* (show_coach_admin/coach_add_ask: _is_full_access).
    # Тренерские ПРОСМОТРЫ (coach_team_view, coach_week_summary) — ниже, они длиннее.
    ("coach_", "full"),

    # ============ COACH: командные просмотры, отчёты, карточки ============
    # список спортсменов и его пагинация (внутри — _is_admin_or_coach + _scoped_athletes)
    ("athlete_list", "coach"),
    ("athlete_page_", "coach"),
    # CSV-выгрузка своих команд (внутри — _is_admin_or_coach + _scoped_athletes)
    ("export_csv", "coach"),
    # тренерская панель (внутри хендлеров — db.is_coach / scope по командам)
    ("coach_team_view", "coach"),
    ("coach_week_summary", "coach"),
    # рекомендации конкретному спортсмену (внутри — scope-проверка)
    ("recs_", "coach"),
    # командные отчёты: report_export_menu, report_team_*, report_period_*,
    # report_watches, report_watch_team_*, report_watch_period_*,
    # report_body_comp, report_bc_period_* (внутри — _is_admin_or_coach + scope)
    ("report_", "coach"),
    # весы/состав тела: bc_menu, bc_overview, bc_profiles, bc_page_*, bc_map_*,
    # bc_assign_*, bc_view_* (scope внутри хендлера), bc_team_*
    # (bc_overview_all / bc_import_to_ / bc_my_trends длиннее — матчатся раньше)
    # выбор спортсменом СВОЕГО слота весов — собственный flow (игрок жмёт кнопку
    # «какой профиль мой»); префикс длиннее, чем «bc_», поэтому матчится раньше
    ("bc_slot_", None),
    ("bc_", "coach"),
    # дашборд тренера (Task 4: dash_today и др.)
    ("dash_", "coach"),

    # ============ NONE: собственный flow спортсмена ============
    ("main_menu", None),
    ("help_menu", None),
    ("start_reg", None),
    # согласие на обработку данных
    ("consent_", None),
    # регистрация: reg_gender_*, reg_*, выбор команды team_* (team_list — выше)
    ("reg_", None),
    ("team_", None),
    # свои данные: my_progress, my_goals, my_stats, my_profile, my_charts, my_cycle
    ("my_", None),
    # сброс СВОЕГО аккаунта (данные только вызывающего, q.from_user.id)
    ("reset_account", None),
    ("confirm_reset", None),
    # свои цели
    ("goal_", None),
    # свои графики
    ("chart_", None),
    ("achievements", None),
    ("view_today", None),
    ("update_weight", None),
    # ежедневный опрос
    ("cancel_survey", None),
    ("do_survey", None),
    ("srv_", None),
    ("painloc_", None),
    ("paingame_", None),
    ("illness_", None),
    ("analg_", None),
    ("train_", None),
    # своя анкета здоровья: questionnaire, q_restart, q_<n> (qview_/questionnaire_list — выше)
    ("questionnaire", None),
    ("q_", None),
    # свои часы
    ("watch_", None),
    # свои весы/состав тела: body_comp_menu, body_comp_history,
    # scale_upload_start, bc_my_trends (bc_my_trends длиннее bc_ — матчится раньше)
    ("body_comp_", None),
    ("scale_", None),
    ("bc_my_trends", None),
    # свой пол и цикл
    ("set_gender", None),
    ("gender_", None),
    ("cycle_", None),
    ("cyc_", None),
    ("anchor_", None),
    # жалобы и консультация
    ("complaint_", None),
    ("consultation_", None),
    ("consult_", None),
    # клубы (мульти-тенантность): свой спорт/клуб
    ("sport_", None),
    ("club_", None),
]

#: Префикс → роль ('full' | 'coach' | None), длинные префиксы раньше коротких.
CALLBACK_ROLE_PREFIXES: list[tuple[str, Optional[str]]] = sorted(
    _TABLE, key=lambda item: -len(item[0])
)


def required_role_for(callback_data: str) -> Optional[str]:
    """Минимальная роль для callback_data: 'full', 'coach' или None (свободно).

    Незнакомый callback_data → 'full' (default-deny): неизвестные кнопки
    не должны открывать ничего, даже если ветка появится в диспетчере позже.
    """
    for prefix, role in CALLBACK_ROLE_PREFIXES:
        if callback_data == prefix or callback_data.startswith(prefix):
            return role
    return "full"
