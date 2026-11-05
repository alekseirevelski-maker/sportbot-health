# -*- coding: utf-8 -*-
"""Дашборд «🔔 На сегодня» — чистая сборка экрана (без Telegram/async).

Блоки (сверху вниз):
  🔴 красные флаги сегодняшних опросов (illness_flag, analgesics, pain_on_game,
     pain_nrs≥5, жалобы с обрезкой, пульс вне индивидуального коридора 2 дня
     подряд, готовность/Hooper ниже порогов ALERT_THRESHOLDS);
  🟡 тренды — паттерны «3 дня подряд» из `_check_trend_alerts` (bot.py) +
     цикл-алерты `cycle_ui.cycle_alert_lines` (для тренера — только его команды,
     решение владельца 2026-11-05);
  ⚪ слепые зоны — не сдали опрос сегодня + заблокировали бота (bot_blocked).

Scope гарантирует вызывающий (`_scoped_athletes`): ФИО/команды берутся только
из `scoped_athletes`. Batch-запросы, без N+1: `get_last_n_days_all(3)` один раз
на всех, `get_individual_baseline` — только для спортсменов с ≥3 днями данных.

Hooper/пороги: опрос собирается на шкале «7=хорошо» из 3 шкал (сон+утомление+
боль, стресс из опроса убран), а `ALERT_THRESHOLDS` задуманы для классического
Hooper Index «больше = хуже» (critical > warning, калибровка под 4 шкалы).
Поэтому здесь сумма приводится к классическому направлению: distress = Σ(8-балл)
по предметам, и distress ≥ critical → 🔴, ≥ warning → 🟡. Для U17+ порог
critical=22 недостижим на 3 шкалах (макс. 21) — красным остаётся только Hooper
у младших групп; у старших тяжёлые состояния попадают в 🟡 (см. отчёт Task 4).
"""
import logging
from typing import Dict, List, Optional, Tuple

from config import ALERT_THRESHOLDS
from cycle_ui import cycle_alert_lines

logger = logging.getLogger(__name__)

_SEPARATOR = "━━━━━━━━━━━━━━━━━━━━━━━━━━"
_HOOPER_ITEMS = ("sleep_score", "fatigue_score", "muscle_soreness")
_COMPLAINT_CLIP = 60


def _clip(text: str, limit: int = _COMPLAINT_CLIP) -> str:
    text = str(text).strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _hooper_distress(survey: Dict) -> Optional[int]:
    """Классический Hooper Index по 3 шкалам опроса (больше = хуже), None если
    не все компоненты сданы (иначе сумма занижается и флаг ложный)."""
    vals = [survey.get(k) for k in _HOOPER_ITEMS]
    if any(v is None for v in vals):
        return None
    return sum(8 - int(v) for v in vals)


def _hooper_severity(survey: Dict, age_group) -> Optional[str]:
    """'red' / 'yellow' / None по ALERT_THRESHOLDS[age_group]."""
    distress = _hooper_distress(survey)
    thr = ALERT_THRESHOLDS.get(age_group) if age_group else None
    if distress is None or not thr:
        return None
    if distress >= thr["critical"]:
        return "red"
    if distress >= thr["warning"]:
        return "yellow"
    return None


def _hr_threshold(bl: Dict) -> Optional[float]:
    """Порог «выше личного коридора»: медиана + max(1.5σ, +10%) — как в
    `_individual_recs` (handlers_survey.py) и `_check_trend_alerts` (bot.py)."""
    if not bl or bl.get("median_hr") is None:
        return None
    std = bl.get("std_hr", 0) or 0
    return bl["median_hr"] + max(1.5 * std, bl["median_hr"] * 0.10)


