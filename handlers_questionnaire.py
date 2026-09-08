"""
Миксин: QuestionnaireMixin
Автоматически извлечён из bot.py
"""

import logging
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from bot import SportHealthBot


class QuestionnaireMixin:
    """Миксин для questionnaire."""

    async def start_questionnaire(self, update, ctx):
        q = update.callback_query
        await q.answer()
        user_id = q.from_user.id
        athlete = self.db.get_athlete_by_telegram_id(user_id)
        if not athlete:
            await q.edit_message_text("❌ Сначала зарегистрируйся!", reply_markup=self.kb([[(f"📝 Регистрация", "start_reg")]]))
            return
        if self.db.has_questionnaire(athlete["id"]) and not self.db.has_incomplete_questionnaire(athlete["id"]):
            await q.edit_message_text(
                "📋 *Анкета уже заполнена.*\n\nОбновить её сейчас? Данные обновятся для врача.",
                parse_mode="Markdown",
                reply_markup=self.kb([
                    [(f"✅ Обновить анкету", "q_restart")],
                    [(f"🏠 Главное меню", "main_menu")]
                ])
            )
            return

        # Если есть незавершённая анкета — предложить продолжить с того места
        incomplete = self.db.has_incomplete_questionnaire(athlete["id"])
        state = self.get_state(user_id)

        if incomplete and state.get("q_data"):
            # продолжаем
            last_step = state.get("step", "q_age")
            await self._q_resume_questionnaire(update, ctx, state)
            return

        # начинаем или продолжаем с сохранённого прогресса
        progress = self.db.get_questionnaire_progress(athlete["id"])
        if progress:
            # переносим сохранённые данные в q_data
            saved = {k: v for k, v in progress.items() if k not in ("id", "athlete_id", "completed_at")}
            state["q_data"] = saved
            # определяем следующий шаг
            await self._q_resume_questionnaire(update, ctx, state)
            return

        state["step"] = "q_age"
        state["q_data"] = {}
        await q.edit_message_text("📋 *Анкета баскетболиста*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nБлок 1: Общие данные\n\nСколько тебе полных лет?", parse_mode="Markdown")


    async def questionnaire_restart(self, update, ctx):
        """Обновление анкеты: очистить старые ответы и начать заново."""
        q = update.callback_query
        await q.answer()
        user_id = q.from_user.id
        athlete = self.db.get_athlete_by_telegram_id(user_id)
        if not athlete:
            return
        # Сбрасываем прогресс в БД (старая анкета перезапишется при сохранении)
        try:
            self.db.conn.execute("DELETE FROM questionnaires WHERE athlete_id = ?", (athlete["id"],))
            self.db.conn.commit()
        except Exception as e:
            logger.warning(f"q_restart delete: {e}")
        self.clear_state(user_id)
        state = self.get_state(user_id)
        state["step"] = "q_age"
        state["q_data"] = {}
        await q.edit_message_text(
            "📋 *Обновление анкеты*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nБлок 1: Общие данные\n\nСколько тебе полных лет?",
            parse_mode="Markdown"
        )


    async def _q_resume_questionnaire(self, update, ctx, state):
        """Продолжить анкету с того шага, где остановился."""
        q = update.callback_query
        data = state.get("q_data", {})
        # Определяем следующий незаполненный шаг
        step_order = [
            ("q_age", "age"), ("q_phone", "phone"), ("q_birth_date", "birth_date"), ("q_gender", "gender"), ("q_position", "position"),
            ("q_level", "level"), ("q_experience", "experience"), ("q_height_weight", "height"),
            ("q_trauma_12m", "trauma_12m"), ("q_zones", "zones"),
            ("q_pain_now", "pain_now"), ("q_chronic", "chronic"),
            ("q_surgery", "surgery"), ("q_meds", "meds"), ("q_allergies", "allergies"),
            ("q_train_count", "train_count"), ("q_train_duration", "train_duration"),
            ("q_season", "season"), ("q_form", "form_score"),
            ("q_sleep_score", "sleep_score"), ("q_warmup", "warmup"),
            ("q_recovery", "recovery"), ("q_water", "water"), ("q_diet", "diet"),
            ("q_pre_meal", "pre_meal"), ("q_supplements", "supplements"),
            ("q_motivation", "motivation"), ("q_stress", "stress"),
            ("q_match_state", "match_state"), ("q_reinjury", "reinjury_fear"),
        ]
        for step, key in step_order:
            if key not in data or data[key] in (None, ""):
                state["step"] = step
                await self._q_ask_step(update, ctx, step)
                return
        # Всё заполнено (кроме текстовых goal/wish) — спрашиваем цель
        if not data.get("goal"):
            state["step"] = "q_goal"
            await q.edit_message_text("📋 *Блок 6: Цели*\n\nКакую главную цель ставишь? (напиши текстом)", parse_mode="Markdown")
            return
        if not data.get("wish"):
            state["step"] = "q_wish"
            await q.edit_message_text("📋 *Блок 6: Цели*\n\nЕсть что-то, с чем нужна помощь?\n(можно пропустить — напиши «-»)", parse_mode="Markdown")
            return
        # завершено
        state["step"] = "q_wish"
        await q.edit_message_text("📋 *Блок 6: Цели*\n\nЕсть что-то, с чем нужна помощь?\n(можно пропустить — напиши «-»)", parse_mode="Markdown")


    async def _q_show_supp_buttons(self, update, ctx, selected):
        """Мультивыбор спортпита: кнопки тоглят выбранное (✅/⬜), Другое + Готово."""
        q = update.callback_query if hasattr(update, 'callback_query') and update.callback_query else None
        supp_options = ["Протеин", "Креатин", "Хондропротекторы", "Изотоники", "Кофеин", "Ничего"]
        # приведём selected к списку
        if isinstance(selected, str):
            sel_list = [selected] if selected and selected != "Ничего" else []
        elif isinstance(selected, list):
            sel_list = selected
        else:
            sel_list = []

        def mark(opt):
            return "✅ " + opt if opt in sel_list else "⬜ " + opt

        buttons = [
            [(f"{mark('Протеин')}", "q_supp_toggle_Протеин"), (f"{mark('Креатин')}", "q_supp_toggle_Креатин")],
            [(f"{mark('Хондропротекторы')}", "q_supp_toggle_Хондропротекторы"), (f"{mark('Изотоники')}", "q_supp_toggle_Изотоники")],
            [(f"{mark('Кофеин')}", "q_supp_toggle_Кофеин"), (f"{mark('Ничего')}", "q_supp_toggle_Ничего")],
            [("✏️ Другое (введу сам)", "q_supp_other")],
            [("✅ Готово", "q_supp_done")],
        ]
        text = (
            "📋 *Блок 4: Питание*\n\nСпортпит / добавки?\n"
            "Можно выбрать несколько. Нажми «Готово», когда закончишь."
        )
        if sel_list:
            text += f"\n\nВыбрано: {', '.join(sel_list)}"
        if q:
            await q.edit_message_text(text, reply_markup=self.kb(buttons), parse_mode="Markdown")
        else:
            await update.message.reply_text(text, reply_markup=self.kb(buttons), parse_mode="Markdown")


    async def _q_ask_step(self, update, ctx, step):
        """Показать вопрос для конкретного шага анкеты."""
        q = update.callback_query
        questions = {
            "q_age": ("📋 *Блок 1: Общие данные*\n\nСколько тебе полных лет?", None),
            "q_phone": ("📋 *Блок 1: Общие данные*\n\nВведи номер телефона:", None),
            "q_birth_date": ("📋 *Блок 1: Общие данные*\n\nВведи дату рождения (ДД.ММ.ГГГГ):", None),
            "q_gender": ("📋 *Блок 1*\n\nВаш пол?", [[("М", "q_gender_М"), ("Ж", "q_gender_Ж")]]),
            "q_position": ("📋 *Блок 1*\n\nИгровая позиция?", [[("PG","q_position_PG"),("SG","q_position_SG"),("SF","q_position_SF")],[("PF","q_position_PF"),("C","q_position_C"),("Универсал","q_position_Универсал")]]),
            "q_level": ("📋 *Блок 1*\n\nУровень игры?", [[("Любитель","q_level_Любитель")],[("Полупрофессионал","q_level_Полупрофессионал")],[("Профессионал","q_level_Профессионал")],[("Ветеран","q_level_Ветеран")]]),
            "q_experience": ("📋 *Блок 1*\n\nКак давно играешь?", [[("менее 1 года","q_exp_менее 1 года")],[("1–3 года","q_exp_1–3 года")],[("3–7 лет","q_exp_3–7 лет")],[("7–15 лет","q_exp_7–15 лет")],[("более 15 лет","q_exp_более 15 лет")]]),
            "q_trauma_12m": ("📋 *Блок 2: Травмы*\n\nБыли травмы за 12 мес?", [[("Да","q_trauma_Да"),("Нет","q_trauma_Нет")]]),
            "q_zones": ("📋 *Блок 2*\n\nЗоны, которые беспокоили:", [[("Голеностопы","q_zones_Голеностопы"),("Колени","q_zones_Колени")],[("Бёдра","q_zones_Бёдра"),("Поясница","q_zones_Поясница")],[("Кисти","q_zones_Кисти"),("Плечи","q_zones_Плечи")],[("Шея","q_zones_Шея"),("Ничего","q_zones_Ничего")]]),
            "q_pain_now": ("📋 *Блок 2*\n\nЕсть боль сейчас?", [[("Да","q_pain_Да"),("Нет","q_pain_Нет")]]),
            "q_chronic": ("📋 *Блок 2*\n\nРецидивирующая травма?", [[("Да","q_chronic_Да"),("Нет","q_chronic_Нет")]]),
            "q_surgery": ("📋 *Блок 2*\n\nБыли операции из-за спорта?", [[("Да","q_surgery_Да"),("Нет","q_surgery_Нет")]]),
            "q_meds": ("📋 *Блок 2*\n\nПринимаешь лекарства/БАДы?", [[("Да","q_meds_Да"),("Нет","q_meds_Нет")]]),
            "q_allergies": ("📋 *Блок 2*\n\nЕсть аллергии?", [[("Да","q_allergies_Да"),("Нет","q_allergies_Нет")]]),
            "q_train_count": ("📋 *Блок 3: Режим*\n\nСколько тренировок в неделю?", [[(str(i),f"q_tcount_{i}") for i in range(0,11)]]),
            "q_train_duration": ("📋 *Блок 3*\n\nДлительность тренировки?", [[(str(i),f"q_tdur_{i}") for i in [30,45,60,90,120,180]]]),
            "q_season": ("📋 *Блок 3*\n\nСезон или межсезонье?", [[("Игровой сезон","q_season_Игровой сезон")],[("Предсезонка","q_season_Предсезонка")],[("Межсезонье","q_season_Межсезонье")],[("Пауза/отпуск","q_season_Пауза/отпуск")]]),
            "q_form": ("📋 *Блок 3*\n\nФизическая форма (1-10)?", [[(str(i),f"q_form_{i}") for i in range(1,6)],[(str(i),f"q_form_{i}") for i in range(6,11)]]),
            "q_sleep_score": ("📋 *Блок 3*\n\nКачество сна (1-10)?", [[(str(i),f"q_sleep_{i}") for i in range(1,6)],[(str(i),f"q_sleep_{i}") for i in range(6,11)]]),
            "q_warmup": ("📋 *Блок 3*\n\nРазминка/заминка?", [[("Да, всегда","q_warm_Да, всегда"),("Только разминку","q_warm_Только разминку")],[("Только заминку","q_warm_Только заминку"),("Нет","q_warm_Нет")],[("Не регулярно","q_warm_Не регулярно")]]),
            "q_recovery": ("📋 *Блок 3*\n\nВосстановительные дни?", [[("Да, регулярно","q_rec_Да, регулярно"),("Иногда","q_rec_Иногда")],[("Нет","q_rec_Нет"),("Не знаю","q_rec_Не знаю")]]),
            "q_water": ("📋 *Блок 4: Питание*\n\nСколько воды (л) в день?", [[("1л","q_water_1"),("1.5л","q_water_1.5")],[("2л","q_water_2"),("2.5л","q_water_2.5")],[("3л","q_water_3"),("3.5л+","q_water_3.5")]]),
            "q_diet": ("📋 *Блок 4*\n\nПлан питания?", [[("Да, с диетологом","q_diet_Да, с диетологом"),("Да, сам","q_diet_Да, сам")],[("Частично","q_diet_Частично"),("Нет","q_diet_Нет")],[("Не задумывался","q_diet_Не задумывался")]]),
            "q_pre_meal": ("📋 *Блок 4*\n\nЗа сколько ешь перед тренировкой?", [[("За 1-2ч","q_meal_За 1-2ч"),("За 3-4ч","q_meal_За 3-4ч")],[("Менее чем за час","q_meal_Менее чем за час"),("Натощак","q_meal_Натощак")]]),
            "q_supplements": ("📋 *Блок 4*\n\nСпортпит?", [[("Протеин","q_supp_Протеин"),("Креатин","q_supp_Креатин")],[("Хондропротекторы","q_supp_Хондропротекторы"),("Изотоники","q_supp_Изотоники")],[("Кофеин","q_supp_Кофеин"),("Ничего","q_supp_Ничего")],[("Другое","q_supp_Другое")]]),
            "q_motivation": ("📋 *Блок 5: Психология*\n\nМотивация (1-10)?", [[(str(i),f"q_motiv_{i}") for i in range(1,6)],[(str(i),f"q_motiv_{i}") for i in range(6,11)]]),
            "q_stress": ("📋 *Блок 5*\n\nВнешние факторы мешают?", [[("Да, сильно","q_stress_Да, сильно"),("Периодически","q_stress_Периодически")],[("Нет","q_stress_Нет"),("Не влияют","q_stress_Не влияют")]]),
            "q_match_state": ("📋 *Блок 5*\n\nСостояние перед матчами?", [[("Спокойная уверенность","q_match_Спокойная уверенность")],[("Лёгкое волнение","q_match_Лёгкое волнение")],[("Сильная тревога","q_match_Сильная тревога")],[("По-разному","q_match_По-разному")]]),
            "q_reinjury": ("📋 *Блок 5*\n\nБоишься повторной травмы?", [[("Да, постоянно","q_reinj_Да, постоянно"),("Иногда","q_reinj_Иногда")],[("Нет","q_reinj_Нет"),("Не было травм","q_reinj_Не было травм")]]),
        }
        if step in questions:
            if step == "q_supplements":
                # мультивыбор спортпита
                state = self.get_state(q.from_user.id)
                cur = state.get("q_data", {}).get("supplements", [])
                await self._q_show_supp_buttons(update, ctx, cur)
                return
            text, buttons = questions[step]
            kb = self.kb(buttons) if buttons else None
            await q.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
        else:
            state = self.get_state(q.from_user.id)
            state["step"] = step
            # текстовые шаги
            text_map = {
                "q_trauma_12m_detail": "📋 *Блок 2*\n\nКакие травмы и как лечил?",
                "q_pain_now_detail": "📋 *Блок 2*\n\nГде и как давно?",
                "q_chronic_detail": "📋 *Блок 2*\n\nКакая? Как часто обостряется?",
                "q_surgery_detail": "📋 *Блок 2*\n\nКакие операции?",
                "q_surgery_date": "📋 *Блок 2*\n\nКогда была операция (год или дата)?",
                "q_meds_detail": "📋 *Блок 2*\n\nКакие именно?",
                "q_allergies_detail": "📋 *Блок 2*\n\nНа что аллергия?",
            }
            await q.edit_message_text(text_map.get(step, "Продолжим"), parse_mode="Markdown")


    async def handle_questionnaire_answer(self, update, ctx):
        q = update.callback_query
        await q.answer()
        user_id = q.from_user.id
        state = self.get_state(user_id)
        step = state.get("step", "")
        data = state.get("q_data", {})
        athlete = self.db.get_athlete_by_telegram_id(user_id)
        if not athlete: return

        if step == "q_age":
            data["age"] = q.data.replace("q_age_", "")
            state["step"] = "q_gender"
            await q.edit_message_text("📋 *Блок 1: Общие данные*\n\nВаш пол?", reply_markup=self.kb([[("М", "q_gender_М"), ("Ж", "q_gender_Ж")]]), parse_mode="Markdown")
            return

        if step == "q_gender":
            data["gender"] = q.data.replace("q_gender_", "")
            state["step"] = "q_position"
            await q.edit_message_text("📋 *Блок 1: Общие данные*\n\nВаша основная игровая позиция?", reply_markup=self.kb([
                [("PG", "q_position_PG"), ("SG", "q_position_SG"), ("SF", "q_position_SF")],
                [("PF", "q_position_PF"), ("C", "q_position_C"), ("Универсал", "q_position_Универсал")],
            ]), parse_mode="Markdown")
            return

        if step == "q_position":
            data["position"] = q.data.replace("q_position_", "")
            state["step"] = "q_level"
            await q.edit_message_text("📋 *Блок 1: Общие данные*\n\nУровень игры?", reply_markup=self.kb([
                [("Любитель", "q_level_Любитель")],
                [("Полупрофессионал", "q_level_Полупрофессионал")],
                [("Профессионал", "q_level_Профессионал")],
                [("Ветеран", "q_level_Ветеран")],
            ]), parse_mode="Markdown")
            return

        if step == "q_level":
            data["level"] = q.data.replace("q_level_", "")
            state["step"] = "q_experience"
            await q.edit_message_text("📋 *Блок 1: Общие данные*\n\nКак давно играешь в баскетбол?", reply_markup=self.kb([
                [("менее 1 года", "q_exp_менее 1 года")],
                [("1–3 года", "q_exp_1–3 года")],
                [("3–7 лет", "q_exp_3–7 лет")],
                [("7–15 лет", "q_exp_7–15 лет")],
                [("более 15 лет", "q_exp_более 15 лет")],
            ]), parse_mode="Markdown")
            return

        if step == "q_experience":
            data["experience"] = q.data.replace("q_exp_", "")
            state["step"] = "q_height_weight"
            await q.edit_message_text(
                "📋 *Блок 1: Общие данные*\n\n"
                "📏 *Введи рост и вес через пробел:*\n"
                "Например: `185 82`\n\n"
                "Если не знаешь — напиши «-»",
                parse_mode="Markdown"
            )
            return

        if step == "q_trauma_12m":
            data["trauma_12m"] = q.data.replace("q_trauma_", "")
            if data["trauma_12m"] == "Да":
                state["step"] = "q_trauma_detail"
                await q.edit_message_text("📋 *Блок 2: Травмы*\n\nКакие травмы и как лечил?", parse_mode="Markdown")
            else:
                data["trauma_12m_detail"] = ""
                state["step"] = "q_zones"
                await self._q_show_zones(update, ctx)
            return

        if step == "q_zones":
            data["zones"] = q.data.replace("q_zones_", "")
            state["step"] = "q_pain_now"
            await q.edit_message_text("📋 *Блок 2: Травмы*\n\nЕсть боль или дискомфорт прямо сейчас?", reply_markup=self.kb([[("Да", "q_pain_Да"), ("Нет", "q_pain_Нет")]]), parse_mode="Markdown")
            return

        if step == "q_pain_now":
            data["pain_now"] = q.data.replace("q_pain_", "")
            if data["pain_now"] == "Да":
                state["step"] = "q_pain_detail"
                await q.edit_message_text("📋 *Блок 2: Травмы*\n\nГде и как давно?", parse_mode="Markdown")
            else:
                data["pain_now_detail"] = ""
                state["step"] = "q_chronic"
                await q.edit_message_text("📋 *Блок 2: Травмы*\n\nЕсть рецидивирующая (повторяющаяся) травма?", reply_markup=self.kb([[("Да", "q_chronic_Да"), ("Нет", "q_chronic_Нет")]]), parse_mode="Markdown")
            return

        if step == "q_chronic":
            data["chronic"] = q.data.replace("q_chronic_", "")
            if data["chronic"] == "Да":
                state["step"] = "q_chronic_detail"
                await q.edit_message_text("📋 *Блок 2: Травмы*\n\nКакая? Как часто обостряется?", parse_mode="Markdown")
            else:
                data["chronic_detail"] = ""
                await self._q_continue_block2(update, ctx)
            return

        if step == "q_surgery":
            data["surgery"] = q.data.replace("q_surgery_", "")
            if data["surgery"] == "Да":
                state["step"] = "q_surgery_detail"
                await q.edit_message_text("📋 *Блок 2: Травмы*\n\nКакие были операции?", parse_mode="Markdown")
            else:
                await self._q_continue_block2_meds(update, ctx)
            return

        if step == "q_meds":
            data["meds"] = q.data.replace("q_meds_", "")
            if data["meds"] == "Да":
                state["step"] = "q_meds_detail"
                await q.edit_message_text("📋 *Блок 2: Травмы*\n\nКакие именно?", parse_mode="Markdown")
            else:
                data["meds_detail"] = ""
                state["step"] = "q_allergies"
                await q.edit_message_text("📋 *Блок 2: Травмы*\n\nЕсть аллергии?", reply_markup=self.kb([[("Да", "q_allergies_Да"), ("Нет", "q_allergies_Нет")]]), parse_mode="Markdown")

        if step == "q_allergies":
            data["allergies"] = q.data.replace("q_allergies_", "")
            if data["allergies"] == "Да":
                state["step"] = "q_allergies_detail"
                await q.edit_message_text("📋 *Блок 2: Травмы*\n\nНа что аллергия? (что вызывает, как проявляется)", parse_mode="Markdown")
            else:
                data["allergies_detail"] = ""
                await self._q_save_then_block3(update, ctx)
            return

        # Блок 3: тренировочный режим
        if step == "q_train_count":
            state["q_data"]["train_count"] = int(q.data.replace("q_tcount_", ""))
            # автoсохранение прогресса
            try:
                await self._db_run(self.db.save_questionnaire, athlete["id"], state.get("q_data", {}))
            except Exception as e:
                logger.error(f"Q autosave: {e}")
            state["step"] = "q_train_duration"
            await q.edit_message_text("📋 *Блок 3: Режим*\n\nСколько минут длится тренировка/игра?", reply_markup=self.kb([[(str(i), f"q_tdur_{i}") for i in [30,45,60,90,120,180]]]), parse_mode="Markdown")
            return

        if step == "q_train_duration":
            state["q_data"]["train_duration"] = int(q.data.replace("q_tdur_", ""))
            # автoсохранение прогресса
            try:
                await self._db_run(self.db.save_questionnaire, athlete["id"], state.get("q_data", {}))
            except Exception as e:
                logger.error(f"Q autosave: {e}")
            state["step"] = "q_season"
            await q.edit_message_text("📋 *Блок 3: Режим*\n\nСейчас сезон или межсезонье?", reply_markup=self.kb([[("Игровой сезон", "q_season_Игровой сезон")], [("Предсезонка", "q_season_Предсезонка")], [("Межсезонье", "q_season_Межсезонье")], [("Пауза/отпуск", "q_season_Пауза/отпуск")]]), parse_mode="Markdown")
            return

        if step == "q_season":
            state["q_data"]["season"] = q.data.replace("q_season_", "")
            # автoсохранение прогресса
            try:
                await self._db_run(self.db.save_questionnaire, athlete["id"], state.get("q_data", {}))
            except Exception as e:
                logger.error(f"Q autosave: {e}")
            state["step"] = "q_form"
            await q.edit_message_text("📋 *Блок 3: Режим*\n\nОцени физическую форму (1-10):", reply_markup=self.kb([[(str(i), f"q_form_{i}") for i in range(1,6)],[(str(i), f"q_form_{i}") for i in range(6,11)]]), parse_mode="Markdown")
            return

        if step == "q_form":
            state["q_data"]["form_score"] = int(q.data.replace("q_form_", ""))
            # автoсохранение прогресса
            try:
                await self._db_run(self.db.save_questionnaire, athlete["id"], state.get("q_data", {}))
            except Exception as e:
                logger.error(f"Q autosave: {e}")
            state["step"] = "q_sleep_score"
            await q.edit_message_text("📋 *Блок 3: Режим*\n\nОцени качество сна (1-10):", reply_markup=self.kb([[(str(i), f"q_sleep_{i}") for i in range(1,6)],[(str(i), f"q_sleep_{i}") for i in range(6,11)]]), parse_mode="Markdown")
            return

        if step == "q_sleep_score":
            state["q_data"]["sleep_score"] = int(q.data.replace("q_sleep_", ""))
            # автoсохранение прогресса
            try:
                await self._db_run(self.db.save_questionnaire, athlete["id"], state.get("q_data", {}))
            except Exception as e:
                logger.error(f"Q autosave: {e}")
            state["step"] = "q_warmup"
            await q.edit_message_text("📋 *Блок 3: Режим*\n\nДелаешь разминку/заминку?", reply_markup=self.kb([[("Да, всегда", "q_warm_Да, всегда"), ("Только разминку", "q_warm_Только разминку")], [("Только заминку", "q_warm_Только заминку"), ("Нет", "q_warm_Нет")], [("Не регулярно", "q_warm_Не регулярно")]]), parse_mode="Markdown")
            return

        if step == "q_warmup":
            state["q_data"]["warmup"] = q.data.replace("q_warm_", "")
            # автoсохранение прогресса
            try:
                await self._db_run(self.db.save_questionnaire, athlete["id"], state.get("q_data", {}))
            except Exception as e:
                logger.error(f"Q autosave: {e}")
            state["step"] = "q_recovery"
            await q.edit_message_text("📋 *Блок 3: Режим*\n\nЕсть восстановительные дни?", reply_markup=self.kb([[("Да, регулярно", "q_rec_Да, регулярно"), ("Иногда", "q_rec_Иногда")], [("Нет", "q_rec_Нет"), ("Не знаю", "q_rec_Не знаю")]]), parse_mode="Markdown")
            return

        if step == "q_recovery":
            state["q_data"]["recovery"] = q.data.replace("q_rec_", "")
            # автoсохранение прогресса
            try:
                await self._db_run(self.db.save_questionnaire, athlete["id"], state.get("q_data", {}))
            except Exception as e:
                logger.error(f"Q autosave: {e}")
            state["step"] = "q_water"
            await q.edit_message_text("📋 *Блок 4: Питание*\n\nСколько воды (л) в день?", reply_markup=self.kb([[("1л", "q_water_1"), ("1.5л", "q_water_1.5")], [("2л", "q_water_2"), ("2.5л", "q_water_2.5")], [("3л", "q_water_3"), ("3.5л+", "q_water_3.5")]]), parse_mode="Markdown")
            return

        if step == "q_water":
            state["q_data"]["water"] = float(q.data.replace("q_water_", ""))
            # автoсохранение прогресса
            try:
                await self._db_run(self.db.save_questionnaire, athlete["id"], state.get("q_data", {}))
            except Exception as e:
                logger.error(f"Q autosave: {e}")
            state["step"] = "q_diet"
            await q.edit_message_text("📋 *Блок 4: Питание*\n\nПлан питания?", reply_markup=self.kb([[("Да, с диетологом", "q_diet_Да, с диетологом"), ("Да, сам", "q_diet_Да, сам")], [("Частично", "q_diet_Частично"), ("Нет", "q_diet_Нет")], [("Не задумывался", "q_diet_Не задумывался")]]), parse_mode="Markdown")
            return

        if step == "q_diet":
            state["q_data"]["diet"] = q.data.replace("q_diet_", "")
            # автoсохранение прогресса
            try:
                await self._db_run(self.db.save_questionnaire, athlete["id"], state.get("q_data", {}))
            except Exception as e:
                logger.error(f"Q autosave: {e}")
            state["step"] = "q_pre_meal"
            await q.edit_message_text("📋 *Блок 4: Питание*\n\nЗа сколько обычно ешь перед тренировкой или игрой?", reply_markup=self.kb([[("За 1-2ч", "q_meal_За 1-2ч"), ("За 3-4ч", "q_meal_За 3-4ч")], [("Менее чем за час", "q_meal_Менее чем за час"), ("Натощак", "q_meal_Натощак")]]), parse_mode="Markdown")
            return

        if step == "q_pre_meal":
            state["q_data"]["pre_meal"] = q.data.replace("q_meal_", "")
            # автoсохранение прогресса
            try:
                await self._db_run(self.db.save_questionnaire, athlete["id"], state.get("q_data", {}))
            except Exception as e:
                logger.error(f"Q autosave: {e}")
            state["step"] = "q_supplements"
            cur = state.get("q_data", {}).get("supplements", [])
            await self._q_show_supp_buttons(update, ctx, cur)
            return

        if step == "q_supplements":
            supp_options = ["Протеин", "Креатин", "Хондропротекторы", "Изотоники", "Кофеин", "Ничего"]

            # Кнопка "Готово" -> сохранить и перейти к блоку 5
            if q.data == "q_supp_done":
                if not state["q_data"].get("supplements"):
                    state["q_data"]["supplements"] = "Ничего"
                # автoсохранение
                try:
                    await self._db_run(self.db.save_questionnaire, athlete["id"], state.get("q_data", {}))
                except Exception as e:
                    logger.error(f"Q autosave: {e}")
                state["step"] = "q_motivation"
                await q.edit_message_text("📋 *Блок 5: Психология*\n\nМотивация (1-10):", reply_markup=self.kb([[(str(i), f"q_motiv_{i}") for i in range(1,6)],[(str(i), f"q_motiv_{i}") for i in range(6,11)]]), parse_mode="Markdown")
                return

            # Кнопка "Другое" -> ввести свой вариант
            if q.data == "q_supp_other":
                state["step"] = "q_supp_other_input"
                await q.edit_message_text("📋 *Блок 4: Питание*\n\nНапиши свой вариант спортпита:", parse_mode="Markdown")
                return

            # Тоггл выбранного варианта (q_supp_toggle_<name>)
            if q.data.startswith("q_supp_toggle_"):
                option = q.data.replace("q_supp_toggle_", "")
                cur = state["q_data"].get("supplements", [])
                if isinstance(cur, str):
                    cur = [cur] if cur and cur != "Ничего" else []
                cur = list(cur) if isinstance(cur, list) else []
                if option == "Ничего":
                    # если выбрали Ничего — очищаем остальные
                    cur = ["Ничего"]
                else:
                    if "Ничего" in cur:
                        cur.remove("Ничего")
                    if option in cur:
                        cur.remove(option)
                    else:
                        cur.append(option)
                state["q_data"]["supplements"] = cur
                # автoсохранение
                try:
                    await self._db_run(self.db.save_questionnaire, athlete["id"], state.get("q_data", {}))
                except Exception as e:
                    logger.error(f"Q autosave: {e}")
                # перерисовать кнопки с отметками
                await self._q_show_supp_buttons(update, ctx, cur)
                return

            # Обратная совместимость: единичный выбор q_supp_X (не используется в новой UI)
            state["q_data"]["supplements"] = q.data.replace("q_supp_", "")
            # автoсохранение
            try:
                await self._db_run(self.db.save_questionnaire, athlete["id"], state.get("q_data", {}))
            except Exception as e:
                logger.error(f"Q autosave: {e}")
            state["step"] = "q_motivation"
            await q.edit_message_text("📋 *Блок 5: Психология*\n\nМотивация (1-10):", reply_markup=self.kb([[(str(i), f"q_motiv_{i}") for i in range(1,6)],[(str(i), f"q_motiv_{i}") for i in range(6,11)]]), parse_mode="Markdown")
            return

        if step == "q_supp_other_input":
            # Сюда данные приходят через handle_text (см. ниже ветку q_supp_other_input в handle_text)
            pass

        if step == "q_motivation":
            state["q_data"]["motivation"] = int(q.data.replace("q_motiv_", ""))
            # автoсохранение прогресса
            try:
                await self._db_run(self.db.save_questionnaire, athlete["id"], state.get("q_data", {}))
            except Exception as e:
                logger.error(f"Q autosave: {e}")
            state["step"] = "q_stress"
            await q.edit_message_text("📋 *Блок 5: Психология*\n\nВнешние факторы мешают?", reply_markup=self.kb([[("Да, сильно", "q_stress_Да, сильно"), ("Периодически", "q_stress_Периодически")], [("Нет", "q_stress_Нет"), ("Не влияют", "q_stress_Не влияют")]]), parse_mode="Markdown")
            return

        if step == "q_stress":
            state["q_data"]["stress"] = q.data.replace("q_stress_", "")
            # автoсохранение прогресса
            try:
                await self._db_run(self.db.save_questionnaire, athlete["id"], state.get("q_data", {}))
            except Exception as e:
                logger.error(f"Q autosave: {e}")
            state["step"] = "q_match_state"
            await q.edit_message_text("📋 *Блок 5: Психология*\n\nСостояние перед матчами?", reply_markup=self.kb([[("Спокойная уверенность", "q_match_Спокойная уверенность")], [("Лёгкое волнение", "q_match_Лёгкое волнение")], [("Сильная тревога", "q_match_Сильная тревога")], [("По-разному", "q_match_По-разному")]]), parse_mode="Markdown")
            return

        if step == "q_match_state":
            state["q_data"]["match_state"] = q.data.replace("q_match_", "")
            # автoсохранение прогресса
            try:
                await self._db_run(self.db.save_questionnaire, athlete["id"], state.get("q_data", {}))
            except Exception as e:
                logger.error(f"Q autosave: {e}")
            state["step"] = "q_reinjury"
            await q.edit_message_text("📋 *Блок 5: Психология*\n\nБоишься повторной травмы?", reply_markup=self.kb([[("Да, постоянно", "q_reinj_Да, постоянно"), ("Иногда", "q_reinj_Иногда")], [("Нет", "q_reinj_Нет"), ("Не было травм", "q_reinj_Не было травм")]]), parse_mode="Markdown")
            return

        if step == "q_reinjury":
            state["q_data"]["reinjury_fear"] = q.data.replace("q_reinj_", "")
            # автoсохранение прогресса
            try:
                await self._db_run(self.db.save_questionnaire, athlete["id"], state.get("q_data", {}))
            except Exception as e:
                logger.error(f"Q autosave: {e}")
            state["step"] = "q_goal"
            await q.edit_message_text("📋 *Блок 6: Цели*\n\nКакую главную цель ставишь? (напиши текстом)", parse_mode="Markdown")
            return

        return


    async def _q_show_zones(self, update, ctx):
        q = update.callback_query
        await q.edit_message_text("📋 *Блок 2: Травмы*\n\nЗоны, которые беспокоили за 3 месяца?", reply_markup=self.kb([
            [("Голеностопы", "q_zones_Голеностопы"), ("Колени", "q_zones_Колени")],
            [("Бёдра/пах", "q_zones_Бёдра"), ("Поясница", "q_zones_Поясница")],
            [("Кисти/пальцы", "q_zones_Кисти"), ("Плечи", "q_zones_Плечи")],
            [("Шея", "q_zones_Шея"), ("Ничего", "q_zones_Ничего")],
        ]), parse_mode="Markdown")


    async def _q_continue_block2(self, update, ctx):
        q = update.callback_query
        state = self.get_state(q.from_user.id)
        state["step"] = "q_surgery"
        await q.edit_message_text("📋 *Блок 2: Травмы*\n\nБыли операции, из-за спорта?", reply_markup=self.kb([[("Да", "q_surgery_Да"), ("Нет", "q_surgery_Нет")]]), parse_mode="Markdown")


    async def _q_continue_block2_meds(self, update, ctx):
        q = update.callback_query
        state = self.get_state(q.from_user.id)
        state["q_data"]["surgery_detail"] = ""
        state["q_data"]["surgery_date"] = ""
        state["step"] = "q_meds"
        await q.edit_message_text("📋 *Блок 2: Травмы*\n\nПринимаешь лекарства, БАДы или физиотерапию?", reply_markup=self.kb([[("Да", "q_meds_Да"), ("Нет", "q_meds_Нет")]]), parse_mode="Markdown")


    async def _q_continue_meds(self, update, ctx, state):
        state["step"] = "q_meds"
        await update.message.reply_text("📋 *Блок 2: Травмы*\n\nПринимаешь лекарства, БАДы или физиотерапию?", reply_markup=self.kb([[("Да", "q_meds_Да"), ("Нет", "q_meds_Нет")]]), parse_mode="Markdown")


    async def _q_save_then_block3(self, update, ctx):
        q = update.callback_query if hasattr(update, 'callback_query') and update.callback_query else None
        if q:
            user_id = q.from_user.id
        else:
            user_id = update.effective_user.id
        state = self.get_state(user_id)
        state["step"] = "q_train_count"
        text_q = "📋 *Блок 3: Тренировочный режим*\n\nСколько раз в неделю тренируешься?"
        kb = self.kb([[(str(i), f"q_tcount_{i}") for i in range(0,11)]])
        if q:
            await q.edit_message_text(text_q, reply_markup=kb, parse_mode="Markdown")
        else:
            await update.message.reply_text(text_q, reply_markup=kb, parse_mode="Markdown")


    async def _q_finish_and_save(self, update, ctx):
        """Сохранить анкету в БД и показать финал"""
        q = update.callback_query
        state = self.get_state(q.from_user.id)
        data = state.get("q_data", {})
        athlete = self.db.get_athlete_by_telegram_id(q.from_user.id)
        if athlete:
            await self._db_run(self.db.save_questionnaire, athlete["id"], data)
            self.db.complete_questionnaire(athlete["id"])
        self.clear_state(q.from_user.id)
        await self._finish_questionnaire_offer(update, ctx)


    async def _finish_questionnaire_offer(self, update, ctx):
        """После анкеты: предложить подписаться на канал «Вне лимита» и перейти к опросу."""
        q = update.callback_query if hasattr(update, 'callback_query') and update.callback_query else None
        text = (
            "✅ *Анкета заполнена!*\n\n"
            "Благодарю за прохождение анкеты!\n\n"
            "В нашем Telegram-канале «Вне лимита» — много актуальной информации о восстановлении, "
            "питании и профилактике травм. Рекомендую подписаться и познакомиться с материалами:\n\n"
            "📢 Нажми кнопку «Подписаться на канал», вернись в чат — и продолжишь с опросом."
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Подписаться на канал", url="https://t.me/vnelimita")],
            [InlineKeyboardButton("✅ Перейти к опросу", callback_data="do_survey")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")],
        ])
        if q:
            await q.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
        else:
            await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)


    async def show_questionnaire_list(self, update, ctx, page=0):
        q = update.callback_query
        await q.answer()
        if not self._is_full_access(q.from_user.id): return
        athletes = self.db.get_all_athletes()
        btns = []
        for a in athletes:
            has = self.db.has_questionnaire(a["id"])
            mark = "✅" if has else "⬜"
            btns.append([(f"{mark} {a['id']}. {a['full_name']}", f"qview_{a['id']}")])
        btns.append([("🔙", "admin_manage")])
        await q.edit_message_text("📋 Анкеты", reply_markup=self.kb(btns))


    async def show_questionnaire_detail(self, update, ctx):
        q = update.callback_query
        await q.answer()
        if not self._is_full_access(q.from_user.id): return
        a_id = int(q.data.replace("qview_", ""))
        athlete = self.db.get_athlete_by_id(a_id)
        if not athlete:
            await q.edit_message_text("Не найден")
            return
        qd = self.db.get_questionnaire(a_id)
        if not qd:
            await q.edit_message_text(f"Анкета для {athlete['full_name']} ещё не заполнена.", reply_markup=self.kb([[(f"🔙", "questionnaire_list")]]))
            return
        fields = [
            ("Возраст", qd.get("age")),
            ("Пол", qd.get("gender")),
            ("Телефон", qd.get("phone")),
            ("Дата рождения", qd.get("birth_date")),
            ("Позиция", qd.get("position")),
            ("Уровень", qd.get("level")),
            ("Стаж", qd.get("experience")),
            ("Рост/Вес", f"{qd.get('height','?')}см/{qd.get('weight','?')}кг"),
            ("Травмы 12м", qd.get("trauma_12m")),
            ("Боль сейчас", qd.get("pain_now")),
            ("Хроническое", qd.get("chronic")),
            ("Операции", qd.get("surgery")),
            ("Операции: что", qd.get("surgery_detail")),
            ("Операции: дата", qd.get("surgery_date")),
            ("Лекарства", qd.get("meds")),
            ("Аллергии", qd.get("allergies")),
            ("Аллергии: на что", qd.get("allergies_detail")),
            ("Трен/нед", qd.get("train_count")),
            ("Мотивация", qd.get("motivation")),
            ("Цель", qd.get("goal","—")),
        ]
        text = f"📋 *Анкета: {athlete['full_name']}*\n━\n\n"
        for label, val in fields:
            if val:
                text += f"*{label}:* {val}\n"

        # ---- Карточка спортсмена: последний опрос + тренд ----
        imported = "from openpyxl import Workbook"  # noqa
        last_wellness = self.db.get_last_wellness(a_id, 7)
        if last_wellness:
            text += f"\n📊 *Последний опрос ({last_wellness[0]['survey_date']}):*\n"
            w0 = last_wellness[0]
            text += f"😴 Сон: {w0.get('sleep_score','—')} | 😩 Утомл: {w0.get('fatigue_score','—')}\n"
            text += f"🤕 Боль: {w0.get('muscle_soreness','—')} | ❤️ Пульс: {w0.get('resting_hr','—')}\n"
            if w0.get('sRPE_score') is not None:
                text += f"💪 Тренировка: {'Да' if w0.get('had_training') else 'Нет'} | sRPE: {w0.get('sRPE_score')}\n"
            if w0.get('cycle_phase'):
                text += f"🔄 Фаза цикла: {w0.get('cycle_phase')}\n"
            if w0.get('complaints'):
                text += f"💬 Жалобы: {w0.get('complaints')}\n"

            # Тренд Hooper за неделю (спарклайн, 3 шкалы: сон+утомление+боль)
            dates = []
            vals = []
            for w in reversed(last_wellness):
                h = sum(filter(None, [w.get('sleep_score'), w.get('fatigue_score'),
                                       w.get('muscle_soreness')]))
                dates.append(w['survey_date'][5:])
                vals.append(h)
            if len(vals) >= 2:
                text += f"\n📈 *Hooper за неделю:*\n"
                for i, h in enumerate(vals):
                    if h >= 17: e = "🟢"
                    elif h >= 12: e = "🟡"
                    else: e = "🔴"
                    text += f"{e}"
                text += "\n"
                text += " ".join(d[-2:] for d in dates) + "\n"
        else:
            text += "\n*Нет опросов за последние 7 дней.*\n"

        buttons = [[(f"🔙", "questionnaire_list"), (f"📊 Отчёт", "admin_report")]]
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=self.kb(buttons))


