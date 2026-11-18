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
from telegram.error import Forbidden, BadRequest, RetryAfter
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
    BMI_PERCENTILES, AGE_GROUP_YEARS,
    BC_NORMS, BC_RED_FLAGS,
)
from cycle_medicine import (
    get_cycle_phase_info, get_cycle_phase, PHASES,
    AGE_SPECIFIC, CYCLE_LENGTH_MEDIAN,
)
from watch_parser import parse_watch
from scale_csv_parser import parse_scale_csv_dated
from scale_archive import archive_upload

from validator import (
    sanitize_text, validate_heart_rate, validate_age_ge14, parse_birth_date,
)
from access_control import required_role_for
from dashboard import build_today_dashboard

if PROXY_URL:
    os.environ["http_proxy"] = PROXY_URL
    os.environ["https_proxy"] = PROXY_URL

from database import Database, TEAMS, ACTIVE_TEAMS
from handlers_survey import SurveyMixin
from handlers_questionnaire import QuestionnaireMixin
from handlers_body_comp import BodyCompMixin
from handlers_reports import ReportsMixin
from handlers_admin import AdminMixin
from handlers_registration import RegistrationMixin
from handlers_athlete import AthleteMixin
from handlers_club import ClubMixin

# TESTING flag — disables all outgoing notifications to athletes
TESTING = os.environ.get("TESTING", "").lower() in ("true", "1", "yes")

class _TokenFilter(logging.Filter):
    def filter(self, record):
        if BOT_TOKEN:
            # Маскируем токен в самом сообщении и в его аргументах
            for attr in ("msg", "message"):
                val = getattr(record, attr, None)
                if isinstance(val, str) and BOT_TOKEN in val:
                    setattr(record, attr, val.replace(BOT_TOKEN, "***TOKEN***"))
        return True

from logging.handlers import RotatingFileHandler
_log_handler = RotatingFileHandler("bot.log", maxBytes=5*1024*1024, backupCount=3, encoding="utf-8")
_log_handler.addFilter(_TokenFilter())
_console = logging.StreamHandler()
_console.addFilter(_TokenFilter())
logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO, handlers=[_log_handler, _console])
logger = logging.getLogger(__name__)

