"""
Миксин: AdminMixin
Автоматически извлечён из bot.py
"""

import logging
from datetime import date
from typing import TYPE_CHECKING
from contact_ui import contact_row
from config import AGE_GROUPS, ADMIN_IDS, REMINDER_HOUR_DEFAULT, REMINDER_MINUTE_DEFAULT
from database import TEAMS

ADMIN_TELEGRAM_IDS = ADMIN_IDS
REMINDER_HOUR = REMINDER_HOUR_DEFAULT
REMINDER_MINUTE = REMINDER_MINUTE_DEFAULT
REMINDER_TZ = "Asia/Yekaterinburg"

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from bot import SportHealthBot


class AdminMixin:
    """Миксин для admin."""

    async def show_admin_manage(self, update, ctx):
        q = update.callback_query
        await q.answer()
        if not self._is_full_access(q.from_user.id):
            return

        text = (
            f"⚙️ *Управление ботом*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Выбери действие:"
        )
        buttons = [
            [(f"👥 Список спортсменов", "athlete_list")],
            [(f"➕ Добавить спортсмена", "admin_add_athlete")],
            [(f"🔒 Блокировка", "ban_menu")],
            [(f"📋 Анкеты", "questionnaire_list")],
            [(f"📅 Отчёт за сегодня", "daily_report")],
            [(f"🚨 Жалобы", "admin_complaints")],
            [(f"🗑 Удалить спортсмена", "delete_athlete")],
            [(f"⏰ Напоминания", "reminder_settings")],
            [(f"🏅 Тренеры", "coach_menu")],
            [(f"🩺 Врачи", "doctor_menu")],
            [(f"📄 PDF-отчёт", "pdf_report_menu")],
            [(f"📊 Экспорт CSV", "export_csv")],
            [(f"🔙 Назад", "main_menu")]
        ]
        await q.edit_message_text(text, reply_markup=self.kb(buttons), parse_mode="Markdown")

    # ==================== ДОБАВЛЕНИЕ СПОРТСМЕНА АДМИНОМ ====================


    async def admin_add_athlete_start(self, update, ctx):
        """Начать процесс добавления спортсмена — запрос Telegram ID."""
        q = update.callback_query
        await q.answer()
        if not self._is_full_access(q.from_user.id):
            return
        state = self.get_state(q.from_user.id)
        state["step"] = "admin_add_athlete_tg_id"
        state["data"] = {}
        await q.edit_message_text(
            "➕ *Добавить спортсмена*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Пришли *Telegram ID* спортсмена (число):",
            reply_markup=self.kb([[(f"🔙 Управление", "admin_manage")]]),
            parse_mode="Markdown"
        )


    async def admin_add_athlete_team(self, update, ctx):
        """Выбор команды для нового спортсмена."""
        q = update.callback_query
        await q.answer()
        team = q.data.replace("admin_add_team_", "")
        state = self.get_state(q.from_user.id)
        state["data"]["add_team"] = team
        buttons = [[(desc, f"admin_add_age_{key}")] for key, desc in AGE_GROUPS.items()]
        buttons.append([("🔙 Назад", "admin_add_athlete")])
        await q.edit_message_text(
            f"➕ *Команда:* {team}\n\nВыбери *возрастную группу:*",
            reply_markup=self.kb(buttons), parse_mode="Markdown"
        )


    async def admin_add_athlete_age(self, update, ctx):
        """Выбор возрастной группы — запрос ФИО."""
        q = update.callback_query
        await q.answer()
        age = q.data.replace("admin_add_age_", "")
        state = self.get_state(q.from_user.id)
        state["data"]["add_age_group"] = age
        state["step"] = "admin_add_athlete_name"
        await q.edit_message_text(
            f"➕ *Команда:* {state['data']['add_team']}\n"
            f"*Группа:* {AGE_GROUPS.get(age, age)}\n\n"
            f"Введи *Фамилию Имя* спортсмена:",
            parse_mode="Markdown"
        )


    async def admin_add_athlete_save(self, update, user_id, state):
        """Сохранить нового спортсмена в БД."""
        data = state.get("data", {})
        tg_id = data.get("add_tg_id")
        team = data.get("add_team")
        age_group = data.get("add_age_group")
        name = data.get("add_name")

        if not all([tg_id, team, age_group, name]):
            await update.message.reply_text("❌ Ошибка: не все данные заполнены.",
                reply_markup=self.kb([[(f"🔙 Управление", "admin_manage")]]))
            return

        # Проверяем, нет ли уже такого telegram_id
        existing = self.db.get_athlete_by_telegram_id(int(tg_id))
        if existing:
            await update.message.reply_text(
                f"⚠️ Спортсмен с ID {tg_id} уже зарегистрирован:\n"
                f"👤 {existing['full_name']} | {existing['team']}",
                reply_markup=self.kb([[(f"🔙 Управление", "admin_manage")]])
            )
            return

        ok = self.db.register_athlete(
            telegram_id=int(tg_id), username=None,
            full_name=name, age_group=age_group, team=team
        )
        if ok:
            await update.message.reply_text(
                f"✅ *Спортсмен добавлен!*\n\n"
                f"👤 {name}\n"
                f"🏀 Команда: {team}\n"
                f"📋 Группа: {AGE_GROUPS.get(age_group, age_group)}\n"
                f"🆔 Telegram ID: {tg_id}\n\n"
                f"Попроси спортсмена написать /start в боте.",
                reply_markup=self.kb([[(f"🔙 Управление", "admin_manage")]]),
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                "❌ Ошибка сохранения. Возможно, этот ID уже есть в базе.",
                reply_markup=self.kb([[(f"🔙 Управление", "admin_manage")]])
            )
        self.clear_state(user_id)


    async def admin_complaints_menu(self, update, ctx):
        """Панель жалоб: список спортсменов с жалобами за период."""
        q = update.callback_query
        await q.answer()
        if not self._is_full_access(q.from_user.id):
            return
        buttons = [
            [("📅 Сегодня", "admin_complaints_page_1")],
            [("📆 Неделя", "admin_complaints_page_7")],
            [("📊 Месяц", "admin_complaints_page_30")],
            [("🔙 Управление", "admin_manage")],
        ]
        await q.edit_message_text(
            "🚨 *Жалобы спортсменов*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nВыбери период:",
            reply_markup=self.kb(buttons), parse_mode="Markdown"
        )


    async def admin_complaints_page(self, update, ctx):
        """Показать жалобы за N дней."""
        q = update.callback_query
        await q.answer()
        if not self._is_full_access(q.from_user.id):
            return
        days = int(q.data.replace("admin_complaints_page_", ""))
        complaints = self.db.get_athletes_with_complaints(days)
        if not complaints:
            period = {1: "сегодня", 7: "неделю", 30: "месяц"}.get(days, f"{days} дн.")
            await q.edit_message_text(
                f"✅ Жалоб за {period} нет!",
                reply_markup=self.kb([[(f"🔙 Назад", "admin_complaints"), (f"🏠 Главное меню", "main_menu")]])
            )
            return
        text = f"🚨 *Жалобы за {days} дн. ({len(complaints)}):*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        btns = []
        for c in complaints[:15]:  # лимит чтобы не переполнить
            date_str = c.get("survey_date", "?")
            text += f"👤 *{c['full_name']}* ({c.get('team', '?')})\n"
            text += f"   📅 {date_str} | 💬 {c.get('complaints', '?')}\n\n"
            _crow = contact_row(c)
            if _crow:
                btns.append(_crow)
        if len(complaints) > 15:
            text += f"… и ещё {len(complaints) - 15}\n"
        btns.append([("🔙 Назад", "admin_complaints"), (f"🏠 Главное меню", "main_menu")])
        await q.edit_message_text(text, reply_markup=self.kb(btns), parse_mode="Markdown")


    async def show_admin_report(self, update, ctx):
        q = update.callback_query
        await q.answer()
        if not self._is_full_access(q.from_user.id):
            await q.edit_message_text("❌ Нет доступа.")
            return

        data = self.db.get_athletes_week_stats()
        teams = {}
        for a in data:
            teams.setdefault(a.get("team", "?"), []).append(a)

        text = f"📋 *ОТЧЕТ ДЛЯ ВРАЧА*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n📅 {date.today().strftime('%d.%m.%Y')}\n\n"

        total_ok, total_warn, total_crit = 0, 0, 0

        for tn in sorted(teams.keys()):
            ta = teams[tn]
            text += f"*🏀 {tn}* ({len(ta)}):\n"
            for a in ta:
                name = a["full_name"]
                streak = a.get("survey_streak", 0)
                days = a.get("days_count", 0)
                if days > 0:
                    sleep = a.get("avg_sleep")
                    stress = a.get("avg_stress")
                    fatigue = a.get("avg_fatigue")
                    hr = a.get("avg_hr")

                    issues = []
                    # Шкалы 1-7, 7=хорошо (сон/утомление: низкий балл = плохо; стресс: НИЗКИЙ балл = «бесит всё» = плохо)
                    if sleep and sleep < 3: issues.append("сон🔴"); total_crit += 1
                    elif sleep and sleep < 5: issues.append("сон🟡"); total_warn += 1
                    if stress and stress < 3: issues.append("стресс🔴"); total_crit += 1
                    elif stress and stress < 5: issues.append("стресс🟡"); total_warn += 1
                    if fatigue and fatigue < 3: issues.append("утом.🔴"); total_crit += 1
                    elif fatigue and fatigue < 5: issues.append("утом.🟡"); total_warn += 1
                    if hr and hr > 70: issues.append("пульс🔴"); total_crit += 1
                    elif hr and hr > 60: issues.append("пульс🟡"); total_warn += 1

                    status = "🟢" if not issues else ("🔴" if any("🔴" in i for i in issues) else "🟡")
                    if not issues: total_ok += 1

                    ss = f"{sleep:.1f}" if sleep else "—"
                    sts = f"{stress:.1f}" if stress else "—"
                    fs = f"{fatigue:.1f}" if fatigue else "—"
                    hs = f"{hr:.0f}" if hr else "—"

                    text += f"  {status} *{name}* ({a['age_group']})\n"
                    text += f"     😴{ss} 😰{sts} 😩{fs} ❤️{hs} | 🔥{streak}д\n"
                    if issues:
                        text += f"     ⚠️ {', '.join(issues)}\n"
                else:
                    text += f"  ⚪ *{name}* — нет данных\n"
            text += "\n"

        text += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        text += f"👥 Всего: {len(data)} | 🟢 {total_ok} | 🟡 {total_warn} | 🔴 {total_crit}\n"

        await q.edit_message_text(text, reply_markup=self.kb([
            [(f"📊 Экспорт CSV", "export_csv")],
            [(f"🔙 Назад", "main_menu")]
        ]), parse_mode="Markdown")


    async def daily_report(self, update, ctx):
        q = update.callback_query
        await q.answer()
        if not self._is_full_access(q.from_user.id):
            return

        athletes = self.db.get_all_athletes()
        today = date.today()

        passed = []
        not_passed = []
        for a in athletes:
            if self.db.has_survey_today(a["id"]):
                passed.append(a)
            else:
                not_passed.append(a)

        from cycle_ui import athlete_cycle_line

        def _report_text(mode):
            """mode: 'all' — цикл каждой; 'abnormal' — только задержка/🚫;
            'off' — без цикла (иначе не влезет в лимит Telegram 4096)."""
            text = (
                f"📅 *Отчёт за {today.strftime('%d.%m.%Y')}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"✅ *Прошли опрос ({len(passed)}):*\n"
            )
            for a in passed:
                text += f"  • {a['full_name']} ({a['team']})\n"
                cyc = athlete_cycle_line(a, today) if mode != "off" else None
                if cyc and (mode == "all" or "⚠️" in cyc or "🚫" in cyc):
                    text += f"      🌸 {cyc}\n"
            if not_passed:
                text += f"\n❌ *Не прошли ({len(not_passed)}):*\n"
                for a in not_passed:
                    text += f"  • {a['full_name']} ({a['team']})\n"
                    cyc = athlete_cycle_line(a, today) if mode != "off" else None
                    if cyc and (mode == "all" or "⚠️" in cyc or "🚫" in cyc):
                        text += f"      🌸 {cyc}\n"
            text += f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            text += f"Итого: {len(passed)}/{len(athletes)} прошли"
            return text

        text = _report_text("all")
        if len(text) > 3900:
            text = _report_text("abnormal")
        if len(text) > 3900:
            text = _report_text("off")

        buttons = [[(f"📊 Экспорт CSV", "export_csv")], [(f"🔙 Назад", "main_menu")]]
        await q.edit_message_text(text, reply_markup=self.kb(buttons), parse_mode="Markdown")

    # ==================== ОТЧЕТ ДЛЯ ВРАЧА ====================


    async def show_doctor_admin(self, update, ctx):
        q = update.callback_query
        await q.answer()
        uid = q.from_user.id
        if not self._is_full_access(uid):
            return
        docs = self.db.get_all_doctors()
        admins = sorted(ADMIN_TELEGRAM_IDS)
        text = "🩺 *Врачи и админы*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        text += "*(полный доступ, как у админа)*\n\n"
        text += "👑 *Супер-админы (из .env):*\n"
        for a in admins:
            text += f"  • id {a}\n"
        text += "\n🩺 *Врачи:*\n"
        if not docs:
            text += "  Врачей пока нет.\n"
        for d in docs:
            text += f"  • {' / '.join(d['names'])} ({d['telegram_id']})\n"
        btns = [[(f"➕ Добавить врача", "doctor_add")]]
        for d in docs:
            btns.append([(f"🗑 Удалить {d['telegram_id']}", f"doctor_del_{d['telegram_id']}")])
        btns.append([(f"🔙 Управление", "admin_manage")])
        await q.edit_message_text(text, reply_markup=self.kb(btns))


    async def doctor_add_ask(self, update, ctx):
        q = update.callback_query
        await q.answer()
        uid = q.from_user.id
        if not self._is_full_access(uid):
            return
        state = self.get_state(uid)
        state["step"] = "admin_doctor_add"
        await q.edit_message_text(
            "🩺 *Добавить врача*\n\nПришли *Telegram ID* пользователя, которому нужен полный доступ (как у админа).\n\n"
            "⭐ Совет: супер-админ получает id в чате бота через 'мой ID' или у техподдержки.",
            reply_markup=self.kb([[(f"🔙 Врачи", "doctor_menu")]])
        )


    async def doctor_delete(self, update, ctx):
        q = update.callback_query
        await q.answer()
        if not self._is_full_access(q.from_user.id):
            return
        tg_id = int(q.data.replace("doctor_del_", ""))
        self.db.remove_doctor(tg_id)
        await q.edit_message_text(f"🗑 Врач (id {tg_id}) удалён.",
                                  reply_markup=self.kb([[(f"🔙 Врачи", "doctor_menu")]]))
        await self.show_doctor_admin(update, ctx)

    # ==================== ДОСТИЖЕНИЯ ====================


    async def delete_athlete_menu(self, update, ctx):
        q = update.callback_query
        await q.answer()
        if not self._is_full_access(q.from_user.id):
            return

        athletes = self.db.get_all_athletes()
        if not athletes:
            await q.edit_message_text("📭 Нет спортсменов.", reply_markup=self.kb([[(f"🔙 Назад", "main_menu")]]))
            return

        buttons = []
        last_team = None
        for a in athletes:
            team = a.get("team") or "Без команды"
            if team != last_team:
                last_team = team
                buttons.append([(f"🏀 {team}", "hdr")])
            buttons.append([(f"❌ {a['full_name']}", f"del_{a['id']}")])
        buttons.append([(f"🔙 Назад", "main_menu")])

        await q.edit_message_text(
            "🗑 *Удаление спортсмена*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nВыбери кого удалить:",
            reply_markup=self.kb(buttons), parse_mode="Markdown"
        )


    async def delete_athlete_confirm(self, update, ctx):
        q = update.callback_query
        await q.answer()
        if not self._is_full_access(q.from_user.id):
            return

        athlete_id = int(q.data.replace("del_", ""))
        athlete = self.db.get_athlete_by_id(athlete_id) if hasattr(self.db, 'get_athlete_by_id') else None

        # Fallback
        if not athlete:
            row = self.db.conn.execute("SELECT * FROM athletes WHERE id = ?", (athlete_id,)).fetchone()
            athlete = dict(row) if row else None

        if not athlete:
            await q.edit_message_text("❌ Спортсмен не найден.", reply_markup=self.kb([[(f"🔙 Назад", "main_menu")]]))
            return

        await q.edit_message_text(
            f"⚠️ *Удалить {athlete['full_name']}?*\n\nВсе данные будут удалены.",
            reply_markup=self.kb([
                [("✅ Да, удалить", f"delconfirm_{athlete_id}")],
                [("❌ Нет", "admin_manage")]
            ]), parse_mode="Markdown"
        )


    async def delete_athlete_final(self, update, ctx):
        q = update.callback_query
        await q.answer()
        if not self._is_full_access(q.from_user.id):
            return

        athlete_id = int(q.data.replace("delconfirm_", ""))
        # Удаление ВСЕХ данных спортсмена (включая PII)
        tables_with_athlete = [
            "daily_wellness", "alerts", "questionnaires", "watch_data",
            "body_composition", "scale_profiles", "consultations", "athlete_goals"
        ]
        for table in tables_with_athlete:
            try:
                self.db.conn.execute(f"DELETE FROM {table} WHERE athlete_id = ?", (athlete_id,))
            except Exception:
                pass  # Таблица может не существовать
        self.db.conn.execute("DELETE FROM athletes WHERE id = ?", (athlete_id,))
        self.db.conn.commit()

        await q.edit_message_text(
            "✅ Спортсмен и ВСЕ его данные удалены.",
            reply_markup=self.kb([[(f"🔙 Назад", "main_menu")]]), parse_mode="Markdown"
        )

    # ==================== НАПОМИНАНИЯ ====================


    async def athlete_list(self, update, ctx, page=0):
        q = update.callback_query
        await q.answer()
        uid = q.from_user.id
        if not self._is_admin_or_coach(uid): return
        athletes = self._scoped_athletes(uid)
        banned = set(x["id"] for x in self.db.get_banned_athletes())
        pp = 15
        total = len(athletes)
        s = page * pp
        e = min(s + pp, total)
        pa = athletes[s:e]
        text = f"👥 Список ({total}) Стр.{page+1}/{(total-1)//pp+1}\n\n"
        prev_team = athletes[s - 1].get("team") if s > 0 else None
        for a in pa:
            t = a.get("team") or "Без команды"
            if t != prev_team:
                text += f"\n🏀 *{t}*\n"
                prev_team = t
            m = "🔒 " if a["id"] in banned else ""
            u = f" @{a['username']}" if a.get("username") else ""
            text += f"{m}{a['id']}. {a['full_name']}{u}\n"
        btns = []
        for a in pa:
            btns.append([(f"🏥 Рекомендации: {a['full_name']}", f"recs_{a['id']}")])
            btns.append([(f"⚖️ Состав тела: {a['full_name']}", f"bc_view_{a['id']}")])
        nav = []
        if page > 0: nav.append(("⬅️", f"athlete_page_{page-1}"))
        if e < total: nav.append(("➡️", f"athlete_page_{page+1}"))
        if nav: btns.append(nav)
        btns.append([("🔙", "admin_manage")])
        await q.edit_message_text(text, reply_markup=self.kb(btns))


    async def show_ban_menu(self, update, ctx, page=0):
        q = update.callback_query
        await q.answer()
        if not self._is_full_access(q.from_user.id): return
        athletes = self.db.get_all_athletes()
        banned = set(x["id"] for x in self.db.get_banned_athletes())
        pp = 10
        total = len(athletes)
        s = page * pp
        e = min(s + pp, total)
        pa = athletes[s:e]
        btns = []
        for a in pa:
            if a["id"] in banned:
                btns.append([(f"✅ {a['id']}. {a['full_name']} Разблок.", f"unban_{a['id']}")])
            else:
                btns.append([(f"🔴 {a['id']}. {a['full_name']} Заблок.", f"ban_{a['id']}")])
        nav = []
        if page > 0: nav.append(("⬅️", f"ban_page_{page-1}"))
        if e < total: nav.append(("➡️", f"ban_page_{page+1}"))
        if nav: btns.append(nav)
        btns.append([("🔙", "admin_manage")])
        await q.edit_message_text(f"🔒 Блокировка Стр.{page+1}/{(total-1)//pp+1}\n\n", reply_markup=self.kb(btns))

    # ==================== ЗАПУСК ====================


    async def reminder_settings(self, update, ctx):
        q = update.callback_query
        await q.answer()
        if not self._is_full_access(q.from_user.id):
            return

        text = (
            f"⏰ *Настройка напоминаний*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Текущее время: *{REMINDER_HOUR:02d}:{REMINDER_MINUTE:02d}*\n\n"
            f"Выбери время или напиши своё (например: 14:30):"
        )

        buttons = [
            [("🌅 08:00", "set_reminder_8"), ("🌞 12:00", "set_reminder_12")],
            [("🌇 18:00", "set_reminder_18"), ("🌙 20:00", "set_reminder_20")],
            [("✏️ Своё время", "set_reminder_custom")],
            [("📨 Разослать напоминание сейчас", "send_reminder_now")],
            [("📋 Предложить заполнить анкету", "send_q_reminder")],
            [("🔕 Выключить", "set_reminder_off")],
            [("🔙 Назад", "main_menu")]
        ]

        await q.edit_message_text(text, reply_markup=self.kb(buttons), parse_mode="Markdown")


    def _schedule_reminder_job(self, hour, minute=0):
        """Пере)планировать ежедневную рассылку напоминаний. Старые задания снимаем во избежанie дублей."""
        if not self.job_queue or hour is None:
            return
        import pytz
        from datetime import datetime as dt
        local_tz = pytz.timezone(REMINDER_TZ)
        now_local = dt.now(local_tz)
        target_local = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        target_utc = target_local.astimezone(pytz.UTC)
        for old in self.job_queue.get_jobs_by_name("daily_reminder"):
            old.schedule_removal()
        self.job_queue.run_daily(
            self._send_daily_reminder,
            time=target_utc.time(),
            days=tuple(range(7)),
            name="daily_reminder"
        )


    def _unschedule_reminder_job(self):
        """Полностью убрать ежедневную рассылку напоминаний."""
        if not self.job_queue:
            return
        for old in self.job_queue.get_jobs_by_name("daily_reminder"):
            old.schedule_removal()


    async def set_reminder_time(self, update, ctx):
        global REMINDER_HOUR, REMINDER_MINUTE
        q = update.callback_query
        await q.answer()
        if not self._is_full_access(q.from_user.id):
            return

        data = q.data.replace("set_reminder_", "")
        if data == "off":
            REMINDER_HOUR = None
            self._unschedule_reminder_job()
            self.db.set_setting("reminder_hour", "")
            await q.edit_message_text("🔕 Напоминания выключены.", reply_markup=self.kb([[(f"🔙 Назад", "main_menu")]]))
            return

        if data == "custom":
            state = self.get_state(q.from_user.id)
            state["step"] = "set_reminder_custom"
            await q.edit_message_text("✏️ *Напиши время в формате ЧЧ:ММ*\n\nНапример: 09:30 или 14:15", parse_mode="Markdown", reply_markup=self.kb([[(f"🔙 Назад", "main_menu")]]))
            return

        if data == "send_now":
            await q.edit_message_text("📨 *Отправляю напоминания спортсменам...*", parse_mode="Markdown")
            try:
                await self._send_daily_reminder(ctx)
                await q.edit_message_text("✅ *Напоминания отправлены!*", parse_mode="Markdown", reply_markup=self.kb([[(f"🔙 Назад", "main_menu")]]))
            except Exception as e:
                logger.error(f"Send now error: {e}")
                await q.edit_message_text(f"❌ Ошибка", reply_markup=self.kb([[(f"🔙 Назад", "main_menu")]]))
            return

        hour = int(data)
        REMINDER_HOUR = hour
        REMINDER_MINUTE = 0
        REMINDER_TZ = "Asia/Yekaterinburg"
        self.db.set_setting("reminder_hour", str(hour))
        self.db.set_setting("reminder_tz", REMINDER_TZ)
        self._schedule_reminder_job(hour, 0)

        await q.edit_message_text(
            f"⏰ Напоминание настроено на *{hour:02d}:00* по Челябинску\n\n"
            f"Спортсменам будет приходить уведомление в это время ежедневно.",
            reply_markup=self.kb([[(f"🔙 Назад", "main_menu")]]), parse_mode="Markdown"
        )


    async def send_reminder_now(self, update, ctx):
        q = update.callback_query
        await q.answer()
        if not self._is_full_access(q.from_user.id):
            return
        await q.edit_message_text("📨 *Отправляю напоминания спортсменам...*", parse_mode="Markdown")
        try:
            await self._send_daily_reminder(ctx)
            await q.edit_message_text("✅ *Напоминания отправлены!*", parse_mode="Markdown", reply_markup=self.kb([[(f"🔙 Назад", "main_menu")]]))
        except Exception as e:
            logger.error(f"Send now error: {e}")
            await q.edit_message_text(f"❌ Ошибка", reply_markup=self.kb([[(f"🔙 Назад", "main_menu")]]))


    async def send_questionnaire_reminder(self, update, ctx):
        """Рассылка тем спортсменам, у кого не заполнена анкета здоровья."""
        q = update.callback_query
        await q.answer()
        if not self._is_full_access(q.from_user.id):
            return
        await q.edit_message_text("📋 *Отправляю предложение заполнить анкету...*", parse_mode="Markdown")
        try:
            bot = ctx.bot
            athletes = self.db.get_all_athletes()
            target = []
            for a in athletes:
                if self.db.is_coach(a["telegram_id"]):
                    continue
                if self.db.has_questionnaire(a["id"]):
                    continue
                target.append(a)
            sent = 0
            failed = 0
            for a in target:
                try:
                    await bot.send_message(
                        chat_id=a["telegram_id"],
                        text=(
                            "📋 *ЧБК — Анкета здоровья*\n"
                            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                            f"👋 Привет, {self._first_name(a)}!\n\n"
                            "Мы видим, что ты ещё не заполнил(а) *анкету спортсмена*.\n"
                            "Она нужна врачу: рост, вес, амплуа, травмы, противопоказания.\n\n"
                            "⏱️ Это займёт пару минут.\n\n"
                            "👉 Нажми /start → выбери *«📋 Заполнить анкету»*"
                        ),
                        parse_mode="Markdown"
                    )
                    sent += 1
                except Exception as e:
                    failed += 1
                    logger.error(f"Q-reminder error for {a['full_name']} (tg={a['telegram_id']}): {e}")
            logger.info(f"Questionnaire remind sent={sent} failed={failed} of {len(target)}")
            await q.edit_message_text(
                f"✅ *Предложение отправлено:* {sent}\n"
                f"⏭️ Пропущено (уже заполнили): {len(athletes) - len(target)}\n"
                f"❌ Не доставилось: {failed}\n\n"
                f"Без анкеты осталось: {len(target)}",
                parse_mode="Markdown", reply_markup=self.kb([[(f"🔙 Назад", "main_menu")]])
            )
        except Exception as e:
            logger.error(f"Send questionnaire reminder error: {e}")
            await q.edit_message_text(f"❌ Ошибка", reply_markup=self.kb([[(f"🔙 Назад", "main_menu")]]))

    # ==================== ОТЧЕТ ПО ДНЯМ ====================


    async def _send_weekly_coach_summary(self, context):
        """Понедельничная сводка по командам — каждому тренеру."""
        from bot import TESTING
        if TESTING:
            logger.info("TESTING MODE — сводки тренерам не отправляются")
            return
        bot = context.bot
        for coach in self.db.get_all_coaches():
            text = self._coach_summary_text(coach["teams"])
            if not text:
                continue
            try:
                await bot.send_message(
                    chat_id=coach["telegram_id"], text=text, parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Coach summary error for {coach['telegram_id']}: {e}")

    # ==================== УПРАВЛЕНИЕ ТРЕНЕРАМИ (ТОЛЬКО АДМИН) ====================

    async def show_coach_admin(self, update, ctx):
        q = update.callback_query
        await q.answer()
        if not self._is_full_access(q.from_user.id):
            return
        coaches = self.db.get_all_coaches()
        text = "🏅 *Тренеры и команды*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        if not coaches:
            text += "Тренеров пока нет.\n"
        for c in coaches:
            text += f"👤 {' / '.join(c['names'])} ({c['telegram_id']})\n"
            text += f"   🏀 {', '.join(c['teams']) or '— без команд'}\n\n"
        btns = [[(f"➕ Добавить тренера", "coach_add")]]
        for c in coaches:
            btns.append([(f"✏️ {c['telegram_id']} — изменить", f"coach_edit_{c['telegram_id']}"),
                         (f"🗑 Удалить", f"coach_del_{c['telegram_id']}")])
        btns.append([(f"🔙 Управление", "admin_manage")])
        await q.edit_message_text(text, reply_markup=self.kb(btns))


    async def coach_team_view(self, update, ctx):
        """Панель тренера: спортсмены только его команд + кнопка «Рекомендации»."""
        q = update.callback_query
        await q.answer()
        user_id = q.from_user.id
        if not self.db.is_coach(user_id):
            await q.edit_message_text("🔒 Нет доступа.", reply_markup=self.kb([[(f"🏠 Главная", "main_menu")]]))
            return
        athletes = self._scoped_athletes(user_id)
        teams = self._coach_teams(user_id)

        # Batch: один запрос вместо 2N
        survey_map = self.db.get_today_survey_map()

        # Статус готовности команды на сегодня
        ready = unsure = not_ready = no_data = 0
        for a in athletes:
            today = survey_map.get(a["id"])
            r = today.get("readiness") if today else None
            if r is None:
                no_data += 1
            elif r >= 7:
                ready += 1
            elif r >= 5:
                unsure += 1
            else:
                not_ready += 1

        status_line = (
            f"🎯 *Готовность сегодня:* 🟢 {ready} готовы | 🟡 {unsure} под вопросом | 🔴 {not_ready} не готовы"
            + (f" | ⚪ {no_data} без опроса" if no_data else "")
        )

        text = f"👕 *Мои команды ({len(teams)}):*\n"
        text += "  " + ", ".join(sorted(teams)) + "\n\n"
        text += status_line + "\n\n"
        text += f"👥 *Спортсмены ({len(athletes)}):*\n"
        banned = set(x["id"] for x in self.db.get_banned_athletes())
        for a in athletes:
            s = a.get("survey_streak", 0)
            today = survey_map.get(a["id"])
            r = today.get("readiness") if today else None
            if r is None:
                ricon = "⚪"
            elif r >= 7:
                ricon = "🟢"
            elif r >= 5:
                ricon = "🟡"
            else:
                ricon = "🔴"
            icon = "🟢" if s >= 7 else ("🟡" if s >= 3 else ("🟠" if s > 0 else "🔴"))
            lock = "🔒 " if a["id"] in banned else ""
            text += f"{lock}{ricon} {a['full_name']} | {a.get('age_group', '?')} | 🔥{s}д\n"

        btns = []
        for a in athletes:
            btns.append([(f"🏥 Рекомендации: {a['full_name']}", f"recs_{a['id']}")])
        btns.append([(f"📊 Сводка за неделю", "coach_week_summary")])
        btns.append([(f"📊 Мой отчёт (Excel)", "report_export_menu")])
        btns.append([(f"⚖️ Отчёт по весам", "report_body_comp")])
        btns.append([(f"🔙 Главное меню", "main_menu")])
        await q.edit_message_text(text, reply_markup=self.kb(btns))


    async def coach_athlete_recs(self, update, ctx):
        """Рекомендации для конкретного спортсмена (только своей команды / админ)."""
        q = update.callback_query
        await q.answer()
        user_id = q.from_user.id
        athlete_id = int(q.data.replace("recs_", ""))
        if not self._is_admin_or_coach(user_id):
            await q.edit_message_text("🔒 Нет доступа.", reply_markup=self.kb([[(f"🏠 Главная", "main_menu")]]))
            return
        # Проверяем, что спортсмен в рамках прав
        scoped = {a["id"] for a in self._scoped_athletes(user_id)}
        if athlete_id not in scoped:
            await q.edit_message_text("🔒 Нет доступа к этому спортсмену.", reply_markup=self.kb([[(f"🔙 Главное меню", "main_menu")]]))
            return
        a = next((x for x in self.db.get_all_athletes() if x["id"] == athlete_id), None)
        if not a:
            await q.edit_message_text("❌ Спортсмен не найден.", reply_markup=self.kb([[(f"🔙 Моя команда", "coach_team_view")]]))
            return
        hist = self.db.get_last_wellness(athlete_id, 1)
        head = (
            f"🏥 *Рекомендации врача*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 *{a['full_name']}* | {a.get('age_group', '?')} | 🏀 {a.get('team', '?')}\n"
            f"📅 Последний опрос: {hist[0].get('survey_date') if hist else 'нет'}\n\n"
        )
        if not hist:
            text = head + "📭 Опросов пока нет."
        else:
            recs = self._doctor_recs(hist[0], a.get("age_group", "Pro"), athlete_id=athlete_id)
            text = head + recs + "\n\n⚠️ *Это не медицинское заключение.* При сомнениях — обратись к врачу лично."
        btns = [[(f"🔙 Моя команда", "coach_team_view")], [(f"🏠 Главное меню", "main_menu")]]
        await q.edit_message_text(text, reply_markup=self.kb(btns))


    def _coach_summary_text(self, teams):
        """Текст недельной сводки для тренера по его командам."""
        if not teams:
            return ""
        lines = ["📊 *Недельная сводка по командам*\n━━━━━━━━━━━━━━━━━━━━━━━━━━"]
        for team in sorted(teams):
            s = self.db.get_team_week_summary(team, 7)
            if not s["athletes"]:
                lines.append(f"\n👕 *{team}*\n— спортсменов нет")
                continue
            total = s["athletes"] * 7
            fill = f"{s['surveys']} из {total}"
            if total:
                fill += f" ({s['surveys'] * 100 // total}%)"
            avg_r = s["avg_readiness"]
            lines.append(
                f"\n👕 *{team}*\n"
                f"👥 Спортсменов: {s['athletes']} (отвечали {s['active']})\n"
                f"📝 Опросов за неделю: {fill}\n"
                f"⚡ Средняя готовность: {avg_r:.1f}/10" if avg_r else
                f"\n👕 *{team}*\n"
                f"👥 Спортсменов: {s['athletes']} (отвечали {s['active']})\n"
                f"📝 Опросов за неделю: {fill}\n"
                f"⚡ Средняя готовность: —"
            )
            flags = []
            if s["pain_hi"]:
                flags.append(f"🤕 Боль (NRS≥5): {s['pain_hi']}")
            if s["analgesics"]:
                flags.append(f"💊 Обезболивающие: {s['analgesics']}")
            if s["illness"]:
                flags.append(f"🤒 Болезнь: {s['illness']}")
            # Цикл (дизайн: тренер видит): задержки/аменорея по девушкам команды
            from cycle_ui import cycle_alert_lines
            girls = [a for a in self.db.get_all_athletes()
                     if a.get("team") == team and a.get("gender") == "female"]
            for _key, cyc_line in cycle_alert_lines(girls, date.today(), {}):
                flags.append(cyc_line)
            lines.append("🚨 *Требуют внимания:*\n  • " + "\n  • ".join(flags) if flags
                         else "✅ За неделю всё спокойно")
        return "\n".join(lines)


    async def coach_week_summary(self, update, ctx):
        """Сводка за неделю по командам тренера (по кнопке)."""
        q = update.callback_query
        await q.answer()
        user_id = q.from_user.id
        if not self.db.is_coach(user_id):
            await q.edit_message_text("🔒 Нет доступа.")
            return
        text = self._coach_summary_text(self._coach_teams(user_id)) or "Нет команд."
        btns = [[(f"🔙 Моя команда", "coach_team_view")], [(f"🏠 Главное меню", "main_menu")]]
        await q.edit_message_text(text, reply_markup=self.kb(btns))


    async def coach_add_ask(self, update, ctx):
        q = update.callback_query
        await q.answer()
        if not self._is_full_access(q.from_user.id):
            return
        state = self.get_state(q.from_user.id)
        state["step"] = "admin_coach_add"
        await q.edit_message_text(
            "➕ *Добавить тренера*\n\nПришли *Telegram ID* пользователя, которого нужно сделать тренером.",
            reply_markup=self.kb([[(f"🔙 Тренеры", "coach_menu")]])
        )


    async def coach_edit(self, update, ctx):
        q = update.callback_query
        await q.answer()
        if not self._is_full_access(q.from_user.id):
            return
        tg_id = int(q.data.replace("coach_edit_", ""))
        state = self.get_state(q.from_user.id)
        state["data"]["coach_edit_id"] = tg_id
        state["data"]["coach_edit_teams"] = self.db.get_coach_teams(tg_id)
        await self._render_coach_teams(q, tg_id, state["data"]["coach_edit_teams"])


    async def coach_toggle(self, update, ctx):
        q = update.callback_query
        await q.answer()
        if not self._is_full_access(q.from_user.id):
            return
        _, team, tg_id = q.data.rsplit("_", 2)
        tg_id = int(tg_id)
        state = self.get_state(q.from_user.id)
        teams = list(state["data"].get("coach_edit_teams", []))
        if team in teams:
            teams.remove(team)
        else:
            teams.append(team)
        state["data"]["coach_edit_teams"] = teams
        await self._render_coach_teams(q, tg_id, teams)


    def _coach_picker_parts(self, tg_id, teams):
        teams = list(teams)
        btns = []
        for t in TEAMS:
            mark = "✅" if t in teams else "⬜"
            btns.append([(f"{mark} {t}", f"coach_toggle_{t}_{tg_id}")])
        btns.append([(f"💾 Сохранить", f"coach_save_{tg_id}")])
        btns.append([(f"🔙 Тренеры", "coach_menu")])
        lines = "\n".join(f"  {'✅' if t in teams else '⬜'} {t}" for t in TEAMS)
        text = (
            f"✏️ *Команды тренера (id {tg_id})*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Отметь/сними команды:\n{lines}\n\n"
            f"*Выбрано:* {', '.join(teams) or '—'}"
        )
        return text, btns


    async def _render_coach_teams(self, q, tg_id, teams):
        text, btns = self._coach_picker_parts(tg_id, teams)
        await q.edit_message_text(text, reply_markup=self.kb(btns))


    async def _send_coach_team_picker(self, update, tg_id, teams):
        text, btns = self._coach_picker_parts(tg_id, teams)
        await update.message.reply_text(text, reply_markup=self.kb(btns))


    async def coach_save(self, update, ctx):
        q = update.callback_query
        await q.answer()
        if not self._is_full_access(q.from_user.id):
            return
        tg_id = int(q.data.replace("coach_save_", ""))
        state = self.get_state(q.from_user.id)
        teams = state["data"].get("coach_edit_teams", [])
        self.db.set_coach_teams(tg_id, teams)
        await q.edit_message_text(
            f"✅ Тренер (id {tg_id}) обновлён. Команды: {', '.join(teams) or '—'}",
            reply_markup=self.kb([[(f"🔙 Тренеры", "coach_menu")]])
        )


    async def coach_delete(self, update, ctx):
        q = update.callback_query
        await q.answer()
        if not self._is_full_access(q.from_user.id):
            return
        tg_id = int(q.data.replace("coach_del_", ""))
        self.db.remove_coach(tg_id)
        await q.edit_message_text(f"🗑 Тренер (id {tg_id}) удалён.",
                                  reply_markup=self.kb([[(f"🔙 Тренеры", "coach_menu")]]))
        await self.show_coach_admin(update, ctx)

    # ==================== УПРАВЛЕНИЕ ВРАЧАМИ (полный доступ, как у админа) ====================

