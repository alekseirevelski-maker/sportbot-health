"""
Миксин: SurveyMixin
Автоматически извлечён из bot.py
"""

import logging
from typing import TYPE_CHECKING
from contact_ui import contact_row
from config import SIMPLE_PROTOCOLS, MOTIVATIONAL_MESSAGES, HR_NORMS
from gamification import get_motivation_key

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from bot import SportHealthBot


def red_flag_recipients(db, team, full_ids, sender_id) -> list[int]:
    """Получатели red-flag алерта опроса: врачи (full_access) + тренеры команды.

    Отправитель (sender_id) исключается — не дублируем тому, кто прошёл опрос.
    Тренер, уже входящий в full_ids, тоже попадёт в список один раз (union).
    """
    ids = set(full_ids or ())
    if team:
        try:
            ids.update(db.get_team_coaches(team))
        except Exception as e:
            logger.error(f"red_flag_recipients get_team_coaches: {e}")
    ids.discard(sender_id)
    return sorted(ids)


class SurveyMixin:
    """Миксин для survey."""

    async def start_survey(self, update, ctx):
        q = update.callback_query
        await q.answer()
        # Тренер — админ команды, опрос не проходит
        if self.db.is_coach(q.from_user.id):
            await q.edit_message_text(
                "👕 Тренер не проходит опрос — панель тренера в главном меню.",
                reply_markup=self.kb([[(f"👕 Моя команда", "coach_team_view")],
                                      [(f"🏠 Главное меню", "main_menu")]])
            )
            return
        athlete = self.db.get_athlete_by_telegram_id(q.from_user.id)
        if not athlete:
            return

        if self.db.is_athlete_banned(athlete["id"]):
            await q.edit_message_text("🔒 Ты заблокирован. Обратись к администратору.", reply_markup=self.kb([[(f"🏠 Главное меню", "main_menu")]]))
            return

        if self.db.has_survey_today(athlete["id"]):
            await q.edit_message_text("✅ Уже прошёл опрос сегодня!", reply_markup=self.kb([[(f"🏠 Главная", "main_menu")]]))
            return

        state = self.get_state(q.from_user.id)
        state["step"] = "survey_sleep"
        state["data"] = {"athlete": athlete}

        await q.edit_message_text(
            f"📝 *Опрос* (1/8)\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"😴 *Качество сна*\n\nКак спал?\n\n"
            f"1 — 😫 Ужасно\n4 — 😐 Нормально\n7 — 😍 Отлично\n\nВыбери:",
            reply_markup=self.kb(self.score_buttons("sleep")), parse_mode="Markdown"
        )


    async def survey_callback(self, update, ctx):
        q = update.callback_query
        await q.answer()
        user_id = q.from_user.id
        state = self.get_state(user_id)
        step = state.get("step")
        data = state.get("data", {})
        athlete = data.get("athlete")

        # Если athlete нет в state — пробуем дозагрузить из БД
        if not athlete:
            athlete_db = self.db.get_athlete_by_telegram_id(user_id)
            if athlete_db:
                data["athlete"] = athlete_db
                athlete = athlete_db
            else:
                await q.edit_message_text(
                    "❌ *Ошибка*. Пожалуйста, начните заново.",
                    reply_markup=self.kb([[(f"🏠 Главное меню", "main_menu")]])
                )
                return

        # Если step не survey — значит опрос устарел / рестарт
        if not step or not step.startswith("survey_"):
            await q.edit_message_text(
                "❌ *Опрос устарел*. Начните заново.",
                reply_markup=self.kb([[(f"🔄 Пройти опрос", "do_survey")]])
            )
            return

        parts = q.data.split("_")
        if len(parts) < 2:
            await q.edit_message_text(
                "❌ Ошибка данных. Попробуйте ещё раз.",
                reply_markup=self.kb([[(f"🏠 Главное меню", "main_menu")]])
            )
            return

        # Зона боли: painloc_<зона> — значение это название зоны (кириллица), не число
        if parts[0] == "painloc":
            data["pain_location"] = parts[1]
            state["step"] = "survey_pain_game"
            await q.edit_message_text(
                "🏀 *Болит ли на игре/тренировке?*",
                reply_markup=self.kb([[(f"✅ Да", "paingame_1"), (f"❌ Нет", "paingame_0")]]),
                parse_mode="Markdown"
            )
            return

        # srv_<поле>_<значение> — поле в parts[1]; двухчастные (paingame_/illness_/analg_) — поле в parts[0]
        field = parts[1] if parts[0] == "srv" else parts[0]

        try:
            value = int(parts[-1])
        except ValueError:
            await q.edit_message_text(
                "❌ Ошибка данных. Попробуйте ещё раз.",
                reply_markup=self.kb([[(f"🏠 Главное меню", "main_menu")]])
            )
            return

        field_map = {"sleep": "sleep_score", "fatigue": "fatigue_score",
                     "soreness": "muscle_soreness", "readiness": "readiness",
                     "painnrs": "pain_nrs"}
        if field in field_map:
            data[field_map[field]] = value

        # Новая последовательность опроса (урезанные шкалы + новые пункты):
        # сон → часы сна → readiness → утомление → боль → NRS → локация → на игре → болезнь → обезболивающие
        if field in ("sleep", "readiness", "fatigue", "soreness"):
            nxt = {
                "sleep": "survey_sleep_hours",
                "readiness": "survey_fatigue",
                "fatigue": "survey_soreness",
                "soreness": "survey_pain_nrs",
            }[field]
            state["step"] = nxt
            labels = {
                "sleep_hours": "😴 *Сколько часов реально спал?*\n\nНапиши числом (например: 7.5):",
                "fatigue": "😩 *Утомление*\n\nНасколько бодр?\n\n1 💀 — Упадок\n4 🦦 — Умеренно\n7 ⚡ — Бодрый\n\nВыбери:",
                "soreness": "🤕 *Мышечная боль*\n\nБолят мышцы?\n\n1 🤕 — Сильно\n4 😣 — Чувствуется\n7 ✨ — Не болят\n\nВыбери:",
            }
            if nxt == "survey_sleep_hours":
                await q.edit_message_text(
                    f"📝 *Опрос*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n{labels['sleep_hours']}",
                    parse_mode="Markdown"
                )
            elif nxt == "survey_pain_nrs":
                # NRS 0-10 (0 = боли нет) — свои кнопки
                await q.edit_message_text(
                    "📝 *Опрос*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "🤕 *Оцени боль по шкале 0-10*\n\n0 — боли нет\n5 — умеренная\n10 — сильнейшая\n\nВыбери:",
                    reply_markup=self.kb([
                        [("0 — нет", "srv_painnrs_0"), ("1", "srv_painnrs_1"), ("2", "srv_painnrs_2")],
                        [("3", "srv_painnrs_3"), ("4", "srv_painnrs_4"), ("5", "srv_painnrs_5")],
                        [("6", "srv_painnrs_6"), ("7", "srv_painnrs_7"), ("8", "srv_painnrs_8")],
                        [("9", "srv_painnrs_9"), ("10", "srv_painnrs_10")],
                    ]), parse_mode="Markdown"
                )
            else:
                await q.edit_message_text(
                    f"📝 *Опрос*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n{labels[nxt.replace('survey_', '')]}\n\nВыбери:",
                    reply_markup=self.kb(self.score_buttons(nxt.replace("survey_", ""))),
                    parse_mode="Markdown"
                )
            return

        if field == "painnrs":
            # NRS 0-10: 0 = боли нет → пропускаем локацию
            if value == 0:
                data["pain_location"] = ""
                data["pain_on_game"] = 0
                state["step"] = "survey_illness"
                await self._ask_illness(update, ctx, state)
            else:
                state["step"] = "survey_pain_location"
                await q.edit_message_text(
                    f"📝 *Опрос*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n🤕 *Где болит?* (боль {value}/10)\n\nВыбери зону:",
                    reply_markup=self.kb([
                        [("🦶 Голеностоп", "painloc_голеностоп"), ("🦵 Колено", "painloc_колено")],
                        [("🍗 Бедро", "painloc_бедро"), ("🔙 Поясница", "painloc_поясница")],
                        [("💪 Плечо", "painloc_плечо"), ("🖐 Кисть", "painloc_кисть")],
                        [("🎗 Шея", "painloc_шея"), ("❓ Другое", "painloc_другое")],
                    ]), parse_mode="Markdown"
                )
            return

        if field == "paingame":
            data["pain_on_game"] = value
            state["step"] = "survey_illness"
            await self._ask_illness(update, ctx, state)
            return

        if field == "illness":
            data["illness_flag"] = {
                0: "", 1: "температура", 2: "насморк", 3: "голова", 4: "другое"
            }.get(value, "")
            state["step"] = "survey_analgesics"
            await q.edit_message_text(
                "💊 *Принимал ли обезболивающие?*\n\n(это важно — они могут скрывать травму)",
                reply_markup=self.kb([[(f"✅ Да", "analg_1"), (f"❌ Нет", "analg_0")]]),
                parse_mode="Markdown"
            )
            return

        if field == "analg":
            data["analgesics"] = value
            # Пульс запрашиваем ВСЕМ (в т.ч. младшим U14-U16, Simple-протокол)
            state["step"] = "survey_hr"
            await q.edit_message_text(
                f"📝 *Опрос*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"❤️ *Пульс покоя*\n\nИзмеряй утром, как только проснулся, ещё лежа в кровати и не вставая.\nПосчитай удары за 15 секунд и умножь на 4.\n\nВведи число:",
                parse_mode="Markdown"
            )
            return

        await q.edit_message_text("❌ Ошибка данных. Попробуйте ещё раз.",
                                  reply_markup=self.kb([[(f"🏠 Главное меню", "main_menu")]]))


    async def _ask_illness(self, update, ctx, state):
        """Флаг болезни (температура/насморк/голова) — шаг опроса."""
        q = update.callback_query
        await q.edit_message_text(
            "🤒 *Признаки болезни?*\n\nТемпература, насморк, головная боль?",
            reply_markup=self.kb([
                [("✅ Всё нормально", "illness_0")],
                [("🌡 Температура", "illness_1")],
                [("🤧 Насморк", "illness_2")],
                [("🤕 Голова болит", "illness_3")],
            ]), parse_mode="Markdown"
        )


    async def _training_yes(self, update, ctx):
        q = update.callback_query
        await q.answer()
        state = self.get_state(q.from_user.id)
        if state.get("step") != "survey_training":
            await q.edit_message_text(
                "❌ *Опрос прерван*. Начните заново.",
                reply_markup=self.kb([[(f"🔄 Пройти опрос", "do_survey")]])
            )
            return
        state["data"]["had_training"] = 1
        state["step"] = "survey_srpe"
        await q.edit_message_text(
            "⚡ *Оцените нагрузку (1-10):*\n\n1 — легко\n5 — умеренно\n10 — максимум\n\nВведи число:",
            parse_mode="Markdown"
        )


    async def _training_no(self, update, ctx):
        q = update.callback_query
        await q.answer()
        state = self.get_state(q.from_user.id)
        if state.get("step") != "survey_training":
            await q.edit_message_text(
                "❌ *Опрос прерван*. Начните заново.",
                reply_markup=self.kb([[(f"🔄 Пройти опрос", "do_survey")]])
            )
            return
        state["data"]["had_training"] = 0
        state["data"]["sRPE_score"] = None
        await self._route_after_survey(update, ctx, q.from_user.id, state)


    async def _route_after_survey(self, update, ctx, user_id, state):
        """Маршрутизация после опроса: пол → цикл → жалобы."""
        athlete = state.get("data", {}).get("athlete")
        if not athlete:
            await self._finish_survey(update, user_id, state)
            return

        gender = athlete.get("gender")
        age_group = athlete.get("age_group", "")
        cycle_length = None

        # Подгружаем свежие данные из БД (на случай если пол/цикл только что сохранили)
        fresh = self.db.get_athlete_by_telegram_id(user_id)
        if fresh:
            cycle_length = fresh.get("cycle_length_default")
        if not cycle_length:
            cycle_length = athlete.get("cycle_length_default")

        # Если пол не указан → спросить пол
        if gender is None:
            state["step"] = "ask_gender_first"
            q = update.callback_query if hasattr(update, 'callback_query') and update.callback_query else None
            text = "👤 *Укажите ваш пол*\n\nЭто нужно для дополнительных вопросов в опросе."
            buttons = self.kb([
                [("♂ Мужской", "gender_first_male")],
                [("♀ Женский", "gender_first_female")],
            ])
            if q:
                await q.edit_message_text(text, reply_markup=buttons, parse_mode="Markdown")
            else:
                await update.message.reply_text(text, reply_markup=buttons, parse_mode="Markdown")
            return

        # Девушка: пол → длина (если нет) → якорь/ежедневный тап по FLOW-модели
        if gender == "female":
            if not cycle_length:
                await self._ask_cycle_length(update, ctx)
                return
            from cycle_engine import cycle_step_for
            start = self.db.get_last_period_start(athlete["id"])
            mstatus = (fresh or {}).get("menarche_status")
            step = cycle_step_for(gender, start, mstatus)
            if step == "anchor":
                await self.ask_cycle_anchor(update, ctx)
            elif step == "daily":
                await self.ask_cycle_day(update, ctx)
            else:  # 'pre' / 'skip' — не nag-ить, цикл обходится
                await self._ask_complaints(update, ctx)
        else:
            await self._ask_complaints(update, ctx)


    async def _gender_first_chosen(self, update, ctx, gender):
        """Сохранить пол, выбранный во время опроса, и продолжить."""
        q = update.callback_query
        await q.answer()
        user_id = q.from_user.id
        state = self.get_state(user_id)
        athlete = state.get("data", {}).get("athlete")

        if not athlete:
            await q.edit_message_text(
                "❌ *Опрос прерван*. Начните заново.",
                reply_markup=self.kb([[(f"🔄 Пройти опрос", "do_survey")]])
            )
            return

        # Сохраняем пол в БД
        if athlete:
            self.db.update_athlete_gender(athlete["id"], gender)
            athlete["gender"] = gender

        # Если female → спросить длину цикла, иначе → жалобы
        if gender == "female":
            await self._ask_cycle_length(update, ctx)
        else:
            await self._ask_complaints(update, ctx)


    async def _finish_survey(self, source, user_id, state):
        from bot import get_score_emoji, score_bar, get_rank
        data = state["data"]
        athlete = data.get("athlete")
        if not athlete:
            return

        recs = self._doctor_recs(data, athlete["age_group"], athlete_id=athlete["id"])

        self.db.save_survey(athlete_id=athlete["id"], data={
            "sleep": data.get("sleep_score"), "stress": data.get("stress_score"),
            "fatigue": data.get("fatigue_score"), "soreness": data.get("muscle_soreness"),
            "mood": data.get("mood_score"), "hr": data.get("resting_hr"),
            "hrv": data.get("hrv_ms"), "training": data.get("had_training", 0),
            "srpe": data.get("sRPE_score"),
            "cycle_day": data.get("cycle_day"), "cycle_length": data.get("cycle_length"),
            "cycle_phase": data.get("cycle_phase"),
            "complaints": data.get("complaints"),
            "protocol": "simple" if athlete["age_group"] in SIMPLE_PROTOCOLS else "full",
            "auto_recommendation": recs if recs != "✅ Все показатели в норме!" else "",
            "sleep_hours": data.get("sleep_hours"),
            "readiness": data.get("readiness"),
            "pain_nrs": data.get("pain_nrs"),
            "pain_location": data.get("pain_location"),
            "pain_on_game": data.get("pain_on_game", 0),
            "illness_flag": data.get("illness_flag"),
            "analgesics": data.get("analgesics", 0),
        })

        # Персонализация цикла: если отмечен день 1 (начало месячных) — пересчитываем
        # фактическую длину цикла по истории отметок и обновляем cycle_length_default.
        cycle_update_note = ""
        if data.get("cycle_day") == 1 and athlete.get("gender") == "female":
            try:
                new_len = self.db.update_cycle_length_from_history(athlete["id"])
                if new_len and new_len != athlete.get("cycle_length_default"):
                    cycle_update_note = f"\n\n📅 Цикл персонализирован: ~*{new_len} дн.* (по твоим отметкам)"
                    athlete["cycle_length_default"] = new_len
            except Exception as e:
                logger.warning(f"cycle length update: {e}")

        # Готовность из 3 шкал (сон+утомление+боль; стресс/настроение убраны из опроса)
        hooper = sum(filter(None, [data.get("sleep_score"), data.get("fatigue_score"),
                                    data.get("muscle_soreness")]))
        hooper_max = 21

        athlete = self.db.get_athlete_by_telegram_id(user_id)
        streak = athlete.get("survey_streak", 0) if athlete else 0

        motivation = ""
        mkey = get_motivation_key(streak)
        if mkey:
            motivation = MOTIVATIONAL_MESSAGES[mkey]

        text = (
            f"✅ *Опрос завершён!*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        )
        if hooper >= 17:
            text += f"🟢 *Уровень готовности: Отличный* ({hooper}/21)\n\n"
        elif hooper >= 12:
            text += f"🟡 *Уровень готовности: Средний* ({hooper}/21)\n\n"
        else:
            text += f"🔴 *Уровень готовности: Низкий* ({hooper}/21)\n\n"
        text += (
            f"😴 Сон: {get_score_emoji(data.get('sleep_score'))} {score_bar(data.get('sleep_score', 0))}\n"
            f"😩 Утомление: {get_score_emoji(data.get('fatigue_score'))} {score_bar(data.get('fatigue_score', 0))}\n"
        )
        if data.get("resting_hr"):
            text += f"❤️ Пульс: {data['resting_hr']} уд/мин\n"
        text += f"\n🔥 Серия: {streak} дней | {get_rank(streak)}\n"
        if motivation:
            text += f"\n{motivation}\n"
        if cycle_update_note:
            text += cycle_update_note + "\n"
        if recs:
            text += f"\n*🏥 Рекомендации врача:*\n{recs}\n"
            text += f"\n⚠️ *Это не медицинское заключение.* При сомнениях обратись к врачу лично."

        buttons = [
            [(f"🏠 Главное меню", "main_menu")],
        ]

        try:
            if hasattr(source, "callback_query") and source.callback_query:
                await source.callback_query.edit_message_text(text, reply_markup=self.kb(buttons), parse_mode="Markdown")
            elif hasattr(source, "message") and source.message:
                await source.message.reply_text(text, reply_markup=self.kb(buttons), parse_mode="Markdown")
            elif hasattr(source, "edit_message_text"):
                await source.edit_message_text(text, reply_markup=self.kb(buttons), parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Finish survey error: {e}")

        # Отправляем рекомендации врачу (админу)
        try:
            for admin_id in self._full_access_ids():
                if admin_id != user_id:  # Не дублируем тому кто прошел
                    athlete_name = athlete.get("full_name", "?")
                    team = athlete.get("team", "?")
                    admin_text = (
                        f"📋 *Новый опрос — {athlete_name}*\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🏀 Команда: {team} | {athlete.get('age_group', '?')}\n\n"
                        f"😴 Сон: {get_score_emoji(data.get('sleep_score'))} {score_bar(data.get('sleep_score', 0))}\n"
                        f"😩 Утомление: {get_score_emoji(data.get('fatigue_score'))} {score_bar(data.get('fatigue_score', 0))}\n"
                    )
                    if data.get("resting_hr"):
                        admin_text += f"❤️ Пульс: {data['resting_hr']} уд/мин\n"
                    complaints = data.get("complaints", "")
                    if complaints:
                        admin_text += f"💬 Жалобы: {complaints}\n"
                    admin_text += f"\n🔥 Серия: {streak}д | Готовность: {hooper}/21"
                    if recs and recs != "✅ Все показатели в норме!":
                        admin_text += f"\n⚠️ *Нужно внимание:*\n{recs}\n"
                    else:
                        admin_text += f"\n✅ Все в норме\n"

                    # Кнопки врачу: прямой чат (у некоторых не открывается из-за приватности) + релей через бота
                    need_reply = bool(complaints) or (recs and recs != "✅ Все показатели в норме!")
                    _crow = contact_row(athlete) if need_reply else None
                    admin_buttons = [_crow] if _crow else None

                    await self._send_admin(admin_id, admin_text, buttons=admin_buttons)
        except Exception as e:
            logger.error(f"Admin notify error: {e}")

        # Уведомление тренеру команды спортсмена (в дополнение к врачу)
        try:
            _team = athlete.get("team")
            if _team:
                _coaches = self.db.get_team_coaches(_team)
                _full_ids = self._full_access_ids()
                for _cid in _coaches:
                    if _cid == user_id or _cid in _full_ids:
                        continue
                    _ctext = (
                        f"📋 *Опрос пройден — {athlete.get('full_name', '?')}*\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🏀 {_team} | {athlete.get('age_group', '?')}\n"
                    )
                    if hooper >= 17:
                        _ctext += f"🟢 Готовность: отличная ({hooper}/21)\n"
                    elif hooper >= 12:
                        _ctext += f"🟡 Готовность: средняя ({hooper}/21)\n"
                    else:
                        _ctext += f"🔴 Готовность: низкая ({hooper}/21)\n"
                    _nrs = data.get("pain_nrs")
                    if _nrs is not None and _nrs > 0:
                        _ctext += f"🤕 Боль: {_nrs}/10" + (f" ({data.get('pain_location', '')})" if data.get("pain_location") else "") + "\n"
                    if data.get("resting_hr"):
                        _ctext += f"❤️ Пульс: {data['resting_hr']} уд/мин\n"
                    if data.get("complaints"):
                        _ctext += f"💬 Жалобы: {data['complaints']}\n"
                    await self._send_admin(_cid, _ctext, buttons=None)
        except Exception as e:
            logger.error(f"Coach notify error: {e}")

        # 🚨 Алерт врачу при красных флагах опроса (боль NRS≥5, обезболивающие, болезнь)
        try:
            _flags = []
            _nrs = data.get("pain_nrs")
            if _nrs is not None and _nrs >= 5:
                _flags.append(f"🤕 Боль {_nrs}/10" + (f" ({data.get('pain_location', '')})" if data.get("pain_location") else ""))
            if data.get("analgesics"):
                _flags.append("💊 Обезболивающие (маскируют травму)")
            if data.get("illness_flag"):
                _flags.append(f"🤒 Болезнь: {data['illness_flag']}")
            if data.get("pain_on_game"):
                _flags.append("🏀 Болит на игре/тренировке")
            if _flags:
                _alert = (
                    f"🚨 *ТРЕБУЕТ ВНИМАНИЯ — {athlete.get('full_name', '?')}*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🏀 {athlete.get('team', '?')} | {athlete.get('age_group', '?')}\n\n"
                    + "\n".join(f"• {f}" for f in _flags)
                )
                _crow = contact_row(athlete)
                _alert_btn = [_crow] if _crow else None
                # Red-flag видят врачи И тренер команды спортсмена
                for _aid in red_flag_recipients(self.db, athlete.get("team"), self._full_access_ids(), user_id):
                    await self._send_admin(_aid, _alert, buttons=_alert_btn)
        except Exception as e:
            logger.error(f"Red-flag alert error: {e}")

        # Проверка прогресса целей
        try:
            goal_msgs = self.db.check_goals_progress(athlete["id"], data)
            if goal_msgs:
                for gm in goal_msgs:
                    try:
                        if hasattr(source, "callback_query") and source.callback_query:
                            await source.callback_query.message.reply_text(gm, parse_mode="Markdown")
                        elif hasattr(source, "message") and source.message:
                            await source.message.reply_text(gm, parse_mode="Markdown")
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"Goal check: {e}")

        self.clear_state(user_id)


    async def _ask_complaints(self, update, ctx):
        """Спросить про жалобы перед завершением опроса."""
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
        state["step"] = "survey_complaints"

        buttons = [
            [("💬 Ввести жалобу", "complaint_text")],
            [("✅ Нет жалоб", "complaint_none")],
        ]
        text = "💬 *Есть ли жалобы?*\n\nЧто беспокоит? Боли, дискомфорт, самочувствие?"

        if q:
            await q.edit_message_text(text, reply_markup=self.kb(buttons), parse_mode="Markdown")
        elif update.message:
            await update.message.reply_text(text, reply_markup=self.kb(buttons), parse_mode="Markdown")


    async def complaint_text(self, update, ctx):
        q = update.callback_query
        await q.answer()
        state = self.get_state(q.from_user.id)
        state["step"] = "complaint_text_input"
        await q.edit_message_text("📝 Опишите жалобу (отправьте текстовым сообщением):")


    async def complaint_none(self, update, ctx):
        q = update.callback_query
        await q.answer()
        state = self.get_state(q.from_user.id)
        state["data"]["complaints"] = ""
        await self._finish_survey(update, q.from_user.id, state)


    def _doctor_recs(self, data, age_group, athlete_id=None):
        recs = []
        # Личная норма (30 дн.) для подсказок в тексте рекомендаций
        bl = None
        if athlete_id:
            try:
                bl = self.db.get_individual_baseline(athlete_id, 30)
            except Exception:
                pass

        def _personal_note(metric):
            if not bl:
                return ""
            med = (bl.get("median") or {}).get(metric)
            return f" (ваша норма: ~{med:.1f})" if med is not None else ""

        def _esc(s):
            # Пользовательский текст не должен ломать Markdown (непарные * _ [ `)
            return str(s).replace("\\", "\\\\").replace("*", "\\*").replace("_", "\\_").replace("[", "\\[").replace("`", "\\`")

        sleep = data.get("sleep_score")
        if sleep is not None:
            if sleep <= 2:
                recs.append("😴 *Критический дефицит сна*\n• Норма: 7-9ч, оценка 5-7\n• Ложиться до 22:30\n• Исключить кофеин после 16:00\n• При сохранении >3 дней → врач")
            elif sleep <= 4:
                recs.append(f"😴 *Недостаток сна{_personal_note('sleep')}*\n• Норма: 7-9ч, оценка 5-7\n• Ложиться до 23:00\n• Убрать гаджеты за 1ч до сна")
        stress = data.get("stress_score")
        if stress is not None:
            if stress <= 2:
                recs.append("🧘 *Высокий стресс*\n• Дыхание 4-7-8: 5 циклов\n• Прогулка 30мин\n• Снизить нагрузку на 20%")
            elif stress <= 3:
                recs.append(f"🧘 *Умеренный стресс{_personal_note('stress')}*\n• Дыхательные практики 5мин\n• Ограничить соцсети за 2ч до сна")
        fatigue = data.get("fatigue_score")
        if fatigue is not None:
            if fatigue <= 2:
                recs.append("⚡ *Критическое утомление*\n• Отдых 1-2 дня\n• Сон не менее 9ч\n• Белок 1.6-2.2г/кг")
            elif fatigue <= 4:
                recs.append(f"⚡ *Повышенное утомление{_personal_note('fatigue')}*\n• Легкая тренировка (50%)\n• Сон +1ч\n• Вода 2-3л")
        hr = data.get("resting_hr")
        hr_norms = HR_NORMS.get(age_group, {"min": 40, "max": 70})
        if hr:
            personal_hr = ""
            if bl and bl.get("median_hr") is not None:
                personal_hr = f"\n• Ваша личная норма: ~{int(bl['median_hr'])} уд/мин"
            if hr > hr_norms["max"] + 15:
                recs.append(f"❤️ *Пульс критически высокий ({hr})*\n• Норма {age_group}: {hr_norms['min']}-{hr_norms['max']}{personal_hr}\n• Исключить интенсивные тренировки\n• При пульсе >80 >3 дней → ЭКГ")
            elif hr > hr_norms["max"] + 5:
                recs.append(f"❤️ *Пульс выше нормы ({hr})*\n• Норма: {hr_norms['min']}-{hr_norms['max']}{personal_hr}\n• Снизить нагрузку на 30%")
        # HRV-рекомендации (только для Full-протокола, где HRV собирается)
        hrv = data.get("hrv_ms") or data.get("hrv")
        hrv_norms = {"U16": {"min": 40, "max": 60, "crit": 30}, "U17": {"min": 35, "max": 55, "crit": 25},
                     "U18": {"min": 35, "max": 55, "crit": 25}, "U19": {"min": 35, "max": 50, "crit": 25},
                     "U21": {"min": 35, "max": 50, "crit": 25}, "Pro": {"min": 30, "max": 50, "crit": 20}}
        if hrv and age_group in hrv_norms:
            hn = hrv_norms[age_group]
            if hrv < hn["crit"]:
                recs.append(f"📉 *HRV критически низкий ({hrv}мс)*\n• Норма {age_group}: {hn['min']}-{hn['max']}мс\n• Признак перетренированности или болезни\n• Отдых 1-2 дня, лёгкая тренировка")
            elif hrv < hn["min"]:
                recs.append(f"📉 *HRV снижен ({hrv}мс)*\n• Норма: {hn['min']}-{hn['max']}мс\n• Снизить интенсивность на 30%\n• Качественный сон 8ч+")
        soreness = data.get("muscle_soreness")
        if soreness is not None and soreness <= 2:
            recs.append("🤕 *Выраженная мышечная боль*\n• Активное восстановление (плавание)\n• Массаж через 48ч\n• При боли >3 дней → врач")
        mood = data.get("mood_score")
        if mood is not None and mood <= 2:
            recs.append("😊 *Сниженное настроение*\n• Прогулка 30мин/день\n• Общение с близкими\n• При сохранении >2 недель → психолог")
        cycle_phase = data.get("cycle_phase")
        if cycle_phase:
            phase_lower = cycle_phase.lower()
            if "менструа" in phase_lower or "фоллику" in phase_lower:
                recs.append("🔄 *Фаза: Менструальная/Фолликулярная (1-13 день)*\n• Нагрузка: легкая-средняя\n• Добавки: Железо, Витамин C, Магний\n• Восстановление: растяжка, йога\n• Питание: больше белка, зелени")
            elif "овуля" in phase_lower:
                recs.append("🔄 *Фаза: Овуляторная (14-16 день)*\n• Нагрузка: пиковая — можно максимум\n• Добавки: Омега-3, Цинк\n• Восстановление: качественная заминка\n• Питание: сложные углеводы")
            elif "люте" in phase_lower or "желт" in phase_lower:
                recs.append("🔄 *Фаза: Лютеиновая (17-28 день)*\n• Нагрузка: сниженная\n• Добавки: Магний, Витамин B6, Омега-3\n• Восстановление: сон +1ч, массаж\n• Питание: магний, калий, уменьшить соль")
            else:
                recs.append(f"🔄 *Фаза цикла:* {cycle_phase}\n• Учитывай фазу при планировании нагрузок")

        # ============ ОБЪЕКТИВНЫЕ ФЛАГИ (Фаза 4, по научной критике) ============
        # RHR-флаг: пульс ≥ медиана(7д) + max(8, 10%) — флаг инфекции/перегрузки (2 дня подряд — в _individual_recs)
        if athlete_id and hr:
            try:
                if self.db.rhr_flag(athlete_id, hr, days=7):
                    recs.append(f"💓 *Пульс заметно выше личной нормы ({hr})*\n• Возможный признак начала болезни или перегрузки\n• Проверь температуру, при подтверждении — отдых")
            except Exception as e:
                logger.warning(f"rhr flag: {e}")

        # EWMA ACWR (uncoupled): только при ≥28 дней sRPE. Пороги не жёсткие (калибровка на своих данных).
        if athlete_id:
            try:
                acwr = self.db.ewma_acwr(athlete_id)
                if acwr:
                    ratio = acwr["acwr"]
                    if ratio >= 1.5:
                        recs.append(f"📊 *Нагрузка резко выросла (ACWR {ratio})*\n• Острая нагрузка {acwr['acute']} vs хроническая {acwr['chronic']}\n• Обсуди с тренером снижение на 2-3 дня")
                    elif ratio >= 1.3:
                        recs.append(f"📊 *Нагрузка на границе (ACWR {ratio})*\n• Острая/хроническая: {acwr['acute']}/{acwr['chronic']}\n• Контролируй восстановление (сон, HRV)")
                    elif ratio < 0.8:
                        recs.append(f"📊 *Нагрузка низкая (ACWR {ratio})*\n• Если возврат после паузы — наращивай постепенно")
            except Exception as e:
                logger.warning(f"acwr: {e}")

        # ============ АНКЕТА → РЕКОМЕНДАЦИИ (правила «если-то») ============
        # Данные анкеты (датированы) влияют на фокус рекомендаций. Устаревшая анкета (>120 дней) помечается.
        if athlete_id:
            try:
                qd = self.db.get_questionnaire(athlete_id)
                if qd:
                    q_stale = False
                    try:
                        from datetime import datetime as _dt
                        if qd.get("completed_at"):
                            q_dt = _dt.fromisoformat(str(qd["completed_at"]).replace("Z", "+00:00"))
                            if (_dt.now() - q_dt.replace(tzinfo=None)).days > 120:
                                q_stale = True
                    except Exception:
                        pass

                    # 1. Зоны травм за 3 мес / боль в зоне: если сегодня боль (≤3/7) — фокус
                    zones = str(qd.get("zones") or "")
                    pain_now = str(qd.get("pain_now_detail") or qd.get("pain_now") or "")
                    if zones and zones != "Ничего":
                        sore = data.get("muscle_soreness")
                        pain_nrs = data.get("pain_nrs")
                        if (sore is not None and sore <= 3) or (pain_nrs is not None and pain_nrs >= 4):
                            recs.append(f"🩹 *Проблемная зона из анкеты:* {_esc(zones)}\n• Боль в этой зоне — снизь нагрузку, покажи врачу")


                    # 2. Операция в анамнезе → боли в оперированной конечности вес ×2
                    surgery = str(qd.get("surgery_detail") or "")
                    if surgery and surgery not in ("Нет", "нет", "-"):
                        pain_nrs = data.get("pain_nrs")
                        if pain_nrs is not None and pain_nrs >= 3:
                            recs.append(f"🏥 *Операция в анамнезе ({_esc(surgery[:60])}) + боль сегодня ({pain_nrs}/10)*\n• Обязательно показать врачу, даже если боль слабая")

                    # 3. Страх рецидива высокий → ступенчатая экспозиция
                    # Показываем НЕ каждый день: только если анкета свежая (≤4 мес) и сегодня
                    # есть сопутствующий сигнал осторожности с нагрузкой (боль/крепатура/недосып/
                    # повышенное утомление/«болит на игре»/низкая готовность).
                    reinjury = str(qd.get("reinjury_fear") or "")
                    if reinjury == "Да, постоянно" and not q_stale:
                        _pain_today = (data.get("pain_nrs") is not None and data.get("pain_nrs") >= 3)
                        _on_game = bool(data.get("pain_on_game"))
                        _not_ready = (data.get("readiness") is not None and data.get("readiness") < 5)
                        _sore = (data.get("muscle_soreness") is not None and data.get("muscle_soreness") <= 3)
                        _tired = (data.get("fatigue_score") is not None and data.get("fatigue_score") <= 4)
                        _sleep = (data.get("sleep_score") is not None and data.get("sleep_score") <= 4)
                        if _pain_today or _on_game or _not_ready or _sore or _tired or _sleep:
                            recs.append("🧠 *Страх рецидива высокий*\n• Ступенчатое возвращение к нагрузке (начни с лёгкого)\n• Обсуди с врачом программу возврата")

                    # 4. Анкетные подсказки (тихие, не алерты)
                    if pain_now and pain_now not in ("Нет", "нет", "-"):
                        recs.append(f"📝 _Из анкеты:_ боль — {_esc(pain_now[:80])}")
                    meds = str(qd.get("meds_detail") or "")
                    if meds and any(k in meds.lower() for k in ("стимулят", "аддерал", "ритолин", "концерт", "страттера", "бета-блокат", "пропранолол", "атенолол", "метопролол")):
                        recs.append("📝 _Из анкеты:_ лекарства, влияющие на пульс (нормы RHR/HRV не применять)")
                    try:
                        water = float(qd.get("water") or 0)
                        if 0 < water < 1.5:
                            recs.append("📝 _Из анкеты:_ мало воды (<1.5л/день)")
                    except (TypeError, ValueError):
                        pass

                    # 5. Сезон → целевой диапазон нагрузки
                    season = str(qd.get("season") or "")
                    if "соревн" in season.lower() or "сезон" in season.lower():
                        pass  # целевой диапазон используется в Фазе 4 (прокси нагрузки)

                    # 6. Свежесть анкеты
                    if q_stale:
                        recs.append("📋 *Анкета устарела (>4 мес)* — обнови её (меню «Обновить анкету»), чтобы рекомендации были точными")
            except Exception as e:
                logger.warning(f"questionnaire recs: {e}")

        # ============ ТРЕНД-АНАЛИЗ (3 дня подряд) ============
        if athlete_id:
            try:
                hist3 = self.db.get_last_wellness(athlete_id, 3)
                if len(hist3) >= 3:
                    for metric, label, invert in [
                        ("sleep_score", "Сон", True),
                        ("readiness", "Готовность", True),
                        ("resting_hr", "Пульс", False),
                        ("fatigue_score", "Утомление", True),
                    ]:
                        vals = [h.get(metric) for h in hist3 if h.get(metric) is not None]
                        if len(vals) == 3:
                            if invert:
                                if vals[0] < vals[1] < vals[2]:
                                    recs.append(f"📉 *{label} снижается 3 дня* ({vals[2]}→{vals[1]}→{vals[0]})")
                                elif vals[0] > vals[1] > vals[2]:
                                    recs.append(f"📈 *{label} растёт 3 дня* ({vals[2]}→{vals[1]}→{vals[0]}) ✅")
                            else:
                                if vals[0] > vals[1] > vals[2]:
                                    recs.append(f"📈 *{label} растёт 3 дня* ({vals[2]}→{vals[1]}→{vals[0]})")
                                elif vals[0] < vals[1] < vals[2]:
                                    recs.append(f"📉 *{label} снижается 3 дня* ({vals[2]}→{vals[1]}→{vals[0]})")
            except Exception as e:
                logger.warning(f"trend analysis in recs: {e}")

        # Индивидуальные коридоры (личная норма 30 дн.) — если у спортсмена есть достаточная история
        if athlete_id:
            try:
                bl = self.db.get_individual_baseline(athlete_id, 30)
                if bl:
                    # для правила «2 отклонения подряд» берём предыдущий опрос (вчера)
                    hist = self.db.get_last_wellness(athlete_id, 2)
                    prev = hist[1] if len(hist) >= 2 else None
                    ind = self._individual_recs(data, bl, prev=prev)
                    if ind:
                        pad = "\n\n".join(ind)
                        recs.append("📊 *Отклонение от личной нормы (30 дн., 2 дня подряд):*\n" + pad)
            except Exception as e:
                logger.warning(f"individual baseline recs: {e}")

        return "\n\n".join(recs) if recs else "✅ Все показатели в норме!"


    def _individual_recs(self, data, bl, prev=None):
        """Алерты по личной норме (30 дн.), только при 2 ОТКЛОНЕНИЯХ ПОДРЯД (сегодня + вчера).
        Пороги: медиана ± max(1.5σ, 1.0) — устойчиво к выбросам.
        ВНИМАНИЕ: шкала опроса единая «7=хорошо, 1=плохо» для всех пяти показателей,
        поэтому для stress/fatigue/soreness плохо = балл НИЖЕ нормы (как и sleep/mood)."""
        median = bl.get("median") or {}
        std = bl.get("std") or {}

        def dev(value, kind):
            """True, если `value` выходит за личный порог (медиана - порог)."""
            if value is None:
                return False
            m = median.get(kind)
            if m is None:
                return False
            s = std.get(kind, 0) or 0
            threshold = max(1.0, 1.5 * s) if s > 0 else 1.5
            return value < m - threshold

        def dev_hr(value):
            """Пульс: выше медианы + max(1.5σ, +10%)."""
            if value is None or bl.get("median_hr") is None:
                return False
            s = bl.get("std_hr", 0) or 0
            thr = bl["median_hr"] + max(1.5 * s, bl["median_hr"] * 0.10)
            return value > thr

        out = []
        # если нет данных за «вчера» — не сигналим по индивидуальному (только общие коридоры)
        if prev is None:
            return out

        # пульс
        cur_hr = data.get("resting_hr")
        prev_hr = prev.get("resting_hr")
        if dev_hr(cur_hr) and dev_hr(prev_hr):
            out.append(f"❤️ *Пульс выше личной нормы 2 дня подряд ({cur_hr} vs ~{int(bl['median_hr'])}/30д)*\n• Обратить внимание на восстановление")

        checks = [
            ("sleep_score", "sleep", "😴 *Сон ниже личной нормы 2 дня подряд*"),
            ("mood_score", "mood", "😊 *Настроение ниже личной нормы 2 дня подряд*"),
            ("stress_score", "stress", "🧘 *Стресс выше личной нормы 2 дня подряд*"),
            ("fatigue_score", "fatigue", "⚡ *Утомление выше личной нормы 2 дня подряд*"),
            ("muscle_soreness", "soreness", "🤕 *Боль выше личной нормы 2 дня подряд*"),
        ]
        for key, kind, label in checks:
            cur = data.get(key); prv = prev.get(key)
            if dev(cur, kind) and dev(prv, kind):
                out.append(f"{label} ({cur} vs ~{median.get(kind):.1f}/30д)")
        return out



    # ==================== УДАЛЕНИЕ СПОРТСМЕНА (ТОЛЬКО АДМИН) ====================


