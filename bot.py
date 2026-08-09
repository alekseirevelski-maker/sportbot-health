#!/usr/bin/env python3
"""Telegram бот ЧБК v3.0 — Мониторинг здоровья спортсменов."""

import os, json, csv, io, re, logging, time, asyncio
from datetime import datetime, date, timedelta
from typing import Dict, Any, Optional, List, Tuple
from collections import defaultdict

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.drawing.image import Image as XLImage

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)

from config import (
    BOT_TOKEN, AGE_GROUPS, SIMPLE_PROTOCOLS,
    HRV_NORMS, HR_NORMS, EMOJIS,
    MOTIVATIONAL_MESSAGES, PROXY_URL,
    RATE_LIMIT_MESSAGES, RATE_LIMIT_WINDOW,
    MENSTRUAL_ENABLED_GROUPS,
    ADMIN_IDS, REMINDER_HOUR_DEFAULT, REMINDER_MINUTE_DEFAULT,
)
from cycle_medicine import (
    get_cycle_phase_info, get_cycle_phase, PHASES,
    AGE_SPECIFIC, CYCLE_LENGTH_MEDIAN,
)
from watch_parser import parse_watch

from validator import sanitize_text, validate_heart_rate, validate_age_ge14

if PROXY_URL:
    os.environ["http_proxy"] = PROXY_URL
    os.environ["https_proxy"] = PROXY_URL

from database import Database, TEAMS

# TESTING flag — disables all outgoing notifications to athletes
TESTING = os.environ.get("TESTING", "").lower() in ("true", "1", "yes")

class _TokenFilter(logging.Filter):
    def filter(self, record):
        if BOT_TOKEN and BOT_TOKEN in record.getMessage():
            record.msg = record.msg.replace(BOT_TOKEN, "***TOKEN***")
        return True

from logging.handlers import RotatingFileHandler
_log_handler = RotatingFileHandler("bot.log", maxBytes=5*1024*1024, backupCount=3, encoding="utf-8")
_log_handler.addFilter(_TokenFilter())
_console = logging.StreamHandler()
_console.addFilter(_TokenFilter())
logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO, handlers=[_log_handler, _console])
logger = logging.getLogger(__name__)

ADMIN_TELEGRAM_IDS = ADMIN_IDS

# Настройки напоминаний (по умолчанию из .env; переопределяются из БД при старте)
REMINDER_HOUR = REMINDER_HOUR_DEFAULT
REMINDER_MINUTE = REMINDER_MINUTE_DEFAULT
REMINDER_TZ = "Asia/Yekaterinburg"


# ==================== УТИЛИТЫ ====================

def sparkline(values, width=7):
    clean = [v for v in values if v is not None]
    if not clean:
        return "▁" * width
    mn, mx = min(clean), max(clean)
    rng = mx - mn if mx != mn else 1
    chars = ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
    return "".join(chars[max(0, min(7, int(round((v - mn) / rng * 7)))) if v is not None else 0] for v in values[-width:])


def sparkline_colored(values, width=7, invert=False):
    """Цветной спарклайн: 🟢🟡🔴 вместо монохромных символов.
    Если invert=True — 1=хорошо, 7=плохо."""
    clean = [v for v in values if v is not None]
    if not clean:
        return "—" * width
    result = ""
    for v in values[-width:]:
        if v is None:
            result += "—"
            continue
        if invert:
            if v >= 6: result += "🔴"
            elif v >= 4: result += "🟡"
            else: result += "🟢"
        else:
            if v <= 2: result += "🔴"
            elif v <= 4: result += "🟡"
            else: result += "🟢"
    return result




def score_bar(score, maximum=7):
    if score is None:
        return "—"
    filled = max(0, min(maximum, int(score)))
    return "▓" * filled + "░" * (maximum - filled) + f" {score}/{maximum}"


def trend_arrow(current, previous, invert=False):
    if current is None or previous is None:
        return "—"
    diff = current - previous
    if abs(diff) < 0.3:
        return "→ стабильно"
    if invert:
        diff = -diff
    return f"↑ +{diff:.1f}" if diff > 0 else f"↓ {diff:.1f}"


def get_rank(streak):
    if streak >= 30:
        return "🥇 Золото"
    elif streak >= 14:
        return "🥈 Серебро"
    elif streak >= 7:
        return "🥉 Бронза"
    elif streak >= 3:
        return "⭐ Старт"
    return "🌱 Новичок"


def get_score_emoji(score):
    """Эмодзи для оценки. 7=отлично: 6-7🟢, 4-5🟡, 1-3🔴."""
    if score is None:
        return "❓"
    s = int(score)
    if s >= 6: return "🟢"
    elif s >= 4: return "🟡"
    return "🔴"


# ==================== БОТ ====================

