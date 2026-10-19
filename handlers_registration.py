"""
Миксин: RegistrationMixin
Автоматически извлечён из bot.py
"""

import logging
from typing import TYPE_CHECKING

from config import AGE_GROUPS, SIMPLE_PROTOCOLS
from database import TEAMS

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from bot import SportHealthBot


class RegistrationMixin:
    """Миксин для registration."""

    async def cmd_start(self, update, ctx):
        user = update.effective_user
        logger.info(f"cmd_start called by {user.id} ({user.username})")
        # Восстановить сессию из БД (переживает рестарт), если существует
        try:
            await self.restore_user_state(user.id)
        except Exception:
            pass
        # Тренер — не спортсмен: сразу панель тренера, без регистрации спортсмена
        if self.db.is_coach(user.id):
            await self.show_main_menu(update, ctx)
            return
        athlete = self.db.get_athlete_by_telegram_id(user.id)
        if athlete:
            if self.db.is_athlete_banned(athlete["id"]):
                if update.callback_query:
                    await update.callback_query.edit_message_text("🔒 Твой аккаунт заблокирован. Обратись к администратору.", reply_markup=self.kb([[(f"🏠 Главное меню", "main_menu")]]))
                else:
                    await update.message.reply_text("🔒 Твой аккаунт заблокирован. Обратись к администратору.", reply_markup=self.kb([[(f"🏠 Главное меню", "main_menu")]]))
                return
            # Само-синхронизация имени из Telegram (только если похоже на имя, не ник)
            if user.first_name and self._looks_like_name(user.first_name) and athlete.get("first_name") != user.first_name:
                self.db.set_athlete_first_name(athlete["id"], user.first_name)
                athlete["first_name"] = user.first_name
            await self.show_main_menu(update, ctx)
            return

        # Согласие на обработку ПДн (152-ФЗ) — обязательно до регистрации
        if not self.db.has_consent(user.id):
            consent_text = (
                "📄 *Согласие на обработку персональных данных*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "Бот собирает данные о твоём здоровье (опросы самочувствия, анкета, данные часов) "
                "для медицинского сопровождения команды. Данные хранятся на сервере и доступны только тренеру и врачу.\n\n"
                "Нажимая кнопку ниже, ты подтверждаешь согласие на их обработку."
            )
            consent_kb = self.kb([[(f"✅ Согласен", "consent_accept")],
                                  [(f"❌ Не согласен", "consent_decline")]])
            if update.callback_query:
                await update.callback_query.edit_message_text(consent_text, reply_markup=consent_kb, parse_mode="Markdown")
            else:
                await update.message.reply_text(consent_text, reply_markup=consent_kb, parse_mode="Markdown")
            return

        await self._ask_age_group(update, ctx)


    async def _ask_age_group(self, update, ctx):
        """Показать выбор возрастной группы при регистрации.

        Работает и из команды /start (update.message), и из callback
        (consent_accept / start_reg — update.message is None)."""
        text = (
            f"🏀 *Добро пожаловать в ЧБК СпортМед!*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Я — твой помощник для мониторинга здоровья.\n"
            f"Каждый день опрос → тренды → рекомендации врача.\n\n"
            f"*Что я умею:*\n"
            f"📝 Ежедневный опрос\n"
            f"📈 Графики и тренды\n"
            f"🏥 Рекомендации спортивного врача\n"
            f"⌚ Импорт данных с часов\n"
            f"🏆 Достижения\n\n"
            f"Выбери свою возрастную группу:"
        )
        buttons = [[(desc, f"reg_{key}")] for key, desc in AGE_GROUPS.items()]

        if update.callback_query:
            # Пришли из кнопки «Согласен» — редактируем текущее сообщение, не плодя новые
            await update.callback_query.edit_message_text(
                text, reply_markup=self.kb(buttons), parse_mode="Markdown"
            )
            return

        _logo = self._logo_file()
        if _logo is not None:
            try:
                # Эмблема — отдельной карточкой (без кнопок), чтобы текстовое меню было редактируемым
                await update.message.reply_photo(_logo)
            except Exception as e:
                logger.warning(f"logo in /start failed, fallback to text: {e}")
            finally:
                try: _logo.close()
                except Exception: pass
        await update.message.reply_text(text, reply_markup=self.kb(buttons), parse_mode="Markdown")


    async def reg_callback(self, update, ctx):
        q = update.callback_query
        await q.answer()
        age = q.data.replace("reg_", "")
        state = self.get_state(q.from_user.id)
        state["step"] = "reg_team"
        state["data"]["age_group"] = age

        protocol = "Simple" if age in SIMPLE_PROTOCOLS else "Full"

        # Определяем список команд: из клуба или глобальный
        club_data = ctx.user_data.get("join_club")
        club_id = club_data.get("club_id") if club_data else None
        if club_id:
            teams = self.db.get_club_teams(club_id)
            if not teams:
                # Клуб без команд — предлагаем ввести вручную
                state["step"] = "reg_team_custom"
                await q.edit_message_text(
                    f"✅ Группа: *{AGE_GROUPS[age]}*\n📋 Протокол: {protocol}\n\n"
                    f"В вашем клубе пока нет команд.\n"
                    f"Введите название вашей команды:",
                    parse_mode="Markdown"
                )
                return
            state["data"]["club_id"] = club_id
        else:
            teams = TEAMS

        buttons = [[(team, f"team_{team}")] for team in teams]
        await q.edit_message_text(
            f"✅ Группа: *{AGE_GROUPS[age]}*\n📋 Протокол: {protocol}\n\nВыбери *команду*:",
            reply_markup=self.kb(buttons), parse_mode="Markdown"
        )


    async def team_callback(self, update, ctx):
        q = update.callback_query
        await q.answer()
        team = q.data.replace("team_", "")
        state = self.get_state(q.from_user.id)
        state["step"] = "reg_gender"
        state["data"]["team"] = team

        await q.edit_message_text(
            f"✅ Команда: *{team}*\n\n👤 *Укажите пол:*",
            reply_markup=self.kb([
                [("♂ Мужской", "reg_gender_male")],
                [("♀ Женский", "reg_gender_female")],
            ]), parse_mode="Markdown"
        )

    async def reg_gender_callback(self, update, ctx):
        q = update.callback_query
        await q.answer()
        gender = q.data.replace("reg_gender_", "")
        state = self.get_state(q.from_user.id)
        state["step"] = "reg_name"
        state["data"]["gender"] = gender
        await q.edit_message_text(
            f"✅ Пол: {'♂ Мужской' if gender == 'male' else '♀ Женский'}\n\nВведи *Фамилию Имя*:",
            parse_mode="Markdown"
        )

    # ==================== СБРОС АККАУНТА ====================


    async def reset_account(self, update, ctx):
        q = update.callback_query
        await q.answer()
        await q.edit_message_text(
            "⚠️ *Сбросить аккаунт?*\n\nВсе данные будут удалены. Можно пройти регистрацию заново.",
            reply_markup=self.kb([
                [("✅ Да, сбросить", "confirm_reset")],
                [("❌ Нет", "main_menu")]
            ]), parse_mode="Markdown"
        )


    async def confirm_reset(self, update, ctx):
        q = update.callback_query
        await q.answer()
        athlete = self.db.get_athlete_by_telegram_id(q.from_user.id)
        if athlete:
            self.db.conn.execute("DELETE FROM daily_wellness WHERE athlete_id = ?", (athlete["id"],))
            self.db.conn.execute("DELETE FROM athletes WHERE id = ?", (athlete["id"],))
            self.db.conn.commit()
        await q.edit_message_text(
            "✅ Аккаунт сброшен!\n\nНапиши /start для регистрации заново.",
            parse_mode="Markdown"
        )

    # ==================== ДАННЫЕ ЧАСОВ ====================


