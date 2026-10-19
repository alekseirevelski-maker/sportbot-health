#!/usr/bin/env python3
"""Обработчик управления клубами (self-onboarding для SaaS)."""

import re
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


class ClubMixin:
    """Миксин для управления клубами (регистрация, настройка, статистика)."""

    def _get_db(self):
        return self.db

    # ============ РЕГИСТРАЦИЯ КЛУБА (self-onboarding) ============

    async def cmd_register_club(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /register_club — начать регистрацию нового клуба."""
        db = self._get_db()
        user_id = update.effective_user.id

        # Проверяем, уже является ли admin клуба
        if db.is_club_admin(user_id):
            club = db.get_club_by_admin(user_id)
            await update.message.reply_text(
                f"✅ Вы уже администратор клуба «{club['name']}».\n\n"
                f"📊 Статистика: {db.get_club_stats(club['id'])}\n\n"
                f"Используйте /club_menu для управления."
            )
            return

        # Начинаем регистрацию
        context.user_data["club_registration"] = {"step": "name"}
        await update.message.reply_text(
            "🏋️ Регистрация нового клуба\n\n"
            "Введите название вашего клуба/команды:\n"
            "(например: «Баскетбольный клуб Олимп»)"
        )

    async def handle_club_registration(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка пошаговой регистрации клуба."""
        db = self._get_db()
        user_id = update.effective_user.id
        text = update.message.text.strip()

        reg = context.user_data.get("club_registration")
        if not reg:
            return False

        step = reg["step"]

        if step == "name":
            if len(text) < 3 or len(text) > 100:
                await update.message.reply_text("❌ Название от 3 до 100 символов. Попробуйте ещё раз:")
                return True
            reg["name"] = text
            reg["step"] = "slug"
            await update.message.reply_text(
                f"✅ Название: «{text}»\n\n"
                "Введите латинский slug для клуба (используется в системе):\n"
                "(например: olimp-basketball)\n"
                "Только латиница, дефисы, без пробелов."
            )
            return True

        elif step == "slug":
            slug = re.sub(r'[^a-z0-9-]', '', text.lower().replace(' ', '-'))
            if len(slug) < 3 or len(slug) > 50:
                await update.message.reply_text("❌ Slug от 3 до 50 символов (латиница, дефисы). Попробуйте:")
                return True
            if db.get_club_by_slug(slug):
                await update.message.reply_text("❌ Этот slug уже занят. Придумайте другой:")
                return True
            reg["slug"] = slug
            reg["step"] = "sport"
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🏀 Баскетбол", callback_data="sport_basketball"),
                 InlineKeyboardButton("⚽ Футбол", callback_data="sport_football")],
                [InlineKeyboardButton("🏐 Волейбол", callback_data="sport_volleyball"),
                 InlineKeyboardButton("🏋️ Фитнес", callback_data="sport_fitness")],
                [InlineKeyboardButton("📝 Другой", callback_data="sport_other")],
            ])
            await update.message.reply_text(
                f"✅ Slug: `{slug}`\n\n"
                "Выберите вид спорта:",
                reply_markup=keyboard
            )
            return True

        elif step == "done":
            # Финализация
            return False

        return False

    async def handle_club_sport_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка выбора вида спорта."""
        db = self._get_db()
        query = update.callback_query
        await query.answer()

        data = query.data
        if not data.startswith("sport_"):
            return

        sport = data.replace("sport_", "")
        reg = context.user_data.get("club_registration")
        if not reg or reg.get("step") != "sport":
            return

        sport_names = {
            "basketball": "Баскетбол", "football": "Футбол",
            "volleyball": "Волейбол", "fitness": "Фитнес", "other": "Другой"
        }
        reg["sport"] = sport
        reg["step"] = "confirm"

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Подтвердить", callback_data="club_confirm_yes"),
             InlineKeyboardButton("❌ Отмена", callback_data="club_confirm_no")],
        ])

        await query.edit_message_text(
            f"📋 Проверьте данные:\n\n"
            f"🏛 Название: «{reg['name']}»\n"
            f"🔤 Slug: {reg['slug']}\n"
            f"🏅 Вид спорта: {sport_names.get(sport, sport)}\n\n"
            f"После подтверждения вы станете администратором клуба.\n"
            f"Спортсмены смогут подключаться по приглашению.",
            reply_markup=keyboard
        )

    async def handle_club_confirm_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Подтверждение или отмена регистрации клуба."""
        db = self._get_db()
        query = update.callback_query
        await query.answer()

        reg = context.user_data.get("club_registration")
        if not reg:
            return

        if query.data == "club_confirm_yes":
            user_id = update.effective_user.id
            club_id = db.create_club(
                name=reg["name"],
                slug=reg["slug"],
                admin_telegram_id=user_id,
                sport=reg.get("sport", "basketball"),
                plan="free"  # Стартовый тариф
            )
            if club_id:
                context.user_data.pop("club_registration", None)
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("⚙️ Настройки клуба", callback_data="club_settings")],
                    [InlineKeyboardButton("👥 Добавить тренера", callback_data="club_add_coach")],
                    [InlineKeyboardButton("📊 Статистика", callback_data="club_stats")],
                ])
                await query.edit_message_text(
                    f"🎉 Клуб «{reg['name']}» зарегистрирован!\n\n"
                    f"📋 Ваша ссылка для подключения спортсменов:\n"
                    f"Отправьте им бота @{context.bot.username} с командой:\n"
                    f"/join {reg['slug']}\n\n"
                    f"⚙️ Управление клубом: /club_menu",
                    reply_markup=keyboard
                )
            else:
                await query.edit_message_text("❌ Ошибка создания клуба. Попробуйте /register_club заново.")
        else:
            context.user_data.pop("club_registration", None)
            await query.edit_message_text("❌ Регистрация отменена.")

    # ============ ПРИСОЕДИНЕНИЕ К КЛУБУ (для спортсменов/тренеров) ============

    async def cmd_join(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /join <slug> — присоединиться к клубу."""
        db = self._get_db()
        if not context.args or len(context.args) != 1:
            await update.message.reply_text(
                "📝 Использование: /join <slug-клуба>\n\n"
                "Попросите тренера или врача предоставить slug вашего клуба."
            )
            return

        slug = context.args[0]
        club = db.get_club_by_slug(slug)
        if not club:
            await update.message.reply_text("❌ Клуб не найден. Проверьте slug.")
            return

        if not club["is_active"]:
            await update.message.reply_text("❌ Клуб деактивирован.")
            return

        user_id = update.effective_user.id

        # Сохраняем slug для последующей регистрации
        context.user_data["join_club"] = {
            "club_id": club["id"],
            "club_name": club["name"],
            "sport": club["sport"]
        }

        await update.message.reply_text(
            f"✅ Вы хотите присоединиться к клубу «{club['name']}» ({club['sport']})\n\n"
            f"Теперь зарегистрируйтесь как спортсмен или тренер.\n"
            f"Для этого используйте /start"
        )

    # ============ МЕНЮ УПРАВЛЕНИЯ КЛУБОМ ============

    async def cmd_club_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /club_menu — меню управления клубом."""
        db = self._get_db()
        user_id = update.effective_user.id

        club = db.get_club_by_admin(user_id)
        if not club:
            await update.message.reply_text(
                "❌ Вы не являетесь администратором клуба.\n\n"
                "Для создания клуба: /register_club\n"
                "Для присоединения: /join <slug>"
            )
            return

        stats = db.get_club_stats(club["id"])
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"👥 Спортсмены ({stats['athletes']})", callback_data="club_athletes"),
             InlineKeyboardButton(f"📋 Команды ({stats['teams']})", callback_data="club_teams")],
            [InlineKeyboardButton(f"🏥 Тренеры ({stats['coaches']})", callback_data="club_coaches")],
            [InlineKeyboardButton("📊 Статистика", callback_data="club_stats"),
             InlineKeyboardButton("⚙️ Настройки", callback_data="club_settings")],
            [InlineKeyboardButton("💳 Тариф", callback_data="club_plan"),
             InlineKeyboardButton("📤 Экспорт данных", callback_data="club_export")],
        ])

        await update.message.reply_text(
            f"🏛 Управление клубом «{club['name']}»\n\n"
            f"📊 Спортсменов: {stats['athletes']} / {club['max_athletes']}\n"
            f"📋 Команд: {stats['teams']}\n"
            f"🏥 Тренеров: {stats['coaches']}\n"
            f"📝 Всего опросов: {stats['surveys']}\n\n"
            f"💳 Тариф: {club['subscription_plan'].upper()}\n\n"
            f"🔗 Ссылка для подключения:\n"
            f"/join {club['slug']}",
            reply_markup=keyboard
        )

    async def handle_club_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка callback-кнопок меню клуба."""
        db = self._get_db()
        query = update.callback_query
        await query.answer()

        data = query.data
        user_id = update.effective_user.id
        club = db.get_club_by_admin(user_id)
        if not club:
            await query.edit_message_text("❌ Клуб не найден.")
            return

        if data == "club_stats":
            stats = db.get_club_stats(club["id"])
            await query.edit_message_text(
                f"📊 Статистика клуба «{club['name']}»\n\n"
                f"👥 Спортсменов: {stats['athletes']}\n"
                f"📋 Команд: {stats['teams']}\n"
                f"🏥 Тренеров: {stats['coaches']}\n"
                f"📝 Опросов: {stats['surveys']}\n\n"
                f"💳 Тариф: {club['subscription_plan'].upper()}\n"
                f"📅 Создан: {club['created_at'][:10]}"
            )

        elif data == "club_teams":
            teams = db.get_club_teams(club["id"])
            if not teams:
                text = "📋 Команды пока не созданы.\nСпортсмены создают команды при регистрации."
            else:
                text = "📋 Команды клуба:\n\n" + "\n".join(f"• {t}" for t in teams)
            await query.edit_message_text(text)

        elif data == "club_settings":
            await query.edit_message_text(
                f"⚙️ Настройки клуба «{club['name']}»\n\n"
                f"Для изменения настроек обратитесь к разработчику.\n"
                f"Текущий тариф: {club['subscription_plan'].upper()}"
            )

        elif data == "club_plan":
            await query.edit_message_text(
                f"💳 Текущий тариф: {club['subscription_plan'].upper()}\n\n"
                f"📦 Доступные тарифы:\n"
                f"• Free: до 10 спортсменов — 0 руб.\n"
                f"• Pro: до 50 спортсменов — 3,000 руб./мес.\n"
                f"• Enterprise: до 200 спортсменов — 7,000 руб./мес.\n\n"
                f"Для смены тарифа: /upgrade"
            )

        elif data == "club_add_coach":
            await query.edit_message_text(
                "🏥 Добавление тренера\n\n"
                "Попросите тренера написать боту /start\n"
                "Затем назначьте его командой:\n"
                "/assign_coach @username команда"
            )