# Подавляем шумный HTTP-лог (python-telegram-bot/httpx печатают URL с токеном на INFO)
for _noisy in ("httpx", "httpcore", "urllib3", "apscheduler", "telegram._httpx"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

ADMIN_TELEGRAM_IDS = ADMIN_IDS

# Настройки напоминаний (по умолчанию из .env; переопределяются из БД при старте)
REMINDER_HOUR = REMINDER_HOUR_DEFAULT
REMINDER_MINUTE = REMINDER_MINUTE_DEFAULT
REMINDER_TZ = "Asia/Yekaterinburg"

# Сколько профилей весов поддерживаем (весы GARLYN Bodyscan Master / MovingLife).
# Физически прибор может держать меньше — тогда взвешивают подгруппами, перепривязывая.
SCALE_PROFILE_COUNT = 50
# Кнопок профилей на одном экране (Telegram не любит >~100), пагинация
BC_PROFILE_PAGE = 25


# ==================== УТИЛИТЫ ====================

def _reminder_should_fire_now(now_local, hour, minute) -> bool:
    """Прошло ли уже сегодня локальное время напоминания (hour:minute)?

    Сравнение ПОЛНОГО времени кортежем: старое условие
    `hour >= H and minute >= M` ломалось на 21:10 vs 20:30 (10 < 30 → False),
    из-за чего рестарт после времени рассылки не доотправлял напоминания.
    """
    return (now_local.hour, now_local.minute) >= (hour, minute)


def validate_questionnaire_age(text) -> Tuple[bool, str]:
    """Валидация возраста анкеты. Единый порог — 14 лет (и максимум 99):
    берём из validator.validate_age_ge14; расхождение с лейблом группы
    «U-14 (12-14 лет)» — осознанное решение (единый порог по всем командам)."""
    try:
        age = int(str(text).strip())
    except (TypeError, ValueError):
        return False, "❌ Введи возраст числом (например: 15)"
    if age < 14:
        return False, "❌ Регистрация с 14 лет"
    if age > 99:
        return False, "❌ Максимальный возраст 99 лет"
    return True, f"✅ Возраст {age} лет"


def validate_questionnaire_birth(text) -> Tuple[bool, str]:
    """Валидация даты рождения анкеты (ДД.ММ.ГГГГ): календарная корректность
    (validator.parse_birth_date) + возраст 14-99 (validator.validate_age_ge14)."""
    raw = str(text or "").strip()
    if parse_birth_date(raw) is None:
        return False, "❌ Неверный формат даты. Введи ДД.ММ.ГГГГ (например 15.03.2008)"
    return validate_age_ge14(raw)


def validate_questionnaire_phone(text) -> Tuple[bool, str]:
    """Валидация телефона анкеты: 10-15 цифр после снятия +/пробелов/скобок/дефисов."""
    raw = str(text or "").strip()
    digits = re.sub(r"[^0-9]", "", raw)
    allowed = re.fullmatch(r"[0-9+\-()\s]{5,30}", raw)
    if not allowed or not (10 <= len(digits) <= 15):
        return False, "❌ Введи номер телефона (например: +7 900 123-45-67)"
    return True, "✅ Номер принят"


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


from gamification import (  # noqa: F401 — get_rank реэкспортируется: старый код делает `from bot import get_rank`
    get_rank,
    get_rank_plain,
)


def get_score_emoji(score):
    """Эмодзи для оценки. 7=отлично: 6-7🟢, 4-5🟡, 1-3🔴."""
    if score is None:
        return "❓"
    s = int(score)
    if s >= 6: return "🟢"
    elif s >= 4: return "🟡"
    return "🔴"


# ==================== ДЕКОРАТОРЫ ====================

def admin_only(method):
    """Декоратор: доступ только для admin/doctor. Показывает alert при отказе."""
    async def wrapper(self, update, ctx):
        uid = None
        if update.callback_query:
            uid = update.callback_query.from_user.id
        elif update.effective_user:
            uid = update.effective_user.id
        if uid and not self._is_full_access(uid):
            q = update.callback_query
            if q:
                try:
                    await q.answer("🔒 Нет доступа", show_alert=True)
                except Exception:
                    pass
            return
        return await method(self, update, ctx)
    wrapper.__name__ = method.__name__
    wrapper.__doc__ = method.__doc__
    return wrapper


# ==================== БОТ ====================

class SportHealthBot(SurveyMixin, QuestionnaireMixin, BodyCompMixin, ReportsMixin, AdminMixin, RegistrationMixin, AthleteMixin, ClubMixin):
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

    async def _cleanup_stale_sessions(self, context=None):
        """Периодическая очистка неактивных сессий (TTL = _state_ttl)."""
        # JobQueue (PTB 22.8) вызывает callback через await — метод обязан быть async,
        # иначе каждые 30 мин падает TypeError: object NoneType can't be used in 'await' expression.
        # Внутри тело sync-совместимое: все операции локальные (dict/time), await не нужен.
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

    def _first_name(self, athlete):
        """Имя для приветствия: хранимое first_name (Telegram) или эвристика по full_name.

        full_name в БД смешанный: импортированные «Фамилия Имя», через бота «Имя Фамилия»,
        поэтому эвристика ненадёжна — приоритет у колонки first_name (из профиля Telegram)."""
        if not athlete:
            return ""
        if isinstance(athlete, dict):
            fn = athlete.get("first_name")
            if fn:
                return fn
            full = athlete.get("full_name", "")
        else:
            full = str(athlete)
        if not full:
            return ""
        parts = str(full).split()
        return parts[-1] if len(parts) > 1 else str(full)

    def _looks_like_name(self, name):
        """Имя из Telegram похоже на настоящее (не ник-хэндл типа mfilkov).

        Принимаем: содержит кириллицу (любой регистр) либо начинается с заглавной буквы."""
        if not name:
            return False
        return bool(re.search(r"[А-Яа-яЁё]", name)) or (name[0].isupper() and len(name) >= 2)

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
            [b if isinstance(b, InlineKeyboardButton) else InlineKeyboardButton(b[0], callback_data=b[1]) for b in row]
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

    async def handle_document(self, update, ctx):
        user_id = update.effective_user.id
        if not self.check_rate_limit(user_id):
            await update.message.reply_text("⚠️ Подожди минуту.")
            return
        athlete = self.db.get_athlete_by_telegram_id(user_id)
        # Врач/тренер может присылать CSV весов, даже если сам не спортсмен.
        if not athlete and not self._is_full_access(user_id):
            await update.message.reply_text("❌ Сначала зарегистрируйся: /start")
            return

        doc = update.message.document
        if not doc:
            return

        fname = doc.file_name.lower() if doc.file_name else ""
        supported = [".csv", ".json", ".txt", ".zip"]
        if not any(fname.endswith(ext) for ext in supported):
            await update.message.reply_text("❌ Формат не поддерживается. Отправь CSV, JSON, TXT или ZIP (Apple Health).")
            return

        # Лимит размера загружаемого файла (ZIP от Apple Health может быть до 50 МБ)
        MAX_FILE_BYTES = 50 * 1024 * 1024 if fname.endswith(".zip") else 5 * 1024 * 1024
        if getattr(doc, "file_size", None) and doc.file_size > MAX_FILE_BYTES:
            mb = round(doc.file_size / (1024 * 1024), 1)
            await update.message.reply_text(f"❌ Файл слишком большой ({mb} МБ). Максимум 5 МБ.")
            return

        try:
            await update.message.reply_text("📥 Обрабатываю файл...")
        except Exception:
            pass  # таймаут Telegram — продолжаем обработку

        try:
            file = await doc.get_file()
            content = await file.download_as_bytearray()
            text_content = content.decode("utf-8", errors="replace")

            # === БИОИМПЕДАНСНЫЕ ВЕСЫ: пробуем распознать CSV состава тела ===
            scale_records = parse_scale_csv_dated(text_content, fname)
            logger.info(f"Scale parse: fname={fname}, records={len(scale_records)}, "
                        f"headers_sample={text_content[:200]}")
            if scale_records and any(
                r.get("weight_kg") is not None or r.get("body_fat_pct") is not None
                for r in scale_records
            ):
                await self._import_scale_csv(update, ctx, scale_records, fname,
                                             content=bytes(content))
                return

            # === Умные часы: обычный путь ===
            # Парсинг CSV/файла часов — CPU/IO-работа: уводим из event loop
            watch_data = await asyncio.to_thread(
                parse_watch, text_content, fname, raw_bytes=bytes(content)
            )

            if not watch_data:
                await update.message.reply_text(
                    "❌ Не удалось распознать данные. Проверь формат файла.\n\n"
                    "Попробуй выбрать свои часы в меню ⌚ и отправить файл по инструкции.",
                    reply_markup=self.kb([[(f"⌚ Выбрать часы", "watch_data")],
                                          [(f"🏠 Главное меню", "main_menu")]])
                )
                return

            msg = self._format_watch_report(watch_data, athlete["age_group"])

            # Сохраняем данные часов в БД (Фаза 5) — питают рекомендации (HRV, вес, сон)
            watch_data["_source"] = fname
            self.db.save_watch_data(athlete["id"], watch_data)

            await update.message.reply_text(
                msg, reply_markup=self.kb([[(f"🏠 Главное меню", "main_menu")]]), parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Watch file error: {e}")
            await update.message.reply_text("❌ Ошибка при обработке файла.")

    async def _import_scale_csv(self, update, ctx, records, fname,
                                content=None, inbox_dir=None):
        """Импорт CSV данных биоимпедансных весов (врач/тренер — группа, спортсмен — свой).

        Исходник кладётся в архив ДО разбора по ролям: потом можно переимпортировать
        после правки парсера и доказать, что именно прилетело.
        """
        user_id = update.effective_user.id
        full_access = self._is_full_access(user_id)
        archive_upload(content or b"", fname, date.today(),
                       base_dir=inbox_dir or self._scale_inbox_dir())

        if not full_access and not self._is_admin_or_coach(user_id):
            # спортсмен прислал свой файл — сохраняем на него (только одна точка замеров у него)
            athlete = self.db.get_athlete_by_telegram_id(user_id)
            if not athlete:
                await update.message.reply_text("❌ Сначала зарегистрируйся: /start")
                return
            from scale_csv_parser import distinct_device_profiles
            # Выгрузку присылает сам спортсмен. Если в файле несколько слотов
            # весов, его строки нельзя угадать: спрашиваем «какой слот твой»
            # (один раз, запоминаем) и пишем только строки этого слота. Чужие
            # цифры при этом не показываем.
            profiles = distinct_device_profiles(records)
            mine = self.db.get_scale_slot(athlete["id"])
            if len(profiles) > 1 and (not mine or mine not in profiles):
                state = self.get_state(user_id)
                state["step"] = "bc_slot_pick"
                state["data"]["bc_slot_profiles"] = profiles
                state["data"]["bc_slot_records"] = records
                try:
                    await self.persist_user_state(user_id)
                except Exception:
                    pass
                buttons = [[(prof, f"bc_slot_{i}")] for i, prof in enumerate(profiles[:10])]
                buttons.append([("🔙 Назад", "body_comp_menu")])
                await update.message.reply_text(
                    "⚖️ В твоём файле несколько профилей весов — это замеры разных "
                    "людей, угадать твой я не могу.\n\n"
                    "Нажми свой профиль (цифры других я не показываю и не записываю). "
                    "Если не знаешь номер — вспомни, под каким ты взвешивался в "
                    "приложении весов, или спроси у тренера.",
                    reply_markup=self.kb(buttons))
                return
            if len(profiles) == 1 and not mine:
                self.db.set_scale_slot(athlete["id"], profiles[0], user_id)
                mine = profiles[0]
            if mine and mine in profiles and len(profiles) >= 1:
                records = [r for r in records
                           if (r.get("device_profile") or "") == mine]
            saved = 0
            for r in records:
                data = dict(r)
                from handlers_body_comp import finalize_scale_metrics
                # рост из анкеты
                try:
                    qd = self.db.get_questionnaire(athlete["id"])
                    if qd and qd.get("height"):
                        data.setdefault("height_cm", qd.get("height"))
                except Exception:
                    pass
                data = finalize_scale_metrics(data)
                if self.db.save_body_composition(athlete["id"], r.get("record_date"), data,
                                                 source="csv", recorded_by=user_id,
                                                 device_profile=r.get("device_profile")):
                    saved += 1
            st = self.get_state(user_id)
            if st.get("step") in ("wait_scale_csv", "bc_slot_pick"):
                self.clear_state(user_id)
            if saved:
                # Показываем полный анализ + уведомления
                await self._analyze_and_notify_scale(update, athlete, records)
            else:
                await update.message.reply_text(
                    "⚠️ Не удалось сохранить данные. Проверь формат файла.",
                    reply_markup=self.kb([[(f"🏠 Главное меню", "main_menu")]])
                )
            return

        # Врач раскладывает всю базу и пишет сразу. Тренер — только своих
        # спортсменов и только после подтверждения раскладки на экране: файл
        # сначала считается (preview_only), пишется по кнопке подтверждения.
        # Раньше тренер либо заливал чужие замеры на себя, либо получал отказ
        # без рабочего пути.
        coach_only = (not full_access) and self.db.is_coach(user_id)
        saved, skipped, unmapped, rows, foreign = await self._import_scale_records(
            update, ctx, records, forced_athlete_id=None, preview_only=coach_only)

        if coach_only:
            state = self.get_state(user_id)
            state["step"] = "bc_import_review"
            state["data"]["pending_scale_records"] = records
            try:
                await self.persist_user_state(user_id)
            except Exception:
                pass  # без persist переживём: после рестарта подтверждение откажет
            head = ["⚖️ *Проверь раскладку — пока не записано*", "━" * 26, ""]
            head.extend(rows[:14])
            if len(rows) > 14:
                head.append(f"… и ещё {len(rows) - 14}")
            head.append("")
            head.append(f"К записи: *{saved}* · чужих профилей: *{len(foreign)}* · "
                        f"без привязки: *{len(unmapped)}*")
            head.append("Подтверждение обязательно — без него в базу не попадёт ничего.")
            await update.message.reply_text(
                "\n".join(head),
                reply_markup=self.kb([
                    [("✅ Подтвердить запись", "bc_import_confirm")],
                    [("❌ Отмена", "bc_import_cancel")],
                    [("🗂 Профили весов", "bc_profiles")],
                    [("🔙 Назад", "bc_menu")]]),
                parse_mode="Markdown")
            return

        # Fallback: CSV без профиля — спросить для кого
        if saved == 0 and skipped > 0:
            athletes = self.db.get_all_athletes()
            btns = []
            for a in athletes:
                btns.append([(f"{a['full_name']} ({a['team']})", f"bc_import_to_{a['id']}")])
            btns.append([("🔙 Назад", "bc_menu")])
            state = self.get_state(update.effective_user.id)
            state["step"] = "bc_import_pick"
            state["data"]["pending_scale_records"] = records
            await update.message.reply_text(
                "⚖️ CSV загружен, но профиль не определён.\nВыбери спортсмена:",
                reply_markup=self.kb(btns)
            )
            return

        head = ["⚖️ *Импорт весов (CSV)*", "━━━━━━━━━━━━━━━━━━━━━━━━━━"]
        if rows:
            head.append(f"✅ Сохранено: *{saved}* записей")
            head.append("")
            head.extend(rows[:12])
            if len(rows) > 12:
                head.append(f"… и ещё {len(rows)-12}")
            head.append("")
        if skipped:
            head.append(f"⚠️ Пропущено: *{skipped}* (без привязки к спортсмену)")
        if unmapped:
            head.append("")
            head.append(f"*Не закреплённые профили:* {', '.join(unmapped)}")
            head.append("Открой ниже «Профили весов» и назначь спортсменов, затем отправь CSV снова.")

        # Уведомления спортсменам, тренерам и врачу по каждому сохранённому замеру
        if saved:
            mapping = self.db.get_scale_profiles()
            notified_athletes = set()
            for r in records:
                prof = r.get("device_profile")
                if not prof:
                    continue
                athlete_id = mapping.get(prof)
                if not athlete_id or athlete_id in notified_athletes:
                    continue
                notified_athletes.add(athlete_id)
                athlete = self.db.get_athlete_by_id(athlete_id)
                if not athlete:
                    continue
                # Уведомление спортсмену
                try:
                    athlete_text = self._build_athlete_bc_report(
                        self.db.get_latest_body_composition(athlete_id),
                        self.db.get_body_composition(athlete_id, days=90),
                        athlete
                    )
                    await self._send_admin(athlete["telegram_id"], athlete_text)
                except Exception as e:
                    logger.error(f"Athlete BC notify error: {e}")
                # Уведомление тренеру
                team = athlete.get("team")
                if team:
                    try:
                        coach_text = self._build_coach_bc_report(
                            self.db.get_latest_body_composition(athlete_id),
                            self.db.get_body_composition(athlete_id, days=90),
                            athlete
                        )
                        coaches = self.db.get_team_coaches(team)
                        for cid in coaches:
                            if cid != athlete.get("telegram_id"):
                                await self._send_admin(cid, coach_text)
                    except Exception as e:
                        logger.error(f"Coach BC notify error: {e}")

        # запрос роста, если есть куда сохранять, но нет роста (пришлёт сводку после ввода)
        if saved or (skipped and not unmapped):
            asked_height = await self._ask_height_for_records(update, ctx, records)
            if asked_height:
                return

        await update.message.reply_text(
            "\n".join(head),
            reply_markup=self.kb([
                [(f"🗂 Профили весов", "bc_profiles")],
                [(f"🏠 Главное меню", "main_menu")]
            ]),
            parse_mode="Markdown"
        )

    def _is_full_access(self, user_id) -> bool:
        """Полный доступ (как у админа): супер-админ из ADMIN_TELEGRAM_IDS или врач из таблицы doctors."""
        return user_id in ADMIN_TELEGRAM_IDS or self.db.is_doctor(user_id)

    def _full_access_ids(self):
        """Все telegram_id для УВЕДОМЛЕНИЙ врачу: админы + врачи, тренеры — исключены.

        Тренеру НЕ приходят уведомления о каждом опросе его команды (только
        еженедельная сводка по понедельникам). Исключение действует, даже если
        тренер записан врачом или админом."""
        docs = {d["telegram_id"] for d in self.db.get_all_doctors()}
        ids = set(ADMIN_TELEGRAM_IDS) | docs
        return {i for i in ids if not self.db.is_coach(i)}

    def _is_admin_or_coach(self, user_id) -> bool:
        """Полный доступ (админ/врач) видит всё; тренер — только свои команды."""
        return self._is_full_access(user_id) or self.db.is_coach(user_id)

    def _scoped_athletes(self, user_id, athletes=None):
        """Список спортсменов в рамках прав пользователя.

        Админ/врач → только активные команды (ACTIVE_TEAMS). Тренер → только спортсмены
        его команд (активных). Обычный пользователь → пусто. Спортсмены неактивных
        команд остаются в БД, но скрыты."""
        _active = set(ACTIVE_TEAMS)
        if self._is_full_access(user_id):
            src = athletes if athletes is not None else self.db.get_all_athletes()
            return [a for a in src if a.get("team") in _active]
        teams = set(self.db.get_coach_teams(user_id)) & _active
        if not teams:
            return []
        src = athletes if athletes is not None else self.db.get_all_athletes()
        return [a for a in src if a.get("team") in teams]

    def _coach_teams(self, user_id):
        return set(self.db.get_coach_teams(user_id))

    async def show_today_dashboard(self, update, ctx):
        """Дашборд «🔔 На сегодня» (роль уже гейтится callback-гейтом: dash_ → coach).

        Тяжёлая сборка — в отдельном потоке (batch-запросы в dashboard.py),
        результат кэшируется на 60 с: ключ — (scope спортсмена, минутный бакет),
        один слот (две минуты/scope не накапливаем — нечему течь)."""
        user_id = update.effective_user.id
        scoped = self._scoped_athletes(user_id)
        cache = getattr(self, "_dash_cache", None)
        if cache is None:
            cache = self._dash_cache = {}
        key = (frozenset(a["id"] for a in scoped), int(time.time() // 60))
        result = cache.get("val") if cache.get("key") == key else None
        if result is None:
            result = await asyncio.to_thread(
                build_today_dashboard, self.db, user_id, scoped, date.today())
            cache.clear()
            cache["key"] = key
            cache["val"] = result
        text, buttons = result
        markup = self.kb(buttons)
        try:
            if update.callback_query:
                await update.callback_query.edit_message_text(
                    text, reply_markup=markup, parse_mode="Markdown")
            else:
                await update.message.reply_text(
                    text, reply_markup=markup, parse_mode="Markdown")
        except BadRequest as e:
            if "not modified" in str(e).lower():
                # штатный «Обновить» в пределах кэша — контент тот же, не ошибка
                return
            # вероятная причина: неэкранированный Markdown в пользовательском
            # вводе (жалобы/ФИО) — «Can't parse entities». Логируем (без PHI) и
            # повторно отправляем тот же текст БЕЗ parse_mode, чтобы экран
            # гарантированно отрисулся.
            logger.warning(f"Dashboard render failed (user_id={user_id}): {e}; "
                           f"fallback without parse_mode")
            try:
                if update.callback_query:
                    await update.callback_query.edit_message_text(
                        text, reply_markup=markup)
                else:
                    await update.message.reply_text(text, reply_markup=markup)
            except Exception as e2:
                logger.error(f"Dashboard fallback failed (user_id={user_id}): {e2}")

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

        # Регистрация клуба (мульти-tenant) — через context.user_data
        if ctx.user_data.get("club_registration"):
            handled = await self.handle_club_registration(update, ctx)
            if handled:
                return

        # Регистрация: имя
        if step == "reg_name":
            name = re.sub(r'[^\w\s\-]', '', text.strip())
            if len(name.split()) < 2 or len(name) < 3:
                await update.message.reply_text("❌ Введи *Имя и Фамилию*:", parse_mode="Markdown")
                return

            name = " ".join(name.split()).title()
            _tg_fn = update.effective_user.first_name
            _reg_fn = _tg_fn if self._looks_like_name(_tg_fn) else name.split()[0]

            # Определяем club_id: из state или из context.user_data
            club_id = state["data"].get("club_id")
            if not club_id:
                club_data = ctx.user_data.get("join_club")
                if club_data:
                    club_id = club_data.get("club_id")

            if club_id:
                ok = self.db.register_athlete_mt(
                    telegram_id=user_id, username=update.effective_user.username,
                    full_name=name, age_group=state["data"]["age_group"],
                    team=state["data"].get("team", "Не указана"),
                    club_id=club_id, first_name=_reg_fn
                )
            else:
                ok = self.db.register_athlete(
                    telegram_id=user_id, username=update.effective_user.username,
                    full_name=name, age_group=state["data"]["age_group"],
                    team=state["data"].get("team", "Не указана"),
                    first_name=_reg_fn
                )

            if not ok:
                existing = self.db.get_athlete_by_telegram_id(user_id)
                if existing:
                    await update.message.reply_text(
                        f"✅ *Добро пожаловать снова!*\n\n👤 {existing['full_name']}\n📋 {existing['age_group']}\n🏀 {existing.get('team', '?')}\n\nВы уже зарегистрированы 🏀",
                        reply_markup=self.kb([[(f"🏠 Главное меню", "main_menu")]]), parse_mode="Markdown"
                    )
                else:
                    await update.message.reply_text("❌ Ошибка регистрации. Попробуйте позже.", parse_mode="Markdown")
                self.clear_state(user_id)
                return

            self.clear_state(user_id)
            athlete_obj = self.db.get_athlete_by_telegram_id(user_id)
            has_q = self.db.has_questionnaire(athlete_obj["id"]) if athlete_obj else False
            if not has_q:
                await update.message.reply_text(
                    f"✅ *Регистрация завершена!*\n\n👤 {name}\n📋 {state['data']['age_group']}\n🏀 {state['data'].get('team', '?')}\n\n📋 *Теперь заполни анкету — это важно для врача!*",
                    reply_markup=self.kb([[(f"📋 Пройти анкету", "questionnaire")]]), parse_mode="Markdown"
                )
            else:
                await update.message.reply_text(
                    f"✅ *Регистрация завершена!*\n\n👤 {name}\n📋 {state['data']['age_group']}\n🏀 {state['data'].get('team', '?')}\n\nДобро пожаловать! 🏀",
                    reply_markup=self.kb([[(f"🏠 Главное меню", "main_menu")]]), parse_mode="Markdown"
                )
            return

        # Регистрация: кастомная команда (для клубов без команд)
        elif step == "reg_team_custom":
            team = text.strip()
            if len(team) < 2 or len(team) > 50:
                await update.message.reply_text("❌ Название команды от 2 до 50 символов:")
                return
            state["step"] = "reg_gender"
            state["data"]["team"] = team
            await update.message.reply_text(
                f"✅ Команда: *{team}*\n\n👤 *Укажите пол:*",
                reply_markup=self.kb([
                    [("♂ Мужской", "reg_gender_male")],
                    [("♀ Женский", "reg_gender_female")],
                ]), parse_mode="Markdown"
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

        # Админ: ввод Telegram ID нового тренера
        if step == "admin_coach_add":
            if user_id not in ADMIN_TELEGRAM_IDS:
                self.clear_state(user_id)
                return
            raw = text.strip().lstrip("@")
            if not raw.isdigit():
                await update.message.reply_text(
                    "❌ Это не похоже на числовой Telegram ID. Пришли число (или @username не поддерживается — нужен ID).",
                    reply_markup=self.kb([[(f"🔙 Тренеры", "coach_menu")]])
                )
                return
            tg_id = int(raw)
            state["data"]["coach_edit_id"] = tg_id
            state["data"]["coach_edit_teams"] = self.db.get_coach_teams(tg_id)
            state["step"] = None  # дальше работаем кнопками (coach_toggle_*)
            await self._send_coach_team_picker(update, tg_id, state["data"]["coach_edit_teams"])
            return

        # Спортсмен: ввод Telegram ID (добавление админом)
        if step == "admin_add_athlete_tg_id":
            if user_id not in ADMIN_TELEGRAM_IDS:
                self.clear_state(user_id)
                return
            raw = text.strip().lstrip("@")
            if not raw.isdigit():
                await update.message.reply_text(
                    "❌ Это не похоже на числовой Telegram ID. Пришли число.",
                    reply_markup=self.kb([[(f"🔙 Управление", "admin_manage")]])
                )
                return
            state["data"]["add_tg_id"] = raw
            # Переход к выбору команды
            buttons = [[(team, f"admin_add_team_{team}")] for team in TEAMS]
            buttons.append([("🔙 Управление", "admin_manage")])
            await update.message.reply_text(
                f"✅ Telegram ID: *{raw}*\n\nВыбери *команду:*",
                reply_markup=self.kb(buttons), parse_mode="Markdown"
            )
            return

        # Спортсмен: ввод ФИО (добавление админом)
        if step == "admin_add_athlete_name":
            if user_id not in ADMIN_TELEGRAM_IDS:
                self.clear_state(user_id)
                return
            name = re.sub(r'[^\w\s\-]', '', text.strip())
            if len(name.split()) < 2 or len(name) < 3:
                await update.message.reply_text(
                    "❌ Введи *Фамилию Имя* ( minimum 2 слова):",
                    parse_mode="Markdown"
                )
                return
            name = " ".join(name.split()).title()
            state["data"]["add_name"] = name
            await self.admin_add_athlete_save(update, user_id, state)
            return

        # Врач: ввод Telegram ID нового врача
        if step == "admin_doctor_add":
            if user_id not in ADMIN_TELEGRAM_IDS:
                self.clear_state(user_id)
                return
            raw = text.strip().lstrip("@")
            if not raw.isdigit():
                await update.message.reply_text(
                    "❌ Это не похоже на числовой Telegram ID. Пришли число.",
                    reply_markup=self.kb([[(f"🔙 Врачи", "doctor_menu")]])
                )
                return
            tg_id = int(raw)
            self.db.add_doctor(tg_id)
            state["step"] = None
            await update.message.reply_text(
                f"✅ Врач id *{tg_id}* добавлен — теперь у него полный доступ, как у админа.",
                reply_markup=self.kb([[(f"🔙 Врачи", "doctor_menu")]]),
                parse_mode="Markdown"
            )
            return

        # Врач/тренер: текст ответа спортсмену по жалобе
        if step == "admin_reply":
            await self._admin_reply_send(update, ctx, user_id, state, text)
            return

        # Обновление веса (своё, из профиля)
        if step == "update_weight":
            await self.update_weight_save(update, ctx, user_id, text)
            return

        # Ввод роста при импорте весов (нет роста в анкете)
        if step == "bc_height":
            await self._bc_height_save(update, ctx, user_id, text)
            return

        # Подсказка, если вместо CSV отправлен текст. В handle_text объекта q нет
        # (это обработчик сообщений), поэтому отвечаем reply_text: раньше эти две
        # ветки падали NameError, и вместо подсказки человек получал
        # «Произошла ошибка».
        if step == "wait_scale_csv":
            await update.message.reply_text(
                "⚠️ Отправь CSV-файл, а не текст.\n"
                "Вернись в меню состава тела и нажми «📤 Загрузить данные».",
                reply_markup=self.kb([[(f"⚖️ Состав тела", "body_comp_menu")]])
            )
            return

        if step == "bc_import_pick":
            await update.message.reply_text(
                "⚠️ Нажми кнопку спортсмена из списка выше.",
                reply_markup=self.kb([[(f"⚖️ Состав тела", "body_comp_menu")]])
            )
            return

        if step == "bc_slot_pick":
            await update.message.reply_text(
                "⚖️ Нажми кнопку со своим профилем весов из сообщения выше — "
                "так я запишу замеры только на тебя.",
                reply_markup=self.kb([[(f"🔙 Назад", "body_comp_menu")]])
            )
            return

        if step == "bc_import_review":
            # Раскладка группового файла ждёт подтверждения: текст не отменяет её
            # и ничего не пишет — только напоминаем про кнопку.
            await update.message.reply_text(
                "⚠️ Раскладка ждёт подтверждения.\n"
                "Нажми «✅ Подтвердить запись» или «❌ Отмена» в сообщении выше.",
                reply_markup=self.kb([
                    [("✅ Подтвердить запись", "bc_import_confirm")],
                    [("❌ Отмена", "bc_import_cancel")]])
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
            athlete = state.get("data", {}).get("athlete")
            is_simple = athlete and athlete.get("age_group") in SIMPLE_PROTOCOLS
            if is_simple:
                # Младшие (U14-U16): после пульса опрос завершается (без тренировки/sRPE)
                await self._route_after_survey(update, ctx, user_id, state)
                return
            state["step"] = "survey_training"
            await update.message.reply_text(
                "💪 *Была ли тренировка вчера?*",
                reply_markup=self.kb([[(f"✅ Да", "train_yes"), (f"❌ Нет", "train_no")]]),
                parse_mode="Markdown"
            )
            return

        # Фактические часы сна (число, напр. 7.5)
        if step == "survey_sleep_hours":
            try:
                hours = float(text.strip().replace(",", "."))
                if not (0 < hours <= 24):
                    raise ValueError
            except ValueError:
                await update.message.reply_text("❌ Введи часы числом (например: 7.5):")
                return
            state["data"]["sleep_hours"] = hours
            state["step"] = "survey_readiness"
            await update.message.reply_text(
                f"📝 *Опрос*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"💪 *Готовность к тренировке?*\n\n"
                f"1 — совсем не готов\n5 — средне\n10 — полностью готов\n\nВыбери:",
                reply_markup=self.kb(self.score_buttons("readiness", mn=1, mx=10)),
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

        # День цикла (ручное исправление числа: 1..длина+30)
        if step == "survey_cycle":
            await self._manual_cycle_day(update, ctx, state, text)
            return

        # Дата якоря / ретро-коррекции (ДД.ММ)
        if step == "survey_anchor_date":
            await self._anchor_date_text(update, ctx)
            return

        # Жалобы (ввод текста)
        if step == "complaint_text_input":
            state["data"]["complaints"] = sanitize_text(text, keep_nl=True)
            await self._finish_survey(update, user_id, state)
            return

        # Анкета: текстовые ответы
        if step == "q_age":
            ok, msg = validate_questionnaire_age(text)
            if not ok:
                await update.message.reply_text(msg)
                return
            state["q_data"]["age"] = str(int(text.strip()))
            state["step"] = "q_phone"
            await update.message.reply_text("📋 *Блок 1: Общие данные*\n\nВведи номер телефона:", parse_mode="Markdown")
            return

        if step == "q_phone":
            ok, msg = validate_questionnaire_phone(text)
            if not ok:
                await update.message.reply_text(msg)
                return
            state["q_data"]["phone"] = text.strip()
            state["step"] = "q_birth_date"
            await update.message.reply_text("📋 *Блок 1: Общие данные*\n\nВведи дату рождения в формате *ДД.ММ.ГГГГ* (например: 15.03.2008):", parse_mode="Markdown")
            return

        if step == "q_birth_date":
            ok, msg = validate_questionnaire_birth(text)
            if not ok:
                await update.message.reply_text(msg, parse_mode="Markdown")
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
                try:
                    h = float(parts[0]); w = float(parts[1])
                    if not (0 < h < 400 and 0 < w < 300):
                        raise ValueError
                except (ValueError, IndexError):
                    await update.message.reply_text("❌ Введи рост и вес числами через пробел (например: 185 82) или «-»")
                    return
                state["q_data"]["height"] = parts[0]
                state["q_data"]["weight"] = parts[1]
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
                self._schedule_reminder_job(hour, minute)
                self.clear_state(user_id)
                await update.message.reply_text(
                    f"✅ Время напоминаний: *{hour:02d}:{minute:02d}* по Челябинску (сохранено)",
                    parse_mode="Markdown",
                    reply_markup=self.kb([[(f"🔙 Назад", "main_menu")]])
                )
            except (ValueError, TypeError):
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
        # Rate-limit: спам инлайн-кнопками (общий счётчик с текстом, 30/мин).
        # Ответ на callback одноразовый — алерт rate-limit'а и есть первый ответ.
        if not self.check_rate_limit(q.from_user.id):
            try:
                await q.answer("⚠️ Подожди минуту.", show_alert=True)
            except Exception:
                pass
            return
        d = q.data
        logger.debug("Callback: %s uid=%s", d, q.from_user.id)

        # Default-deny гейт ролей (access_control.py) — ДО любого роутинга
        # и ДО пустого q.answer(): отказной алерт должен быть ПЕРВЫМ ответом,
        # иначе Telegram вернёт "Query is already answered" и юзер ничего не увидит.
        # Точечные проверки внутри хендлеров сохранены (defense in depth).
        _req = required_role_for(d)
        if _req == "full" and not self._is_full_access(q.from_user.id):
            logger.warning(f"Access denied (full required): {d} from {q.from_user.id}")
            try:
                await q.answer("⛔ Нет доступа", show_alert=True)
            except Exception:
                pass
            return
        if _req == "coach" and not self._is_admin_or_coach(q.from_user.id):
            logger.warning(f"Access denied (coach required): {d} from {q.from_user.id}")
            try:
                await q.answer("⛔ Нет доступа", show_alert=True)
            except Exception:
                pass
            return

        # Гейт пройден — гасим "часики" пустым ответом (первый и единственный).
        try:
            await q.answer()
        except Exception:
            pass

        # Само-синхронизация имени из Telegram (только если похоже на имя, не ник)
        if q.from_user.first_name and self._looks_like_name(q.from_user.first_name):
            try:
                _ath = self.db.get_athlete_by_telegram_id(q.from_user.id)
                if _ath and _ath.get("first_name") != q.from_user.first_name:
                    self.db.set_athlete_first_name(_ath["id"], q.from_user.first_name)
            except Exception:
                pass

        # Глобальный try/except — любая ошибка не должна вешать бота
        try:
            if d == "main_menu":
                await self.show_main_menu(update, ctx)
            elif d == "dash_today":
                await self.show_today_dashboard(update, ctx)
            elif d == "consent_accept":
                # регистрируем согласие и переходим к выбору возрастной группы
                self.db.record_consent(q.from_user.id)
                await self._ask_age_group(update, ctx)
            elif d == "consent_decline":
                await q.edit_message_text("❌ Без согласия на обработку данных использовать бот нельзя.\nЕсли передумаешь — напиши /start.", reply_markup=self.kb([[(f"📄 Дать согласие", "consent_accept")]]), parse_mode="Markdown")
            elif d.startswith("reg_gender_"):
                await self.reg_gender_callback(update, ctx)
            elif d.startswith("reg_"):
                await self.reg_callback(update, ctx)
            elif d.startswith("team_"):
                await self.team_callback(update, ctx)
            elif d == "my_progress":
                await self.show_my_progress(update, ctx)
            elif d == "my_goals":
                await self.show_my_goals(update, ctx)
            elif d.startswith("goal_add_"):
                await self.goal_add_type(update, ctx)
            elif d.startswith("goal_set_"):
                await self.goal_set_value(update, ctx)
            elif d.startswith("goal_done_"):
                await self.goal_complete(update, ctx)
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
            elif d.startswith(("painloc_", "paingame_", "illness_", "analg_")):
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
            elif d == "q_restart":
                await self.questionnaire_restart(update, ctx)
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
            elif d == "bc_menu":
                await self.scale_import_menu(update, ctx)
            elif d == "bc_overview":
                await self.bc_overview(update, ctx)
            elif d == "bc_profiles":
                await self.bc_show_profiles(update, ctx)
            elif d.startswith("bc_page_"):
                await self.bc_page(update, ctx)
            elif d.startswith("bc_map_"):
                await self.bc_assign_pick(update, ctx)
            elif d.startswith("bc_assign_"):
                await self.bc_assign_save(update, ctx)
            elif d.startswith("bc_view_"):
                await self.bc_view_athlete(update, ctx)
            elif d == "body_comp_menu":
                await self.body_comp_menu(update, ctx)
            elif d == "scale_upload_start":
                await self.scale_upload_start(update, ctx)
            elif d.startswith("bc_team_"):
                await self.bc_team_athletes(update, ctx)
            elif d == "bc_overview_all":
                await self.bc_overview_all(update, ctx)
            elif d == "bc_my_trends":
                await self.bc_my_trends(update, ctx)
            elif d.startswith("bc_import_to_"):
                await self._bc_import_to_athlete(update, ctx)
            elif d.startswith("bc_slot_"):
                await self.bc_slot_choose(update, ctx)
            elif d == "bc_import_confirm":
                await self.bc_import_confirm(update, ctx)
            elif d == "bc_import_cancel":
                await self.bc_import_cancel(update, ctx)
            elif d == "body_comp_history":
                await self.body_comp_history(update, ctx)
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
            elif d == "admin_add_athlete":
                await self.admin_add_athlete_start(update, ctx)
            elif d.startswith("admin_add_team_"):
                await self.admin_add_athlete_team(update, ctx)
            elif d.startswith("admin_add_age_"):
                await self.admin_add_athlete_age(update, ctx)
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
            elif d == "report_watches":
                await self.report_watches_choose_period(update, ctx)
            elif d.startswith("report_watch_team_"):
                await self.report_watches_choose_period(update, ctx)
            elif d.startswith("report_watch_period_"):
                await self.generate_watch_xlsx_report(update, ctx)
            elif d == "report_body_comp":
                await self.report_body_comp_choose_period(update, ctx)
            elif d.startswith("bc_team_"):
                # Тренер выбрал команду для отчёта по весам → показать выбор периода
                team = d.replace("bc_team_", "")
                state = self.get_state(q.from_user.id)
                state["data"]["bc_report_team"] = team
                buttons = [
                    [("📌 Последний замер каждого", "report_bc_period_0")],
                    [("📅 2 недели", "report_bc_period_14")],
                    [("📅 Месяц", "report_bc_period_30")],
                    [("📅 3 месяца", "report_bc_period_90")],
                    [("📅 Всё время", "report_bc_period_999")],
                    [("🔙 Назад", "report_body_comp")]
                ]
                await q.edit_message_text(
                    f"⚖️ *Отчёт по весам — {team}*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "Замеры проводятся раз в 2 недели. Выбери период:",
                    reply_markup=self.kb(buttons), parse_mode="Markdown"
                )
            elif d.startswith("report_bc_period_"):
                await self.generate_bc_xlsx_report(update, ctx)
            elif d == "set_gender":
                await self.set_gender_menu(update, ctx)
            # === ТРЕНЕР (роль coach) ===
            elif d == "coach_team_view":
                await self.coach_team_view(update, ctx)
            elif d.startswith("recs_"):
                await self.coach_athlete_recs(update, ctx)
            elif d == "coach_week_summary":
                await self.coach_week_summary(update, ctx)
            elif d == "coach_menu":
                await self.show_coach_admin(update, ctx)
            elif d == "coach_add":
                await self.coach_add_ask(update, ctx)
            elif d.startswith("coach_edit_"):
                await self.coach_edit(update, ctx)
            elif d.startswith("coach_toggle_"):
                await self.coach_toggle(update, ctx)
            elif d.startswith("coach_save_"):
                await self.coach_save(update, ctx)
            elif d.startswith("coach_del_"):
                await self.coach_delete(update, ctx)
            elif d == "doctor_menu":
                await self.show_doctor_admin(update, ctx)
            elif d == "doctor_add":
                await self.doctor_add_ask(update, ctx)
            elif d.startswith("doctor_del_"):
                await self.doctor_delete(update, ctx)
            elif d.startswith("reply_athlete_"):
                await self._admin_reply_start(update, ctx)
            elif d == "admin_complaints":
                await self.admin_complaints_menu(update, ctx)
            elif d.startswith("admin_complaints_page_"):
                await self.admin_complaints_page(update, ctx)
            elif d == "pdf_report_menu":
                await self.pdf_report_menu(update, ctx)
            elif d.startswith("pdf_"):
                await self.pdf_report_generate(update, ctx)
            # === ПОЛ (ПЕРВИЧНЫЙ ВОПРОС В ОПРОСЕ) ===
            # Должен обрабатываться ДО общего префикса gender_ (иначе save_gender поглотит кнопку)
            elif d == "gender_first_male":
                await self._gender_first_chosen(update, ctx, "male")
            elif d == "gender_first_female":
                await self._gender_first_chosen(update, ctx, "female")
            elif d.startswith("gender_"):
                await self.save_gender(update, ctx)
            elif d == "cycle_len_custom":
                await self._ask_cycle_length_custom(update, ctx)
            elif d.startswith("cycle_len_"):
                await self._cycle_length_chosen(update, ctx)
            elif d.startswith("cycle_"):
                # Старые кнопки-корзины (cycle_1..26) из опросов, отправленных
                # до обновления: состояние шага уже не то → предлагаем заново,
                # а не молча теряем тап
                await self._broken_survey_reply(update.callback_query, update)
            elif d.startswith("anchor_"):
                await self.set_cycle_anchor(update, ctx)
            elif d == "cyc_set_anchor":
                await self.set_anchor_manual_start(update, ctx)
            elif d.startswith("cyc_"):
                await self.set_cycle_daily(update, ctx)
            elif d == "my_cycle":
                await self.show_cycle_info(update, ctx)
            elif d == "update_weight":
                await self.update_weight_start(update, ctx)
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

            # === КЛУБЫ (мульти-тенантность) ===
            elif d.startswith("sport_"):
                await self.handle_club_sport_callback(update, ctx)
            elif d.startswith("club_confirm_"):
                await self.handle_club_confirm_callback(update, ctx)
            elif d.startswith("club_"):
                await self.handle_club_callback(update, ctx)

        # Конец try/except для callback_handler
        except Exception as e:
            if "Message is not modified" in str(e):
                # Двойной клик по кнопке: сообщение уже в нужном виде — молча игнорируем
                try:
                    await q.answer()
                except Exception:
                    pass
            else:
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
            await self.persist_user_state(q.from_user.id)
        except Exception:
            pass

    async def _send_admin(self, admin_id, text, buttons=None):
        """Отправить сообщение админу. При ошибке Button_user_privacy_restricted — без кнопок.
        При 429/RetryAfter — сон на retry_after+1 и одна повторная попытка."""
        if TESTING:
            return
        bot = self._bot
        reply_markup = self.kb(buttons) if buttons else None
        for _attempt in range(2):
            try:
                await bot.send_message(
                    chat_id=admin_id, text=text, parse_mode="Markdown",
                    reply_markup=reply_markup
                )
                return
            except RetryAfter as e:
                if _attempt == 0:
                    logger.warning(f"Flood control для admin {admin_id}: сплю {e.retry_after}s")
                    await asyncio.sleep(e.retry_after + 1)
                    continue
                logger.error(f"Send to admin {admin_id}: RetryAfter persists ({e})")
                return
            except Exception as e:
                if "privacy_restricted" in str(e).lower() and buttons:
                    try:
                        await bot.send_message(
                            chat_id=admin_id, text=text, parse_mode="Markdown"
                        )
                    except Exception as e2:
                        logger.error(f"Send to admin {admin_id} (no buttons): {e2}")
                else:
                    logger.error(f"Send to admin {admin_id}: {e}")
                return

    async def _mark_bot_blocked(self, a):
        """Спортсмен заблокировал бота (Forbidden): флаг bot_blocked=1, рассылки
        ему прекращаются. Врач уведомляется ОДИН раз — на переходе 0→1.
        В лог — только athlete_id (без ФИО/telegram_id, гигиена ПДн)."""
        prev = self.db.get_bot_blocked(a["id"])
        self.db.set_bot_blocked(a["id"], 1)
        logger.info(f"Athlete #{a['id']} blocked the bot — unsubscribed from broadcasts")
        if not prev:
            for admin_id in self._full_access_ids():
                await self._send_admin(
                    admin_id,
                    f"⚠️ Спортсмен #{a['id']} ({a.get('full_name', '?')}) "
                    f"заблокировал бота — мониторинг прерван"
                )

    async def _admin_reply_start(self, update, ctx):
        """Врач/тренер отвечает спортсмену по жалобе — ждём текст сообщения."""
        q = update.callback_query
        await q.answer()
        if not self._is_full_access(q.from_user.id):
            return
        try:
            athlete_id = int(q.data.replace("reply_athlete_", ""))
        except ValueError:
            return
        athlete = self.db.get_athlete_by_id(athlete_id)
        if not athlete:
            await q.edit_message_text("❌ Спортсмен не найден.")
            return
        state = self.get_state(q.from_user.id)
        state["step"] = "admin_reply"
        state["data"]["reply_athlete_id"] = athlete_id
        await q.edit_message_text(
            f"📩 *Написать {athlete['full_name']}*\n\n"
            f"Напиши текст — спортсмен получит его в Telegram:\n"
            f"(для отмены отправь /cancel)",
            parse_mode="Markdown"
        )

    async def _admin_reply_send(self, update, ctx, user_id, state, text):
        """Отправка текста врача спортсмену."""
        athlete_id = state.get("data", {}).get("reply_athlete_id")
        self.clear_state(user_id)
        if not athlete_id:
            return
        athlete = self.db.get_athlete_by_id(athlete_id)
        if not athlete or not athlete.get("telegram_id"):
            await update.message.reply_text("❌ Спортсмен не найден.")
            return
        msg = sanitize_text(text, keep_nl=True)
        try:
            await ctx.bot.send_message(
                chat_id=athlete["telegram_id"],
                text=f"📩 *Сообщение от врача:*\n\n{msg}",
                parse_mode="Markdown"
            )
            await update.message.reply_text(
                f"✅ Отправлено: *{athlete['full_name']}*",
                reply_markup=self.kb([[(f"🏠 Главное меню", "main_menu")]]),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Admin reply send error: {e}")
            await update.message.reply_text(
                "❌ Не удалось отправить (спортсмен мог заблокировать бота).",
                reply_markup=self.kb([[(f"🏠 Главное меню", "main_menu")]])
            )

    async def _send_daily_reminder(self, context):
        """Ежедневная рассылка напоминаний спортсменам."""
        if REMINDER_HOUR is None:
            return

        # Дедуп: одна рассылка в день — рестарты не спамят повторно
        import pytz
        from datetime import datetime as _dt
        _today = _dt.now(pytz.timezone(REMINDER_TZ)).strftime("%Y-%m-%d")
        if self.db.get_setting("reminder_sent_date") == _today:
            logger.info("Напоминание уже отправлено сегодня — пропускаю")
            return
        # Дедуп-метку ставим ДО цикла рассылки: крах/рестарт посреди цикла
        # не должен приводить к повторным сообщениям тем, кто уже получил.
        self.db.set_setting("reminder_sent_date", _today)

        if TESTING:
            logger.info("TESTING MODE — напоминания и сводки не отправляются")
            return

        bot = context.bot

        athletes = self.db.get_all_athletes()
        # Batch: один запрос вместо 3N
        survey_map = self.db.get_has_survey_today_map()
        sent = 0
        skipped = 0
        for a in athletes:
            if a.get("bot_blocked"):
                skipped += 1
                continue
            if survey_map.get(a["id"], False):
                skipped += 1
                continue
            if self.db.is_coach(a["telegram_id"]):
                skipped += 1
                continue
            try:
                # 429/RetryAfter: спим указанное Telegram время и одна повторная попытка
                for _attempt in range(2):
                    try:
                        await bot.send_message(
                            chat_id=a["telegram_id"],
                            text=(
                                "🏀 *ЧБК — Напоминание об опросе!*\n"
                                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                                f"👋 Привет, {self._first_name(a)}!\n\n"
                                "📝 *Ежедневный опрос ещё не пройден!*\n"
                                "Всего 1 минута — и у доктора будут данные о твоём состоянии.\n\n"
                                f"🔥 Твоя серия: *{a.get('survey_streak', 0)} дней*\n\n"
                                "👉 Нажми /start чтобы открыть меню"
                            ),
                            parse_mode="Markdown"
                        )
                        sent += 1
                        break
                    except RetryAfter as e:
                        if _attempt == 0:
                            logger.warning(f"Flood control для #{a['id']}: сплю {e.retry_after}s")
                            await asyncio.sleep(e.retry_after + 1)
                            continue
                        logger.error(f"Reminder RetryAfter persists for athlete #{a['id']}: {e}")
            except Forbidden:
                await self._mark_bot_blocked(a)
            except Exception as e:
                logger.error(f"Reminder error for athlete #{a['id']}: {e}")

        logger.info(f"Reminder sent to {sent}/{len(athletes)} ({skipped} already surveyed)")

        # Сводка врачу — используем тот же batch (тренеры не спортсмены)
        try:
            _ath = [a for a in athletes if not self.db.is_coach(a["telegram_id"])]
            passed = [a for a in _ath if survey_map.get(a["id"], False)]
            not_passed = [a for a in _ath if not survey_map.get(a["id"], False)]
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

            for admin_id in self._full_access_ids():
                try:
                    await bot.send_message(chat_id=admin_id, text=summary, parse_mode="Markdown")
                except Exception as e:
                    logger.error(f"Admin summary error: {e}")
        except Exception as e:
            logger.error(f"Doctor summary error: {e}")


    async def _check_trend_alerts(self, context):
        """Ежедневная проверка трендов: 3 дня подряд отклонение → алерт врачу."""
        if TESTING:
            return
        bot = context.bot
        try:
            # Batch: данные за 3 дня для всех
            # Без опросов цикл всё равно идёт: не выходим рано — просто
            # нечего считать по трендам (пустой map → цикл ниже отработает)
            n_days_map = self.db.get_last_n_days_all(3) or {}

            athletes = self.db.get_all_athletes()
            athlete_map = {a["id"]: a for a in athletes}
            alerts = []

            for aid, days_data in n_days_map.items():
                if len(days_data) < 3:
                    continue
                a = athlete_map.get(aid, {})
                name = a.get("full_name", f"#{aid}")
                team = a.get("team", "?")

                # Паттерн 1: Пульс 3 дня подряд выше baseline + 1σ
                bl = self.db.get_individual_baseline(aid, 30)
                if bl and bl.get("median_hr") is not None and bl.get("std_hr", 0) is not None:
                    hr_thr = bl["median_hr"] + max(1.5 * (bl["std_hr"] or 0), bl["median_hr"] * 0.10)
                    hr_high = [d.get("resting_hr") and d["resting_hr"] > hr_thr for d in days_data]
                    if all(hr_high):
                        hrs = [d["resting_hr"] for d in days_data]
                        alerts.append(f"❤️ *{name}* ({team}): пульс 3 дня подряд выше нормы ({hrs[0]}→{hrs[1]}→{hrs[2]}, норма ~{int(bl['median_hr'])})")

                # Паттерн 2: Сон 3 дня подряд ниже baseline - 1σ
                if bl and bl.get("median", {}).get("sleep") is not None:
                    sleep_thr = bl["median"]["sleep"] - max(1.5 * (bl.get("std", {}).get("sleep", 0) or 0), 1.0)
                    sleep_low = [d.get("sleep_score") and d["sleep_score"] < sleep_thr for d in days_data]
                    if all(sleep_low):
                        sleeps = [d["sleep_score"] for d in days_data]
                        alerts.append(f"😴 *{name}* ({team}): сон 3 дня подряд ниже нормы ({sleeps[0]}→{sleeps[1]}→{sleeps[2]})")

                # Паттерн 3: Готовность 3 дня подряд < 5
                readiness = [d.get("readiness") for d in days_data]
                if all(r is not None and r < 5 for r in readiness):
                    alerts.append(f"🎯 *{name}* ({team}): готовность 3 дня подряд < 5 ({readiness[0]}→{readiness[1]}→{readiness[2]})")

                # Паттерн 4: Боль NRS 2 дня подряд >= 5
                pain = [d.get("pain_nrs") for d in days_data[-2:]]
                if all(p is not None and p >= 5 for p in pain):
                    loc = days_data[-1].get("pain_location", "")
                    alerts.append(f"🤕 *{name}* ({team}): боль NRS >= 5 два дня подряд ({pain[0]}→{pain[1]}) {loc}")

                # Паттерн 5: Пропуск опроса 2 дня подряд
                dates_present = [d.get("survey_date") for d in days_data]
                if len(dates_present) < 2:
                    alerts.append(f"⚪ *{name}* ({team}): нет опроса за 2+ дня")

            # Паттерны 6-7: цикл — задержка >7 дн. и аменорея ≥90 дн. (FLOW-модель).
            # Дедуп: повтор не чаще раза в 7 дней на девушку/тип (bot_settings).
            # Цикл-линии первыми: при обрезке дайджеста (15 штук) врач аменорею
            # точно увидит.
            from cycle_ui import cycle_alert_lines
            last_sent = {}
            for a in athletes:
                for kind in ("delay", "amenorrhea"):
                    v = self.db.get_setting(f"cycle_alert_{a['id']}_{kind}")
                    if v:
                        last_sent[f"cycle_alert_{a['id']}_{kind}"] = v
            cycle_fired = cycle_alert_lines(athletes, date.today(), last_sent)
            alerts = [line for _key, line in cycle_fired] + alerts

            # Отправка алертов врачам
            if alerts:
                alert_text = "🚨 *Trend-алерты за сегодня:*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                alert_text += "\n\n".join(alerts[:15])
                if len(alerts) > 15:
                    alert_text += f"\n\n… и ещё {len(alerts) - 15}"

                sent_ok = False
                for admin_id in self._full_access_ids():
                    try:
                        await bot.send_message(chat_id=admin_id, text=alert_text, parse_mode="Markdown")
                        sent_ok = True
                    except Exception as e:
                        logger.error(f"Trend alert send error: {e}")

                if sent_ok:
                    # Метки дедупа — только после успешной доставки: не ушло —
                    # завтра алерт повторится
                    for key, _line in cycle_fired:
                        self.db.set_setting(key, date.today().isoformat())

                logger.info(f"Trend alerts sent: {len(alerts)} patterns detected")
            else:
                logger.info("Trend alerts: no patterns detected")

        except Exception as e:
            logger.error(f"Trend alerts error: {e}", exc_info=True)


    async def _send_weekly_reports(self, context):
        """Воскресный персональный отчёт-картинка каждому спортсмену."""
        if TESTING:
            logger.info("TESTING MODE — воскресные отчёты не отправляются")
            return
        bot = context.bot
        athletes = self.db.get_all_athletes()
        sent = 0
        failed = 0
        for a in athletes:
            if a.get("bot_blocked"):
                continue
            try:
                # Пропускаем тех, у кого нет данных за неделю (нет смысла в отчёте)
                stats = self.db.get_athlete_stats(a["id"], 7)
                if not stats.get("total_days"):
                    continue
                trend = [v for _, v in self.db.get_trend_data(a["id"], "sleep", 7)]
                prev = self.db.get_athlete_stats_window(a["id"], 7, 7)
                baseline = self.db.get_individual_baseline(a["id"], 30)
                trends = {
                    "readiness": [v for _, v in self.db.get_trend_data(a["id"], "readiness", 7)],
                    "hr": [v for _, v in self.db.get_trend_data(a["id"], "hr", 7)],
                }
                pain = self.db.get_week_pain_summary(a["id"], 7)
                from weekly_report import generate_weekly_report
                # ранг без эмодзи (PIL не рисует цветные эмодзи)
                plain_rank = get_rank_plain(a.get("survey_streak", 0))
                # Генерация картинки (PIL) — блокирующая: уводим в поток
                path = await asyncio.to_thread(
                    generate_weekly_report,
                    full_name=a["full_name"],
                    age_group=a.get("age_group", "?"),
                    team=a.get("team", "?"),
                    streak=a.get("survey_streak", 0),
                    rank=plain_rank,
                    total=a.get("total_surveys", 0),
                    stats=stats,
                    trend_7d=trend,
                    prev=prev,
                    baseline=baseline,
                    trends=trends,
                    pain=pain,
                )
                with open(path, "rb") as f:
                    await bot.send_photo(
                        chat_id=a["telegram_id"],
                        photo=f,
                        caption=(
                            f"📊 *Твой недельный отчёт*, {self._first_name(a)}!\n"
                            f"Серия: 🔥 {a.get('survey_streak', 0)} "
                            f"{'день' if a.get('survey_streak', 0) == 1 else ('дня' if a.get('survey_streak', 0) % 10 in (2, 3, 4) and a.get('survey_streak', 0) % 100 not in (12, 13, 14) else 'дней')}\n\n"
                            f"Увидимся в понедельник! 💪"
                        ),
                        parse_mode="Markdown"
                    )
                sent += 1
            except Forbidden:
                await self._mark_bot_blocked(a)
            except Exception as e:
                failed += 1
                logger.error(f"Weekly report error for athlete #{a.get('id')}: {e}")
        logger.info(f"Weekly reports sent={sent} failed={failed} of {len(athletes)}")

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
        from telegram.request import HTTPXRequest
        req = HTTPXRequest(connect_timeout=15, read_timeout=60, write_timeout=30, pool_timeout=10)
        app = (
            Application.builder()
            .token(BOT_TOKEN)
            .request(req)
        )
        if PROXY_URL:
            app = app.proxy(PROXY_URL).get_updates_proxy(PROXY_URL)
        app = app.build()

        # Кэшируем бота — не создавать Bot(token=...) в _send_admin (ЛАКМУС №7)
        self._bot = app.bot

        # Привязываем очередь заданий, чтобы обработчики могли перепланировать рассылку
        self.job_queue = app.job_queue

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
        app.add_handler(CommandHandler("register_club", self.cmd_register_club))
        app.add_handler(CommandHandler("club_menu", self.cmd_club_menu))
        app.add_handler(CommandHandler("join", self.cmd_join))
        app.add_handler(CallbackQueryHandler(self.callback_handler))
        app.add_handler(MessageHandler(filters.Document.ALL, self.handle_document))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))

        # Планируем ежедневную рассылку
        # Читаем время из БД или используем значение по умолчанию
        try:
            saved_hour = self.db.get_setting("reminder_hour")
            saved_tz = self.db.get_setting("reminder_tz", "Asia/Yekaterinburg")
            # Пустая строка = напоминания выключены (кнопка «off»). Не вызывать int('').
            if saved_hour not in (None, ""):
                global REMINDER_HOUR, REMINDER_TZ
                try:
                    REMINDER_HOUR = int(saved_hour)
                    REMINDER_TZ = saved_tz
                    logger.info(f"Время из БД: {REMINDER_HOUR}:00, TZ: {REMINDER_TZ}")
                except (TypeError, ValueError):
                    logger.warning(f"Некорректное reminder_hour в БД: {saved_hour!r} — считаю выключенным")
                    REMINDER_HOUR = None
            else:
                # В БД пусто — оставляем дефолт из config (будет настроено ниже), но не крашимся
                logger.info("reminder_hour в БД пуст — используется дефолт из config")
        except Exception:
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
            # Убираем старые задания с этим именем, чтобы рестарты не плодили дубли
            for old in self.job_queue.get_jobs_by_name("daily_reminder"):
                old.schedule_removal()
            app.job_queue.run_daily(
                self._send_daily_reminder,
                time=target_utc.time(),
                days=tuple(range(7)),
                name="daily_reminder"
            )
            logger.info(f"Ежедневные напоминания настроены на {REMINDER_HOUR:02d}:{REMINDER_MINUTE:02d}")
            
            # Если время уже прошло сегодня — отправляем сразу
            if _reminder_should_fire_now(now_local, REMINDER_HOUR, REMINDER_MINUTE):
                logger.info("Время напоминания уже прошло сегодня — отправляю сразу")
                # Запускаем через 3 секунды, чтобы бот успел инициализироваться
                app.job_queue.run_once(self._send_daily_reminder, 3, name="initial_reminder")

        # Trend-алерты (проверка 10:15, через 15 мин после напоминаний)
        try:
            import pytz as _pytz2
            from datetime import datetime as _dt2
            trend_tz = _pytz2.timezone("Asia/Yekaterinburg")
            trend_target = trend_tz.localize(_dt2(1970, 1, 1, 10, 15))
            for old in self.job_queue.get_jobs_by_name("trend_alerts"):
                old.schedule_removal()
            app.job_queue.run_daily(
                self._check_trend_alerts,
                time=trend_target.timetz(),
                days=tuple(range(7)),
                name="trend_alerts"
            )
            logger.info("Trend-алерты настроены на 10:15 по Челябинску")
        except Exception as e:
            logger.warning(f"Trend alerts not scheduled: {e}")

        # Воскресный персональный отчёт-картинка (каждое воскресенье 18:00 по ЕКБ)
        try:
            import pytz as _pytz
            from datetime import datetime as _dt_cls
            sunday_tz = _pytz.timezone("Asia/Yekaterinburg")
            sunday_target = sunday_tz.localize(_dt_cls(1970, 1, 4, 18, 0))  # воскресенье
            for old in self.job_queue.get_jobs_by_name("weekly_report"):
                old.schedule_removal()
            app.job_queue.run_daily(
                self._send_weekly_reports,
                time=sunday_target.timetz(),
                days=(6,),  # Sunday (0=Mon, 6=Sun)
                name="weekly_report"
            )
            logger.info("Воскресные отчёты настроены на 18:00 по Челябинску")
        except Exception as e:
            logger.warning(f"Weekly report not scheduled: {e}")

        # Понедельничная сводка для тренеров (09:00 по ЕКБ)
        try:
            monday_target = _pytz.timezone("Asia/Yekaterinburg").localize(_dt_cls(1970, 1, 5, 9, 0))  # понедельник
            for old in self.job_queue.get_jobs_by_name("weekly_coach_summary"):
                old.schedule_removal()
            app.job_queue.run_daily(
                self._send_weekly_coach_summary,
                time=monday_target.timetz(),
                days=(0,),  # Monday (0=Mon)
                name="weekly_coach_summary"
            )
            logger.info("Сводка тренеров настроена на понедельник 09:00 по Челябинску")
        except Exception as e:
            logger.warning(f"Coach summary not scheduled: {e}")

        logger.info("Бот v3.0 запущен!")
        app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    bot = SportHealthBot()
    bot.run()

