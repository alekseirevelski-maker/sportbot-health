"""
Миксин: AthleteMixin
Автоматически извлечён из bot.py
"""

import logging
from datetime import datetime, date, timedelta
from typing import TYPE_CHECKING
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from config import HR_NORMS, AGE_GROUP_YEARS, BMI_PERCENTILES
from cycle_medicine import CYCLE_LENGTH_MEDIAN, get_cycle_phase_info, AGE_SPECIFIC

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from bot import SportHealthBot


class AthleteMixin:
    """Миксин для athlete."""

    async def show_main_menu(self, update, ctx):
        from bot import get_rank, score_bar
        user = update.effective_user

        # Тренер — админ своей команды, опрос не проходит
        if self.db.is_coach(user.id):
            text = (
                f"🏀 *ЧБК — Мониторинг здоровья*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"👕 Тренерская панель — команда и отчёты:\n\n"
            )
            buttons = [
                [(f"👕 Моя команда", "coach_team_view")],
                [(f"📊 Мой отчёт (Excel)", "report_export_menu")],
            ]
            if self._is_admin_or_coach(user.id):
                # Весы видят и тренер (свои команды), и врач (вся база)
                buttons.insert(0, [(f"⚖️ Весы и состав тела", "bc_menu")])
            if self._is_full_access(user.id):
                buttons.insert(0, [(f"📋 Отчет для врача", "admin_report")])
                buttons.insert(1, [(f"⚙️ Управление", "admin_manage")])
            # Дашборд «На сегодня» — первая строка меню тренера/врача (роль гейтится)
            if self._is_admin_or_coach(user.id):
                buttons.insert(0, [(f"🔔 На сегодня", "dash_today")])
            buttons.append([(f"❓ Помощь", "help_menu")])
            if update.callback_query:
                await update.callback_query.edit_message_text(text, reply_markup=self.kb(buttons), parse_mode="Markdown")
            else:
                _logo = self._logo_file()
                if _logo is not None:
                    try:
                        await update.message.reply_photo(_logo)
                    except Exception as e:
                        logger.warning(f"logo in menu failed, fallback to text: {e}")
                    finally:
                        try: _logo.close()
                        except Exception: pass
                await update.message.reply_text(text, reply_markup=self.kb(buttons), parse_mode="Markdown")
            return

        athlete = self.db.get_athlete_by_telegram_id(user.id)
        if not athlete:
            await self.cmd_start(update, ctx)
            return

        if self.db.is_athlete_banned(athlete["id"]):
            text = "🔒 Твой аккаунт заблокирован. Обратись к администратору."
            if update.callback_query:
                await update.callback_query.edit_message_text(text, reply_markup=self.kb([[(f"🏠 Главная", "main_menu")]]))
            else:
                await update.message.reply_text(text, reply_markup=self.kb([[(f"🏠 Главная", "main_menu")]]))
            return

        # Если пол не указан — блокируем меню
        if not athlete.get("gender"):
            await self._ask_gender_mandatory(update, ctx)
            return

        today = self.db.get_survey_today(athlete["id"])
        has_survey = today is not None
        streak = athlete.get("survey_streak", 0)
        hour = datetime.now().hour
        greet = "Доброе утро" if hour < 12 else ("Добрый день" if hour < 18 else "Добрый вечер")

        text = (
            f"🏀 *ЧБК — Мониторинг здоровья*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 *{athlete['full_name']}* | {athlete['age_group']}\n"
            f"🏀 Команда: *{athlete.get('team', '?')}*\n"
            f"🔥 Серия: *{streak} дней* | {get_rank(streak)}\n"
            f"📊 Всего опросов: *{athlete.get('total_surveys', 0)}*\n\n"
        )

        if has_survey:
            text += (
                f"✅ *Опрос за сегодня пройден!*\n\n"
                f"😴 Сон: {score_bar(today.get('sleep_score', 0))}\n"
                f"😩 Утомление: {score_bar(today.get('fatigue_score', 0))}\n"
            )
            if today.get("resting_hr"):
                text += f"❤️ Пульс: {today['resting_hr']} уд/мин\n"
        else:
            text += f"⚠️ *Опрос за сегодня ещё не пройден!*\n"

        text += f"\n{greet}, {self._first_name(athlete)}! 💪"

        # Минимальное меню спортсмена (простые, интуитивные действия)
        q_done = self.db.has_questionnaire(athlete["id"]) and not self.db.has_incomplete_questionnaire(athlete["id"])
        buttons = [
            [(f"📝 Пройти опрос" if not has_survey else f"✅ Сегодня пройден",
              "do_survey" if not has_survey else "view_today")],
            [(f"📈 Мой прогресс", "my_progress")],
            [(f"🎯 Мои цели", "my_goals")],
            [(f"📊 Мои показатели", "my_stats")],
            [(f"⌚ Данные часов", "watch_data")],
            [(f"⚖️ Состав тела", "body_comp_menu")],
            ([] if q_done else [(f"📋 Заполнить анкету", "questionnaire")]),
            [(f"📅 Запись к врачу", "consultation_start")],
            [(f"❓ Помощь", "help_menu")],
        ]

        # Админ-блок (виден только врачу/тебе)
        if self._is_full_access(user.id):
            buttons.insert(0, [(f"📋 Отчет для врача", "admin_report")])
            buttons.insert(1, [(f"📊 Экспорт в Excel", "report_export_menu")])
            buttons.insert(2, [(f"⚖️ Весы и состав тела", "bc_menu")])
            buttons.insert(3, [(f"⚙️ Управление", "admin_manage")])
            # Дашборд «На сегодня» — первая строка меню (роль гейтится)
            buttons.insert(0, [(f"🔔 На сегодня", "dash_today")])

        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=self.kb(buttons), parse_mode="Markdown")
        else:
            # Вход из /start: эмблема отдельной карточкой + текстовое меню (которое можно редактировать)
            _logo = self._logo_file()
            if _logo is not None:
                try:
                    await update.message.reply_photo(_logo)
                except Exception as e:
                    logger.warning(f"logo in menu failed, fallback to text: {e}")
                finally:
                    try: _logo.close()
                    except Exception: pass
            await update.message.reply_text(text, reply_markup=self.kb(buttons), parse_mode="Markdown")

    # ==================== СТАРТ / РЕГИСТРАЦИЯ ====================


    async def show_profile(self, update, ctx):
        from bot import get_rank, score_bar, trend_arrow
        q = update.callback_query
        await q.answer()
        athlete = self.db.get_athlete_by_telegram_id(q.from_user.id)
        if not athlete:
            return

        stats = self.db.get_athlete_stats(athlete["id"], 7)
        prev = self.db.get_athlete_stats(athlete["id"], 14)
        streak = athlete.get("survey_streak", 0)

        # Рост/вес из анкеты + расчёт ИМТ (перцентили WHO для юных, взрослые категории 19+)
        bmi_line = ""
        qd = self.db.get_questionnaire(athlete["id"])
        if qd:
            h = qd.get("height")
            w = qd.get("weight")
            if h and w:
                try:
                    h = float(h); w = float(w)
                except (TypeError, ValueError):
                    h = w = 0
                if 0 < h < 400 and 0 < w < 300:
                    bmi = w / (h / 100) ** 2
                    age_group = athlete.get("age_group", "Pro")
                    years = AGE_GROUP_YEARS.get(age_group, 25)
                    gender_key = "m" if athlete.get("gender") == "male" else "f"
                    perc = BMI_PERCENTILES.get(years)
                    if perc and gender_key in perc:
                        # Юные (до 19): перцентили WHO. <5 — скрининг; >95 — без алерта (ИМТ слаб для баскетбола).
                        p5, p95 = perc[gender_key]
                        if bmi < p5:
                            cat, emoji = "ниже 5-й перцентили — стоит обсудить с врачом", "⚠️"
                        elif bmi > p95:
                            cat, emoji = "выше 95-й перцентили (для баскетбола не показатель)", "ℹ️"
                        else:
                            cat, emoji = "в пределах нормы (5-95 перцентиль)", "✅"
                        bmi_line = (f"📏 Рост: {int(h)} см | ⚖️ Вес: {int(w)} кг\n"
                                    f"🧮 ИМТ: {bmi:.1f} ({emoji} {cat})\n")
                    else:
                        # 19+/Pro: взрослые категории WHO
                        if bmi < 18.5:
                            cat, emoji = "недостаточный вес", "⚠️"
                        elif bmi < 25:
                            cat, emoji = "норма", "✅"
                        elif bmi < 30:
                            cat, emoji = "избыточный вес", "⚠️"
                        else:
                            cat, emoji = "ожирение", "🔴"
                        bmi_line = (f"📏 Рост: {int(h)} см | ⚖️ Вес: {int(w)} кг\n"
                                    f"🧮 ИМТ: {bmi:.1f} ({emoji} {cat})\n")

        # Готовность из 3 шкал (сон+утомление+боль)
        sleep_avg = stats.get("avg_sleep", 0) or 0
        fatigue_avg = stats.get("avg_fatigue", 0) or 0
        soreness_avg = stats.get("avg_soreness", 0) or 0
        hooper = sleep_avg + fatigue_avg + soreness_avg
        if hooper >= 17:
            ready = "🟢 Отличная"
        elif hooper >= 12:
            ready = "🟡 Средняя"
        else:
            ready = "🔴 Низкая"

        text = (
            f"👤 *Профиль*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"*{athlete['full_name']}*\n"
            f"📋 {athlete['age_group']} | 🏀 {athlete.get('team', '?')}\n"
            f"🔥 Серия: {streak}д | {get_rank(streak)}\n"
            f"📊 Опросов: {athlete.get('total_surveys', 0)}\n"
            f"{bmi_line}"
            f"{self._bc_trend_block(athlete['id'])}"
            f"📊 *Готовность:* {ready} ({hooper:.0f}/21)\n"
            f"*Средние за 7 дней:*\n"
            f"😴 Сон: {score_bar(round(sleep_avg))}\n"
            f"😩 Утомление: {score_bar(round(fatigue_avg))}\n"
            f"🤕 Боль: {score_bar(round(soreness_avg))}\n"
            f"❤️ Пульс: {(stats.get('avg_hr') or 0):.0f} уд/мин\n\n"
            f"*Тренды (7д vs 14д):*\n"
            f"😴 Сон: {trend_arrow(sleep_avg, prev.get('avg_sleep'))}\n"
            f"❤️ Пульс: {trend_arrow(stats.get('avg_hr'), prev.get('avg_hr'), True)}\n"
        )

        await q.edit_message_text(text, reply_markup=self.kb([
            [(f"📈 Графики", "my_charts")],
            ([] if athlete.get("gender") != "female" else [(f"📅 Мой цикл", "my_cycle")]),
            [(f"⚖️ Обновить вес", "update_weight")],
            [(f"⚖️ Состав тела", "body_comp_menu")],
            [(f"📋 Анкета / обновить", "questionnaire")],
            [(f"🔙 Назад", "main_menu")]
        ]), parse_mode="Markdown")


    async def show_my_progress(self, update, ctx):
        """Персональный дашборд: sparkline за 7 дней + личная норма."""
        q = update.callback_query
        await q.answer()
        athlete = self.db.get_athlete_by_telegram_id(q.from_user.id)
        if not athlete:
            return

        aid = athlete["id"]
        last7 = self.db.get_last_wellness(aid, 7)
        bl = self.db.get_individual_baseline(aid, 30)

        if not last7:
            await q.edit_message_text(
                "📊 *Мой прогресс*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "📭 Пока нет данных. Пройди опрос, и здесь появятся графики!",
                reply_markup=self.kb([[(f"📝 Пройти опрос", "do_survey"), (f"🏠 Главное меню", "main_menu")]]),
                parse_mode="Markdown"
            )
            return

        # Собираем данные за 7 дней (от старых к новым)
        dates = [r["survey_date"][5:] for r in reversed(last7)]
        sleep_vals = [r.get("sleep_score") for r in reversed(last7)]
        fatigue_vals = [r.get("fatigue_score") for r in reversed(last7)]
        soreness_vals = [r.get("muscle_soreness") for r in reversed(last7)]
        hr_vals = [r.get("resting_hr") for r in reversed(last7)]
        readiness_vals = [r.get("readiness") for r in reversed(last7)]

        # Hooper за каждый день
        hooper_vals = []
        for r in reversed(last7):
            h = sum(filter(None, [r.get("sleep_score"), r.get("fatigue_score"), r.get("muscle_soreness")]))
            hooper_vals.append(h if h else None)

        text = f"📈 *Мой прогресс — 7 дней*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        text += f"👤 {athlete['full_name']} | {athlete.get('age_group', '?')}\n\n"

        # Sparkline-линии (спарклайн из emoji-кружков)
        def _spark_line(vals, invert=False):
            clean = [v for v in vals if v is not None]
            if not clean:
                return "—"
            result = ""
            for v in vals:
                if v is None:
                    result += "—"
                    continue
                if invert:
                    if v <= 60: result += "🟢"
                    elif v <= 70: result += "🟡"
                    else: result += "🔴"
                else:
                    if v >= 6: result += "🟢"
                    elif v >= 4: result += "🟡"
                    else: result += "🔴"
            return result

        def _spark_line_hr(vals):
            result = ""
            for v in vals:
                if v is None:
                    result += "—"
                elif v <= 60: result += "🟢"
                elif v <= 70: result += "🟡"
                else: result += "🔴"
            return result

        def _trend(vals):
            clean = [v for v in vals if v is not None]
            if len(clean) < 2:
                return ""
            diff = clean[-1] - clean[0]
            if abs(diff) < 0.5:
                return "→ стабильно"
            return f"{'↑' if diff > 0 else '↓'} {abs(diff):.1f}"

        # Сон
        last_sleep = sleep_vals[-1] if sleep_vals[-1] is not None else "?"
        bl_sleep = bl.get("median", {}).get("sleep") if bl else None
        text += f"😴 *Сон:* {_spark_line(sleep_vals)} {last_sleep}/7\n"
        if bl_sleep: text += f"   Ваша норма: ~{bl_sleep:.1f} | {_trend(sleep_vals)}\n"
        text += "\n"

        # Готовность
        last_ready = readiness_vals[-1] if readiness_vals[-1] is not None else "?"
        text += f"🎯 *Готовность:* {_spark_line(readiness_vals)} {last_ready}/10\n"
        text += "\n"

        # Утомление
        last_fatigue = fatigue_vals[-1] if fatigue_vals[-1] is not None else "?"
        bl_fatigue = bl.get("median", {}).get("fatigue") if bl else None
        text += f"⚡ *Утомление:* {_spark_line(fatigue_vals)} {last_fatigue}/7\n"
        if bl_fatigue: text += f"   Ваша норма: ~{bl_fatigue:.1f}\n"
        text += "\n"

        # Боль
        last_sore = soreness_vals[-1] if soreness_vals[-1] is not None else "?"
        text += f"🤕 *Боль:* {_spark_line(soreness_vals)} {last_sore}/7\n"
        text += "\n"

        # Пульс (если есть)
        hr_clean = [v for v in hr_vals if v is not None]
        if hr_clean:
            last_hr = hr_vals[-1]
            bl_hr = bl.get("median_hr") if bl else None
            text += f"❤️ *Пульс:* {_spark_line_hr(hr_vals)} {last_hr} уд/мин\n"
            if bl_hr: text += f"   Ваша норма: ~{int(bl_hr)} уд/мин\n"
            text += "\n"

        # Hooper
        last_hooper = hooper_vals[-1] if hooper_vals[-1] is not None else "?"
        text += f"🔥 *Hooper:* {last_hooper}/21\n"

        # Боли (NRS) — если были
        pain_vals = [r.get("pain_nrs") for r in reversed(last7)]
        pain_days = [v for v in pain_vals if v is not None and v > 0]
        if pain_days:
            text += f"\n⚠️ *Боль (NRS):* {len(pain_days)}/7 дней\n"
            for i, r in enumerate(reversed(last7)):
                if r.get("pain_nrs") and r["pain_nrs"] > 0:
                    text += f"   {r['survey_date'][5:]}: {r['pain_nrs']}/10"
                    if r.get("pain_location"):
                        text += f" ({r['pain_location']})"
                    text += "\n"

        # Фаза цикла (для девушек)
        phases = [r.get("cycle_phase") for r in reversed(last7) if r.get("cycle_phase")]
        if phases:
            last_phase = phases[-1]
            text += f"\n🔄 *Фаза:* {last_phase}\n"

        # Кнопки
        buttons = [
            [(f"📊 Подробнее", "my_stats"), (f"🏠 Главное меню", "main_menu")],
        ]

        await q.edit_message_text(text, reply_markup=self.kb(buttons), parse_mode="Markdown")

    # ==================== ПЕРСОНАЛЬНЫЕ ЦЕЛИ ====================


    async def show_my_goals(self, update, ctx):
        """Экран целей спортсмена."""
        q = update.callback_query
        await q.answer()
        athlete = self.db.get_athlete_by_telegram_id(q.from_user.id)
        if not athlete:
            return

        goals = self.db.get_active_goals(athlete["id"])
        text = f"🎯 *Мои цели*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        if goals:
            for g in goals:
                gt = g["goal_type"]
                tv = g.get("target_value")
                label = {
                    "sleep_min": f"Сон >= {tv}",
                    "readiness_min": f"Готовность >= {tv}",
                    "pain_free": "Без боли (NRS = 0)",
                }.get(gt, gt)
                text += f"• {label}\n"
            text += "\n"
        else:
            text += "📭 Активных целей нет.\n\n"

        buttons = [
            [(f"➕ Поставить цель", "goal_add_menu")],
            [(f"🏠 Главное меню", "main_menu")],
        ]
        await q.edit_message_text(text, reply_markup=self.kb(buttons), parse_mode="Markdown")


    async def show_charts(self, update, ctx):
        from bot import sparkline, sparkline_colored, get_score_emoji
        q = update.callback_query
        await q.answer()
        athlete = self.db.get_athlete_by_telegram_id(q.from_user.id)
        if not athlete:
            return

        state = self.get_state(q.from_user.id)
        days = state.get("data", {}).get("chart_days", 7)

        # Метрики: для шкалы 1-7 (7=хорошо) invert=False (цвета по новой шкале). Пульс — отдельно.
        metrics = {
            "sleep": ("😴 Сон", False),
            "fatigue": ("😩 Утомление", False),
            "soreness": ("🤕 Боль в мышцах", False),
            "hr": ("❤️ Пульс", True),
        }

        text = f"📈 *Графики за {days} дней*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        for key, (label, invert) in metrics.items():
            data = self.db.get_trend_data(athlete["id"], key, days)
            vals = [v for _, v in data if v is not None]

            if len(vals) >= 2:
                avg = sum(vals) / len(vals)
                mn, mx = min(vals), max(vals)
                spark = sparkline(vals)
                spark_c = sparkline_colored(vals, invert=invert)

                text += f"{label}: `{spark}`\n"
                text += f"{spark_c} ср:{avg:.1f} мин:{mn:.0f} макс:{mx:.0f}\n\n"
            elif len(vals) == 1:
                text += f"{label}: только сегодня ({get_score_emoji(vals[0])} {vals[0]})\n\n"
            else:
                text += f"{label}: нет данных\n\n"

        # Hooper Index за период (сумма sleep+fatigue+soreness, норма до 21)
        hooper_data = self.db.get_trend_data(athlete["id"], "sleep", days)
        fatigue_hooper = self.db.get_trend_data(athlete["id"], "fatigue", days)
        soreness_hooper = self.db.get_trend_data(athlete["id"], "soreness", days)

        if hooper_data and fatigue_hooper:
            # Строим Hooper по дням
            date_map = {}
            for d, v in hooper_data:
                if v is not None:
                    date_map.setdefault(str(d), {})["sleep"] = v
            for d, v in fatigue_hooper:
                if v is not None:
                    date_map.setdefault(str(d), {})["fatigue"] = v
            for d, v in soreness_hooper:
                if v is not None:
                    date_map.setdefault(str(d), {})["soreness"] = v

            hooper_vals = []
            for d in sorted(date_map.keys()):
                m = date_map[d]
                if all(k in m for k in ["sleep", "fatigue"]):
                    h = m["sleep"] + m["fatigue"] + m.get("soreness", 0)
                    hooper_vals.append(h)

            if len(hooper_vals) >= 2:
                avg_h = sum(hooper_vals) / len(hooper_vals)
                text += f"📊 *Hooper Index:* ср {avg_h:.0f}/21\n"
                # Визуализация (чем выше - тем лучше)
                bar = ""
                for h in hooper_vals[-10:]:
                    if h >= 17:
                        bar += "🟢"
                    elif h >= 12:
                        bar += "🟡"
                    else:
                        bar += "🔴"
                text += f"{bar}\n\n"

        text += "\n*Выбери период:*"

        period_buttons = [
            [("7 дней", "chart_7"), ("14 дней", "chart_14"), ("30 дней", "chart_30")],
            [(f"🔙 Назад", "main_menu")]
        ]
        await q.edit_message_text(text, reply_markup=self.kb(period_buttons), parse_mode="Markdown")

    # ==================== КОМАНДА ====================


    async def goal_add_type(self, update, ctx):
        """Выбор типа цели."""
        q = update.callback_query
        await q.answer()
        buttons = [
            [(f"😴 Сон >= 6", "goal_set_sleep_6"), (f"😴 Сон >= 7", "goal_set_sleep_7")],
            [(f"🎯 Готовность >= 7", "goal_set_readiness_7"), (f"🎯 Готовность >= 8", "goal_set_readiness_8")],
            [(f"🤕 Без боли (NRS=0)", "goal_set_painfree")],
            [(f"🔙 Назад", "my_goals")],
        ]
        await q.edit_message_text(
            "🎯 *Поставить цель*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nВыбери цель:",
            reply_markup=self.kb(buttons), parse_mode="Markdown"
        )


    async def goal_set_value(self, update, ctx):
        """Сохранить цель."""
        q = update.callback_query
        await q.answer()
        athlete = self.db.get_athlete_by_telegram_id(q.from_user.id)
        if not athlete:
            return

        d = q.data
        if d.startswith("goal_set_sleep_"):
            tv = int(d.replace("goal_set_sleep_", ""))
            self.db.add_goal(athlete["id"], "sleep_min", target_value=tv)
            label = f"Сон >= {tv}"
        elif d.startswith("goal_set_readiness_"):
            tv = int(d.replace("goal_set_readiness_", ""))
            self.db.add_goal(athlete["id"], "readiness_min", target_value=tv)
            label = f"Готовность >= {tv}"
        elif d == "goal_set_painfree":
            self.db.add_goal(athlete["id"], "pain_free")
            label = "Без боли (NRS = 0)"
        else:
            return

        await q.edit_message_text(
            f"✅ *Цель установлена:* {label}\n\nБот будет отслеживать прогресс!",
            reply_markup=self.kb([[(f"🎯 Мои цели", "my_goals"), (f"🏠 Главное меню", "main_menu")]]),
            parse_mode="Markdown"
        )


    async def goal_complete(self, update, ctx):
        """Отметить цель как достигнутую или удалить."""
        q = update.callback_query
        await q.answer()
        goal_id = int(q.data.replace("goal_done_", ""))
        self.db.update_goal_progress(goal_id, achieved=True)
        await q.edit_message_text(
            "🎉 *Цель отмечена как достигнутая!*\n\nТак держать!",
            reply_markup=self.kb([[(f"🎯 Мои цели", "my_goals"), (f"🏠 Главное меню", "main_menu")]]),
            parse_mode="Markdown"
        )

    # ==================== ГРАФИКИ ====================


    async def show_team(self, update, ctx):
        q = update.callback_query
        await q.answer()
        # Состав доступен только врачу/тренеру (защита ПДн — спортсмены не видят чужие данные)
        if not self._is_full_access(q.from_user.id):
            await q.edit_message_text(
                "🔒 Список команды доступен только врачу/тренеру.",
                reply_markup=self.kb([[(f"🏠 Главная", "main_menu")]])
            )
            return
        athletes = self.db.get_all_athletes()
        if not athletes:
            await q.edit_message_text("📭 Нет спортсменов.", reply_markup=self.kb([[(f"🏠 Главная", "main_menu")]]))
            return

        groups = {}
        for a in athletes:
            groups.setdefault(a["age_group"], []).append(a)

        text = f"👥 *Команда ЧБК*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        for g in sorted(groups.keys()):
            text += f"*{g}* ({len(groups[g])}):\n"
            for a in groups[g]:
                s = a.get("survey_streak", 0)
                icon = "🟢" if s >= 7 else ("🟡" if s >= 3 else ("🟠" if s > 0 else "🔴"))
                text += f"  {icon} {a['full_name']} ({a.get('team', '?')}) {s}д\n"
            text += "\n"

        text += "🟢 7+д | 🟡 3-6 | 🟠 1-2 | 🔴 0"
        await q.edit_message_text(text, reply_markup=self.kb([[(f"🔙 Назад", "main_menu")]]), parse_mode="Markdown")

    # ==================== РОЛИ И ПРАВА ====================


    async def show_achievements(self, update, ctx):
        from gamification import get_rank, STREAK_ACHIEVEMENTS
        q = update.callback_query
        await q.answer()
        athlete = self.db.get_athlete_by_telegram_id(q.from_user.id)
        if not athlete:
            return

        streak = athlete.get("survey_streak", 0)
        total = athlete.get("total_surveys", 0)

        checks = STREAK_ACHIEVEMENTS

        unlocked, locked = [], []
        for th, name in checks:
            (unlocked if streak >= th else locked).append(f"{name} ({th}д)" if streak < th else name)

        text = f"🏆 *Достижения*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        text += f"👤 {athlete['full_name']}\n🔥 {streak}д | {get_rank(streak)}\n📊 {total} опросов\n\n"

        if unlocked:
            text += f"*Открытые ({len(unlocked)}):*\n" + "".join(f"  ✅ {a}\n" for a in unlocked) + "\n"
        if locked:
            text += f"*Заблокированные:*\n" + "".join(f"  🔒 {a}\n" for a in locked[:5])

        await q.edit_message_text(text, reply_markup=self.kb([[(f"🔙 Назад", "main_menu")]]), parse_mode="Markdown")

    # ==================== ОПРОСНИК ====================


    async def show_help(self, update, ctx):
        text = (
            f"❓ *Помощь*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📝 Опрос — 1-2 минуты ежедневно\n"
            f"👤 Профиль — статистика и тренды\n"
            f"📈 Графики — sparkline трендов\n"
            f"📋 Анкета — анкетирование спортсмена\n"
            f"🏆 Достижения — награды за серию\n"
            f"⌚ Данные часов — импорт CSV/JSON\n"
            f"🔄 Заново — сброс и перерегистрация\n\n"
            f"*/start* — Главное меню\n"
        )
        buttons = [[(f"🏠 Главное меню", "main_menu")]]
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=self.kb(buttons), parse_mode="Markdown")
        else:
            await update.message.reply_text(text, reply_markup=self.kb(buttons), parse_mode="Markdown")

    # ==================== АНКЕТИРОВАНИЕ ====================


    async def watch_data_menu(self, update, ctx):
        q = update.callback_query
        await q.answer()
        text = (
            f"⌚ *Импорт данных с часов*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Выбери свои часы — я покажу как выгрузить файл:"
        )
        buttons = [
            [("⌚ Garmin", "watch_garmin"), ("🍏 Apple Watch", "watch_apple")],
            [("⌚ Fitbit / Google", "watch_fitbit"), ("⌚ Xiaomi / Zepp", "watch_xiaomi")],
            [("⌚ Huawei", "watch_huawei"), ("⌚ Samsung", "watch_samsung")],
            [("📄 Любые часы (TXT/CSV)", "watch_other")],
        ]
        if self._is_admin_or_coach(q.from_user.id):
            buttons.append([("⚖️ Весы и состав тела", "bc_menu")])
        buttons.append([(f"🔙 Назад", "main_menu")])
        await q.edit_message_text(text, reply_markup=self.kb(buttons), parse_mode="Markdown")


    async def _watch_brand_instructions(self, update, ctx, brand):
        q = update.callback_query
        await q.answer()

        instructions = {
            "garmin": (
                "⌚ *Garmin*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "📤 *Как выгрузить:*\n"
                "1. Открой Garmin Connect на телефоне\n"
                "2. Нажми ⋮ → *Экспорт в CSV*\n"
                "3. Выбери период (1 день)\n"
                "4. Отправь полученный CSV-файл сюда\n\n"
                "✅ Поддерживается: пульс, сон, шаги, стресс, SpO2\n\n"
                "*Просто отправь файл!*"
            ),
            "apple": (
                "🍏 *Apple Watch*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "📤 *Как выгрузить:*\n"
                "1. Открой *Здоровье* на iPhone\n"
                "2. Профиль (🟣) → *Экспорт всех данных*\n"
                "3. Дождись генерации .zip\n"
                "4. Отправь файл сюда\n\n"
                "✅ Поддерживается: пульс, сон, шаги, SpO2\n\n"
                "*Просто отправь файл!*"
            ),
            "fitbit": (
                "⌚ *Fitbit / Google Pixel Watch*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "📤 *Как выгрузить:*\n"
                "1. Открой Fitbit App\n"
                "2. Профиль → *Настройки* → *Экспорт данных*\n"
                "3. Запроси CSV-экспорт\n"
                "4. Когда файл придёт на почту — отправь его сюда\n\n"
                "✅ Поддерживается: пульс, сон, шаги, стресс\n\n"
                "*Просто отправь файл!*"
            ),
            "xiaomi": (
                "⌚ *Xiaomi / Zepp / Amazfit*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "📤 *Как выгрузить:*\n"
                "1. Открой *Zepp Life* или *Mi Fitness*\n"
                "2. Профиль → *Настройки* → *Экспорт данных*\n"
                "3. Выбери период и формат CSV\n"
                "4. Отправь полученный файл сюда\n\n"
                "✅ Поддерживается: пульс, сон, шаги, стресс, SpO2\n\n"
                "*Просто отправь файл!*"
            ),
            "huawei": (
                "⌚ *Huawei Watch / Band*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "📤 *Как выгрузить:*\n"
                "1. Открой *Huawei Health*\n"
                "2. Я → *Настройки* → *Экспорт данных*\n"
                "3. Выбери CSV и отправь себе\n"
                "4. Перешли файл сюда\n\n"
                "✅ Поддерживается: пульс, сон, шаги, стресс, SpO2\n\n"
                "*Просто отправь файл!*"
            ),
            "samsung": (
                "⌚ *Samsung Galaxy Watch*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "📤 *Как выгрузить:*\n"
                "1. Открой *Samsung Health*\n"
                "2. ⋮ → *Настройки* → *Экспорт данных*\n"
                "3. Выбери JSON-формат\n"
                "4. Отправь файл сюда\n\n"
                "✅ Поддерживается: пульс, сон, шаги\n\n"
                "*Просто отправь файл!*"
            ),
        }

        text = instructions.get(brand,
            "📄 *Любые часы*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Отправь файл с данными в формате CSV, JSON или TXT.\n\n"
            "Файл должен содержать хотя бы один из параметров:\n"
            "пульс, сон, шаги, стресс, SpO2\n\n"
            "Попробуй экспорт из приложения твоих часов."
        )

        await q.edit_message_text(
            text + "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━",
            reply_markup=self.kb([[(f"🔙 К выбору часов", "watch_data")],
                                  [(f"🔙 Главное меню", "main_menu")]]),
            parse_mode="Markdown"
        )


    def _format_watch_report(self, data, age_group):
        """Красивое форматирование данных с часов."""
        lines = [
            "⌚ *📊 Данные с часов*",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ]

        norms = HR_NORMS.get(age_group, {"min": 40, "max": 70})

        fields = [
            ("💓 Пульс покоя", data.get("💓 Пульс"),
             f"Норма {age_group}: {norms['min']}-{norms['max']} уд/мин"),
            ("😴 Сон", data.get("😴 Сон"),
             "Норма: 7-9 часов"),
            ("🏃 Шаги", data.get("🏃 Шаги"),
             "Норма: 8000-12000"),
            ("😰 Стресс", data.get("😰 Стресс"),
             "0-100: <50 низкий, 50-70 средний, >70 высокий"),
            ("🫁 SpO₂", data.get("🫁 SpO2"),
             "Норма: 95-99%"),
            ("📊 HRV", data.get("📊 HRV"),
             "Выше = лучше восстановление"),
        ]

        has_data = False
        for label, value, norm in fields:
            if value is not None:
                has_data = True
                emoji = ""
                if label.startswith("💓") and isinstance(value, (int, float)):
                    if value > norms["max"] + 10:
                        emoji = "🔴"
                    elif value > norms["max"]:
                        emoji = "🟡"
                    else:
                        emoji = "🟢"
                elif label.startswith("😴"):
                    try:
                        h = float(str(value).replace("ч", ""))
                        emoji = "🔴" if h < 6 else ("🟡" if h < 7 else "🟢")
                    except (ValueError, TypeError):
                        pass
                elif label.startswith("🏃"):
                    if isinstance(value, int):
                        emoji = "🟡" if value < 5000 else "🟢"
                elif label.startswith("😰"):
                    if isinstance(value, int):
                        emoji = "🔴" if value > 70 else ("🟡" if value > 50 else "🟢")

                lines.append(f"\n{emoji} *{label}:* {value}")
                lines.append(f"     {norm}")

        if not has_data:
            return "❌ Нет распознанных данных."

        # Сохраняем данные в БД для аналитики
        try:
            hr_val = data.get("💓 Пульс")
            if hr_val and isinstance(hr_val, (int, float)):
                pass
        except Exception:
            pass

        return "\n".join(lines)

    # ==================== БИОИМПЕДАНСНЫЕ ВЕСЫ ====================


    async def update_weight_start(self, update, ctx):
        q = update.callback_query
        await q.answer()
        state = self.get_state(q.from_user.id)
        state["step"] = "update_weight"
        await q.edit_message_text(
            "⚖️ *Обновление веса*\n\nВведи *текущий вес в кг* числом (например: 72):",
            parse_mode="Markdown"
        )


    async def update_weight_save(self, update, ctx, user_id, text):
        try:
            w = float(text.strip().replace(",", "."))
            if not (30 <= w <= 200):
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Введи вес числом от 30 до 200 (например: 72):")
            return
        athlete = self.db.get_athlete_by_telegram_id(user_id)
        if not athlete:
            await update.message.reply_text("❌ Сначала зарегистрируйся: /start")
            return
        # Обновляем вес в анкете (если есть) — ИМТ пересчитается в профиле
        qd = self.db.get_questionnaire(athlete["id"])
        if qd:
            self.db.conn.execute(
                "UPDATE questionnaires SET weight = ? WHERE athlete_id = ?",
                (int(w), athlete["id"])
            )
            self.db.conn.commit()
        self.clear_state(user_id)
        await update.message.reply_text(
            f"✅ Вес обновлён: *{int(w)} кг*",
            reply_markup=self.kb([[(f"🏠 Главное меню", "main_menu")]]),
            parse_mode="Markdown"
        )

    # ==================== ПРОГРЕСП СПОРТСМЕНА ====================


    async def set_gender_menu(self, update, ctx):
        """Меню выбора пола при регистрации — для девушек включаем цикл."""
        q = update.callback_query
        await q.answer()
        state = self.get_state(q.from_user.id)
        state["step"] = "set_gender"

        await q.edit_message_text(
            "👤 *Выбери пол:*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Для девушек будет добавлен вопрос о дне цикла в ежедневный опрос.",
            reply_markup=self.kb([
                [("♂ Мужской", "gender_male")],
                [("♀ Женский", "gender_female")],
            ]), parse_mode="Markdown"
        )


    async def save_gender(self, update, ctx):
        q = update.callback_query
        await q.answer()
        gender = q.data.replace("gender_", "")
        state = self.get_state(q.from_user.id)
        athlete = self.db.get_athlete_by_telegram_id(q.from_user.id)
        if not athlete:
            return

        self.db.update_athlete_gender(athlete["id"], gender)

        text = f"✅ Пол сохранён: {'♀ Женский' if gender == 'female' else '♂ Мужской'}"
        if gender == "female":
            from config import MENSTRUAL_ENABLED_GROUPS
            if athlete.get("age_group") in MENSTRUAL_ENABLED_GROUPS:
                text += "\n\n📅 Теперь в ежедневном опросе будет вопрос о дне цикла."

        await q.edit_message_text(text, reply_markup=self.kb([[(f"🔙 Главное меню", "main_menu")]]), parse_mode="Markdown")


    async def show_cycle_info(self, update, ctx):
        """«Мой цикл»: текущий день/фаза от якоря-события, прогноз, лесенка
        без корзин (старые записи только до первого якоря)."""
        q = update.callback_query
        await q.answer()
        athlete = self.db.get_athlete_by_telegram_id(q.from_user.id)
        if not athlete:
            return

        from datetime import date as _d
        from cycle_ui import build_cycle_display, status_line
        today = _d.today()
        start = self.db.get_last_period_start(athlete["id"])
        events = self.db.get_cycle_events(athlete["id"])
        cl = athlete.get("cycle_length_default") or CYCLE_LENGTH_MEDIAN
        learned = bool(athlete.get("cycle_length_learned"))

        text = (
            f"📅 *Менструальный цикл*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 {athlete['full_name']}\n"
            f"📋 {athlete['age_group']}\n\n"
        )
        buttons = [[(f"🗓 Отметить начало", "cyc_set_anchor")]]

        if start is None:
            mstatus = athlete.get("menarche_status")
            if mstatus == "pre":
                text += "Отмечено «пока не было». Как начнутся — нажми «Отметить начало» " \
                        "или ответь на вопрос в опросе.\n"
            else:
                text += "Нет даты начала — бот считает день цикла от события «месячные " \
                        "начались». Нажми «Отметить начало» или ответь на один вопрос в опросе.\n"
            stored = self.db.get_cycle_history(athlete["id"], 60)
            if stored:
                text += "\n*Старые записи (до якоря):*\n"
                for e in stored[:7]:
                    text += f"  📅 {e.get('survey_date', '?')}: день {e.get('cycle_day', '—')}\n"
            buttons.append([(f"🔙 Назад", "main_menu")])
            await q.edit_message_text(text, reply_markup=self.kb(buttons),
                                      parse_mode="Markdown")
            return

        line = status_line(start, cl, today, learned)
        if line:
            text += f"{line}\n\n"

        # База знаний по текущей фазе (если день в пределах цикла)
        from cycle_engine import cycle_day
        info = get_cycle_phase_info(cycle_day(start, today), cl)
        if info:
            text += info["text"] + "\n"

        text += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"

        # Возрастные рекомендации
        age_info = AGE_SPECIFIC.get(athlete.get("age_group", ""))
        if age_info:
            text += f"\n*Для {athlete['age_group']}:*\n{age_info.training_note}\n\n🚩 {age_info.concerns}"

        # Лесенка: вычисляемые дни + stored до якоря (RF №4)
        stored = self.db.get_cycle_history(athlete["id"], 60)
        rows = build_cycle_display(events, stored, today)
        if rows:
            text += "\n*Последние записи:*\n"
            for d_label, day, kind in rows[-7:]:
                note = " (до якоря)" if kind == "stored" else ""
                text += f"  📅 {d_label}: день {day}{note}\n"

        buttons.append([(f"🔙 Назад", "main_menu")])
        await q.edit_message_text(
            text,
            reply_markup=self.kb(buttons),
            parse_mode="Markdown"
        )

    async def set_anchor_manual_start(self, update, ctx):
        """Кнопка «Отметить начало» вне опроса (экран «Мой цикл») → шаг ввода даты."""
        q = update.callback_query
        await q.answer()
        athlete = self.db.get_athlete_by_telegram_id(q.from_user.id)
        if not athlete:
            return
        state = self.get_state(q.from_user.id)
        state["step"] = "survey_anchor_date"
        await q.edit_message_text(
            "🗓 Когда начались месячные? Введи дату ДД.ММ (например 05.10).\n"
            "Можно задним числом — день цикла пересчитается от новой даты.",
            parse_mode="Markdown")


    def _cycle_ctx(self, update):
        """Общий сбор контекста шага цикла: (callback_query|None, user_id, state, athlete)."""
        q = update.callback_query if hasattr(update, 'callback_query') and update.callback_query else None
        if update.effective_user:
            user_id = update.effective_user.id
        elif q:
            user_id = q.from_user.id
        else:
            user_id = None
        if not user_id:
            return None, None, None, None
        state = self.get_state(user_id)
        athlete = state.get("data", {}).get("athlete")
        return q, user_id, state, athlete

    async def _render_cycle_step(self, q, update, text, buttons):
        """Показать шаг цикла: отредадовать callback-сообщение или ответить сообщением."""
        if q:
            await q.edit_message_text(text, reply_markup=self.kb(buttons), parse_mode="Markdown")
        elif update.message:
            await update.message.reply_text(text, reply_markup=self.kb(buttons), parse_mode="Markdown")

    async def _broken_survey_reply(self, q, update):
        """Потеря состояния опроса (ребут бота посреди шага)."""
        text = "❌ *Опрос прерван* (возможно бот перезагружался).\n\nХотите начать заново?"
        kb = self.kb([[(f"🔄 Пройти опрос", "do_survey")],
                      [(f"🏠 Главное меню", "main_menu")]])
        if q:
            await q.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
        elif update.message:
            await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")

    async def ask_cycle_anchor(self, update, ctx):
        """Одноразовый вопрос: когда начались последние месячные (FLOW-модель).
        Больше формат «выбери день корзинкой» не возвращается."""
        q, user_id, state, athlete = self._cycle_ctx(update)
        if not athlete:
            return
        if q:
            await q.answer()
        state["step"] = "survey_anchor"

        from datetime import date as _d
        from cycle_ui import anchor_buttons
        today = _d.today()
        text = (
            "🌸 *Цикл: новый формат*\n\n"
            "Больше не нужно каждый день считать день цикла — бот будет считать сам. "
            "Нужно один раз сказать, когда начались последние месячные.\n\n"
            "💡 Дальше — один вопрос в день: «начались сегодня?» (одна кнопка).\n\n"
            "Когда начались последние месячные?"
        )
        await self._render_cycle_step(q, update, text, anchor_buttons(today))

    async def _save_cycle_anchor(self, q, state, athlete, target, source):
        """Общий путь: сохранить якорь (или коррекцию), эхо, переход к жалобам."""
        from datetime import date as _d
        from cycle_ui import apply_anchor, cycle_saved_text, anchor_text
        today = _d.today()
        cl = athlete.get("cycle_length_default") or 28
        res = apply_anchor(self.db, athlete["id"], target, today, cl, source=source)
        if not res:
            await q.edit_message_text(
                "❌ Не удалось сохранить дату. Попробуй ещё раз или введи ДД.ММ.")
            return False
        self._apply_anchor_to_state(state, athlete, res)
        note = f"✅ Записано: *{anchor_text(target)}*.\n\n" if source == "manual_date" else ""
        await q.edit_message_text(note + cycle_saved_text(res), parse_mode="Markdown")
        return True

    def _apply_anchor_to_state(self, state, athlete, res):
        """Вычисленные день/фаза — в данные опроса (stored-совместимость Excel)."""
        state["data"]["cycle_day"] = res["cycle_day"]
        state["data"]["cycle_length"] = res["length"]
        state["data"]["cycle_phase"] = res["cycle_phase"]
        if res.get("learned"):
            athlete["cycle_length_default"] = res["learned"]
            athlete["cycle_length_learned"] = 1
        if res.get("start"):
            athlete["last_period_start"] = res["start"]

    async def set_cycle_anchor(self, update, ctx):
        """Ответ на одноразовый якорь: кнопки anchor_0..5 / custom / pre / skip."""
        q = update.callback_query
        await q.answer()
        state = self.get_state(q.from_user.id)
        athlete = state.get("data", {}).get("athlete")
        if not athlete or state.get("step") != "survey_anchor":
            await self._broken_survey_reply(q, update)
            return

        from datetime import date as _d, timedelta
        today = _d.today()
        d = q.data

        if d.startswith("anchor_") and d[7:].isdigit():
            target = today - timedelta(days=int(d[7:]))
            if await self._save_cycle_anchor(q, state, athlete, target, "one_time_question"):
                await self._ask_complaints(update, ctx)
        elif d == "anchor_custom":
            state["step"] = "survey_anchor_date"
            await q.edit_message_text(
                "🗓 Введи дату начала (ДД.ММ), например 05.10.\n"
                "Можно не дальше 90 дней назад и не будущую дату.", parse_mode="Markdown")
        elif d == "anchor_pre":
            self.db.set_menarche_status(athlete["id"], "pre")
            await self._ask_complaints(update, ctx)
        elif d == "anchor_skip":
            self.db.set_menarche_status(athlete["id"], "skip")
            await self._ask_complaints(update, ctx)

    async def _anchor_date_text(self, update, ctx):
        """Текстовый шаг: девушка ввела дату якоря/коррекции «ДД.ММ».
        Работает и из опроса, и с экрана «Мой цикл» (athlete тогда из БД)."""
        user_id = update.effective_user.id
        state = self.get_state(user_id)
        in_survey = bool(state.get("data", {}).get("athlete"))
        athlete = (state["data"]["athlete"] if in_survey
                   else self.db.get_athlete_by_telegram_id(user_id))
        if not athlete:
            await update.message.reply_text("❌ Не нашла твою анкету — напиши врачу.")
            return

        from datetime import date as _d
        from cycle_ui import parse_date_ru
        target = parse_date_ru(update.message.text, _d.today())
        if target is None:
            await update.message.reply_text(
                "❌ Не понял дату. Введи ДД.ММ (например 05.10) — "
                "не дальше 90 дней назад и не в будущем.", parse_mode="Markdown")
            return

        class _MsgAsQ:  # общий путь _save_cycle_anchor ждёт q.edit_message_text
            def __init__(self, msg):
                self.msg = msg

            async def edit_message_text(self, *a, **kw):
                await self.msg.reply_text(*a, **kw)

        ok = await self._save_cycle_anchor(_MsgAsQ(update.message), state, athlete,
                                           target, "manual_date")
        if ok and in_survey:
            await self._ask_complaints(update, ctx)
        elif ok:
            # вне опроса: шаг снимаем, иначе этот же текстовый шаг ловит
            # любое следующее сообщение до скончания веков
            state["step"] = None

    async def ask_cycle_day(self, update, ctx):
        """Ежедневный шаг цикла: один тап «начались сегодня?» (FLOW-модель).
        Без якоря — сначала одноразовый вопрос."""
        q, user_id, state, athlete = self._cycle_ctx(update)
        if not athlete:
            return
        from datetime import date as _d
        today = _d.today()
        start = self.db.get_last_period_start(athlete["id"])
        if start is None:
            await self.ask_cycle_anchor(update, ctx)
            return
        state["step"] = "survey_cycle_daily"
        fresh = self.db.get_athlete_by_telegram_id(user_id) or athlete
        cl = fresh.get("cycle_length_default") or 28
        from cycle_engine import status as cycle_status
        from cycle_ui import daily_cycle_keyboard, daily_cycle_text
        name = athlete.get("first_name") or str(athlete.get("full_name", "")).split()[-1]
        text = daily_cycle_text(name, start, cl, today)
        buttons = daily_cycle_keyboard(cycle_status(start, cl, today))
        await self._render_cycle_step(q, update, text, buttons)

    async def set_cycle_daily(self, update, ctx):
        """Тапы ежедневного шага: cyc_start / cyc_no / cyc_fix / cyc_anchor_again."""
        q = update.callback_query
        await q.answer()
        state = self.get_state(q.from_user.id)
        athlete = state.get("data", {}).get("athlete")
        if not athlete or state.get("step") != "survey_cycle_daily":
            await self._broken_survey_reply(q, update)
            return

        from datetime import date as _d
        from cycle_ui import survey_cycle_values
        today = _d.today()
        # длину берём из БД: в state-копии может быть устаревший cycle_length_default
        fresh = self.db.get_athlete_by_telegram_id(q.from_user.id) or athlete
        start = self.db.get_last_period_start(athlete["id"])
        if start is None:
            await self.ask_cycle_anchor(update, ctx)
            return
        cl = fresh.get("cycle_length_default") or 28
        d = q.data

        if d == "cyc_start":
            if not self.db.add_cycle_event(athlete["id"], today,
                                           type="start", source="survey_button"):
                await self._broken_survey_reply(q, update)
                return
            learned = self.db.update_cycle_length_from_history(athlete["id"])
            if learned:
                athlete["cycle_length_default"] = learned
                athlete["cycle_length_learned"] = 1
            vals = survey_cycle_values(today, learned or cl, today)  # день 1
            state["data"].update(vals)
            await self._ask_complaints(update, ctx)
        elif d == "cyc_no":
            state["data"].update(survey_cycle_values(start, cl, today))
            await self._ask_complaints(update, ctx)
        elif d == "cyc_fix":
            state["step"] = "survey_cycle"
            await q.edit_message_text(
                f"✏️ Введи число — какой сегодня день цикла (1-{cl + 30}).\n"
                "День 1 = первый день месячных.", parse_mode="Markdown")
        elif d == "cyc_anchor_again":
            state["step"] = "survey_anchor_date"
            await q.edit_message_text(
                "🗓 Когда на самом деле начались? Введи дату ДД.ММ — "
                "можно задним числом, счёт пересчитается.", parse_mode="Markdown")

    async def _manual_cycle_day(self, update, ctx, state, text):
        """Ручной день цикла («✏️ Исправить день») — это утверждение о дате начала.

        Раньше правилась только цифра текущего опроса, а якорь оставался прежним:
        со следующего опроса счёт снова шёл от него (у #106 16.11 вручную 40 →
        17.11 автоматически 42, правка «испарялась»). Теперь якорь сдвигается на
        today-(day-1), поэтому день сохраняется и завтра.
        """
        athlete = state.get("data", {}).get("athlete")
        if not athlete:
            await update.message.reply_text(
                "❌ *Опрос прерван* (возможно бот перезагружался).\n\nХотите начать заново?",
                reply_markup=self.kb([[(f"🔄 Пройти опрос", "do_survey")],
                                      [(f"🏠 Главное меню", "main_menu")]]),
                parse_mode="Markdown"
            )
            return
        from cycle_engine import parse_day_input
        from cycle_ui import apply_anchor, values_for_day
        today = date.today()
        cl = athlete.get("cycle_length_default") or 28
        cd = parse_day_input(text, cl)
        if cd is None:
            await update.message.reply_text(f"❌ Введи число от 1 до {cl + 30}:")
            return
        target = today - timedelta(days=cd - 1)
        res = apply_anchor(self.db, athlete["id"], target, today, cl,
                           source="manual_day")
        if res:
            state["data"].update({k: res[k] for k in
                                  ("cycle_day", "cycle_length", "cycle_phase")})
            if res.get("learned"):
                athlete["cycle_length_default"] = res["learned"]
                athlete["cycle_length_learned"] = 1
        else:
            # пересчитать якорь не вышло — день не теряем, но честно предупреждаем
            state["data"].update(values_for_day(cd, cl))
            await update.message.reply_text(
                "⚠️ День записан, но дату начала пересчитать не удалось. "
                "Если знаешь её — нажми «🗓 Отметить начало».")
        await self._ask_complaints(update, ctx)


    async def _ask_cycle_length(self, update, ctx):
        """Спросить длину цикла при первом заполнении."""
        q = update.callback_query if hasattr(update, 'callback_query') and update.callback_query else None
        if update.effective_user:
            user_id = update.effective_user.id
        elif q:
            user_id = q.from_user.id
        else:
            user_id = None
        if not user_id:
            return
        state = self.get_state(user_id)
        state["step"] = "survey_cycle_length"

        buttons = [
            [("21 день", "cycle_len_21"), ("24 дня", "cycle_len_24")],
            [("28 дней (норма)", "cycle_len_28"), ("30 дней", "cycle_len_30")],
            [("35 дней", "cycle_len_35"), ("✏️ Ввести число", "cycle_len_custom")],
            [("Не знаю", "cycle_len_28")],
        ]
        text = (
            "📅 *Длина менструального цикла*\n\n"
            "Менструальный цикл — это дни от первого дня месячных до начала следующих.\n"
            "Обычно он длится 21–35 дней (в среднем 28).\n\n"
            "Помогает отслеживать фазы и лучше понимать самочувствие. "
            "Если ты ещё не ведёшь дневник и не знаешь свою длину — не переживай, выбери 28 (среднее значение), "
            "позже это можно будет скорректировать.\n\n"
            "*Сколько дней обычно длится твой цикл?*"
        )
        if q:
            await q.edit_message_text(text, reply_markup=self.kb(buttons), parse_mode="Markdown")
        elif update.message:
            await update.message.reply_text(text, reply_markup=self.kb(buttons), parse_mode="Markdown")


    async def _cycle_length_chosen(self, update, ctx):
        """Сохранить длину цикла и спросить день цикла."""
        q = update.callback_query
        await q.answer()
        data = q.data
        cycle_length = int(data.replace("cycle_len_", ""))
        user_id = q.from_user.id
        state = self.get_state(user_id)
        athlete = state.get("data", {}).get("athlete")
        if not athlete:
            await q.edit_message_text(
                "❌ *Опрос прерван*. Начните заново.",
                reply_markup=self.kb([[(f"🔄 Пройти опрос", "do_survey")]])
            )
            return
        self.db.update_athlete_gender(athlete["id"], athlete.get("gender", "female"), cycle_length)
        athlete["cycle_length_default"] = cycle_length
        await self.ask_cycle_day(update, ctx)


    async def _ask_cycle_length_custom(self, update, ctx):
        """Запросить длину цикла ручным вводом числа (кнопка «Ввести число»)."""
        q = update.callback_query
        await q.answer()
        state = self.get_state(q.from_user.id)
        state["step"] = "survey_cycle_length"
        await q.edit_message_text(
            "📅 *Длина менструального цикла*\n\n"
            "Введи число — сколько дней длится твой цикл (21-35):"
        )


    async def _apply_cycle_length(self, user_id, length):
        """Сохранить длину цикла (кнопки или ручной ввод) и перейти к дню цикла."""
        state = self.get_state(user_id)
        athlete = state.get("data", {}).get("athlete")
        if not athlete:
            return False
        self.db.update_athlete_gender(athlete["id"], athlete.get("gender", "female"), length)
        athlete["cycle_length_default"] = length
        return True

    # ==================== КОНСУЛЬТАЦИЯ ====================


    async def consultation_start(self, update, ctx):
        q = update.callback_query
        await q.answer()
        state = self.get_state(q.from_user.id)
        state["step"] = "consult_wait_complaints"
        state["data"] = {}

        text = (
            "📅 *Запись на консультацию к врачу*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Опиши, что тебя беспокоит:"
        )
        buttons = [
            [("📝 Описать жалобы", "consult_text")],
            [("🚫 Без жалоб", "consult_no")],
            [("🏠 Отмена", "main_menu")],
        ]
        await q.edit_message_text(text, reply_markup=self.kb(buttons), parse_mode="Markdown")


    async def consultation_text(self, update, ctx):
        q = update.callback_query
        await q.answer()
        state = self.get_state(q.from_user.id)
        state["step"] = "consult_text_input"
        await q.edit_message_text("📝 Напиши свои жалобы (что беспокоит):")


    async def consultation_no_complaints(self, update, ctx):
        q = update.callback_query
        await q.answer()
        state = self.get_state(q.from_user.id)
        state["data"]["consult_complaints"] = "Нет жалоб"
        await self._show_calendar(update, ctx, "consult")


    async def _show_calendar(self, update, ctx, prefix="survey"):
        q = update.callback_query if hasattr(update, 'callback_query') and update.callback_query else None
        today = date.today()
        buttons = []
        row = []
        for i in range(1, 15):
            d = today + timedelta(days=i)
            row.append((d.strftime("%d.%m"), f"{prefix}_{d.isoformat()}"))
            if len(row) == 4:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([("🏠 Отмена", "main_menu")])

        text = "📅 Выберите дату консультации:"
        if q:
            await q.edit_message_text(text, reply_markup=self.kb(buttons))
        else:
            await update.message.reply_text(text, reply_markup=self.kb(buttons))


    async def consultation_date(self, update, ctx):
        q = update.callback_query
        await q.answer()
        d = q.data
        parts = d.split("_", 1)
        if len(parts) == 2:
            date_str = parts[1]
            state = self.get_state(q.from_user.id)
            state["data"]["consult_date"] = date_str
            await self._save_consultation(update, ctx)
        else:
            await q.edit_message_text("❌ Ошибка даты. Попробуйте снова.")


    async def _save_consultation(self, update, ctx):
        q = update.callback_query
        user = update.effective_user
        state = self.get_state(user.id)
        athlete = self.db.get_athlete_by_telegram_id(user.id)
        if not athlete:
            await q.edit_message_text("❌ Ошибка. Начните с /start")
            return

        complaints = state.get("data", {}).get("consult_complaints", "Не указано")
        consult_date = state.get("data", {}).get("consult_date", "Не определена")

        # Уведомление врачу
        admin_text = (
            f"📅 *Новая запись на консультацию*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 *{athlete['full_name']}*\n"
            f"🏀 Команда: *{athlete.get('team', '?')}*\n"
            f"📊 Возраст: *{athlete.get('age_group', '?')}*\n\n"
            f"📝 Жалобы: {complaints}\n"
            f"📅 Дата: {consult_date}\n\n"
            f"🆔 ID: [{user.id}](tg://user?id={user.id})"
        )

        for admin_id in self._full_access_ids():
            btn_name = self._first_name(athlete)
            url_btn = InlineKeyboardButton(f"💬 Написать {btn_name}", url=f"tg://user?id={user.id}")
            markup = InlineKeyboardMarkup([[url_btn]])
            try:
                await ctx.bot.send_message(
                    admin_id, admin_text,
                    parse_mode="Markdown",
                    reply_markup=markup
                )
            except Exception as e:
                logger.error(f"Send consult admin error: {e}")

        text = (
            f"✅ *Запись на консультацию создана!*\n\n"
            f"📝 Жалобы: {complaints}\n"
            f"📅 Дата: {consult_date}\n\n"
            f"Врач свяжется с тобой! 💪"
        )
        self.clear_state(user.id)
        try:
            await q.edit_message_text(text, reply_markup=self.kb([[(f"🏠 Главное меню", "main_menu")]]), parse_mode="Markdown")
        except Exception:
            pass