def _today_red_flags(survey: Dict, bl: Optional[Dict],
                     prev: Optional[Dict]) -> List[str]:
    """Красные флаги сегодняшнего опроса — тот же набор триггеров, что уходит
    врачу/тренеру в red-flag пуше (handlers_survey.py) + личный коридор пульса."""
    flags = []
    nrs = survey.get("pain_nrs")
    if nrs is not None and nrs >= 5:
        loc = survey.get("pain_location") or ""
        flags.append(f"🤕 Боль {nrs}/10" + (f" ({loc})" if loc else ""))
    if survey.get("analgesics"):
        flags.append("💊 Обезболивающие (маскируют травму)")
    if survey.get("illness_flag"):
        flags.append(f"🤒 Болезнь: {survey['illness_flag']}")
    if survey.get("pain_on_game"):
        flags.append("🏀 Болит на игре/тренировке")
    if survey.get("complaints"):
        flags.append(f"💬 Жалобы: {_clip(survey['complaints'])}")
    # Пульс вне индивидуального коридора 2 дня подряд (сегодня + вчера)
    thr = _hr_threshold(bl) if bl else None
    if thr is not None and prev is not None:
        cur_hr, prev_hr = survey.get("resting_hr"), prev.get("resting_hr")
        if cur_hr and prev_hr and cur_hr > thr and prev_hr > thr:
            flags.append(f"❤️ Пульс выше личной нормы 2 дня подряд "
                         f"({cur_hr} vs ~{int(bl['median_hr'])}/30д)")
    return flags


def _trend_lines(name: str, team: str, days: List[Dict],
                 bl: Optional[Dict]) -> List[str]:
    """Паттерны «N дней подряд» из `_check_trend_alerts` (bot.py), on-demand.

    Пропуск опроса 2+ дня здесь НЕ дублируем — его покрывает блок «не сдали
    сегодня» (слепые зоны)."""
    out = []
    if len(days) < 3:
        return out

    # Паттерн 1: пульс 3 дня подряд выше baseline + max(1.5σ, 10%)
    thr = _hr_threshold(bl) if bl else None
    if thr is not None:
        if all(d.get("resting_hr") and d["resting_hr"] > thr for d in days):
            hrs = [d["resting_hr"] for d in days]
            out.append(f"❤️ пульс 3 дня подряд выше нормы "
                       f"({hrs[0]}→{hrs[1]}→{hrs[2]}, норма ~{int(bl['median_hr'])})")

    # Паттерн 2: сон 3 дня подряд ниже baseline - max(1.5σ, 1.0)
    if bl and bl.get("median", {}).get("sleep") is not None:
        sleep_thr = bl["median"]["sleep"] - max(
            1.5 * (bl.get("std", {}).get("sleep", 0) or 0), 1.0)
        if all(d.get("sleep_score") and d["sleep_score"] < sleep_thr for d in days):
            sleeps = [d["sleep_score"] for d in days]
            out.append(f"😴 сон 3 дня подряд ниже нормы "
                       f"({sleeps[0]}→{sleeps[1]}→{sleeps[2]})")

    # Паттерн 3: готовность 3 дня подряд < 5
    readiness = [d.get("readiness") for d in days]
    if all(r is not None and r < 5 for r in readiness):
        out.append(f"🎯 готовность 3 дня подряд < 5 "
                   f"({readiness[0]}→{readiness[1]}→{readiness[2]})")

    # Паттерн 4: боль NRS ≥ 5 два дня подряд
    pain = [d.get("pain_nrs") for d in days[-2:]]
    if all(p is not None and p >= 5 for p in pain):
        loc = days[-1].get("pain_location") or ""
        out.append(f"🤕 боль NRS ≥ 5 два дня подряд ({pain[0]}→{pain[1]})"
                   + (f" {loc}" if loc else ""))
    return out