class SportHealthBot:
    def __init__(self):
        self.db = Database()
        self.user_states: Dict[int, Dict[str, Any]] = {}
        self.rate_limiter = defaultdict(list)
        self.job_queue = None
        self._state_ttl = 3 * 24 * 3600  # TTL состояний/сессий (3 дня) — защита от утечки памяти

    def get_state(self, uid):
        if uid not in self.user_states:
            self.user_states[uid] = {"step": None, "data": {}, "_seen": time.time()}
        else:
            self.user_states[uid]["_seen"] = time.time()
        return self.user_states[uid]

    def clear_state(self, uid):
        self.user_states.pop(uid, None)
        # асинхронно удаляем из БД (fire-and-forget)
        try:
            import asyncio as _asio
            loop = _asio.get_event_loop()
            if loop.is_running():
                loop.run_in_executor(None, lambda: self.db.delete_user_state(uid))
        except Exception:
            pass

    async def _db_run(self, fn, *a, **k):
        """Выполнить синхронный БД-вызов в executor, не блокируя event loop."""
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: fn(*a, **k))

    async def restore_user_state(self, uid):
        """При старте (cmd_start) восстановить сессию из БД в RAM, если её ещё нет."""
        if uid in self.user_states:
            return self.user_states[uid]
        try:
            import json as _json
            payload = await self._db_run(self.db.load_user_state, uid)
            if payload:
                data = _json.loads(payload)
                data["_seen"] = time.time()
                self.user_states[uid] = data
                return self.user_states[uid]
        except Exception as e:
            logger.warning(f"restore_user_state: {e}")
        self.user_states[uid] = {"step": None, "data": {}, "_seen": time.time()}
        return self.user_states[uid]

    async def persist_user_state(self, uid):
        """Сохранить RAM-сессию в БД (защита от потери при рестарте)."""
        st = self.user_states.get(uid)
        if not st:
            return
        # не храним служебные поля
        payload = {k: v for k, v in st.items() if k != "_seen"}
        try:
            await self._db_run(self.db.save_user_state, uid, payload)
        except Exception as e:
            logger.warning(f"persist_user_state: {e}")

    def _cleanup_stale_sessions(self, context=None):
        """Периодическая очистка неактивных сессий (TTL = _state_ttl)."""
        now = time.time()
        stale = [uid for uid, st in list(self.user_states.items())
                 if now - st.get("_seen", now) > self._state_ttl]
        for uid in stale:
            self.user_states.pop(uid, None)
        # rate_limiter: отсекаем записи, чей последний запрос старше 2x window (по сути TTL)
        for uid in list(self.rate_limiter.keys()):
            lst = [t for t in self.rate_limiter[uid] if now - t < RATE_LIMIT_WINDOW]
            if not lst:
                self.rate_limiter.pop(uid, None)
            else:
                self.rate_limiter[uid] = lst
        if stale or len(self.rate_limiter) > 5000:
            logger.info(f"TTL cleanup: удалено сессий={len(stale)}")

    def check_rate_limit(self, uid):
        now = time.time()
        self.rate_limiter[uid] = [t for t in self.rate_limiter[uid] if now - t < RATE_LIMIT_WINDOW]
        if len(self.rate_limiter[uid]) >= RATE_LIMIT_MESSAGES:
            return False
        self.rate_limiter[uid].append(now)
        return True

    def _first_name(self, full_name):
        """Имя из «Фамилия Имя» — берём последнее слово (имя), fallback на всю строку."""
        if not full_name:
            return ""
        parts = str(full_name).split()
        return parts[-1] if len(parts) > 1 else str(full_name)

    def _logo_file(self):
        """Открывает файл клубной эмблемы для отправки фото. None — если файла нет."""
        for p in ("logo_cbk.png",):
            if os.path.exists(p):
                try:
                    return open(p, "rb")
                except OSError:
                    return None
        return None

    def _add_logo_to_ws(self, ws, anchor="A1", size=90):
        """Вставляет эмблему клуба на лист Excel. Нет файла — ничего не делает."""
        try:
            if not os.path.exists("logo_cbk.png"):
                return
            img = XLImage("logo_cbk.png")
            # эмблема 480x480 квадратная — уменьшаем до size px, сохраняя пропорции
            img.width = size
            img.height = size
            img.anchor = anchor
            ws.add_image(img)
        except Exception as e:
            logger.warning(f"logo insert failed: {e}")

    def kb(self, buttons):
        # Отфильтровываем пустые строки кнопок
        buttons = [row for row in buttons if row]
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(t, callback_data=d) for t, d in row]
            for row in buttons
        ])

    def score_buttons(self, prefix, mn=1, mx=7, add_cancel=True):
        buttons, row = [], []
        for i in range(mn, mx + 1):
            row.append((f"{get_score_emoji(i)} {i}", f"srv_{prefix}_{i}"))
            if len(row) == 4:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        if add_cancel:
            buttons.append([("❌ Отмена", "main_menu")])
        return buttons

    # ==================== ГЛАВНОЕ МЕНЮ ====================

    async def _ask_gender_mandatory(self, update, ctx):
        """Запросить пол у пользователя — без этого не пускать в меню."""
        user = update.effective_user
        if not user:
            return
        text = (
            "⚠️ *Для продолжения нужно указать пол*\n\n"
            "Выбери ниже 👇"
        )
        buttons = self.kb([
            [("♂ Мужской", "gender_mandatory_male")],
            [("♀ Женский", "gender_mandatory_female")],
        ])
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=buttons, parse_mode="Markdown")
        else:
            await update.message.reply_text(text, reply_markup=buttons, parse_mode="Markdown")

    async def _gender_mandatory_chosen(self, update, ctx, gender):
        """Сохранить пол и показать главное меню."""
        q = update.callback_query
        await q.answer()
        user_id = q.from_user.id
        athlete = self.db.get_athlete_by_telegram_id(user_id)
        if athlete:
            self.db.update_athlete_gender(athlete["id"], gender)
        await self.show_main_menu(update, ctx)

    async def show_main_menu(self, update, ctx):
        user = update.effective_user
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
                f"😰 Стресс: {score_bar(today.get('stress_score', 0))}\n"
                f"😩 Утомление: {score_bar(today.get('fatigue_score', 0))}\n"
            )
            if today.get("resting_hr"):
                text += f"❤️ Пульс: {today['resting_hr']} уд/мин\n"
        else:
            text += f"⚠️ *Опрос за сегодня ещё не пройден!*\n"

        text += f"\n{greet}, {self._first_name(athlete['full_name'])}! 💪"

        # Минимальное меню спортсмена (простые, интуитивные действия)
        q_done = self.db.has_questionnaire(athlete["id"]) and not self.db.has_incomplete_questionnaire(athlete["id"])
        buttons = [
            [(f"📝 Пройти опрос" if not has_survey else f"✅ Сегодня пройден",
              "do_survey" if not has_survey else "view_today")],
            [(f"📊 Мои показатели", "my_stats")],
            [(f"⌚ Данные часов", "watch_data")],
            ([] if q_done else [(f"📋 Заполнить анкету", "questionnaire")]),
            [(f"📅 Запись к врачу", "consultation_start")],
            [(f"❓ Помощь", "help_menu")],
        ]

        # Админ-блок (виден только врачу/тебе)
        if user.id in ADMIN_TELEGRAM_IDS:
            buttons.insert(0, [(f"📋 Отчет для врача", "admin_report")])
            buttons.insert(1, [(f"📊 Экспорт в Excel", "report_export_menu")])
            buttons.insert(2, [(f"⚙️ Управление", "admin_manage")])

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

    async def cmd_start(self, update, ctx):
        user = update.effective_user
        logger.info(f"cmd_start called by {user.id} ({user.username})")
        # Восстановить сессию из БД (переживает рестарт), если существует
        try:
            await self.restore_user_state(user.id)
        except Exception:
            pass
        athlete = self.db.get_athlete_by_telegram_id(user.id)
        if athlete:
            if self.db.is_athlete_banned(athlete["id"]):
                await update.message.reply_text("🔒 Твой аккаунт заблокирован. Обратись к администратору.", reply_markup=self.kb([[(f"🏠 Главное меню", "main_menu")]]))
                return
            await self.show_main_menu(update, ctx)
            return

        # Согласие на обработку ПДн (152-ФЗ) — обязательно до регистрации
        if not self.db.has_consent(user.id):
            await update.message.reply_text(
                "📄 *Согласие на обработку персональных данных*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "Бот собирает данные о твоём здоровье (опросы самочувствия, анкета, данные часов) "
                "для медицинского сопровождения команды. Данные хранятся на сервере и доступны только тренеру и врачу.\n\n"
                "Нажимая кнопку ниже, ты подтверждаешь согласие на их обработку.",
                reply_markup=self.kb([[(f"✅ Согласен", "consent_accept")],
                                      [(f"❌ Не согласен", "consent_decline")]]),
                parse_mode="Markdown"
            )
            return

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
        else:
            await update.message.reply_text(text, reply_markup=self.kb(buttons), parse_mode="Markdown")

    async def reg_callback(self, update, ctx):
        q = update.callback_query
        await q.answer()
        age = q.data.replace("reg_", "")
        state = self.get_state(q.from_user.id)
        state["step"] = "reg_team"
        state["data"]["age_group"] = age

        protocol = "Simple" if age in SIMPLE_PROTOCOLS else "Full"
        buttons = [[(team, f"team_{team}")] for team in TEAMS]
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
            [(f"🔙 Назад", "main_menu")],
        ]
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

    async def handle_document(self, update, ctx):
        user_id = update.effective_user.id
        athlete = self.db.get_athlete_by_telegram_id(user_id)
        if not athlete:
            await update.message.reply_text("❌ Сначала зарегистрируйся: /start")
            return

        doc = update.message.document
        if not doc:
            return

        fname = doc.file_name.lower() if doc.file_name else ""
        supported = [".csv", ".json", ".txt"]
        if not any(fname.endswith(ext) for ext in supported):
            await update.message.reply_text("❌ Формат не поддерживается. Отправь CSV, JSON или TXT.")
            return

        # Лимит размера загружаемого файла (защита от DoS большими файлами)
        MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 МБ
        if getattr(doc, "file_size", None) and doc.file_size > MAX_FILE_BYTES:
            mb = round(doc.file_size / (1024 * 1024), 1)
            await update.message.reply_text(f"❌ Файл слишком большой ({mb} МБ). Максимум 5 МБ.")
            return

        await update.message.reply_text("📥 Обрабатываю файл...")

        try:
            file = await doc.get_file()
            content = await file.download_as_bytearray()
            text_content = content.decode("utf-8", errors="replace")
            watch_data = parse_watch(text_content, fname)

            if not watch_data:
                await update.message.reply_text(
                    "❌ Не удалось распознать данные. Проверь формат файла.\n\n"
                    "Попробуй выбрать свои часы в меню ⌚ и отправить файл по инструкции.",
                    reply_markup=self.kb([[(f"⌚ Выбрать часы", "watch_data")],
                                          [(f"🏠 Главное меню", "main_menu")]])
                )
                return

            msg = self._format_watch_report(watch_data, athlete["age_group"])

            await update.message.reply_text(
                msg, reply_markup=self.kb([[(f"🏠 Главное меню", "main_menu")]]), parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Watch file error: {e}")
            await update.message.reply_text("❌ Ошибка при обработке файла.")

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
                    except:
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

    async def show_profile(self, update, ctx):
        q = update.callback_query
        await q.answer()
        athlete = self.db.get_athlete_by_telegram_id(q.from_user.id)
        if not athlete:
            return

        stats = self.db.get_athlete_stats(athlete["id"], 7)
        prev = self.db.get_athlete_stats(athlete["id"], 14)
        streak = athlete.get("survey_streak", 0)

        # Рост/вес из анкеты + расчёт ИМТ
        bmi_line = ""
        qd = self.db.get_questionnaire(athlete["id"])
        if qd:
            h = qd.get("height")
            w = qd.get("weight")
            if h and w:
                h = float(h); w = float(w)
                if 0 < h < 400 and 0 < w < 300:
                    bmi = w / (h / 100) ** 2
                    if bmi < 18.5:
                        cat, emoji = "недостаточный вес", "⚠️"
                    elif bmi < 25:
                        cat, emoji = "норма", "✅"
                    elif bmi < 30:
                        cat, emoji = "избыточный вес", "⚠️"
                    else:
                        cat, emoji = "ожирение", "🔴"
                    bmi_line = f"📏 Рост: {int(h)} см | ⚖️ Вес: {int(w)} кг\n🧮 ИМТ: {bmi:.1f} ({emoji} {cat})\n"

        # Hooper Index
        sleep_avg = stats.get("avg_sleep", 0) or 0
        stress_avg = stats.get("avg_stress", 0) or 0
        fatigue_avg = stats.get("avg_fatigue", 0) or 0
        soreness_avg = stats.get("avg_soreness", 0) or 0
        mood_avg = stats.get("avg_mood", 0) or 0
        hooper = sleep_avg + stress_avg + fatigue_avg + soreness_avg + mood_avg
        if hooper >= 28:
            ready = "🟢 Отличная"
        elif hooper >= 20:
            ready = "🟡 Средняя"
        else:
            ready = "🔴 Низкая"

        text = (
            f"👤 *Профиль*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"*{athlete['full_name']}*\n"
            f"📋 {athlete['age_group']} | 🏀 {athlete.get('team', '?')}\n"
            f"🔥 Серия: {streak}д | {get_rank(streak)}\n"
            f"📊 Опросов: {athlete.get('total_surveys', 0)}\n"
            f"{bmi_line}\n"
            f"📊 *Готовность:* {ready} ({hooper:.0f}/35)\n"
            f"*Средние за 7 дней:*\n"
            f"😴 Сон: {score_bar(round(sleep_avg))}\n"
            f"😰 Стресс: {score_bar(round(stress_avg))}\n"
            f"😩 Утомление: {score_bar(round(fatigue_avg))}\n"
            f"🤕 Боль: {score_bar(round(soreness_avg))}\n"
            f"😊 Настроение: {score_bar(round(stats.get('avg_mood', 0) or 0))}\n"
            f"❤️ Пульс: {stats.get('avg_hr', 0):.0f} уд/мин\n\n"
            f"*Тренды (7д vs 14д):*\n"
            f"😴 Сон: {trend_arrow(sleep_avg, prev.get('avg_sleep'))}\n"
            f"😰 Стресс: {trend_arrow(stress_avg, prev.get('avg_stress'))}\n"
            f"❤️ Пульс: {trend_arrow(stats.get('avg_hr'), prev.get('avg_hr'), True)}\n"
        )

        await q.edit_message_text(text, reply_markup=self.kb([
            [(f"📈 Графики", "my_charts")],
            ([] if athlete.get("gender") != "female" else [(f"📅 Мой цикл", "my_cycle")]),
            [(f"🔙 Назад", "main_menu")]
        ]), parse_mode="Markdown")

    # ==================== ГРАФИКИ ====================

    async def show_charts(self, update, ctx):
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
            "stress": ("😰 Стресс", False),
            "fatigue": ("😩 Утомление", False),
            "soreness": ("🤕 Боль в мышцах", False),
            "mood": ("😊 Настроение", False),
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

        # Hooper Index за период (сумма sleep+stress+fatigue+soreness+mood, норма до 35)
        hooper_data = self.db.get_trend_data(athlete["id"], "sleep", days)
        stress_hooper = self.db.get_trend_data(athlete["id"], "stress", days)
        fatigue_hooper = self.db.get_trend_data(athlete["id"], "fatigue", days)
        soreness_hooper = self.db.get_trend_data(athlete["id"], "soreness", days)
        mood_hooper = self.db.get_trend_data(athlete["id"], "mood", days)

        if hooper_data and stress_hooper and fatigue_hooper:
            # Строим Hooper по дням
            date_map = {}
            for d, v in hooper_data:
                if v is not None:
                    date_map.setdefault(str(d), {})["sleep"] = v
            for d, v in stress_hooper:
                if v is not None:
                    date_map.setdefault(str(d), {})["stress"] = v
            for d, v in fatigue_hooper:
                if v is not None:
                    date_map.setdefault(str(d), {})["fatigue"] = v
            for d, v in soreness_hooper:
                if v is not None:
                    date_map.setdefault(str(d), {})["soreness"] = v
            for d, v in mood_hooper:
                if v is not None:
                    date_map.setdefault(str(d), {})["mood"] = v

            hooper_vals = []
            for d in sorted(date_map.keys()):
                m = date_map[d]
                if all(k in m for k in ["sleep", "stress", "fatigue"]):
                    h = m["sleep"] + m["stress"] + m["fatigue"] + m.get("soreness", 0) + m.get("mood", 0)
                    hooper_vals.append(h)

            if len(hooper_vals) >= 2:
                avg_h = sum(hooper_vals) / len(hooper_vals)
                text += f"📊 *Hooper Index:* ср {avg_h:.0f}/35\n"
                # Визуализация (чем выше - тем лучше)
                bar = ""
                for h in hooper_vals[-10:]:
                    if h >= 28:
                        bar += "🟢"
                    elif h >= 20:
                        bar += "🟡"
                    else:
                        bar += "🔴"
                        bar += "🟢"
                text += f"{bar}\n\n"

        text += "\n*Выбери период:*"

        period_buttons = [
            [("7 дней", "chart_7"), ("14 дней", "chart_14"), ("30 дней", "chart_30")],
            [(f"🔙 Назад", "main_menu")]
        ]
        await q.edit_message_text(text, reply_markup=self.kb(period_buttons), parse_mode="Markdown")

    # ==================== КОМАНДА ====================

    async def show_team(self, update, ctx):
        q = update.callback_query
        await q.answer()
        # Состав доступен только врачу/тренеру (защита ПДн — спортсмены не видят чужие данные)
        if q.from_user.id not in ADMIN_TELEGRAM_IDS:
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

    # ==================== ДОСТИЖЕНИЯ ====================

    async def show_achievements(self, update, ctx):
        q = update.callback_query
        await q.answer()
        athlete = self.db.get_athlete_by_telegram_id(q.from_user.id)
        if not athlete:
            return

        streak = athlete.get("survey_streak", 0)
        total = athlete.get("total_surveys", 0)

        checks = [
            (1, "🌱 Первая запись"), (3, "🔥 Огненная серия"),
            (7, "⭐ Неделя без пропусков"), (14, "🏆 Две недели!"),
            (30, "💎 Месяц идеала!"),
        ]

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

    async def start_survey(self, update, ctx):
        q = update.callback_query
        await q.answer()
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

        is_simple = athlete["age_group"] in SIMPLE_PROTOCOLS
        total = 3 if is_simple else 5

        await q.edit_message_text(
            f"📝 *Опрос* (1/{total})\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
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
        if len(parts) < 3:
            await q.edit_message_text(
                "❌ Ошибка данных. Попробуйте ещё раз.",
                reply_markup=self.kb([[(f"🏠 Главное меню", "main_menu")]])
            )
            return

        value = int(parts[-1])
        field = parts[1]

        field_map = {"sleep": "sleep_score", "stress": "stress_score", "fatigue": "fatigue_score",
                     "soreness": "muscle_soreness", "mood": "mood_score"}
        if field in field_map:
            data[field_map[field]] = value

        is_simple = athlete["age_group"] in SIMPLE_PROTOCOLS
        steps = ["sleep", "stress", "fatigue"] if is_simple else ["sleep", "stress", "fatigue", "soreness", "mood"]
        total = len(steps)

        try:
            idx = steps.index(field)
        except ValueError:
            return

        if idx + 1 < len(steps):
            nxt = steps[idx + 1]
            questions = {
                "stress": "😰 *Стресс*\n\nНасколько спокоен?\n\n1 😤 — Бесит всё\n4 😑 — Справляюсь\n7 😎 — Полностью спокоен",
                "fatigue": "😩 *Утомление*\n\nНасколько бодр?\n\n1 💀 — Упадок\n4 🦦 — Умеренно\n7 ⚡ — Бодрый",
                "soreness": "🤕 *Мышечная боль*\n\nБолят мышцы?\n\n1 🤕 — Сильно\n4 😣 — Чувствуется\n7 ✨ — Не болят",
                "mood": "😊 *Настроение*\n\nКак настроение?\n\n1 😭 — Ужасное\n4 😐 — Ровное\n7 🤩 — Отличное!",
            }
            state["step"] = f"survey_{nxt}"
            await q.edit_message_text(
                f"📝 *Опрос* ({idx+2}/{total})\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"{questions[nxt]}\n\nВыбери:",
                reply_markup=self.kb(self.score_buttons(nxt)), parse_mode="Markdown"
            )
        elif not is_simple:
            state["step"] = "survey_hr"
            await q.edit_message_text(
                f"📝 *Опрос* ({total}/{total})\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"❤️ *Пульс покоя*\n\nИзмеряй утром, как только проснулся, ещё лежа в кровати и не вставая.\nПосчитай удары за 15 секунд и умножь на 4.\n\nВведи число:",
                parse_mode="Markdown"
            )
        else:
            await self._route_after_survey(update, ctx, user_id, state)

    async def handle_text(self, update, ctx):
        user_id = update.effective_user.id
        if not self.check_rate_limit(user_id):
            await update.message.reply_text("⚠️ Подожди минуту.")
            return

        athlete = self.db.get_athlete_by_telegram_id(user_id)
        if athlete and self.db.is_athlete_banned(athlete["id"]):
            await update.message.reply_text("🔒 Ты заблокирован. Обратись к администратору.")
            self.clear_state(user_id)
            return

        state = self.get_state(user_id)
        step = state.get("step")
        text = update.message.text.strip()

        # Регистрация: имя
        if step == "reg_name":
            name = re.sub(r'[^\w\s\-]', '', text.strip())
            if len(name.split()) < 2 or len(name) < 3:
                await update.message.reply_text("❌ Введи *Имя и Фамилию*:", parse_mode="Markdown")
                return

            name = " ".join(name.split()).title()
            ok = self.db.register_athlete(
                telegram_id=user_id, username=update.effective_user.username,
                full_name=name, age_group=state["data"]["age_group"],
                team=state["data"].get("team", "Не указана")
            )
            if not ok:
                # Если пользователь уже существует — пробуем войти
                existing = self.db.get_athlete_by_telegram_id(user_id)
                if existing:
                    await update.message.reply_text(
                        f"✅ *Добро пожаловать снова!*\n\n👤 {existing['full_name']}\n📋 {existing['age_group']}\n🏀 {existing.get('team', '?')}\n\nВы уже зарегистрированы 🏀",
                        reply_markup=self.kb([[(f"🏠 Главное меню", "main_menu")]]), parse_mode="Markdown"
                    )
                else:
                    await update.message.reply_text("❌ Ошибка регистрации. Попробуйте позже.", parse_mode="Markdown")
                return
            # Сохраняем пол, если указан
            gender = state["data"].get("gender")
            if gender:
                athlete = self.db.get_athlete_by_telegram_id(user_id)
                if athlete:
                    self.db.update_athlete_gender(athlete["id"], gender)
            self.clear_state(user_id)
            gender_text = "♂ Мужской" if gender == "male" else "♀ Женский" if gender == "female" else ""
            gender_line = f"\n👤 Пол: {gender_text}" if gender_text else ""
            # Если анкета ещё не заполнена — предложить
            athlete_obj = self.db.get_athlete_by_telegram_id(user_id)
            has_q = self.db.has_questionnaire(athlete_obj["id"]) if athlete_obj else False
            if not has_q:
                await update.message.reply_text(
                    f"✅ *Регистрация завершена!*\n\n👤 {name}\n📋 {state['data']['age_group']}\n🏀 {state['data'].get('team', '?')}{gender_line}\n\n📋 *Теперь заполни анкету — это важно для врача!*",
                    reply_markup=self.kb([[(f"📋 Пройти анкету", "questionnaire")]]), parse_mode="Markdown"
                )
            else:
                await update.message.reply_text(
                    f"✅ *Регистрация завершена!*\n\n👤 {name}\n📋 {state['data']['age_group']}\n🏀 {state['data'].get('team', '?')}{gender_line}\n\nДобро пожаловать! 🏀",
                    reply_markup=self.kb([[(f"🏠 Главное меню", "main_menu")]]), parse_mode="Markdown"
                )
            return

        # Пульс
        if step == "survey_hr":
            try:
                hr = int(text)
                if not (30 <= hr <= 220):
                    raise ValueError
            except ValueError:
                await update.message.reply_text("❌ Введи число от 30 до 220:")
                return
            state["data"]["resting_hr"] = hr
            state["step"] = "survey_training"
            await update.message.reply_text(
                "💪 *Была ли тренировка вчера?*",
                reply_markup=self.kb([[(f"✅ Да", "train_yes"), (f"❌ Нет", "train_no")]]),
                parse_mode="Markdown"
            )
            return

        # SRPE
        if step == "survey_srpe":
            try:
                srpe = int(text)
                if not (1 <= srpe <= 10):
                    raise ValueError
            except ValueError:
                await update.message.reply_text("❌ Введи число от 1 до 10:")
                return
            state["data"]["sRPE_score"] = srpe
            await self._route_after_survey(update, ctx, user_id, state)
            return

        # Длина цикла (ручной ввод числа)
        if step == "survey_cycle_length":
            if not text.strip().isdigit():
                await update.message.reply_text("❌ Введи число дней цикла (21-35):")
                return
            length = int(text.strip())
            if not (21 <= length <= 35):
                await update.message.reply_text("❌ Норма цикла 21-35 дней. Введи число от 21 до 35:")
                return
            saved = await self._apply_cycle_length(user_id, length)
            if not saved:
                await update.message.reply_text(
                    "❌ *Опрос прерван*. Начните заново.",
                    reply_markup=self.kb([[(f"🔄 Пройти опрос", "do_survey")]])
                )
                return
            await self.ask_cycle_day(update, ctx)
            return

        # День цикла (ввод числа)
        if step == "survey_cycle":
            athlete = state.get("data", {}).get("athlete")
            if not athlete:
                await update.message.reply_text(
                    "❌ *Опрос прерван* (возможно бот перезагружался).\n\nХотите начать заново?",
                    reply_markup=self.kb([[(f"🔄 Пройти опрос", "do_survey")],
                                          [(f"🏠 Главное меню", "main_menu")]]),
                    parse_mode="Markdown"
                )
                return
            try:
                cd = int(text)
                cl = athlete.get("cycle_length_default", 28)
                if not (1 <= cd <= cl):
                    raise ValueError
            except ValueError:
                await update.message.reply_text(f"❌ Введи число от 1 до {athlete.get('cycle_length_default', 28)}:")
                return

            from cycle_medicine import get_cycle_phase
            phase_key, phase_info = get_cycle_phase(cd, athlete.get("cycle_length_default", 28))
            state["data"]["cycle_day"] = cd
            state["data"]["cycle_length"] = athlete.get("cycle_length_default", 28)
            state["data"]["cycle_phase"] = phase_info.name_ru if phase_info else ""
            await self._ask_complaints(update, ctx)
            return

        # Жалобы (ввод текста)
        if step == "complaint_text_input":
            state["data"]["complaints"] = sanitize_text(text, keep_nl=True)
            await self._finish_survey(update, user_id, state)
            return

        # Анкета: текстовые ответы
        if step == "q_age":
            try:
                age = int(text)
                if age < 10 or age > 99: raise ValueError
                state["q_data"]["age"] = str(age)
                state["step"] = "q_phone"
                await update.message.reply_text("📋 *Блок 1: Общие данные*\n\nВведи номер телефона:", parse_mode="Markdown")
            except:
                await update.message.reply_text("❌ Введи число от 10 до 99")
            return

        if step == "q_phone":
            phone = text.strip()
            if len(phone) < 5:
                await update.message.reply_text("❌ Введи номер телефона (например: +7 900 123-45-67):")
                return
            state["q_data"]["phone"] = phone
            state["step"] = "q_birth_date"
            await update.message.reply_text("📋 *Блок 1: Общие данные*\n\nВведи дату рождения в формате *ДД.ММ.ГГГГ* (например: 15.03.2008):", parse_mode="Markdown")
            return

        if step == "q_birth_date":
            import re as _re
            if not _re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", text.strip()):
                await update.message.reply_text("❌ Введи дату в формате *ДД.ММ.ГГГГ* (например: 15.03.2008):", parse_mode="Markdown")
                return
            state["q_data"]["birth_date"] = text.strip()
            state["step"] = "q_gender"
            await update.message.reply_text("📋 *Блок 1: Общие данные*\n\nВаш пол?", reply_markup=self.kb([[("М", "q_gender_М"), ("Ж", "q_gender_Ж")]]), parse_mode="Markdown")
            return

        if step == "q_height_weight":
            if text == "-":
                state["q_data"]["height"] = ""
                state["q_data"]["weight"] = ""
            else:
                parts = text.split()
                if len(parts) == 2:
                    state["q_data"]["height"] = parts[0]
                    state["q_data"]["weight"] = parts[1]
                else:
                    await update.message.reply_text("❌ Введи рост и вес через пробел (например: 185 82) или «-»")
                    return
            state["step"] = "q_trauma_12m"
            await update.message.reply_text("📋 *Блок 2: Травмы*\n\nБыли травмы за последние 12 месяцев?", reply_markup=self.kb([[("Да", "q_trauma_Да"), ("Нет", "q_trauma_Нет")]]), parse_mode="Markdown")
            return

        if step == "q_trauma_detail":
            state["q_data"]["trauma_12m_detail"] = sanitize_text(text, keep_nl=True)
            state["step"] = "q_zones"
            await update.message.reply_text("📋 *Блок 2: Травмы*\n\nЗоны, которые беспокоили за 3 месяца:", reply_markup=self.kb([
                [("Голеностопы", "q_zones_Голеностопы"), ("Колени", "q_zones_Колени")],
                [("Бёдра", "q_zones_Бёдра"), ("Поясница", "q_zones_Поясница")],
                [("Кисти", "q_zones_Кисти"), ("Плечи", "q_zones_Плечи")],
                [("Шея", "q_zones_Шея"), ("Ничего", "q_zones_Ничего")],
            ]), parse_mode="Markdown")
            return

        if step == "q_pain_detail":
            state["q_data"]["pain_now_detail"] = sanitize_text(text, keep_nl=True)
            state["step"] = "q_chronic"
            await update.message.reply_text("📋 *Блок 2: Травмы*\n\nЕсть рецидивирующая (повторяющаяся) травма?", reply_markup=self.kb([[("Да", "q_chronic_Да"), ("Нет", "q_chronic_Нет")]]), parse_mode="Markdown")
            return

        if step == "q_chronic_detail":
            state["q_data"]["chronic_detail"] = sanitize_text(text, keep_nl=True)
            state["step"] = "q_surgery"
            await update.message.reply_text("📋 *Блок 2: Травмы*\n\nБыли операции, из-за спорта?", reply_markup=self.kb([[("Да", "q_surgery_Да"), ("Нет", "q_surgery_Нет")]]), parse_mode="Markdown")
            return

        if step == "q_surgery_detail":
            state["q_data"]["surgery_detail"] = sanitize_text(text, keep_nl=True)
            state["step"] = "q_surgery_date"
            await update.message.reply_text("📋 *Блок 2: Травмы*\n\nКогда была(и) операция(и) (год или дата)?\nНапример: 2023 или 12.05.2023", parse_mode="Markdown")
            return

        if step == "q_surgery_date":
            state["q_data"]["surgery_date"] = text.strip()
            await self._q_continue_meds(update, ctx, state)
            return

        if step == "q_meds_detail":
            state["q_data"]["meds_detail"] = sanitize_text(text, keep_nl=True)
            state["step"] = "q_allergies"
            await update.message.reply_text("📋 *Блок 2: Травмы*\n\nЕсть аллергии?", reply_markup=self.kb([[("Да", "q_allergies_Да"), ("Нет", "q_allergies_Нет")]]), parse_mode="Markdown")
            return

        if step == "q_allergies_detail":
            state["q_data"]["allergies_detail"] = sanitize_text(text, keep_nl=True)
            await self._q_save_then_block3(update, ctx)
            return

        if step == "q_supp_other_input":
            # пользователь ввёл свой вариант спортпита — добавить к выбранным
            cur = state["q_data"].get("supplements", [])
            if isinstance(cur, str):
                cur = [cur] if cur and cur != "Ничего" else []
            cur = list(cur) if isinstance(cur, list) else []
            if text.strip():
                cur.append(text.strip())
            state["q_data"]["supplements"] = cur
            # автoсохранение
            try:
                await self._db_run(self.db.save_questionnaire, athlete["id"], state.get("q_data", {}))
            except Exception as e:
                logger.error(f"Q autosave: {e}")
            state["step"] = "q_supplements"
            await self._q_show_supp_buttons(update, ctx, cur)
            return

        if step == "q_goal":
            state["q_data"]["goal"] = sanitize_text(text, keep_nl=True)
            state["step"] = "q_wish"
            await update.message.reply_text("📋 *Блок 6: Цели*\n\nЕсть что-то, с чем нужна помощь?\n(можно пропустить — напиши «-»)", parse_mode="Markdown")
            return

        if step == "q_wish":
            state["q_data"]["wish"] = (sanitize_text(text, keep_nl=True)) if text != "-" else ""
            # Сохраняем
            athlete = self.db.get_athlete_by_telegram_id(user_id)
            if athlete:
                state["q_data"]["athlete_id"] = athlete["id"]
                await self._db_run(self.db.save_questionnaire, athlete["id"], state["q_data"])
                self.db.complete_questionnaire(athlete["id"])
            self.clear_state(user_id)
            await self._finish_questionnaire_offer(update, ctx)
            return

        # Консультация — ввод жалоб текстом
        if step == "consult_text_input":
            state["data"]["consult_complaints"] = sanitize_text(text, keep_nl=True)
            await self._show_calendar(update, ctx, "consult")
            return

        # Своё время напоминания
        if step == "set_reminder_custom":
            try:
                parts = text.split(":")
                hour = int(parts[0])
                minute = int(parts[1]) if len(parts) > 1 else 0
                if hour < 0 or hour > 23 or minute < 0 or minute > 59:
                    raise ValueError
                global REMINDER_HOUR, REMINDER_MINUTE, REMINDER_TZ
                REMINDER_HOUR = hour
                REMINDER_MINUTE = minute
                REMINDER_TZ = "Asia/Yekaterinburg"
                self.db.set_setting("reminder_hour", str(hour))
                self.db.set_setting("reminder_tz", REMINDER_TZ)
                self.clear_state(user_id)
                await update.message.reply_text(
                    f"✅ Время напоминаний: *{hour:02d}:{minute:02d}* по Челябинску (сохранено)",
                    parse_mode="Markdown",
                    reply_markup=self.kb([[(f"🔙 Назад", "main_menu")]])
                )
            except:
                await update.message.reply_text("❌ Неверный формат. Напиши время как ЧЧ:ММ (например: 14:30)")
                return
            return

        if step:
            await update.message.reply_text("Используй кнопки.", reply_markup=self.kb([[(f"🏠 Меню", "main_menu")]]))
        else:
            # Проверяем, зарегистрирован ли пользователь — может быть потеря состояния
            athlete = self.db.get_athlete_by_telegram_id(user_id)
            if athlete:
                await update.message.reply_text(
                    "❌ *Опрос прерван* (возможно бот перезагружался).\n\nХотите начать заново?",
                    reply_markup=self.kb([[(f"🔄 Пройти опрос", "do_survey")],
                                          [(f"🏠 Главное меню", "main_menu")]]),
                    parse_mode="Markdown"
                )
            else:
                await update.message.reply_text("Напиши /start для входа.")

        # Сохраняем сессию в БД (переживает рестарт) — асинхронно
        try:
            await self.persist_user_state(user_id)
        except Exception:
            pass

    async def callback_handler(self, update, ctx):
        q = update.callback_query
        try:
            await q.answer()
        except Exception:
            pass
        d = q.data
        logger.info(f"Callback: {d} from {q.from_user.id}")

        # Глобальный try/except — любая ошибка не должна вешать бота
        try:
            if d == "main_menu":
                await self.show_main_menu(update, ctx)
            elif d == "consent_accept":
                # регистрируем согласие и переходим к выбору возрастной группы
                self.db.record_consent(q.from_user.id)
                await self.cmd_start(update, ctx)
            elif d == "consent_decline":
                await q.edit_message_text("❌ Без согласия на обработку данных использовать бот нельзя.\nЕсли передумаешь — напиши /start.", reply_markup=self.kb([[(f"📄 Дать согласие", "consent_accept")]]), parse_mode="Markdown")
            elif d.startswith("reg_gender_"):
                await self.reg_gender_callback(update, ctx)
            elif d.startswith("reg_"):
                await self.reg_callback(update, ctx)
            elif d.startswith("team_"):
                await self.team_callback(update, ctx)
            elif d == "my_stats":
                await self.show_profile(update, ctx)
            elif d == "my_profile":
                await self.show_profile(update, ctx)
            elif d in ("my_charts",):
                await self.show_charts(update, ctx)
            elif d.startswith("chart_"):
                days = int(d.replace("chart_", ""))
                state = self.get_state(q.from_user.id)
                state["data"]["chart_days"] = days
                await self.show_charts(update, ctx)
            elif d == "team_list":
                await self.show_team(update, ctx)
            elif d == "achievements":
                await self.show_achievements(update, ctx)
            elif d == "cancel_survey":
                self.clear_state(q.from_user.id)
                await self.show_main_menu(update, ctx)
            elif d == "do_survey":
                await self.start_survey(update, ctx)
            elif d.startswith("srv_"):
                await self.survey_callback(update, ctx)
            elif d == "train_yes":
                await self._training_yes(update, ctx)
            elif d == "train_no":
                await self._training_no(update, ctx)
            elif d == "help_menu":
                await self.show_help(update, ctx)
            elif d == "questionnaire_list":
                await self.show_questionnaire_list(update, ctx)
            elif d == "questionnaire":
                await self.start_questionnaire(update, ctx)
            elif d.startswith("q_"):
                await self.handle_questionnaire_answer(update, ctx)
            elif d == "start_reg":
                await self.cmd_start(update, ctx)
            elif d == "gender_mandatory_male":
                await self._gender_mandatory_chosen(update, ctx, "male")
            elif d == "gender_mandatory_female":
                await self._gender_mandatory_chosen(update, ctx, "female")
            elif d == "view_today":
                await self.show_profile(update, ctx)
            elif d == "admin_report":
                await self.show_admin_report(update, ctx)
            elif d == "watch_data":
                await self.watch_data_menu(update, ctx)
            elif d.startswith("watch_"):
                brand = d.replace("watch_", "")
                await self._watch_brand_instructions(update, ctx, brand)
            elif d == "reset_account":
                await self.reset_account(update, ctx)
            elif d == "confirm_reset":
                await self.confirm_reset(update, ctx)
            elif d == "export_csv":
                await self.export_csv(update, ctx)
            elif d == "athlete_list":
                await self.athlete_list(update, ctx)
            elif d.startswith("qview_"):
                await self.show_questionnaire_detail(update, ctx)
            elif d.startswith("athlete_page_"):
                await self.athlete_list(update, ctx, int(d.split("_")[-1]))
            elif d == "ban_menu":
                await self.show_ban_menu(update, ctx)
            elif d.startswith("ban_page_"):
                await self.show_ban_menu(update, ctx, int(d.split("_")[-1]))
            elif d.startswith("ban_"):
                a_id = int(d.split("_")[1])
                self.db.ban_athlete(a_id, q.from_user.id)
                await q.answer("✅ Заблокирован", show_alert=True)
                await self.show_ban_menu(update, ctx)
            elif d.startswith("unban_"):
                a_id = int(d.split("_")[1])
                self.db.unban_athlete(a_id)
                await q.answer("✅ Разблокирован", show_alert=True)
                await self.show_ban_menu(update, ctx)
            elif d == "admin_manage":
                await self.show_admin_manage(update, ctx)
            elif d == "daily_report":
                await self.daily_report(update, ctx)
            elif d == "delete_athlete":
                await self.delete_athlete_menu(update, ctx)
            elif d.startswith("del_") and not d.startswith("delconfirm_"):
                await self.delete_athlete_confirm(update, ctx)
            elif d.startswith("delconfirm_"):
                await self.delete_athlete_final(update, ctx)
            elif d == "reminder_settings":
                await self.reminder_settings(update, ctx)
            elif d.startswith("set_reminder_"):
                await self.set_reminder_time(update, ctx)
            elif d == "send_reminder_now":
                await self.send_reminder_now(update, ctx)
            elif d == "send_q_reminder":
                await self.send_questionnaire_reminder(update, ctx)
            # === НОВЫЕ ОБРАБОТЧИКИ ===
            elif d == "report_export_menu":
                await self.report_export_menu(update, ctx)
            elif d == "export_q":
                await self.export_questionnaires_xlsx(update, ctx)
            elif d.startswith("report_team_"):
                await self.report_choose_period(update, ctx)
            elif d.startswith("report_period_"):
                await self.generate_xlsx_report(update, ctx)
            elif d == "set_gender":
                await self.set_gender_menu(update, ctx)
            elif d.startswith("gender_"):
                await self.save_gender(update, ctx)
            elif d == "cycle_len_custom":
                await self._ask_cycle_length_custom(update, ctx)
            elif d == "cycle_custom":
                await self._ask_cycle_day_custom(update, ctx)
            elif d.startswith("cycle_len_"):
                await self._cycle_length_chosen(update, ctx)
            elif d.startswith("cycle_"):
                await self.set_cycle_day(update, ctx)
            elif d == "my_cycle":
                await self.show_cycle_info(update, ctx)
            # === ПОЛ (ПЕРВИЧНЫЙ ВОПРОС В ОПРОСЕ) ===
            elif d == "gender_first_male":
                await self._gender_first_chosen(update, ctx, "male")
            elif d == "gender_first_female":
                await self._gender_first_chosen(update, ctx, "female")
            # === ЖАЛОБЫ И КОНСУЛЬТАЦИЯ ===
            elif d == "complaint_text":
                await self.complaint_text(update, ctx)
            elif d == "complaint_none":
                await self.complaint_none(update, ctx)
            elif d == "consultation_start":
                await self.consultation_start(update, ctx)
            elif d == "consult_text":
                await self.consultation_text(update, ctx)
            elif d == "consult_no":
                await self.consultation_no_complaints(update, ctx)
            elif d.startswith("consult_") and d not in ["consultation_start", "consult_no", "consult_text"]:
                await self.consultation_date(update, ctx)

        # Конец try/except для callback_handler
        except Exception as e:
            logger.error(f"Callback handler error: {e} | data={d}", exc_info=True)
            try:
                await q.edit_message_text(
                    "❌ Произошла ошибка. Попробуйте ещё раз.",
                    reply_markup=self.kb([[(f"🏠 Главное меню", "main_menu")]])
                )
            except Exception:
                pass

        # Сохраняем сессию в БД (переживает рестарт) — асинхронно, без блокировки
        try:
            await self.persist_user_state(user_id)
        except Exception:
            pass

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

        # Если девушка → спросить длину цикла (если не сохранена) или день цикла
        if gender == "female":
            if not cycle_length:
                await self._ask_cycle_length(update, ctx)
            else:
                await self.ask_cycle_day(update, ctx)
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
        data = state["data"]
        athlete = data.get("athlete")
        if not athlete:
            return

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
        })

        recs = self._doctor_recs(data, athlete["age_group"], athlete_id=athlete["id"])
        # Hooper Index из 5 показателей: сон+стресс+утомление+боль+настроение
        hooper = sum(filter(None, [data.get("sleep_score"), data.get("stress_score"),
                                    data.get("fatigue_score"), data.get("muscle_soreness"),
                                    data.get("mood_score")]))
        hooper_max = 35

        athlete = self.db.get_athlete_by_telegram_id(user_id)
        streak = athlete.get("survey_streak", 0) if athlete else 0

        motivation = ""
        if streak >= 30: motivation = MOTIVATIONAL_MESSAGES["streak_30"]
        elif streak >= 14: motivation = MOTIVATIONAL_MESSAGES["streak_14"]
        elif streak >= 7: motivation = MOTIVATIONAL_MESSAGES["streak_7"]
        elif streak >= 3: motivation = MOTIVATIONAL_MESSAGES["streak_3"]

        text = (
            f"✅ *Опрос завершён!*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        )
        if hooper >= 28:
            text += f"🟢 *Уровень готовности: Отличный* ({hooper}/35)\n\n"
        elif hooper >= 20:
            text += f"🟡 *Уровень готовности: Средний* ({hooper}/35)\n\n"
        else:
            text += f"🔴 *Уровень готовности: Низкий* ({hooper}/35)\n\n"
        text += (
            f"😴 Сон: {get_score_emoji(data.get('sleep_score'))} {score_bar(data.get('sleep_score', 0))}\n"
            f"😰 Стресс: {get_score_emoji(data.get('stress_score'))} {score_bar(data.get('stress_score', 0))}\n"
            f"😩 Утомление: {get_score_emoji(data.get('fatigue_score'))} {score_bar(data.get('fatigue_score', 0))}\n"
        )
        if data.get("resting_hr"):
            text += f"❤️ Пульс: {data['resting_hr']} уд/мин\n"
        text += f"\n🔥 Серия: {streak} дней | {get_rank(streak)}\n"
        if motivation:
            text += f"\n{motivation}\n"
        if recs:
            text += f"\n*🏥 Рекомендации врача:*\n{recs}\n"

        buttons = [[(f"🏠 Главное меню", "main_menu")]]

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
            for admin_id in ADMIN_TELEGRAM_IDS:
                if admin_id != user_id:  # Не дублируем тому кто прошел
                    athlete_name = athlete.get("full_name", "?")
                    team = athlete.get("team", "?")
                    admin_text = (
                        f"📋 *Новый опрос — {athlete_name}*\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🏀 Команда: {team} | {athlete.get('age_group', '?')}\n\n"
                        f"😴 Сон: {get_score_emoji(data.get('sleep_score'))} {score_bar(data.get('sleep_score', 0))}\n"
                        f"😰 Стресс: {get_score_emoji(data.get('stress_score'))} {score_bar(data.get('stress_score', 0))}\n"
                        f"😩 Утомление: {get_score_emoji(data.get('fatigue_score'))} {score_bar(data.get('fatigue_score', 0))}\n"
                    )
                    if data.get("resting_hr"):
                        admin_text += f"❤️ Пульс: {data['resting_hr']} уд/мин\n"
                    complaints = data.get("complaints", "")
                    if complaints:
                        admin_text += f"💬 Жалобы: {complaints}\n"
                    admin_text += f"\n🔥 Серия: {streak}д | Готовность: {hooper}/35"
                    if recs and recs != "✅ Все показатели в норме! Продолжай в том же духе.":
                        admin_text += f"\n⚠️ *Нужно внимание:*\n{recs}\n"
                    else:
                        admin_text += f"\n✅ Все в норме\n"

                    await self._send_admin(admin_id, admin_text)
        except Exception as e:
            logger.error(f"Admin notify error: {e}")

        self.clear_state(user_id)

    async def _send_admin(self, admin_id, text):
        """Отправить сообщение админу."""
        try:
            from telegram import Bot
            bot = Bot(token=BOT_TOKEN)
            await bot.send_message(
                chat_id=admin_id, text=text, parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Send to admin {admin_id}: {e}")

    def _doctor_recs(self, data, age_group, athlete_id=None):
        recs = []
        sleep = data.get("sleep_score")
        if sleep is not None:
            if sleep <= 2:
                recs.append("😴 *Критический дефицит сна*\n• Норма: 7-9ч, оценка 5-7\n• Ложиться до 22:30\n• Исключить кофеин после 16:00\n• При сохранении >3 дней → врач")
            elif sleep <= 4:
                recs.append("😴 *Недостаток сна*\n• Норма: 7-9ч, оценка 5-7\n• Ложиться до 23:00\n• Убрать гаджеты за 1ч до сна")
        stress = data.get("stress_score")
        if stress is not None:
            if stress <= 2:
                recs.append("🧘 *Высокий стресс*\n• Дыхание 4-7-8: 5 циклов\n• Прогулка 30мин\n• Снизить нагрузку на 20%")
            elif stress <= 3:
                recs.append("🧘 *Умеренный стресс*\n• Дыхательные практики 5мин\n• Ограничить соцсети за 2ч до сна")
        fatigue = data.get("fatigue_score")
        if fatigue is not None:
            if fatigue <= 2:
                recs.append("⚡ *Критическое утомление*\n• Отдых 1-2 дня\n• Сон не менее 9ч\n• Белок 1.6-2.2г/кг")
            elif fatigue <= 4:
                recs.append("⚡ *Повышенное утомление*\n• Легкая тренировка (50%)\n• Сон +1ч\n• Вода 2-3л")
        hr = data.get("resting_hr")
        hr_norms = HR_NORMS.get(age_group, {"min": 40, "max": 70})
        if hr:
            if hr > hr_norms["max"] + 15:
                recs.append(f"❤️ *Пульс критически высокий ({hr})*\n• Норма {age_group}: {hr_norms['min']}-{hr_norms['max']}\n• Исключить интенсивные тренировки\n• При пульсе >80 >3 дней → ЭКГ")
            elif hr > hr_norms["max"] + 5:
                recs.append(f"❤️ *Пульс выше нормы ({hr})*\n• Норма: {hr_norms['min']}-{hr_norms['max']}\n• Снизить нагрузку на 30%")
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
        """Алерты по личной норме (30 дн.), только при 2 ОТКЛОНЕНИЯХ ПОДРЯД (сегодня + вчера)."""
        avg = bl.get("avg") or {}

        def dev(value, kind):
            """True, если `value` выходит за личный порог по показателю `kind`."""
            if value is None:
                return False
            if kind == "hr":
                return bl.get("avg_hr") is not None and value > bl["avg_hr"] * 1.10
            if kind == "sleep":
                a = avg.get("sleep"); return a is not None and value < a - 1.5
            if kind == "mood":
                a = avg.get("mood"); return a is not None and value < a - 1.5
            if kind == "stress":
                a = avg.get("stress"); return a is not None and value > a + 1.5
            if kind == "fatigue":
                a = avg.get("fatigue"); return a is not None and value > a + 1.5
            if kind == "soreness":
                a = avg.get("soreness"); return a is not None and value > a + 1.5
            return False

        out = []
        # если нет данных за «вчера» — не сигналим по индивидуальному (только общие коридоры)
        if prev is None:
            return out

        # пульс
        cur_hr = data.get("resting_hr")
        prev_hr = prev.get("resting_hr")
        if dev(cur_hr, "hr") and dev(prev_hr, "hr"):
            out.append(f"❤️ *Пульс выше личной нормы 2 дня подряд ({cur_hr} vs ~{int(bl['avg_hr'])}/30д)*\n• Обратить внимание на восстановление")

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
                out.append(f"{label} ({cur} vs ~{avg.get(kind):.1f}/30д)*")
        return out
    


    # ==================== УДАЛЕНИЕ СПОРТСМЕНА (ТОЛЬКО АДМИН) ====================

    async def delete_athlete_menu(self, update, ctx):
        q = update.callback_query
        await q.answer()
        if q.from_user.id not in ADMIN_TELEGRAM_IDS:
            return

        athletes = self.db.get_all_athletes()
        if not athletes:
            await q.edit_message_text("📭 Нет спортсменов.", reply_markup=self.kb([[(f"🔙 Назад", "main_menu")]]))
            return

        buttons = []
        for a in athletes:
            buttons.append([(f"❌ {a['full_name']} ({a['team']})", f"del_{a['id']}")])
        buttons.append([(f"🔙 Назад", "main_menu")])

        await q.edit_message_text(
            "🗑 *Удаление спортсмена*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nВыбери кого удалить:",
            reply_markup=self.kb(buttons), parse_mode="Markdown"
        )

    async def delete_athlete_confirm(self, update, ctx):
        q = update.callback_query
        await q.answer()
        if q.from_user.id not in ADMIN_TELEGRAM_IDS:
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
        if q.from_user.id not in ADMIN_TELEGRAM_IDS:
            return

        athlete_id = int(q.data.replace("delconfirm_", ""))
        self.db.conn.execute("DELETE FROM daily_wellness WHERE athlete_id = ?", (athlete_id,))
        self.db.conn.execute("DELETE FROM alerts WHERE athlete_id = ?", (athlete_id,))
        self.db.conn.execute("DELETE FROM athletes WHERE id = ?", (athlete_id,))
        self.db.conn.commit()

        await q.edit_message_text(
            "✅ Спортсмен удалён.",
            reply_markup=self.kb([[(f"🔙 Назад", "main_menu")]]), parse_mode="Markdown"
        )

    # ==================== НАПОМИНАНИЯ ====================

    async def reminder_settings(self, update, ctx):
        q = update.callback_query
        await q.answer()
        if q.from_user.id not in ADMIN_TELEGRAM_IDS:
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

    async def set_reminder_time(self, update, ctx):
        global REMINDER_HOUR, REMINDER_MINUTE
        q = update.callback_query
        await q.answer()
        if q.from_user.id not in ADMIN_TELEGRAM_IDS:
            return

        data = q.data.replace("set_reminder_", "")
        if data == "off":
            REMINDER_HOUR = None
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
                await q.edit_message_text(f"❌ Ошибка: {e}", reply_markup=self.kb([[(f"🔙 Назад", "main_menu")]]))
            return

        hour = int(data)
        REMINDER_HOUR = hour
        REMINDER_MINUTE = 0
        REMINDER_TZ = "Asia/Yekaterinburg"
        self.db.set_setting("reminder_hour", str(hour))
        self.db.set_setting("reminder_tz", REMINDER_TZ)

        if self.job_queue:
            import pytz
            from datetime import datetime as dt
            local_tz = pytz.timezone(REMINDER_TZ)
            now_local = dt.now(local_tz)
            target_local = now_local.replace(hour=hour, minute=0, second=0, microsecond=0)
            target_utc = target_local.astimezone(pytz.UTC)
            self.job_queue.run_daily(
                self._send_daily_reminder,
                time=target_utc.time(),
                days=tuple(range(7)),
                name="daily_reminder"
            )

        await q.edit_message_text(
            f"⏰ Напоминание настроено на *{hour:02d}:00* по Челябинску\n\n"
            f"Спортсменам будет приходить уведомление в это время ежедневно.",
            reply_markup=self.kb([[(f"🔙 Назад", "main_menu")]]), parse_mode="Markdown"
        )

    async def send_reminder_now(self, update, ctx):
        q = update.callback_query
        await q.answer()
        if q.from_user.id not in ADMIN_TELEGRAM_IDS:
            return
        await q.edit_message_text("📨 *Отправляю напоминания спортсменам...*", parse_mode="Markdown")
        try:
            await self._send_daily_reminder(ctx)
            await q.edit_message_text("✅ *Напоминания отправлены!*", parse_mode="Markdown", reply_markup=self.kb([[(f"🔙 Назад", "main_menu")]]))
        except Exception as e:
            logger.error(f"Send now error: {e}")
            await q.edit_message_text(f"❌ Ошибка: {e}", reply_markup=self.kb([[(f"🔙 Назад", "main_menu")]]))

    async def send_questionnaire_reminder(self, update, ctx):
        """Рассылка тем спортсменам, у кого не заполнена анкета здоровья."""
        q = update.callback_query
        await q.answer()
        if q.from_user.id not in ADMIN_TELEGRAM_IDS:
            return
        await q.edit_message_text("📋 *Отправляю предложение заполнить анкету...*", parse_mode="Markdown")
        try:
            bot = ctx.bot
            athletes = self.db.get_all_athletes()
            target = []
            for a in athletes:
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
                            f"👋 Привет, {self._first_name(a['full_name'])}!\n\n"
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
            await q.edit_message_text(f"❌ Ошибка: {e}", reply_markup=self.kb([[(f"🔙 Назад", "main_menu")]]))

    # ==================== ОТЧЕТ ПО ДНЯМ ====================

    async def daily_report(self, update, ctx):
        q = update.callback_query
        await q.answer()
        if q.from_user.id not in ADMIN_TELEGRAM_IDS:
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

        text = (
            f"📅 *Отчёт за {today.strftime('%d.%m.%Y')}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"✅ *Прошли опрос ({len(passed)}):*\n"
        )
        for a in passed:
            text += f"  • {a['full_name']} ({a['team']})\n"

        if not_passed:
            text += f"\n❌ *Не прошли ({len(not_passed)}):*\n"
            for a in not_passed:
                text += f"  • {a['full_name']} ({a['team']})\n"

        text += f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        text += f"Итого: {len(passed)}/{len(athletes)} прошли"

        buttons = [[(f"📊 Экспорт CSV", "export_csv")], [(f"🔙 Назад", "main_menu")]]
        await q.edit_message_text(text, reply_markup=self.kb(buttons), parse_mode="Markdown")

    # ==================== ОТЧЕТ ДЛЯ ВРАЧА ====================

    async def show_admin_manage(self, update, ctx):
        q = update.callback_query
        await q.answer()
        if q.from_user.id not in ADMIN_TELEGRAM_IDS:
            return

        text = (
            f"⚙️ *Управление ботом*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Выбери действие:"
        )
        buttons = [
            [(f"👥 Список спортсменов", "athlete_list")],
            [(f"🔒 Блокировка", "ban_menu")],
            [(f"📋 Анкеты", "questionnaire_list")],
            [(f"📅 Отчёт за сегодня", "daily_report")],
            [(f"🗑 Удалить спортсмена", "delete_athlete")],
            [(f"⏰ Напоминания", "reminder_settings")],
            [(f"📊 Экспорт CSV", "export_csv")],
            [(f"🔙 Назад", "main_menu")]
        ]
        await q.edit_message_text(text, reply_markup=self.kb(buttons), parse_mode="Markdown")

    async def show_admin_report(self, update, ctx):
        q = update.callback_query
        await q.answer()
        if q.from_user.id not in ADMIN_TELEGRAM_IDS:
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
                    if sleep and sleep < 3: issues.append("сон🔴"); total_crit += 1
                    elif sleep and sleep < 5: issues.append("сон🟡"); total_warn += 1
                    if stress and stress > 6: issues.append("стресс🔴"); total_crit += 1
                    elif stress and stress > 4: issues.append("стресс🟡"); total_warn += 1
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

    async def export_csv(self, update, ctx):
        q = update.callback_query
        await q.answer()
        if q.from_user.id not in ADMIN_TELEGRAM_IDS:
            return

        athletes = self.db.get_all_athletes()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["ФИО", "Группа", "Команда", "Серия", "Опросов", "Сон", "Стресс", "Утомление", "Пульс", "Активность"])

        for a in athletes:
            stats = self.db.get_athlete_stats(a["id"], 7)
            writer.writerow([
                a["full_name"], a["age_group"], a["team"],
                a.get("survey_streak", 0), a.get("total_surveys", 0),
                round(stats.get("avg_sleep", 0) or 0, 1),
                round(stats.get("avg_stress", 0) or 0, 1),
                round(stats.get("avg_fatigue", 0) or 0, 1),
                round(stats.get("avg_hr", 0) or 0, 1),
                a.get("last_active", "—")
            ])

        output.seek(0)
        await q.message.reply_document(
            document=output.getvalue().encode("utf-8-sig"),
            filename=f"report_{date.today().strftime('%Y%m%d')}.csv",
            caption="📊 Отчет по спортсменам"
        )

    async def export_questionnaires_xlsx(self, update, ctx):
        """Экспорт анкет всех спортсменов в Excel (общая справка для врача)."""
        q = update.callback_query
        await q.answer()
        if q.from_user.id not in ADMIN_TELEGRAM_IDS:
            return

        athletes = self.db.get_all_athletes()
        from openpyxl.utils import get_column_letter
        wb = Workbook()
        ws = wb.active
        ws.title = "Анкеты"

        hf = Font(bold=True, size=11, color="FFFFFF")
        hfl = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        hdr_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
        thin_border = Border(left=Side(style="thin"), right=Side(style="thin"), top=Side(style="thin"), bottom=Side(style="thin"))

        # поля анкеты (ключ -> заголовок)
        fields = [
            ("age", "Возраст"), ("gender", "Пол"), ("phone", "Телефон"), ("birth_date", "Дата рождения"), ("position", "Позиция"),
            ("level", "Уровень"), ("experience", "Стаж"), ("height", "Рост"),
            ("weight", "Вес"), ("trauma_12m", "Травмы 12мес"),
            ("trauma_12m_detail", "Травмы детали"), ("zones", "Проблемные зоны"),
            ("pain_now", "Боль сейчас"), ("pain_now_detail", "Боль детали"),
            ("chronic", "Рецидивы"), ("chronic_detail", "Рецидивы детали"),
            ("surgery", "Операции"), ("surgery_detail", "Операции детали"), ("surgery_date", "Операции даты"),
            ("meds", "Лекарства/БАДы"), ("meds_detail", "БАДы детали"),
            ("allergies", "Аллергии"), ("allergies_detail", "Аллергии детали"),
            ("train_count", "Трен/нед"), ("train_duration", "Длит. трен"),
            ("season", "Сезон"), ("form_score", "Форма"), ("sleep_score", "Сон (анкета)"),
            ("warmup", "Разминка"), ("recovery", "Восстановление"),
            ("water", "Вода л"), ("diet", "Питание"), ("pre_meal", "За сколько ест"),
            ("supplements", "Спортпит"), ("motivation", "Мотивация"),
            ("stress", "Внешний стресс"), ("match_state", "Состояние перед матчем"),
            ("reinjury_fear", "Страх травмы"), ("goal", "Цель"), ("wish", "Пожелания"),
        ]

        # ---- Определяем блоки анкеты (для читабельной двухуровневой шапки) ----
        # блок: (название, цвет, список ключей)
        blocks = [
            ("ОБЩИЕ ДАННЫЕ", "4472C4", ["age","gender","phone","birth_date","position","level","experience","height","weight"]),
            ("ТРАВМЫ / ЗДОРОВЬЕ", "70AD47", ["trauma_12m","trauma_12m_detail","zones","pain_now","pain_now_detail",
                                                "chronic","chronic_detail","surgery","surgery_detail","surgery_date",
                                                "meds","meds_detail","allergies","allergies_detail"]),
            ("ТРЕНИРОВКИ", "ED7D31", ["train_count","train_duration","season","form_score","sleep_score","warmup","recovery"]),
            ("ПИТАНИЕ", "FFC000", ["water","diet","pre_meal","supplements"]),
            ("ПСИХОЛОГИЯ", "A9A9A9", ["motivation","stress","match_state","reinjury_fear","goal","wish"]),
        ]
        # индекс начала каждой колонки-поля: базовые (ФИО,Команда,Группа,Статус) = 1-4, поля с 5
        key_col = {}
        ci = 5
        for bl in blocks:
            for k in bl[2]:
                key_col[k] = ci
                ci += 1
        total_cols = ci - 1  # 4 базовых + все поля

        def _col_letter(n):
            return get_column_letter(n)

        ws.merge_cells(f"A1:{_col_letter(total_cols)}1")
        c = ws.cell(row=1, column=1, value="🏀 ЧБК — АНКЕТЫ СПОРТСМЕНОВ")
        c.font = Font(bold=True, size=14, color="1F4E79")
        ws.merge_cells(f"A2:{_col_letter(total_cols)}2")
        c = ws.cell(row=2, column=1, value=f"Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')} | Всего: {len(athletes)}")
        c.font = Font(bold=True, size=10, color="808080")

        # ---- Двухуровневая шапка: строка 4 = блоки, строка 5 = поля ----
        r_hdr = 4
        # базовые колонки 1-4
        base_block_cols = [("СПОРТСМЕН", "4472C4", 4)]
        # блоки-колонки
        block_ranges = []
        cur = 5
        for bname, bcolor, keys in blocks:
            n_keys = len(keys)
            block_ranges.append((bname, bcolor, cur, cur + n_keys - 1))
            cur += n_keys
        # мержим блоки в строке 4
        for bname, bcolor, c0, c1 in block_ranges:
            ws.merge_cells(start_row=r_hdr, start_column=c0, end_row=r_hdr, end_column=c1)
        # базовые 4 колонки объединяем в "Спортсмен"
        ws.merge_cells(start_row=r_hdr, start_column=1, end_row=r_hdr, end_column=4)
        # заполняем заголовки блоков
        from openpyxl.styles import PatternFill as _PF2
        def _block_cell(col, text, color):
            cell = ws.cell(row=r_hdr, column=col, value=text)
            cell.font = Font(bold=True, size=10, color="FFFFFF")
            cell.fill = PatternFill(start_color=color, end_color=color, fill_type="solid")
            cell.alignment = hdr_align
            cell.border = thin_border
            return cell
        _block_cell(1, "СПОРТСМЕН", "4472C4")
        for bname, bcolor, c0, c1 in block_ranges:
            _block_cell(c0, bname, bcolor)
        ws.row_dimensions[r_hdr].height = 20

        # строка 5 = названия полей
        r2 = 5
        base_headers = ["ФИО", "Команда", "Группа", "Статус"]
        for ci2, h in enumerate(base_headers, 1):
            c = ws.cell(row=r2, column=ci2, value=h)
            c.font = hf
            c.fill = hfl
            c.alignment = hdr_align
            c.border = thin_border
        for key, h in fields:
            ci2 = key_col[key]
            c = ws.cell(row=r2, column=ci2, value=h)
            # подкрасить заголовок поля цветом блока
            for bname, bcolor, c0, c1 in block_ranges:
                if c0 <= ci2 <= c1:
                    c.fill = PatternFill(start_color=bcolor, end_color=bcolor, fill_type="solid")
            c.font = hf
            c.alignment = hdr_align
            c.border = thin_border
        ws.freeze_panes = f"A6"
        ws.auto_filter.ref = f"A{r2}:{_col_letter(total_cols)}{r2}"

        r = 6
        zebra = False
        for a in athletes:
            qd = self.db.get_questionnaire(a["id"]) or {}
            is_done = bool(qd.get("completed_at"))
            status = "✅ Заполнена" if is_done else "⬜ Не заполнена"
            row_vals = [a["full_name"], a.get("team", ""), a.get("age_group", ""), status]
            for key, _ in fields:
                v = qd.get(key, "")
                if isinstance(v, float):
                    v = round(v, 1)
                row_vals.append(v if v is not None else "")
            row_fill = PatternFill(start_color="F7F9FC", end_color="F7F9FC", fill_type="solid") if zebra else None
            for ci, v in enumerate(row_vals, 1):
                c = ws.cell(row=r, column=ci, value=v)
                c.border = thin_border
                if row_fill:
                    c.fill = row_fill
                if ci == 4:
                    c.font = Font(bold=True, size=10)
                    c.alignment = Alignment(horizontal="center")
            r += 1
            zebra = not zebra

        # ширины
        from openpyxl.utils import get_column_letter
        ws.column_dimensions["A"].width = 24
        ws.column_dimensions["B"].width = 14
        ws.column_dimensions["C"].width = 10
        ws.column_dimensions["D"].width = 16
        for ci in range(5, total_cols + 1):
            ws.column_dimensions[get_column_letter(ci)].width = 18

        # ---- Лист «Легенда»: расшифровка значений для читателя ----
        ws_l = wb.create_sheet("Легенда")
        ws_l.column_dimensions["A"].width = 34
        ws_l.column_dimensions["B"].width = 70
        lr = 1
        c = ws_l.cell(lr, 1, "ЧТО ЗНАЧИТ КАЖДОЕ ПОЛЕ В АНКЕТЕ")
        c.font = Font(bold=True, size=13, color="1F4E79"); lr += 1
        c = ws_l.cell(lr, 1, "Цвет поля в шапке = блок анкеты. Краткая расшифровка ниже.")
        c.font = Font(size=9, italic=True, color="808080"); lr += 2
        legend_rows = [
            ("СПОРТСМЕН", "ФИО, команда, возрастная группа, заполнена ли анкета (✅/⬜)."),
            ("Возраст / Дата рождения", "Полные годы и дата рождения для мед. карты."),
            ("Телефон", "Номер для связи (спортсмен видит у себя)."),
            ("Пол", "М / Ж. Для девушек в ежедневном опросе добавляются вопросы цикла."),
            ("Позиция / Уровень / Стаж", "PG/SG/SF/PF/C/Универсал; уровень игры; стаж в баскетболе."),
            ("Рост / Вес", "см / кг (например 185/82)."),
            ("Травмы 12 мес", "Да/Нет + детали: какие травмы и как лечил."),
            ("Проблемные зоны", "Какие зоны беспокоили (голеностоп, колени, спина…)."),
            ("Боль сейчас", "Есть ли боль прямо сейчас + где и как давно."),
            ("Рецидивы", "Повторяющаяся травма: какая, как часто обостряется."),
            ("Операции", "Да/Нет + что за операции + дата/год."),
            ("Лекарства / БАДы", "Принимает ли лекарства/БАД/физиотерапию + какие."),
            ("Аллергии", "Да/Нет + на что аллергия (если Да)."),
            ("Тренировки/нед", "Сколько тренировок в неделю (0-10)."),
            ("Длит. тренировки", "В минутах (30-180)."),
            ("Сезон", "Сезон / Предсезонка / Межсезонье / Пауза."),
            ("Форма (1-10)", "10 = отличная форма, 1 = плохая."),
            ("Сон в анкете (1-10)", "10 = отличный сон, 1 = плохой (базовое качество сна)."),
            ("Разминка / Восстановление", "Практикует ли разминку/заминку и восстановительные дни."),
            ("Вода (л) / Питание / За сколько ест", "Пьёт воды в день; есть ли план питания; время до тренировки."),
            ("Спортпит", "Протеин/Креатин/Хондропротекторы/Изотоники/Кофеин/Другое (через запятую)."),
            ("Мотивация (1-10)", "10 = максимальная мотивация."),
            ("Внешний стресс / Состояние перед матчем", "Мешают ли внешние факторы; тревога перед играми."),
            ("Страх травмы", "Боится ли повторной травмы, насколько сильно."),
            ("Цель / Пожелания", "Главная цель и особая нужда в помощи (для врача)."),
        ]
        for col1, col2 in legend_rows:
            c = ws_l.cell(lr, 1, col1); c.font = Font(bold=True, size=10)
            c = ws_l.cell(lr, 2, col2); c.font = Font(size=10)
            lr += 1
        lr += 1
        c = ws_l.cell(lr, 1, "ЦВЕТА БЛОКОВ (в шапке таблицы Анкеты)")
        c.font = Font(bold=True, size=11); lr += 1
        for bname, bcolor, *_ in blocks:
            cc = ws_l.cell(lr, 1, bname)
            cc.fill = PatternFill(start_color=bcolor, end_color=bcolor, fill_type="solid")
            cc.font = Font(bold=True, size=10, color="FFFFFF")
            lr += 1

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        await q.message.reply_document(
            document=output,
            filename=f"ankety_{date.today().strftime('%Y%m%d')}.xlsx",
            caption="📋 Анкеты спортсменов (справка для врача)"
        )

    # ==================== ЭКСПОРТ В EXCEL ====================

    async def report_export_menu(self, update, ctx):
        q = update.callback_query
        await q.answer()
        if q.from_user.id not in ADMIN_TELEGRAM_IDS:
            return

        teams = set(a["team"] for a in self.db.get_all_athletes())
        buttons = [[(f"👥 Все команды", "report_team_all")]]
        for t in sorted(teams):
            buttons.append([(f"🏀 {t}", f"report_team_{t}")])
        buttons.append([(f"📋 Экспорт анкет", "export_q")])
        buttons.append([(f"🔙 Назад", "main_menu")])

        await q.edit_message_text(
            "📊 *Экспорт в Excel*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nВыбери команду:",
            reply_markup=self.kb(buttons), parse_mode="Markdown"
        )

    async def report_choose_period(self, update, ctx):
        q = update.callback_query
        await q.answer()
        if q.from_user.id not in ADMIN_TELEGRAM_IDS:
            return

        team = q.data.replace("report_team_", "")
        state = self.get_state(q.from_user.id)
        state["data"]["report_team"] = team

        buttons = [
            [("📅 Сегодня", f"report_period_1")],
            [("📅 Последние 7 дней", f"report_period_7")],
            [("📅 Последние 30 дней", f"report_period_30")],
            [("📅 Последние 90 дней", f"report_period_90")],
            [("🔙 Назад", "report_export_menu")]
        ]

        display_team = "Все команды" if team == "all" else team
        await q.edit_message_text(
            f"📊 *Выбери период*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nКоманда: *{display_team}*\n\nЗа какой период выгрузить данные?",
            reply_markup=self.kb(buttons), parse_mode="Markdown"
        )

    async def generate_xlsx_report(self, update, ctx):
        q = update.callback_query
        await q.answer()
        if q.from_user.id not in ADMIN_TELEGRAM_IDS:
            return

        days = int(q.data.replace("report_period_", ""))
        state = self.get_state(q.from_user.id)
        team = state.get("data", {}).get("report_team", "all")
        team_filter = None if team == "all" else team

        period_names = {1: "сегодня", 7: "7 дней", 30: "30 дней", 90: "90 дней"}

        wb = Workbook()

        # ---- Стили ----
        hf = Font(bold=True, size=11, color="FFFFFF")
        hfl = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        hdr_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
        thin_border = Border(
            left=Side(style="thin"), right=Side(style="thin"),
            top=Side(style="thin"), bottom=Side(style="thin")
        )
        good_fill = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")
        warn_fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
        crit_fill = PatternFill(start_color="F8D7DA", end_color="F8D7DA", fill_type="solid")
        gray_fill = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")
        # Лёгкая зебра для блоков дней (чередование) + жирная граница стыка дней
        zebra_fill = PatternFill(start_color="EAF1FB", end_color="EAF1FB", fill_type="solid")
        zebra_hdr_fill = PatternFill(start_color="8EAADB", end_color="8EAADB", fill_type="solid")
        day_side = Side(style="medium", color="1F4E79")
        day_right = Border(left=thin_border.left, right=day_side, top=thin_border.top, bottom=thin_border.bottom)
        day_left = Border(left=day_side, right=thin_border.right, top=thin_border.top, bottom=thin_border.bottom)
        title_font = Font(bold=True, size=14, color="1F4E79")
        subtitle_font = Font(bold=True, size=10, color="808080")
        bold_font = Font(bold=True, size=10)
        normal_font = Font(size=10)
        center = Alignment(horizontal="center", vertical="center", wrap_text=True)
        wrap = Alignment(wrap_text=True, vertical="top")

        def cell(ws, row, col, value, font=None, fill=None, align=None, border=thin_border):
            c = ws.cell(row=row, column=col, value=value)
            if font: c.font = font
            if fill is not None: c.fill = fill
            if align: c.alignment = align
            if border: c.border = border
            return c

        # ---- Получаем данные по дням ----
        # Сводка по командам (средние для шапки) и детальные опросы
        summary_by_team = {}
        athletes = self.db.get_team_stats_period(team_filter, days)
        for a in athletes:
            t = a.get("team", "?")
            summary_by_team.setdefault(t, []).append(a)

        # Детальные опросы по дням
        wellness_rows = self.db.get_wellness_by_period(days, team_filter)

        # Группируем детальные опросы по командам
        rows_by_team = {}
        for row in wellness_rows:
            t = row.get("team", "?")
            rows_by_team.setdefault(t, []).append(row)

        # Команды для листов
        all_teams = sorted(set(list(summary_by_team.keys()) + list(rows_by_team.keys())))
        if not all_teams:
            await q.edit_message_text("❌ Нет данных за выбранный период.", reply_markup=self.kb([[(f"🔙 Назад", "main_menu")]]))
            return

        # ---- ЛИСТ СВОДКА (первый) ----
        ws0 = wb.active
        ws0.title = "Сводка"
        ws0.merge_cells("A1:H1")
        cell(ws0, 1, 1, "📋 ЧБК — СВОДНЫЙ ОТЧЁТ", font=title_font, border=None)
        ws0.merge_cells("A2:H2")
        cell(ws0, 2, 1, f"Период: {period_names[days]} | Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}", font=subtitle_font, border=None)

        r = 4
        sum_headers = ["Команда", "Спортсменов", "С опросами", "Опросов", "Участие %", "Сон ср.", "Стресс ср.", "Утомл. ср."]
        for ci, h in enumerate(sum_headers, 1):
            cell(ws0, r, ci, h, font=hf, fill=hfl, align=hdr_align)
        r += 1

        for t in all_teams:
            ta = summary_by_team.get(t, [])
            rows = rows_by_team.get(t, [])
            total = len(ta)
            with_data = sum(1 for a in ta if a.get("days_count", 0) > 0)
            if total > 0 and with_data > 0:
                # средние из сводки
                avg_s = sum((a.get("avg_sleep") or 0) for a in ta if a.get("avg_sleep")) / sum(1 for a in ta if a.get("avg_sleep"))
                avg_st = sum((a.get("avg_stress") or 0) for a in ta if a.get("avg_stress")) / sum(1 for a in ta if a.get("avg_stress"))
                avg_f = sum((a.get("avg_fatigue") or 0) for a in ta if a.get("avg_fatigue")) / sum(1 for a in ta if a.get("avg_fatigue"))
                particip = round(with_data / total * 100) if total else 0
            else:
                avg_s = avg_st = avg_f = 0
                particip = 0
            cell(ws0, r, 1, t, font=bold_font)
            cell(ws0, r, 2, total, align=center)
            cell(ws0, r, 3, with_data, align=center)
            cell(ws0, r, 4, len(rows), align=center)
            cell(ws0, r, 5, f"{particip}%", align=center)
            cell(ws0, r, 6, f"{avg_s:.1f}", align=center, fill=self._xl_score_fill(avg_s))
            cell(ws0, r, 7, f"{avg_st:.1f}", align=center, fill=self._xl_score_fill(avg_st))
            cell(ws0, r, 8, f"{avg_f:.1f}", align=center, fill=self._xl_score_fill(avg_f))
            r += 1

        # Итоговая строка по всем командам
        total_ath = sum(len(summary_by_team.get(t, [])) for t in all_teams)
        total_rows = sum(len(rows_by_team.get(t, [])) for t in all_teams)
        cell(ws0, r, 1, "ИТОГО", font=Font(bold=True, size=11))
        cell(ws0, r, 2, total_ath, font=bold_font, align=center)
        cell(ws0, r, 3, "-", align=center)
        cell(ws0, r, 4, total_rows, font=bold_font, align=center)
        for ci in range(5, 9):
            cell(ws0, r, ci, "-", align=center)
        r += 1

        # эмблема клуба в правом верхнем углу листа «Сводка»
        self._add_logo_to_ws(ws0, anchor="J3", size=90)

        for ci in range(1, 9):
            ws0.column_dimensions[chr(64+ci)].width = 18

        # ---- ЛИСТЫ ПО КОМАНДАМ: детальные опросы по дням ----
        for t in all_teams:
            ws = wb.create_sheet(title=t[:31])
            ta = summary_by_team.get(t, [])
            rows = rows_by_team.get(t, [])

            r = 1
            ws.merge_cells("A1:L1")
            cell(ws, r, 1, f"🏀 ЧБК — Опросы по дням: {t}", font=title_font, border=None)
            r += 1
            ws.merge_cells(f"A{r}:L{r}")
            cell(ws, r, 1, f"Период: {period_names[days]} | Дата отчёта: {datetime.now().strftime('%d.%m.%Y %H:%M')} | Всего спортсменов: {len(ta)}", font=subtitle_font, border=None)
            r += 1

            # ===== БЛОК: МАТРИЦА «ИГРОК-СТРОКА, ДЕНЬ-СТОЛБЕЦ, ПОДСТОЛБЦЫ=ПОКАЗАТЕЛИ» =====
            from openpyxl.utils import get_column_letter
            # Ключи row: full_name, team, age_group, athlete_id, survey_date,
            # sleep_score, stress_score, fatigue_score, muscle_soreness, mood_score,
            # resting_hr, hrv_ms, had_training, sRPE_score, cycle_day, cycle_phase, complaints
            metrics = ["Сон", "Стресс", "Утомл", "Боль", "Настр", "Пульс", "sRPE", "Hooper"]
            namedays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

            def _m_hindex(r):
                v = [r.get("sleep_score"), r.get("stress_score"), r.get("fatigue_score"),
                     r.get("muscle_soreness"), r.get("mood_score")]
                v = [x for x in v if x is not None]
                return sum(v) if v else None

            # собираем по игроку: name -> {date: row}; даты — общий упорядоченный набор
            athlete_days = {}
            all_dates = []
            for row in rows:
                name = row.get("full_name", "?")
                d = row.get("survey_date")
                athlete_days.setdefault(name, {})[str(d)] = row
                if str(d) not in all_dates:
                    all_dates.append(str(d))
            all_dates.sort()

            # заголовок команды уже есть на r-2 (merge A:L). Дальше легенда + шапка.
            # ЛЕГЕНДА (с цветными ●)
            n_total_cols = 1 + len(all_dates) * len(metrics) + 1  # Игрок | дни*метрики | Ср.
            legend_top = n_total_cols if n_total_cols > 13 else 13
            r += 1
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=legend_top)
            cell(ws, r, 1, "ЛЕГЕНДА   (показатели по дням, 7 = отлично)", font=Font(bold=True, size=11, color="1F4E79"), border=None)
            r += 1

            circ_colors = ["1E7B1E", "BF8F00", "C00000"]
            # Строка 3: шкала 1-7
            cell(ws, r, 1, "Шкала 1-7:", font=Font(size=9, bold=True, color="404040"), border=None)
            seq1 = [(circ_colors[0], "6-7 норма"), (circ_colors[1], "4-5 вни.мание"), (circ_colors[2], "<4 критично")]
            sc = 3
            for col_c, lab in seq1:
                cc = cell(ws, r, sc, "●", font=Font(bold=True, size=11, color=col_c), border=None)
                cell(ws, r, sc + 1, lab, font=normal_font, border=None)
                sc += 3
            r += 1
            # Строка: Пульс + Hooper + пропуск
            cell(ws, r, 1, "Пульс:", font=Font(size=9, bold=True, color="404040"), border=None)
            seq2 = [(circ_colors[2], ">70"), (circ_colors[1], "61-70"), (circ_colors[0], "<=60")]
            sc = 3
            for col_c, lab in seq2:
                cell(ws, r, sc, "●", font=Font(bold=True, size=11, color=col_c), border=None)
                cell(ws, r, sc + 1, lab, font=normal_font, border=None)
                sc += 3
            cell(ws, r, 12, "Hooper:", font=Font(size=9, bold=True, color="404040"), border=None)
            seq3 = [(circ_colors[0], ">=28"), (circ_colors[1], "20-27"), (circ_colors[2], "<20")]
            sc = 14
            for col_c, lab in seq3:
                cell(ws, r, sc, "●", font=Font(bold=True, size=11, color=col_c), border=None)
                cell(ws, r, sc + 1, lab, font=normal_font, border=None)
                sc += 3
            cell(ws, r, 23, "Пропуск:", font=Font(size=9, bold=True, color="404040"), border=None)
            cc = cell(ws, r, 25, "", fill=gray_fill)
            cell(ws, r, 26, "не заполнен · «Ср.»=Hooper", font=normal_font, border=None)
            r += 1

            # Двухуровневая шапка: строка дат (SR_DATE), строка метрик (SR_METR)
            SR_DATE = r
            SR_METR = r + 1
            # Игрок — объединён вертикально по двум строкам шапки
            ws.merge_cells(start_row=SR_DATE, start_column=1, end_row=SR_METR, end_column=1)
            cell(ws, SR_DATE, 1, "Игрок", font=hf, fill=hfl, align=hdr_align)
            col = 2
            for di, dt in enumerate(all_dates):
                try:
                    from datetime import datetime as _ddt
                    wd = namedays[_ddt.strptime(dt, "%Y-%m-%d").weekday()]
                    datelabel = dt[5:] + "\n" + wd
                except Exception:
                    datelabel = dt[5:]
                # лёгкая зебра: чётные блоки дней — светло-голубой фон у ячейки даты (и её блока)
                hdr_fill = zebra_hdr_fill if di % 2 == 1 else hfl
                ws.merge_cells(start_row=SR_DATE, start_column=col, end_row=SR_DATE, end_column=col + len(metrics) - 1)
                cell(ws, SR_DATE, col, datelabel, font=hf, fill=hdr_fill, align=hdr_align)
                # жирная граница справа у последней колонки блока дня (визуальный стык дней)
                last_col = col + len(metrics) - 1
                for k, m in enumerate(metrics):
                    bd = day_right if (col + k) == last_col else thin_border
                    cell(ws, SR_METR, col + k, m, font=Font(bold=True, size=8, color="FFFFFF"), fill=hfl, align=hdr_align, border=bd)
                    if (col + k) == last_col:
                        # жирный правый борт и у ячейки даты
                        dc = ws.cell(row=SR_DATE, column=last_col)
                        dc.border = Border(left=thin_border.left, right=day_side, top=thin_border.top, bottom=thin_border.bottom)
                col += len(metrics)
            # колонка «Ср.» объединена по двум строкам
            ws.merge_cells(start_row=SR_DATE, start_column=col, end_row=SR_METR, end_column=col)
            cell(ws, SR_DATE, col, "Ср.", font=hf, fill=hfl, align=hdr_align)
            SRCOL = col
            r = SR_METR + 1

            ws.freeze_panes = f"B{r}"

            if not athlete_days:
                ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=SRCOL)
                cell(ws, r, 1, "Нет опросов за данный период.", font=Font(italic=True, color="808080"), border=None)
                r += 1
            else:
                for name in sorted(athlete_days.keys()):
                    daysmap = athlete_days[name]
                    # по умолчанию числовые значения; пустой день = серый
                    col = 2
                    ho_list = []
                    for dt in all_dates:
                        row = daysmap.get(dt)
                        if row is None:
                            for k in range(len(metrics)):
                                cc = cell(ws, r, col + k, "", border=thin_border)
                                cc.fill = gray_fill
                                if k == len(metrics) - 1:
                                    cc.border = day_right
                            col += len(metrics)
                            continue
                        for k, m in enumerate(metrics):
                            if m == "Сон": v = row.get("sleep_score")
                            elif m == "Стресс": v = row.get("stress_score")
                            elif m == "Утомл": v = row.get("fatigue_score")
                            elif m == "Боль": v = row.get("muscle_soreness")
                            elif m == "Настр": v = row.get("mood_score")
                            elif m == "Пульс": v = row.get("resting_hr")
                            elif m == "sRPE": v = row.get("sRPE_score")
                            elif m == "Hooper": v = _m_hindex(row)
                            v = v if v is not None else ""
                            cc = cell(ws, r, col + k, v, align=center)
                            if isinstance(v, (int, float)):
                                if m == "Пульс":
                                    cc.fill = self._xl_hr_fill(v)
                                elif m == "sRPE":
                                    cc.fill = self._xl_srpe_fill(v)
                                else:
                                    cc.fill = self._xl_score_fill(v)
                                if m == "Hooper":
                                    ho_list.append(v)
                        # жирная граница справа у последней колонки блока дня (стык дней)
                        last_col = col + len(metrics) - 1
                        ws.cell(row=r, column=last_col).border = day_right
                        col += len(metrics)
                    # средний Hooper в колонке «Ср.»
                    if ho_list:
                        av = round(sum(ho_list) / len(ho_list), 1)
                        cc = cell(ws, r, SRCOL, av, align=center, font=Font(bold=True, size=10))
                        cc.fill = self._xl_score_fill(av)
                    else:
                        cell(ws, r, SRCOL, "", border=thin_border)
                    # имя игрока слева
                    cell(ws, r, 1, name, font=bold_font)
                    r += 1

            # ширина колонок
            ws.column_dimensions["A"].width = 22
            first_big = min(len(all_dates) * len(metrics) + 2, 60)
            for i in range(2, first_big):
                cl = get_column_letter(i)
                if ws.column_dimensions[cl].width < 5.2:
                    ws.column_dimensions[cl].width = 5.2
            ws.column_dimensions[get_column_letter(SRCOL)].width = 8

            # эмблема клуба в правом верхнем углу листа команды (после последней колонки матрицы)
            self._add_logo_to_ws(ws, anchor=f"{get_column_letter(SRCOL + 2)}{SR_DATE}", size=80)

        # Убираем move_worksheet - Сводка уже первый лист
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        await q.message.reply_document(
            document=output,
            filename=f"report_{date.today().strftime('%Y%m%d')}.xlsx",
            caption=f"📊 Excel-отчёт за {period_names.get(days, f'{days} дн.')} — детализация по дням"
        )


    def _xl_score_fill(self, val):
        """Возврат заливки по шкале 1-7 (7=отлично). None для пустых."""
        if val is None:
            return None
        if val >= 6.0:
            return PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")  # зелёный
        if val >= 4.0:
            return PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")  # жёлтый
        return PatternFill(start_color="F8D7DA", end_color="F8D7DA", fill_type="solid")      # красный

    def _xl_hr_fill(self, val):
        """Заливка по пульсу покоя: >70 критично, 61-70 внимание, <=60 норма."""
        if val is None:
            return None
        if val > 70:
            return PatternFill(start_color="F8D7DA", end_color="F8D7DA", fill_type="solid")
        if val > 60:
            return PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
        return PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")

    def _xl_srpe_fill(self, val):
        """Заливка по sRPE (1-10): низкая/средняя/высокая нагрузка."""
        if val is None:
            return None
        if val >= 8:
            return PatternFill(start_color="F8D7DA", end_color="F8D7DA", fill_type="solid")
        if val >= 5:
            return PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
        return PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")

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
        """Показать информацию о текущей фазе цикла с научной базой."""
        q = update.callback_query
        await q.answer()
        athlete = self.db.get_athlete_by_telegram_id(q.from_user.id)
        if not athlete:
            return

        # Берём последний день цикла из опросов
        cycle_history = self.db.get_cycle_history(athlete["id"], 30)
        if not cycle_history:
            await q.edit_message_text(
                "📅 *Менструальный цикл*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "Нет данных о цикле. Заполняй день цикла в ежедневном опросе, и здесь появится информация.",
                reply_markup=self.kb([[(f"🔙 Назад", "main_menu")]]), parse_mode="Markdown"
            )
            return

        last = cycle_history[0]
        cd = last.get("cycle_day")
        cl = last.get("cycle_length") or athlete.get("cycle_length_default", CYCLE_LENGTH_MEDIAN)

        if cd is None:
            await q.edit_message_text(
                "📅 Ещё нет данных о дне цикла. Укажи его в опросе.",
                reply_markup=self.kb([[(f"🔙 Назад", "main_menu")]]), parse_mode="Markdown"
            )
            return

        # Используем новый модуль cycle_medicine для детальной информации
        info = get_cycle_phase_info(cd, cl)

        text = (
            f"📅 *Менструальный цикл*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 {athlete['full_name']}\n"
            f"📋 {athlete['age_group']}\n\n"
        )

        if info:
            text += info["text"]
        else:
            text += "⚠️ Не удалось определить фазу.\n\n"

        text += "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"

        # Возрастные рекомендации
        age_info = AGE_SPECIFIC.get(athlete.get("age_group", ""))
        if age_info:
            text += f"\n*Для {athlete['age_group']}:*\n{age_info.training_note}\n\n🚩 {age_info.concerns}"

        # История последних дней
        if len(cycle_history) > 1:
            text += "\n*Последние записи:*\n"
            for entry in cycle_history[:7]:
                d = entry.get("survey_date", "?")
                day = entry.get("cycle_day", "—")
                ph = entry.get("cycle_phase", "—")
                text += f"  📅 {d}: день {day} ({ph})\n"

        await q.edit_message_text(
            text,
            reply_markup=self.kb([[(f"🔙 Назад", "main_menu")]]),
            parse_mode="Markdown"
        )

    async def ask_cycle_day(self, update, ctx):
        """Спрашиваем день цикла у девушек после опроса."""
        q = update.callback_query if hasattr(update, 'callback_query') and update.callback_query else None
        user_id = update.effective_user.id if update.effective_user else (q.from_user.id if q else good_fill)
        if not user_id:
            return
        state = self.get_state(user_id)
        athlete = state.get("data", {}).get("athlete")
        if not athlete:
            return

        state["step"] = "survey_cycle"

        # Берём последний cycle_day и длину цикла
        history = self.db.get_cycle_history(athlete["id"], 60)
        last_day = history[0]["cycle_day"] if history else None
        cl = athlete.get("cycle_length_default", 28)

        # Подсказка по фазе
        from cycle_medicine import get_cycle_phase_info
        next_day = (last_day + 1) if last_day else 1
        if next_day > cl:
            next_day = 1
        phase_info = get_cycle_phase_info(next_day, cl)
        phase_text = f" → предполагается {phase_info['phase'].name_ru.lower()}" if phase_info else ""

        hint = f"\n\n💡 Предп. день: {next_day}{phase_text}" if last_day else ""

        buttons = [
            [(f"🩸 1-5", "cycle_1"), (f"6-10", "cycle_6")],
            [(f"11-15", "cycle_11"), (f"16-20", "cycle_16")],
            [(f"21-25", "cycle_21"), (f"26+", "cycle_26")],
            [(f"✏️ Ввести число", "cycle_custom"), (f"❌ Нет данных", "cycle_none")],
        ]

        text = (
            f"📅 *День цикла* (цикл ~{cl} дн.){hint}\n\n"
            f"Какой сегодня день цикла?\n\n"
            f"*Как посчитать:* день 1 — это первый день месячных. "
            f"Сегодняшний день = сколько дней прошло с их начала.\n"
            f"*Пример:* месячные начались в понедельник, сегодня среда — значит день 3.\n\n"
            f"Если ещё не ведёшь дневник — просто выбери примерный день или нажми «Нет данных»."
        )

        if q:
            await q.edit_message_text(text, reply_markup=self.kb(buttons), parse_mode="Markdown")
        else:
            await update.message.reply_text(text, reply_markup=self.kb(buttons), parse_mode="Markdown")

    async def _ask_cycle_day_custom(self, update, ctx):
        """Запросить день цикла ручным вводом числа (кнопка «Ввести число»)."""
        q = update.callback_query
        await q.answer()
        state = self.get_state(q.from_user.id)
        athlete = state.get("data", {}).get("athlete")
        cl = (athlete.get("cycle_length_default") or 28) if athlete else 28
        state["step"] = "survey_cycle"
        await q.edit_message_text(
            f"📅 *День цикла* (цикл ~{cl} дн.)\n\n"
            f"Введи число — какой сегодня день цикла (1-{cl}).\n"
            f"*Подсказка:* день 1 = первый день месячных."
        )

    async def set_cycle_day(self, update, ctx):
        """Сохранить день цикла из кнопок."""
        q = update.callback_query
        await q.answer()
        data = q.data
        user_id = q.from_user.id
        state = self.get_state(user_id)
        athlete = state.get("data", {}).get("athlete")
        if not athlete or state.get("step") != "survey_cycle":
            # Потеря состояния — предлагаем начать заново
            await q.edit_message_text(
                "❌ *Опрос прерван* (возможно бот перезагружался).\n\nХотите начать заново?",
                reply_markup=self.kb([[(f"🔄 Пройти опрос", "do_survey")],
                                      [(f"🏠 Главное меню", "main_menu")]]),
                parse_mode="Markdown"
            )
            return

        from cycle_medicine import get_cycle_phase
        if data == "cycle_none":
            await self._ask_complaints(update, ctx)
            return

        ranges = {"cycle_1": 3, "cycle_6": 8, "cycle_11": 13,
                  "cycle_16": 18, "cycle_21": 23, "cycle_26": 28}
        cycle_day = ranges.get(data, 1)
        cycle_length = athlete.get("cycle_length_default", 28)
        phase_key, phase_info = get_cycle_phase(cycle_day, cycle_length)

        state["data"]["cycle_day"] = cycle_day
        state["data"]["cycle_length"] = cycle_length
        state["data"]["cycle_phase"] = phase_info.name_ru if phase_info else ""
        await self._ask_complaints(update, ctx)

    async def _ask_complaints(self, update, ctx):
        """Спросить про жалобы перед завершением опроса."""
        q = update.callback_query if hasattr(update, 'callback_query') and update.callback_query else None
        user_id = update.effective_user.id if update.effective_user else q.from_user.id
        state = self.get_state(user_id)
        state["step"] = "survey_complaints"

        buttons = [
            [("💬 Ввести жалобу", "complaint_text")],
            [("✅ Нет жалоб", "complaint_none")],
        ]
        text = "💬 *Есть ли жалобы?*\n\nЧто беспокоит? Боли, дискомфорт, самочувствие?"

        if q:
            await q.edit_message_text(text, reply_markup=self.kb(buttons), parse_mode="Markdown")
        else:
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

    async def _ask_cycle_length(self, update, ctx):
        """Спросить длину цикла при первом заполнении."""
        q = update.callback_query if hasattr(update, 'callback_query') and update.callback_query else None
        user_id = update.effective_user.id if update.effective_user else q.from_user.id
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
        else:
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

        for admin_id in ADMIN_TELEGRAM_IDS:
            btn_name = self._first_name(athlete["full_name"])
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

    async def start_questionnaire(self, update, ctx):
        q = update.callback_query
        await q.answer()
        user_id = q.from_user.id
        athlete = self.db.get_athlete_by_telegram_id(user_id)
        if not athlete:
            await q.edit_message_text("❌ Сначала зарегистрируйся!", reply_markup=self.kb([[(f"📝 Регистрация", "start_reg")]]))
            return
        if self.db.has_questionnaire(athlete["id"]) and not self.db.has_incomplete_questionnaire(athlete["id"]):
            await q.edit_message_text("✅ *Анкета уже заполнена!*", parse_mode="Markdown", reply_markup=self.kb([[(f"🏠 Главное меню", "main_menu")]]))
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

    async def _q_resume_questionnaire(self, update, ctx, state):
        """Продолжить анкету с того шага, где остановился."""
        q = update.callback_query
        data = state.get("q_data", {})
        # Определяем следующий незаполненный шаг
        step_order = [
            ("q_age", "age"), ("q_phone", "phone"), ("q_birth_date", "birth_date"), ("q_gender", "gender"), ("q_position", "position"),
            ("q_level", "level"), ("q_experience", "experience"),
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
        if q.from_user.id not in ADMIN_TELEGRAM_IDS: return
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
        if q.from_user.id not in ADMIN_TELEGRAM_IDS: return
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
            text += f"😴 Сон: {w0.get('sleep_score','—')} | 😰 Стресс: {w0.get('stress_score','—')}\n"
            text += f"😩 Утомл: {w0.get('fatigue_score','—')} | 🤕 Боль: {w0.get('muscle_soreness','—')}\n"
            text += f"😊 Настроение: {w0.get('mood_score','—')} | ❤️ Пульс: {w0.get('resting_hr','—')}\n"
            if w0.get('sRPE_score') is not None:
                text += f"💪 Тренировка: {'Да' if w0.get('had_training') else 'Нет'} | sRPE: {w0.get('sRPE_score')}\n"
            if w0.get('cycle_phase'):
                text += f"🔄 Фаза цикла: {w0.get('cycle_phase')}\n"
            if w0.get('complaints'):
                text += f"💬 Жалобы: {w0.get('complaints')}\n"

            # Тренд Hooper за неделю (спарклайн)
            dates = []
            vals = []
            for w in reversed(last_wellness):
                h = sum(filter(None, [w.get('sleep_score'), w.get('stress_score'),
                                       w.get('fatigue_score'), w.get('muscle_soreness'), w.get('mood_score')]))
                dates.append(w['survey_date'][5:])
                vals.append(h)
            if len(vals) >= 2:
                text += f"\n📈 *Hooper за неделю:*\n"
                for i, h in enumerate(vals):
                    if h >= 28: e = "🟢"
                    elif h >= 20: e = "🟡"
                    else: e = "🔴"
                    text += f"{e}"
                text += "\n"
                text += " ".join(d[-2:] for d in dates) + "\n"
        else:
            text += "\n*Нет опросов за последние 7 дней.*\n"

        buttons = [[(f"🔙", "questionnaire_list"), (f"📊 Отчёт", "admin_report")]]
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=self.kb(buttons))

    async def athlete_list(self, update, ctx, page=0):
        q = update.callback_query
        await q.answer()
        if q.from_user.id not in ADMIN_TELEGRAM_IDS: return
        athletes = self.db.get_all_athletes()
        banned = set(x["id"] for x in self.db.get_banned_athletes())
        pp = 15
        total = len(athletes)
        s = page * pp
        e = min(s + pp, total)
        pa = athletes[s:e]
        text = f"👥 Список ({total}) Стр.{page+1}/{(total-1)//pp+1}\n\n"
        for a in pa:
            m = "🔒 " if a["id"] in banned else ""
            u = f" @{a['username']}" if a.get("username") else ""
            text += f"{m}{a['id']}. {a['full_name']}{u} | {a['team']}\n"
        btns = []
        nav = []
        if page > 0: nav.append(("⬅️", f"athlete_page_{page-1}"))
        if e < total: nav.append(("➡️", f"athlete_page_{page+1}"))
        if nav: btns.append(nav)
        btns.append([("🔙", "admin_manage")])
        await q.edit_message_text(text, reply_markup=self.kb(btns))

    async def show_ban_menu(self, update, ctx, page=0):
        q = update.callback_query
        await q.answer()
        if q.from_user.id not in ADMIN_TELEGRAM_IDS: return
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

    async def _send_daily_reminder(self, context):
        """Ежедневная рассылка напоминаний спортсменам."""
        if REMINDER_HOUR is None:
            return

        bot = context.bot

        athletes = self.db.get_all_athletes()
        sent = 0
        skipped = 0
        for a in athletes:
            if self.db.has_survey_today(a["id"]):
                skipped += 1
                continue
            try:
                await bot.send_message(
                    chat_id=a["telegram_id"],
                    text=(
                        "🏀 *ЧБК — Напоминание об опросе!*\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"👋 Привет, {self._first_name(a['full_name'])}!\n\n"
                        "📝 *Ежедневный опрос ещё не пройден!*\n"
                        "Всего 1 минута — и у доктора будут данные о твоём состоянии.\n\n"
                        f"🔥 Твоя серия: *{a.get('survey_streak', 0)} дней*\n\n"
                        "👉 Нажми /start чтобы открыть меню"
                    ),
                    parse_mode="Markdown"
                )
                sent += 1
            except Exception as e:
                logger.error(f"Reminder error for {a['full_name']} (tg={a['telegram_id']}): {e}")

        logger.info(f"Reminder sent to {sent}/{len(athletes)} ({skipped} already surveyed)")

        # Сводка врачу о прошедших/не прошедших опрос
        try:
            passed = [a for a in athletes if self.db.has_survey_today(a["id"])]
            not_passed = [a for a in athletes if not self.db.has_survey_today(a["id"])]
            summary = (
                f"📊 *Сводка по опросам на сегодня*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"✅ Прошли ({len(passed)}):\n"
            )
            if passed:
                for a in passed[:15]:
                    summary += f"  • {a['full_name']} ({a['team']})\n"
            else:
                summary += "  — ещё никто не прошёл\n"
            summary += f"\n❌ Не прошли ({len(not_passed)}):\n"
            if not_passed:
                for a in not_passed[:20]:
                    summary += f"  • {a['full_name']} ({a['team']})\n"
            else:
                summary += "  — все прошли! 🎉\n"
            if len(not_passed) > 20:
                summary += f"  • … и ещё {len(not_passed)-20}\n"

            for admin_id in ADMIN_TELEGRAM_IDS:
                try:
                    await bot.send_message(chat_id=admin_id, text=summary, parse_mode="Markdown")
                except Exception as e:
                    logger.error(f"Admin summary error: {e}")
        except Exception as e:
            logger.error(f"Doctor summary error: {e}")


    @staticmethod
    async def error_handler(update, context):
        """Global error handler."""
        logger.error("Exception: %s", context.error, exc_info=context.error)
        if update and update.effective_message:
            try:
                await update.effective_message.reply_text(
                    "\u26a0\ufe0f \u041f\u0440\u043e\u0438\u0437\u043e\u0448\u043b\u0430 \u043e\u0448\u0438\u0431\u043a\u0430. \u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 \u043f\u043e\u0437\u0436\u0435 \u0438\u043b\u0438 \u043d\u0430\u043f\u0438\u0448\u0438\u0442\u0435 /start"
                )
            except Exception:
                pass

    def run(self):
        app = (
            Application.builder()
            .token(BOT_TOKEN)
        )
        if PROXY_URL:
            app = app.proxy(PROXY_URL).get_updates_proxy(PROXY_URL)
        app = app.build()

        # Global error handler
        app.add_error_handler(self.error_handler)

        if TESTING:
            logger.warning('TESTING MODE — notifications disabled')

        # Периодическая очистка неактивных сессий (защита от утечки памяти)
        try:
            app.job_queue.run_repeating(self._cleanup_stale_sessions, interval=1800, first=600, name="ttl_cleanup")
        except Exception as e:
            logger.warning(f"TTL cleanup not scheduled: {e}")

        app.add_handler(CommandHandler("start", self.cmd_start))
        app.add_handler(CommandHandler("help", self.show_help))
        app.add_handler(CallbackQueryHandler(self.callback_handler))
        app.add_handler(MessageHandler(filters.Document.ALL, self.handle_document))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))

        # Планируем ежедневную рассылку
        # Читаем время из БД или используем значение по умолчанию
        try:
            saved_hour = self.db.get_setting("reminder_hour")
            saved_tz = self.db.get_setting("reminder_tz", "Asia/Yekaterinburg")
            if saved_hour is not None:
                global REMINDER_HOUR, REMINDER_TZ
                REMINDER_HOUR = int(saved_hour)
                REMINDER_TZ = saved_tz
                logger.info(f"Время из БД: {REMINDER_HOUR}:00, TZ: {REMINDER_TZ}")
        except:
            pass

        if REMINDER_HOUR is not None:
            import pytz
            from datetime import datetime as dt
            local_tz = pytz.timezone(REMINDER_TZ)
            now_local = dt.now(local_tz)
            target_local = now_local.replace(hour=REMINDER_HOUR, minute=REMINDER_MINUTE, second=0, microsecond=0)
            target_utc = target_local.astimezone(pytz.UTC)
            logger.info(f"Напоминание по {REMINDER_TZ} в {REMINDER_HOUR:02d}:{REMINDER_MINUTE:02d} = UTC {target_utc.hour:02d}:{target_utc.minute:02d}")
            
            # Если время ещё не прошло сегодня — планируем через run_daily
            app.job_queue.run_daily(
                self._send_daily_reminder,
                time=target_utc.time(),
                days=tuple(range(7)),
                name="daily_reminder"
            )
            logger.info(f"Ежедневные напоминания настроены на {REMINDER_HOUR:02d}:{REMINDER_MINUTE:02d}")
            
            # Если время уже прошло сегодня — отправляем сразу
            if now_local.hour >= REMINDER_HOUR and now_local.minute >= REMINDER_MINUTE:
                logger.info("Время напоминания уже прошло сегодня — отправляю сразу")
                # Запускаем через 3 секунды, чтобы бот успел инициализироваться
                app.job_queue.run_once(self._send_daily_reminder, 3, name="initial_reminder")

        logger.info("Бот v3.0 запущен!")
        app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    bot = SportHealthBot()
    bot.run()