def build_today_dashboard(db, user_id: int, scoped_athletes: List[Dict],
                          today) -> Tuple[str, List[List[Tuple[str, str]]]]:
    """Собрать экран «🔔 На сегодня».

    Возвращает (Markdown-текст, ряды кнопок для `SportHealthBot.kb`):
    по кнопке `bc_view_<id>` на каждую красную/жёлтую строку + «Обновить»
    (тот же `dash_today`) и «Главное меню». `user_id` — только для логов:
    состав экрана полностью определяется `scoped_athletes` (scope гарантирован
    вызывающим)."""
    by_id = {a["id"]: a for a in scoped_athletes}
    # Batch: сегодняшние опросы (SELECT *), кто сдал, 3 дня истории — по запросу на всех
    today_map = db.get_today_survey_map() or {}
    has_survey = db.get_has_survey_today_map() or {}
    n_days_map = db.get_last_n_days_all(3) or {}

    red_rows: List[Tuple[Dict, str]] = []
    yellow_rows: List[Tuple[Dict, str]] = []

    for a in scoped_athletes:
        aid = a["id"]
        name = a.get("full_name") or f"#{aid}"
        team = a.get("team") or "?"
        days = n_days_map.get(aid) or []
        # baseline — только у кого достаточно истории (нет N+1 по всем)
        bl = db.get_individual_baseline(aid, 30) if len(days) >= 3 else None

        survey = today_map.get(aid)
        if survey:
            # «вчера» — последний опрос до today (для правила 2 дня подряд)
            prev = next((d for d in reversed(days)
                         if str(d.get("survey_date")) != today.isoformat()), None)
            reasons = _today_red_flags(survey, bl, prev)
            severity = _hooper_severity(survey, a.get("age_group"))
            if severity == "red":
                good = sum(int(survey[k]) for k in _HOOPER_ITEMS)
                reasons.append(f"😵 Готовность критически низкая (Hooper {good}/21)")
            if reasons:
                red_rows.append((a, f"• *{name}* ({team}): " + "; ".join(reasons)))
                continue
            if severity == "yellow":
                good = sum(int(survey[k]) for k in _HOOPER_ITEMS)
                yellow_rows.append(
                    (a, f"• *{name}* ({team}): 😪 Готовность ниже нормы "
                        f"(Hooper {good}/21)"))

        for line in _trend_lines(name, team, days, bl):
            yellow_rows.append((a, f"• *{name}* ({team}): {line}"))

    # Цикл-алерты (задержка/аменорея) — только по scoped (тренер видит свои команды).
    # Дедуп last_sent не применяется: экран on-demand, показываем все активные.
    for key, line in cycle_alert_lines(scoped_athletes, today, {}):
        try:
            aid = int(key.split("_")[2])
        except (IndexError, ValueError):
            continue
        a = by_id.get(aid)
        if a is not None:
            yellow_rows.append((a, f"• {line}"))

    # ⚪ Слепые зоны
    not_passed = [a for a in scoped_athletes if not has_survey.get(a["id"], False)]
    blocked = [a for a in scoped_athletes if a.get("bot_blocked")]

    n_done = len(scoped_athletes) - len(not_passed)
    text = (f"🔔 *На сегодня* — {today.strftime('%d.%m.%Y')}\n"
            f"{_SEPARATOR}\n\n")

    if not red_rows and not yellow_rows:
        text += (f"🟢 Тревог нет, {n_done} из {len(scoped_athletes)} "
                 f"сдали опрос\n\n")
    else:
        if red_rows:
            text += "🔴 *Красные флаги — сегодняшние опросы*\n"
            text += "\n".join(line for _a, line in red_rows) + "\n\n"
        if yellow_rows:
            text += "🟡 *Тренды*\n"
            text += "\n".join(line for _a, line in yellow_rows) + "\n\n"

    if not_passed:
        text += (f"⚪ *Не сдали сегодня ({len(not_passed)} "
                 f"из {len(scoped_athletes)})*\n")
        for a in not_passed:
            text += (f"• {a.get('full_name') or '#' + str(a['id'])} "
                     f"({a.get('team') or '?'})\n")
        text += "\n"
    if blocked:
        text += "🚫 *Заблокировали бота — мониторинг прерван*\n"
        for a in blocked:
            text += (f"• {a.get('full_name') or '#' + str(a['id'])} "
                     f"({a.get('team') or '?'})\n")
        text += "\n"

    # Кнопки: карточка на каждую красную/жёлтую строку (без дублей) + сервисные
    buttons: List[List[Tuple[str, str]]] = []
    seen = set()
    for a, _line in red_rows + yellow_rows:
        if a["id"] in seen:
            continue
        seen.add(a["id"])
        buttons.append([(f"📋 {a.get('full_name') or a['id']}",
                         f"bc_view_{a['id']}")])
    buttons.append([("🔄 Обновить", "dash_today")])
    buttons.append([("🏠 Главное меню", "main_menu")])

    logger.debug("dashboard for user %s: %d red, %d yellow, %d missing, %d blocked",
                 user_id, len(red_rows), len(yellow_rows),
                 len(not_passed), len(blocked))
    return text, buttons
