"""Модуль работы с базой данных спортсменов v2.2."""

import json
import sqlite3
import logging
import threading
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any, Tuple
from pathlib import Path

from config import DB_PATH

logger = logging.getLogger(__name__)

# Команды ЧБК
TEAMS = [
    "Курчатов",
    "Челбаскет",
    "ЧБК-2",
    "ЧБК-Челябинская область",
    "Позитив",
    "Славянка ЧКПЗ",
    "Славянка ЧКПЗ-2",
    "Студ. сборная ЧО",
    "Шаблоны",
]

# Активные команды — только они показываются в интерфейсе врача/отчётах/весах.
# Спортсмены прочих команд остаются в БД (история цела), но скрыты.
ACTIVE_TEAMS = ("ЧБК-Челябинская область", "Славянка ЧКПЗ-2")


def compute_streak(survey_dates, anchor, outage_dates=frozenset()) -> int:
    """Серия = число реальных опросов подряд от якоря назад.

    День сбоя сервера без опроса перешагивается: не обрывает серию и не
    прибавляется к ней (решение владельца 22.11.2026, инцидент 21.11).
    Реальный опрос в день сбоя считается обычным днём. Обычный пропуск
    (не сбой) по-прежнему обрывает цепочку.
    """
    surveys = set(survey_dates)
    outages = set(outage_dates)
    streak = 0
    d = anchor
    while d in surveys or d in outages:
        if d in surveys:
            streak += 1
        d -= timedelta(days=1)
    return streak


def _is_real_name(name):
    """Имя похоже на настоящее (кириллица или первая заглавная), не ник-хэндл типа 'mfilkov'."""
    if not name:
        return False
    if any("а" <= ch.lower() <= "я" or ch in "ёЁ" for ch in name):
        return True
    return name[0].isupper() and len(name) >= 2


class Database:
    def __init__(self, db_path: str = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Ограничение прав на файл БД (только владелец может читать/писать)
        if self.db_path.exists():
            try:
                import stat
                self.db_path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600
            except Exception:
                pass  # Windows не поддерживает chmod

        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # Один коннект используется из event loop и фоновых потоков (to_thread):
        # все публичные методы сериализуются RLock (вложенные вызовы безопасны).
        self._lock = threading.RLock()
        # WAL-журнал повышает конкурентность чтения/записи (важно при асинхронном доступе)
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        self._create_tables()

    def _create_tables(self):
        with self._lock:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS athletes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER UNIQUE NOT NULL,
                    username TEXT,
                    full_name TEXT NOT NULL,
                    age_group TEXT NOT NULL,
                    team TEXT NOT NULL DEFAULT 'Не указана',
                    gender TEXT DEFAULT 'male',
                    cycle_length_default INTEGER DEFAULT 28,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_active DATE,
                    survey_streak INTEGER DEFAULT 0,
                    total_surveys INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS daily_wellness (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    athlete_id INTEGER NOT NULL,
                    survey_date DATE NOT NULL,
                    protocol_type TEXT NOT NULL DEFAULT 'simple',
                    sleep_score INTEGER CHECK (sleep_score BETWEEN 1 AND 7),
                    stress_score INTEGER CHECK (stress_score BETWEEN 1 AND 7),
                    fatigue_score INTEGER CHECK (fatigue_score BETWEEN 1 AND 7),
                    muscle_soreness INTEGER CHECK (muscle_soreness BETWEEN 1 AND 7),
                    mood_score INTEGER CHECK (mood_score BETWEEN 1 AND 7),
                    resting_hr INTEGER,
                    hrv_ms REAL,
                    had_training INTEGER DEFAULT 0,
                    sRPE_score INTEGER CHECK (sRPE_score BETWEEN 1 AND 10),
                    cycle_day INTEGER,
                    cycle_length INTEGER,
                    cycle_phase TEXT,
                    auto_recommendation TEXT,
                    complaints TEXT,
                    sleep_hours REAL,
                    readiness INTEGER CHECK (readiness BETWEEN 1 AND 10),
                    pain_nrs INTEGER CHECK (pain_nrs BETWEEN 0 AND 10),
                    pain_location TEXT,
                    pain_on_game INTEGER DEFAULT 0,
                    illness_flag TEXT,
                    analgesics INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(athlete_id, survey_date),
                    FOREIGN KEY (athlete_id) REFERENCES athletes(id)
                );

                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    athlete_id INTEGER NOT NULL,
                    alert_date DATE NOT NULL,
                    metric_type TEXT NOT NULL,
                    value REAL,
                    threshold REAL,
                    severity TEXT NOT NULL DEFAULT 'warning',
                    is_read INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (athlete_id) REFERENCES athletes(id)
                );
            """)
            self.conn.commit()
            self.conn.execute("CREATE TABLE IF NOT EXISTS bot_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            self.conn.execute("""CREATE TABLE IF NOT EXISTS questionnaires (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                athlete_id INTEGER NOT NULL,
                completed_at TIMESTAMP,
                age INTEGER,
                gender TEXT,
                phone TEXT,
                birth_date TEXT,
                position TEXT,
                level TEXT,
                experience TEXT,
                height INTEGER,
                weight INTEGER,
                trauma_12m TEXT,
                trauma_12m_detail TEXT,
                zones TEXT,
                pain_now TEXT,
                pain_now_detail TEXT,
                chronic TEXT,
                chronic_detail TEXT,
                surgery TEXT,
                surgery_detail TEXT,
                surgery_date TEXT,
                meds TEXT,
                meds_detail TEXT,
                allergies TEXT,
                allergies_detail TEXT,
                train_count INTEGER,
                train_duration INTEGER,
                season TEXT,
                form_score INTEGER,
                sleep_score INTEGER,
                warmup TEXT,
                recovery TEXT,
                water REAL,
                diet TEXT,
                pre_meal TEXT,
                supplements TEXT,
                motivation INTEGER,
                stress TEXT,
                match_state TEXT,
                reinjury_fear TEXT,
                goal TEXT,
                wish TEXT,
                FOREIGN KEY (athlete_id) REFERENCES athletes(id)
            )""")
            # Таблица блокировок (используется в is_athlete_banned/ban_tool — обязана существовать)
            self.conn.execute("""CREATE TABLE IF NOT EXISTS banned_athletes (
                athlete_id INTEGER PRIMARY KEY,
                banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                banned_by INTEGER,
                reason TEXT
            )""")
            # Согласие на обработку ПДн (152-ФЗ)
            self.conn.execute("""CREATE TABLE IF NOT EXISTS consents (
                user_id INTEGER PRIMARY KEY,
                athlete_id INTEGER,
                accepted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ip_tz TEXT
            )""")
            # Тренеры и их команды (ограниченная роль: просмотр своей команды, Excel, рекомендации)
            self.conn.execute("""CREATE TABLE IF NOT EXISTS coach_teams (
                telegram_id INTEGER NOT NULL,
                team TEXT NOT NULL,
                assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (telegram_id, team)
            )""")
            # Врачи (полный доступ, как у администратора; назначаются супер-админом)
            self.conn.execute("""CREATE TABLE IF NOT EXISTS doctors (
                telegram_id INTEGER PRIMARY KEY,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
            # Персистентное хранение пользовательских сессий (переживает рестарты)
            self.conn.execute("""CREATE TABLE IF NOT EXISTS user_states (
                user_id INTEGER PRIMARY KEY,
                payload TEXT,
                updated_at TIMESTAMP
            )""")
            # Индексы для ускорения частых запросов (производительность при росте данных)
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_wellness_athlete_date ON daily_wellness(athlete_id, survey_date)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_q_athlete ON questionnaires(athlete_id)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_athletes_team ON athletes(team)")
            # Schema version tracking
            self.conn.execute("""CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                description TEXT
            )""")

            # Consultations table
            self.conn.execute("""CREATE TABLE IF NOT EXISTS consultations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                athlete_id INTEGER NOT NULL,
                complaints TEXT,
                consult_date TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                admin_notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (athlete_id) REFERENCES athletes(id)
            )""")

            # Index for athletes by telegram_id (fast lookup during registration)
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_athletes_telegram ON athletes(telegram_id)")

        
            # Миграция: добавляем колонки в questionnaires для существующих БД (CREATE IF NOT EXISTS их не добавит)
            for col, coltype in (("phone", "TEXT"), ("birth_date", "TEXT"), ("surgery_date", "TEXT"),
                                 ("allergies", "TEXT"), ("allergies_detail", "TEXT")):
                try:
                    self.conn.execute(f"ALTER TABLE questionnaires ADD COLUMN {col} {coltype}")
                except Exception:
                    pass  # колонка уже существует
            # Миграция: complaints в daily_wellness (добавлена, но CREATE IF NOT EXISTS её не добавит в старые БД)
            try:
                self.conn.execute("ALTER TABLE daily_wellness ADD COLUMN complaints TEXT")
            except Exception:
                pass  # колонка уже есть
            # Миграция: auto_recommendation в daily_wellness (для PDF-отчётов врача)
            try:
                self.conn.execute("ALTER TABLE daily_wellness ADD COLUMN auto_recommendation TEXT")
            except Exception:
                pass  # колонка уже есть
            # Миграция: новые поля опроса (Фаза 2: часы сна, readiness, NRS-боль, локация, болезнь, обезболивающие)
            for col, coltype in (("sleep_hours", "REAL"), ("readiness", "INTEGER"),
                                 ("pain_nrs", "INTEGER"), ("pain_location", "TEXT"),
                                 ("pain_on_game", "INTEGER DEFAULT 0"),
                                 ("illness_flag", "TEXT"), ("analgesics", "INTEGER DEFAULT 0")):
                try:
                    self.conn.execute(f"ALTER TABLE daily_wellness ADD COLUMN {col} {coltype}")
                except Exception:
                    pass  # колонка уже есть
            # Миграция: bot_blocked в athletes — спортсмен заблокировал бота,
            # рассылки ему прекращаются до повторного /start
            try:
                self.conn.execute("ALTER TABLE athletes ADD COLUMN bot_blocked INTEGER DEFAULT 0")
            except Exception:
                pass  # колонка уже есть
            # Таблица данных с умных часов (Фаза 5)
            self.conn.execute("""CREATE TABLE IF NOT EXISTS watch_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                athlete_id INTEGER NOT NULL,
                record_date DATE,
                resting_hr INTEGER,
                heart_rate INTEGER,
                sleep_hours REAL,
                steps INTEGER,
                stress INTEGER,
                spo2 REAL,
                hrv REAL,
                weight REAL,
                source TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (athlete_id) REFERENCES athletes(id)
            )""")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_watch_athlete_date ON watch_data(athlete_id, record_date)")
            # ============ БИОИМПЕДАНСНЫЕ ВЕСЫ (GARLYN Bodyscan Master) ============
            # Состав тела: одна запись на спортсмена на дату (UPSERT). Расширенный набор метрик.
            self.conn.execute("""CREATE TABLE IF NOT EXISTS body_composition (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                athlete_id INTEGER NOT NULL,
                record_date DATE NOT NULL,
                source TEXT DEFAULT 'csv',
                recorded_by INTEGER,
                device_profile TEXT,
                height_cm INTEGER,
                weight_kg REAL,
                bmi REAL,
                body_fat_pct REAL,
                muscle_mass_kg REAL,
                body_water_pct REAL,
                bone_mass_kg REAL,
                visceral_fat_index REAL,
                subcutaneous_fat_pct REAL,
                lean_mass_kg REAL,
                skeletal_muscle_pct REAL,
                bone_unit TEXT,
                bmi_source TEXT,
                protein_pct REAL,
                bmr_kcal INTEGER,
                amr_kcal INTEGER,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (athlete_id) REFERENCES athletes(id),
                UNIQUE(athlete_id, record_date)
            )""")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_bc_athlete_date ON body_composition(athlete_id, record_date)")
            # Миграция: скелетная мышечная масса (в файле весов подписана «Cкелетные
            # мыщцы(%)») — раньше не маппилась и некуда было ложиться.
            try:
                self.conn.execute("ALTER TABLE body_composition ADD COLUMN skeletal_muscle_pct REAL")
            except Exception:
                pass  # колонка уже есть
            # Миграция: единица костной массы из файла весов («кг» или «%») —
            # на экране больше не угадываем.
            try:
                self.conn.execute("ALTER TABLE body_composition ADD COLUMN bone_unit TEXT")
            except Exception:
                pass  # колонка уже есть
            # Миграция: откуда ИМТ — из файла весов или досчитан нами.
            try:
                self.conn.execute("ALTER TABLE body_composition ADD COLUMN bmi_source TEXT")
            except Exception:
                pass  # колонка уже есть
            # Сопоставление профилей весов → спортсмен (для CSV на 16 профилей MovingLife)
            self.conn.execute("""CREATE TABLE IF NOT EXISTS scale_profiles (
                profile_name TEXT PRIMARY KEY,
                athlete_id INTEGER NOT NULL,
                mapped_by INTEGER,
                mapped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (athlete_id) REFERENCES athletes(id)
            )""")
            # Слот весов, который спортсмен сам назвал своим (один слот = один
            # спортсмен). Нужен, чтобы личная выгрузка MovingLife с несколькими
            # профилями разбиралась без тренера: спросили один раз — дальше сами.
            self.conn.execute("""CREATE TABLE IF NOT EXISTS scale_slots (
                athlete_id INTEGER PRIMARY KEY,
                profile_name TEXT NOT NULL,
                chosen_by INTEGER,
                chosen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (athlete_id) REFERENCES athletes(id)
            )""")
            # Персональные цели спортсменов
            self.conn.execute("""CREATE TABLE IF NOT EXISTS athlete_goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                athlete_id INTEGER NOT NULL,
                goal_type TEXT NOT NULL,
                target_value REAL,
                target_text TEXT,
                period_days INTEGER DEFAULT 30,
                started_at DATE DEFAULT CURRENT_DATE,
                achieved_at DATE,
                status TEXT DEFAULT 'active',
                FOREIGN KEY (athlete_id) REFERENCES athletes(id)
            )""")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_goals_athlete ON athlete_goals(athlete_id, status)")
            # Миграция: first_name (имя из Telegram) — для приветствий; формат full_name смешанный
            try:
                self.conn.execute("ALTER TABLE athletes ADD COLUMN first_name TEXT")
            except Exception:
                pass  # колонка уже есть
            # Бэкафилл: последнее слово «Фамилия Имя» = имя (для импортированных записей)
            for r in self.conn.execute(
                "SELECT id, full_name FROM athletes WHERE first_name IS NULL AND instr(full_name, ' ') > 0"
            ).fetchall():
                self.conn.execute(
                    "UPDATE athletes SET first_name = ? WHERE id = ?",
                    (r["full_name"].rsplit(" ", 1)[1], r["id"]),
                )
            # Чистка: хранимое имя обязано выглядеть как имя (могли записаться ники 'mfilkov', '♡')
            for r in self.conn.execute("SELECT id, full_name, first_name FROM athletes").fetchall():
                fn = r["first_name"]
                if fn and not _is_real_name(fn):
                    parts = str(r["full_name"]).split()
                    clean = parts[-1] if len(parts) > 1 else str(r["full_name"])
                    self.conn.execute(
                        "UPDATE athletes SET first_name = ? WHERE id = ?", (clean, r["id"])
                    )
            # Цикл (FLOW-модель, спека 2026-10-18): события «месячные начались» + кэш якоря
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS cycle_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    athlete_id INTEGER NOT NULL,
                    event_date DATE NOT NULL,
                    type TEXT NOT NULL CHECK (type IN ('start','anchor','end')),
                    source TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (athlete_id, event_date, type),
                    FOREIGN KEY (athlete_id) REFERENCES athletes(id)
                )""")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_cycle_events_athlete ON cycle_events(athlete_id, event_date)")
            # Миграция: кэш последней даты начала; добровольный статус («пока не было»/«нет данных»)
            try:
                self.conn.execute("ALTER TABLE athletes ADD COLUMN last_period_start DATE")
            except Exception:
                pass  # колонка уже есть
            try:
                self.conn.execute("ALTER TABLE athletes ADD COLUMN menarche_status TEXT")
            except Exception:
                pass  # колонка уже есть
            try:
                self.conn.execute("ALTER TABLE athletes ADD COLUMN cycle_length_learned INTEGER DEFAULT 0")
            except Exception:
                pass  # колонка уже есть
            # Проверка миграции: проглоченный ALTER (locked/ручная ковырка) иначе уронит
            # опрос у каждой девушки на первом же get_last_period_start
            cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(athletes)")}
            missing = {"last_period_start", "menarche_status", "cycle_length_learned"} - cols
            if missing:
                logger.error(f"Миграция цикла не прошла: нет колонок {sorted(missing)} — учёт цикла не будет работать")
            self.conn.commit()

    def get_setting(self, key: str, default: str = None) -> str:
        with self._lock:
            row = self.conn.execute("SELECT value FROM bot_settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> bool:
        with self._lock:
            try:
                self.conn.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)", (key, value))
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"Set setting error: {e}")
                return False

    def get_outage_dates(self) -> set:
        """Дни сбоя сервера (bot_settings.outage_dates, JSON-список ISO-дат).

        Битое значение → пустое множество + лог: save_survey не должен
        падать из-за реестра, а пропуски не должны «лечиться» молча.
        """
        raw = self.get_setting("outage_dates", "")
        if not raw:
            return set()
        try:
            items = json.loads(raw)
            if not isinstance(items, list):
                raise ValueError(f"ожидался список, получено {type(items).__name__}")
            return {date.fromisoformat(s) for s in items if isinstance(s, str)}
        except (ValueError, TypeError) as e:
            logger.error(f"outage_dates: битое значение {raw!r}: {e}")
            return set()

    def set_outage_dates(self, dates) -> bool:
        items = sorted({d.isoformat() for d in dates})
        return self.set_setting("outage_dates", json.dumps(items))

    def recalc_streaks(self, dry_run: bool = True, outage_dates=None) -> List[Dict]:
        """Пересчёт survey_streak всех атлетов с учётом дней сбоя.

        Якорь — последняя дата опроса атлета (серия «на момент последнего
        ответа», как в реконструкции 11.11), а не сегодня: у не ответивших
        сегодня серия не обнуляется. Меняет ТОЛЬКО survey_streak;
        total_surveys/last_active не трогает. Идемпотентен.
        outage_dates=None — взять реестр из bot_settings; явный набор —
        считать с ним, не записывая реестр (dry-run скрипта с --outage).
        Возвращает список изменений {id, full_name, old, new}.
        """
        with self._lock:
            outage = self.get_outage_dates() if outage_dates is None else set(outage_dates)
            changes = []
            athletes = self.conn.execute(
                "SELECT id, full_name, survey_streak FROM athletes").fetchall()
            for a in athletes:
                rows = self.conn.execute(
                    "SELECT DISTINCT survey_date FROM daily_wellness WHERE athlete_id = ?",
                    (a["id"],)).fetchall()
                dates = []
                for r in rows:
                    d = r["survey_date"]
                    if isinstance(d, str):
                        d = date.fromisoformat(d)
                    dates.append(d)
                if not dates:
                    continue
                new = compute_streak(dates, max(dates), outage)
                if new != a["survey_streak"]:
                    changes.append({"id": a["id"], "full_name": a["full_name"],
                                    "old": a["survey_streak"], "new": new})
                    if not dry_run:
                        self.conn.execute(
                            "UPDATE athletes SET survey_streak = ? WHERE id = ?",
                            (new, a["id"]))
            if not dry_run:
                self.conn.commit()
            return changes

    def update_athlete_gender(self, athlete_id: int, gender: str,
                              cycle_length: int = None) -> bool:
        with self._lock:
            try:
                if cycle_length is None:
                    # Смена только пола: длину и флаг «выучена» не трогаем —
                    # иначе пересоздание анкеты стирает персональный цикл
                    self.conn.execute("UPDATE athletes SET gender = ? WHERE id = ?",
                                      (gender, athlete_id))
                else:
                    # Ручная длина = ручной источник: флаг «выучена по истории» сбрасываем
                    self.conn.execute(
                        "UPDATE athletes SET gender = ?, cycle_length_default = ?, "
                        "cycle_length_learned = 0 WHERE id = ?",
                        (gender, cycle_length, athlete_id)
                    )
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"Update gender: {e}")
                return False

    def get_cycle_history(self, athlete_id: int, days: int = 30) -> List[Dict]:
        """Получить историю дней цикла для спортсменки."""
        with self._lock:
            rows = self.conn.execute("""
                SELECT survey_date, cycle_day, cycle_length, cycle_phase
                FROM daily_wellness
                WHERE athlete_id = ? AND cycle_day IS NOT NULL
                    AND survey_date >= date('now', '-' || ? || ' days')
                ORDER BY survey_date DESC
            """, (athlete_id, days)).fetchall()
            return [dict(r) for r in rows]

    # ---------- События цикла (FLOW-модель) ----------

    def add_cycle_event(self, athlete_id: int, event_date: date,
                        type: str = "start", source: str = "survey_button") -> bool:
        """Записать событие цикла; повтор того же дня/типа идемпотентен (двойной тап).

        Будущая дата отклоняется: день от неё бросал ValueError и вали отчёт по
        всем девушкам. 'start' пересиливает 'anchor' той же даты (одна дата —
        одно начало), обратный порядок — anchor игнорируется.
        'start'/'anchor' задвигают кэш last_period_start, но назад не откатывают
        (для ретро-коррекции есть correct_cycle_anchor).
        Даты храним ISO-строками — порядковое сравнение работает лексикографически.
        """
        with self._lock:
            if event_date > date.today():
                logger.warning(f"cycle_event: будущая дата {event_date} отклонена (ath #{athlete_id})")
                return False
            iso = event_date.isoformat()
            try:
                if type == "anchor":
                    has_start = self.conn.execute(
                        "SELECT 1 FROM cycle_events WHERE athlete_id=? AND event_date=? AND type='start'",
                        (athlete_id, iso)).fetchone()
                    if has_start:
                        return True  # это же начало уже отмечено как 'start'
                self.conn.execute(
                    "INSERT OR IGNORE INTO cycle_events (athlete_id, event_date, type, source) "
                    "VALUES (?, ?, ?, ?)", (athlete_id, iso, type, source))
                if type == "start":
                    self.conn.execute(
                        "DELETE FROM cycle_events WHERE athlete_id=? AND event_date=? AND type='anchor'",
                        (athlete_id, iso))
                if type in ("start", "anchor"):
                    self.conn.execute(
                        "UPDATE athletes SET last_period_start = ? WHERE id = ? "
                        "AND (last_period_start IS NULL OR last_period_start < ?)",
                        (iso, athlete_id, iso))
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"Add cycle event: {e}")
                return False

    def correct_cycle_anchor(self, athlete_id: int, new_date: date) -> bool:
        """Коррекция задним числом: начало было на new_date, а не позже.

        Стирает призрачные события 'start'/'anchor' после new_date (ошибочные тапы),
        ставит якорь ровно на new_date и снимает menarche_status (раз дата есть —
        менструация была). Источник истины после коррекции — эта дата.
        """
        with self._lock:
            if new_date > date.today():
                return False
            iso = new_date.isoformat()
            try:
                self.conn.execute(
                    "DELETE FROM cycle_events WHERE athlete_id = ? AND event_date > ? "
                    "AND type IN ('start','anchor')", (athlete_id, iso))
                self.conn.execute(
                    "INSERT OR IGNORE INTO cycle_events (athlete_id, event_date, type, source) "
                    "VALUES (?, ?, 'anchor', 'correction')", (athlete_id, iso))
                self.conn.execute(
                    "UPDATE athletes SET last_period_start = ?, menarche_status = NULL WHERE id = ?",
                    (iso, athlete_id))
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"Correct cycle anchor: {e}")
                return False

    def get_last_period_start(self, athlete_id: int) -> Optional[date]:
        """Кэш якоря (день цикла считается от него), None если якоря нет."""
        with self._lock:
            row = self.conn.execute(
                "SELECT last_period_start FROM athletes WHERE id = ?", (athlete_id,)).fetchone()
            if not row or not row["last_period_start"]:
                return None
            v = row["last_period_start"]
            return date.fromisoformat(str(v)) if isinstance(v, str) else v

    def get_cycle_events(self, athlete_id: int,
                         types: tuple = ("start", "anchor")) -> List[date]:
        """Даты событий цикла по возрастанию (обучение длины, экран «Мой цикл»)."""
        with self._lock:
            if not types:
                return []  # пустой кортеж -> "IN ()" на старых sqlite синтаксическая ошибка
            placeholders = ",".join("?" * len(types))
            rows = self.conn.execute(
                f"SELECT event_date FROM cycle_events WHERE athlete_id = ? "
                f"AND type IN ({placeholders}) ORDER BY event_date",
                (athlete_id, *types)).fetchall()
            return [date.fromisoformat(str(r["event_date"])) if isinstance(r["event_date"], str)
                    else r["event_date"] for r in rows]

    def set_menarche_status(self, athlete_id: int, status: Optional[str]) -> bool:
        """'pre' — пока не было (опрос не спрашивает), 'skip' — нет данных (не напоминать),
        None — снять флаги (вернулась через «Мой цикл»)."""
        with self._lock:
            try:
                self.conn.execute(
                    "UPDATE athletes SET menarche_status = ? WHERE id = ?", (status, athlete_id))
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"Set menarche status: {e}")
                return False

    def register_athlete(self, telegram_id: int, username: str,
                         full_name: str, age_group: str, team: str,
                         first_name: str = None) -> bool:
        with self._lock:
            try:
                self.conn.execute(
                    """INSERT INTO athletes
                       (telegram_id, username, full_name, age_group, team, last_active, first_name)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (telegram_id, username, full_name, age_group, team, date.today(), first_name)
                )
                self.conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def set_athlete_first_name(self, athlete_id: int, first_name: str) -> None:
        """Синхронизировать имя из Telegram-профиля (для приветствий)."""
        with self._lock:
            self.conn.execute(
                "UPDATE athletes SET first_name = ? WHERE id = ?", (first_name, athlete_id)
            )
            self.conn.commit()

    def get_athlete_by_telegram_id(self, telegram_id: int) -> Optional[Dict]:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM athletes WHERE telegram_id = ?", (telegram_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_all_athletes(self) -> List[Dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM athletes "
                "WHERE NOT EXISTS (SELECT 1 FROM coach_teams ct WHERE ct.telegram_id = athletes.telegram_id) "
                "ORDER BY team, full_name"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_today_survey_map(self) -> Dict[int, Dict]:
        """Один запрос: все сегодняшние опросы {athlete_id: survey_dict}."""
        with self._lock:
            today = date.today()
            rows = self.conn.execute(
                "SELECT * FROM daily_wellness WHERE survey_date = ?", (today,)
            ).fetchall()
            return {r["athlete_id"]: dict(r) for r in rows}

    def get_has_survey_today_map(self) -> Dict[int, bool]:
        """Один запрос: кто прошёл опрос сегодня {athlete_id: True/False}."""
        with self._lock:
            today = date.today()
            rows = self.conn.execute(
                "SELECT athlete_id FROM daily_wellness WHERE survey_date = ?", (today,)
            ).fetchall()
            done = {r["athlete_id"] for r in rows}
            return {a["id"]: (a["id"] in done) for a in self.get_all_athletes()}

    def get_questionnaire_map(self) -> Dict[int, Dict]:
        """Один запрос: все анкеты {athlete_id: questionnaire_dict}."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM questionnaires WHERE completed_at IS NOT NULL"
            ).fetchall()
            return {r["athlete_id"]: dict(r) for r in rows}

    def get_last_n_days_all(self, days: int = 3) -> Dict[int, List[Dict]]:
        """Один запрос: опросы за N дней {athlete_id: [survey_dict, ...]} (от старых к новым)."""
        with self._lock:
            rows = self.conn.execute("""
                SELECT athlete_id, survey_date, sleep_score, fatigue_score,
                       muscle_soreness, resting_hr, readiness, pain_nrs,
                       pain_location, had_training, cycle_phase, complaints
                FROM daily_wellness
                WHERE survey_date >= date('now', '-' || ? || ' days')
                ORDER BY athlete_id, survey_date ASC
            """, (days,)).fetchall()
            result = {}
            for r in rows:
                aid = r["athlete_id"]
                result.setdefault(aid, []).append(dict(r))
            return result

    def has_survey_today(self, athlete_id: int) -> bool:
        with self._lock:
            row = self.conn.execute(
                "SELECT id FROM daily_wellness WHERE athlete_id = ? AND survey_date = ?",
                (athlete_id, date.today())
            ).fetchone()
            return row is not None

    def get_survey_today(self, athlete_id: int) -> Optional[Dict]:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM daily_wellness WHERE athlete_id = ? AND survey_date = ?",
                (athlete_id, date.today())
            ).fetchone()
            return dict(row) if row else None

    def get_wellness_by_period(self, days: int = 7, team: str = None) -> List[Dict]:
        """Получить ВСЕ опросы по дням за период (не средние), опционально по команде."""
        with self._lock:
            if team:
                rows = self.conn.execute("""
                    SELECT a.full_name, a.team, a.age_group, a.id as athlete_id,
                           dw.survey_date, dw.protocol_type,
                           dw.sleep_score, dw.stress_score, dw.fatigue_score,
                           dw.muscle_soreness, dw.mood_score, dw.resting_hr,
                           dw.hrv_ms, dw.had_training, dw.sRPE_score,
                           dw.pain_nrs, dw.readiness, dw.sleep_hours,
                           dw.cycle_day, dw.cycle_phase, dw.complaints
                    FROM daily_wellness dw
                    JOIN athletes a ON a.id = dw.athlete_id
                    WHERE dw.survey_date >= date('now', '-' || ? || ' days') AND a.team = ?
                      AND NOT EXISTS (SELECT 1 FROM coach_teams ct WHERE ct.telegram_id = a.telegram_id)
                    ORDER BY a.full_name, dw.survey_date DESC
                """, (days, team)).fetchall()
            else:
                rows = self.conn.execute("""
                    SELECT a.full_name, a.team, a.age_group, a.id as athlete_id,
                           dw.survey_date, dw.protocol_type,
                           dw.sleep_score, dw.stress_score, dw.fatigue_score,
                           dw.muscle_soreness, dw.mood_score, dw.resting_hr,
                           dw.hrv_ms, dw.had_training, dw.sRPE_score,
                           dw.pain_nrs, dw.readiness, dw.sleep_hours,
                           dw.cycle_day, dw.cycle_phase, dw.complaints
                    FROM daily_wellness dw
                    JOIN athletes a ON a.id = dw.athlete_id
                    WHERE dw.survey_date >= date('now', '-' || ? || ' days')
                      AND NOT EXISTS (SELECT 1 FROM coach_teams ct WHERE ct.telegram_id = a.telegram_id)
                    ORDER BY a.full_name, dw.survey_date DESC
                """, (days,)).fetchall()
            return [dict(r) for r in rows]

    def get_team_week_summary(self, team: str, days: int = 7) -> Dict:
        """Сводка по команде за период (для еженедельного отчёта тренера)."""
        with self._lock:
            n_athletes = self.conn.execute(
                "SELECT COUNT(*) c FROM athletes WHERE team = ? "
                "AND NOT EXISTS (SELECT 1 FROM coach_teams ct WHERE ct.telegram_id = athletes.telegram_id)",
                (team,)
            ).fetchone()["c"]
            row = self.conn.execute("""
                SELECT COUNT(*) AS surveys,
                       AVG(readiness) AS avg_readiness,
                       SUM(CASE WHEN pain_nrs >= 5 THEN 1 ELSE 0 END) AS pain_hi,
                       SUM(CASE WHEN analgesics = 1 THEN 1 ELSE 0 END) AS analgesics,
                       SUM(CASE WHEN illness_flag IS NOT NULL AND illness_flag != '' THEN 1 ELSE 0 END) AS illness
                FROM daily_wellness
                WHERE athlete_id IN (SELECT id FROM athletes WHERE team = ?
                  AND NOT EXISTS (SELECT 1 FROM coach_teams ct WHERE ct.telegram_id = athletes.telegram_id))
                  AND survey_date >= date('now', '-' || ? || ' days')
            """, (team, days)).fetchone()
            active = self.conn.execute("""
                SELECT COUNT(DISTINCT athlete_id) c FROM daily_wellness
                WHERE athlete_id IN (SELECT id FROM athletes WHERE team = ?
                  AND NOT EXISTS (SELECT 1 FROM coach_teams ct WHERE ct.telegram_id = athletes.telegram_id))
                  AND survey_date >= date('now', '-' || ? || ' days')
            """, (team, days)).fetchone()["c"]
            d = dict(row)
            d["athletes"] = n_athletes
            d["active"] = active
            for k in ("pain_hi", "analgesics", "illness"):
                if d[k] is None:
                    d[k] = 0
            return d

    def save_survey(self, athlete_id: int, data: Dict[str, Any]) -> bool:
        with self._lock:
            today = date.today()

            existing = self.conn.execute(
                "SELECT id FROM daily_wellness WHERE athlete_id = ? AND survey_date = ?",
                (athlete_id, today)
            ).fetchone()

            if existing:
                self.conn.execute("""UPDATE daily_wellness SET
                    sleep_score=?, stress_score=?, fatigue_score=?,
                    muscle_soreness=?, mood_score=?, resting_hr=?,
                    hrv_ms=?, had_training=?, sRPE_score=?, protocol_type=?,
                    cycle_day=?, cycle_length=?, cycle_phase=?, complaints=?,
                    auto_recommendation=?, sleep_hours=?, readiness=?,
                    pain_nrs=?, pain_location=?, pain_on_game=?, illness_flag=?, analgesics=?
                    WHERE athlete_id=? AND survey_date=?""", (
                    data.get("sleep"), data.get("stress"), data.get("fatigue"),
                    data.get("soreness"), data.get("mood"), data.get("hr"),
                    data.get("hrv"), data.get("training", 0), data.get("srpe"),
                    data.get("protocol", "simple"),
                    data.get("cycle_day"), data.get("cycle_length"), data.get("cycle_phase"),
                    data.get("complaints"), data.get("auto_recommendation"),
                    data.get("sleep_hours"), data.get("readiness"),
                    data.get("pain_nrs"), data.get("pain_location"),
                    1 if data.get("pain_on_game") else 0,
                    data.get("illness_flag"), 1 if data.get("analgesics") else 0,
                    athlete_id, today
                ))
            else:
                self.conn.execute("""INSERT INTO daily_wellness
                    (athlete_id, survey_date, protocol_type, sleep_score,
                     stress_score, fatigue_score, muscle_soreness, mood_score,
                     resting_hr, hrv_ms, had_training, sRPE_score,
                     cycle_day, cycle_length, cycle_phase, complaints, auto_recommendation,
                     sleep_hours, readiness, pain_nrs, pain_location, pain_on_game, illness_flag, analgesics)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                    athlete_id, today, data.get("protocol", "simple"),
                    data.get("sleep"), data.get("stress"), data.get("fatigue"),
                    data.get("soreness"), data.get("mood"), data.get("hr"),
                    data.get("hrv"), data.get("training", 0), data.get("srpe"),
                    data.get("cycle_day"), data.get("cycle_length"), data.get("cycle_phase"),
                    data.get("complaints"), data.get("auto_recommendation"),
                    data.get("sleep_hours"), data.get("readiness"),
                    data.get("pain_nrs"), data.get("pain_location"),
                    1 if data.get("pain_on_game") else 0,
                    data.get("illness_flag"), 1 if data.get("analgesics") else 0
                ))

            # Обновляем streak. ВАЖНО: без LIMIT — серия должна расти
            # дальше 60 дней (баг от 2026-10: LIMIT 60 навечно запирал streak=60).
            # Дни сбоя сервера (outage_dates) перешагиваются — инцидент 21.11.2026.
            last_surveys = self.conn.execute(
                """SELECT DISTINCT survey_date FROM daily_wellness
                   WHERE athlete_id = ?
                   ORDER BY survey_date DESC""",
                (athlete_id,)
            ).fetchall()
            dates = []
            for row in last_surveys:
                d = row["survey_date"]
                if isinstance(d, str):
                    d = date.fromisoformat(d)
                dates.append(d)

            streak = compute_streak(dates, today, self.get_outage_dates())

            self.conn.execute(
                """UPDATE athletes SET
                   last_active = ?,
                   survey_streak = ?,
                   total_surveys = total_surveys + 1
                   WHERE id = ?""",
                (today, streak, athlete_id)
            )

            self.conn.commit()
            return True

    def update_cycle_length_from_history(self, athlete_id: int) -> Optional[int]:
        """Персонализация длины цикла: медиана интервалов между событиями начала
        месячных (cycle_events). Обновляет cycle_length_default, если медиана в
        норме 21-35. Возвращает новую длину или None (недостаточно данных / вне нормы)."""
        with self._lock:
            dates = self.get_cycle_events(athlete_id)  # start/anchor, по возрастанию
            if len(dates) < 3:  # нужно минимум 3 начала цикла → 2 интервала
                return None
            intervals = []
            prev = None
            for d in dates:
                if prev is not None:
                    gap = (d - prev).days
                    if gap >= 15 and gap <= 45:  # отсекаем пропуски/дубли
                        intervals.append(gap)
                prev = d
            if len(intervals) < 2:
                return None
            # медиана устойчива к выбросам (при чётном числе — .5 округляем вверх, предсказуемо)
            s = sorted(intervals)
            n = len(s)
            if n % 2 == 1:
                median = s[n // 2]
            else:
                median = (s[n // 2 - 1] + s[n // 2] + 1) // 2
            if 21 <= median <= 35:  # норма длины цикла (CYCLE_LENGTH_NORM_MIN/MAX из cycle_medicine)
                self.conn.execute(
                    "UPDATE athletes SET cycle_length_default = ?, cycle_length_learned = 1 WHERE id = ?",
                    (median, athlete_id)
                )
                self.conn.commit()
                return median
            return None

    def get_athlete_stats(self, athlete_id: int, days: int = 7) -> Dict:
        with self._lock:
            row = self.conn.execute("""
                SELECT
                    COUNT(*) as total_days,
                    AVG(sleep_score) as avg_sleep,
                    AVG(stress_score) as avg_stress,
                    AVG(fatigue_score) as avg_fatigue,
                    AVG(muscle_soreness) as avg_soreness,
                    AVG(mood_score) as avg_mood,
                    AVG(resting_hr) as avg_hr,
                    AVG(hrv_ms) as avg_hrv,
                    AVG(readiness) as avg_readiness
                FROM daily_wellness
                WHERE athlete_id = ? AND survey_date >= date('now', '-' || ? || ' days')
            """, (athlete_id, days)).fetchone()
            return dict(row) if row else {}

    def get_athlete_stats_window(self, athlete_id: int, days: int = 7, offset: int = 0) -> Dict:
        """Средние за окно [now-(offset+days), now-offset). offset=0 — последние `days` дней,
        offset=7 — предыдущая неделя (для сравнения «неделя к неделе»)."""
        with self._lock:
            start = offset + days
            row = self.conn.execute("""
                SELECT
                    COUNT(*) as total_days,
                    AVG(sleep_score) as avg_sleep,
                    AVG(stress_score) as avg_stress,
                    AVG(fatigue_score) as avg_fatigue,
                    AVG(muscle_soreness) as avg_soreness,
                    AVG(mood_score) as avg_mood,
                    AVG(resting_hr) as avg_hr,
                    AVG(hrv_ms) as avg_hrv,
                    AVG(readiness) as avg_readiness
                FROM daily_wellness
                WHERE athlete_id = ? AND survey_date >= date('now', '-' || ? || ' days')
                    AND survey_date < date('now', '-' || ? || ' days')
            """, (athlete_id, start, offset)).fetchone()
            return dict(row) if row else {}

    def get_week_pain_summary(self, athlete_id: int, days: int = 7) -> List[Dict]:
        """Жалобы за неделю: боль/локация/на игре/болезнь/обезболивающие (для блока боли в отчёте)."""
        with self._lock:
            rows = self.conn.execute("""
                SELECT survey_date, pain_nrs, pain_location, pain_on_game, illness_flag, analgesics
                FROM daily_wellness
                WHERE athlete_id = ? AND survey_date >= date('now', '-' || ? || ' days')
                  AND (pain_nrs > 0 OR pain_location != '' OR illness_flag != '' OR analgesics = 1)
                ORDER BY survey_date DESC
            """, (athlete_id, days)).fetchall()
            return [dict(r) for r in rows]

    def get_trend_data(self, athlete_id: int, metric: str, days: int = 7) -> List[Tuple]:
        with self._lock:
            column = {
                "sleep": "sleep_score",
                "stress": "stress_score",
                "fatigue": "fatigue_score",
                "soreness": "muscle_soreness",
                "mood": "mood_score",
                "hr": "resting_hr",
                "hrv": "hrv_ms",
                "readiness": "readiness",
            }.get(metric, "sleep_score")

            # Защита от SQL-инъекции: допускаем ТОЛЬКО whitelist-колонки
            allowed = ["sleep_score","stress_score","fatigue_score","muscle_soreness","mood_score","resting_hr","hrv_ms","readiness"]
            if column not in allowed:
                column = "sleep_score"

            rows = self.conn.execute(f"""
                SELECT survey_date, {column} as value
                FROM daily_wellness
                WHERE athlete_id = ? AND survey_date >= date('now', '-' || ? || ' days')
                ORDER BY survey_date
            """, (athlete_id, days)).fetchall()
            return [(row["survey_date"], row["value"]) for row in rows]

    def get_athletes_with_today_data(self) -> List[Dict]:
        """Получить всех спортсменов с сегодняшними данными (для отчета врача)."""
        with self._lock:
            rows = self.conn.execute("""
                SELECT a.id, a.full_name, a.team, a.age_group, a.survey_streak, a.total_surveys,
                       dw.sleep_score, dw.stress_score, dw.fatigue_score,
                       dw.muscle_soreness, dw.mood_score, dw.resting_hr, dw.hrv_ms
                FROM athletes a
                LEFT JOIN daily_wellness dw ON a.id = dw.athlete_id AND dw.survey_date = date('now')
                WHERE NOT EXISTS (SELECT 1 FROM coach_teams ct WHERE ct.telegram_id = a.telegram_id)
                ORDER BY a.team, a.full_name
            """).fetchall()
            return [dict(r) for r in rows]

    def get_athletes_week_stats(self) -> List[Dict]:
        """Получить всех спортсменов со средними за 7 дней (для отчета врача)."""
        with self._lock:
            rows = self.conn.execute("""
                SELECT a.id, a.full_name, a.team, a.age_group, a.survey_streak, a.total_surveys,
                       AVG(dw.sleep_score) as avg_sleep,
                       AVG(dw.stress_score) as avg_stress,
                       AVG(dw.fatigue_score) as avg_fatigue,
                       AVG(dw.resting_hr) as avg_hr,
                       COUNT(dw.id) as days_count
                FROM athletes a
                LEFT JOIN daily_wellness dw ON a.id = dw.athlete_id
                    AND dw.survey_date >= date('now', '-7 days')
                WHERE NOT EXISTS (SELECT 1 FROM coach_teams ct WHERE ct.telegram_id = a.telegram_id)
                GROUP BY a.id
                ORDER BY a.team, a.full_name
            """).fetchall()
            return [dict(r) for r in rows]

    def get_team_stats_period(self, team: str = None, days: int = 7) -> List[Dict]:
        """Получить статистику спортсменов за период, опционально по команде."""
        with self._lock:
            if team:
                rows = self.conn.execute("""
                    SELECT a.id, a.full_name, a.team, a.age_group, a.survey_streak, a.total_surveys,
                           AVG(dw.sleep_score) as avg_sleep,
                           AVG(dw.stress_score) as avg_stress,
                           AVG(dw.fatigue_score) as avg_fatigue,
                           AVG(dw.resting_hr) as avg_hr,
                           COUNT(dw.id) as days_count
                    FROM athletes a
                    LEFT JOIN daily_wellness dw ON a.id = dw.athlete_id
                        AND dw.survey_date >= date('now', '-' || ? || ' days')
                    WHERE a.team = ?
                    AND NOT EXISTS (SELECT 1 FROM coach_teams ct WHERE ct.telegram_id = a.telegram_id)
                    GROUP BY a.id
                    ORDER BY a.full_name
                """, (days, team)).fetchall()
            else:
                rows = self.conn.execute("""
                    SELECT a.id, a.full_name, a.team, a.age_group, a.survey_streak, a.total_surveys,
                           AVG(dw.sleep_score) as avg_sleep,
                           AVG(dw.stress_score) as avg_stress,
                           AVG(dw.fatigue_score) as avg_fatigue,
                           AVG(dw.resting_hr) as avg_hr,
                           COUNT(dw.id) as days_count
                    FROM athletes a
                    LEFT JOIN daily_wellness dw ON a.id = dw.athlete_id
                        AND dw.survey_date >= date('now', '-' || ? || ' days')
                    WHERE NOT EXISTS (SELECT 1 FROM coach_teams ct WHERE ct.telegram_id = a.telegram_id)
                    GROUP BY a.id
                    ORDER BY a.team, a.full_name
                """, (days,)).fetchall()
            return [dict(r) for r in rows]

    def get_athletes_by_team(self, team: str) -> List[Dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM athletes WHERE team = ? "
                "AND NOT EXISTS (SELECT 1 FROM coach_teams ct WHERE ct.telegram_id = athletes.telegram_id) "
                "ORDER BY full_name", (team,)
            ).fetchall()
            return [dict(r) for r in rows]


    # ============ КОНСУЛЬТАЦИИ ============
    def save_consultation(self, athlete_id: int, complaints: str, consult_date: str) -> bool:
        with self._lock:
            try:
                self.conn.execute(
                    "INSERT INTO consultations (athlete_id, complaints, consult_date) VALUES (?, ?, ?)",
                    (athlete_id, complaints, consult_date)
                )
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"save_consultation: {e}")
                return False

    def get_consultations(self, status: str = None) -> list:
        with self._lock:
            try:
                if status:
                    rows = self.conn.execute(
                        """SELECT c.*, a.full_name, a.team FROM consultations c
                           JOIN athletes a ON c.athlete_id = a.id
                           WHERE c.status = ? ORDER BY c.created_at DESC""",
                        (status,)
                    ).fetchall()
                else:
                    rows = self.conn.execute(
                        """SELECT c.*, a.full_name, a.team FROM consultations c
                           JOIN athletes a ON c.athlete_id = a.id
                           ORDER BY c.created_at DESC"""
                    ).fetchall()
                return [dict(r) for r in rows]
            except Exception as e:
                logger.error(f"get_consultations: {e}")
                return []

    def update_consultation_status(self, consult_id: int, status: str, admin_notes: str = None) -> bool:
        with self._lock:
            try:
                if admin_notes:
                    self.conn.execute(
                        "UPDATE consultations SET status = ?, admin_notes = ? WHERE id = ?",
                        (status, admin_notes, consult_id)
                    )
                else:
                    self.conn.execute(
                        "UPDATE consultations SET status = ? WHERE id = ?", (status, consult_id)
                    )
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"update_consultation_status: {e}")
                return False


    def delete_athlete(self, athlete_id: int) -> bool:
        with self._lock:
            try:
                self.conn.execute("DELETE FROM daily_wellness WHERE athlete_id = ?", (athlete_id,))
                self.conn.execute("DELETE FROM alerts WHERE athlete_id = ?", (athlete_id,))
                self.conn.execute("DELETE FROM questionnaires WHERE athlete_id = ?", (athlete_id,))
                self.conn.execute("DELETE FROM consultations WHERE athlete_id = ?", (athlete_id,))
                self.conn.execute("DELETE FROM banned_athletes WHERE athlete_id = ?", (athlete_id,))
                self.conn.execute("DELETE FROM athletes WHERE id = ?", (athlete_id,))
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"delete_athlete: {e}")
                return False

    def close(self):
        with self._lock:
            self.conn.close()

    def get_athlete_by_id(self, athlete_id):
        with self._lock:
            row = self.conn.execute("SELECT * FROM athletes WHERE id = ?", (athlete_id,)).fetchone()
            return dict(row) if row else None

    def search_athletes(self, query):
        with self._lock:
            like = f"%{query}%"
            rows = self.conn.execute("SELECT * FROM athletes WHERE (full_name LIKE ? OR username LIKE ? OR CAST(telegram_id AS TEXT) LIKE ?) "
                                     "AND NOT EXISTS (SELECT 1 FROM coach_teams ct WHERE ct.telegram_id = athletes.telegram_id) "
                                     "ORDER BY team, full_name", (like, like, f"%{query}%")).fetchall()
            return [dict(r) for r in rows]

    def is_athlete_banned(self, athlete_id):
        with self._lock:
            row = self.conn.execute("SELECT 1 FROM banned_athletes WHERE athlete_id = ?", (athlete_id,)).fetchone()
            return row is not None

    def ban_athlete(self, athlete_id, admin_id, reason="Заблокирован администратором"):
        with self._lock:
            try:
                self.conn.execute("INSERT OR REPLACE INTO banned_athletes (athlete_id, banned_by, reason) VALUES (?, ?, ?)", (athlete_id, admin_id, reason))
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"Ban athlete: {e}")
                return False

    def unban_athlete(self, athlete_id):
        with self._lock:
            try:
                self.conn.execute("DELETE FROM banned_athletes WHERE athlete_id = ?", (athlete_id,))
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"Unban athlete: {e}")
                return False

    def set_bot_blocked(self, athlete_id: int, flag: int = 1) -> bool:
        """Спортсмен заблокировал бота (1) или снова написал /start (0)."""
        with self._lock:
            try:
                self.conn.execute(
                    "UPDATE athletes SET bot_blocked = ? WHERE id = ?", (flag, athlete_id)
                )
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"set_bot_blocked: {e}")
                return False

    def get_bot_blocked(self, athlete_id: int) -> int:
        with self._lock:
            row = self.conn.execute(
                "SELECT bot_blocked FROM athletes WHERE id = ?", (athlete_id,)
            ).fetchone()
            return int(row["bot_blocked"] or 0) if row else 0

    def get_banned_athletes(self):
        with self._lock:
            rows = self.conn.execute("SELECT a.id, a.full_name, a.team, b.banned_at, b.reason FROM athletes a JOIN banned_athletes b ON a.id = b.athlete_id ORDER BY b.banned_at DESC").fetchall()
            return [dict(r) for r in rows]

    def get_questionnaire(self, athlete_id):
        with self._lock:
            row = self.conn.execute("SELECT * FROM questionnaires WHERE athlete_id = ?", (athlete_id,)).fetchone()
            if not row:
                return None
            d = dict(row)
            try:
                from encryptor import decrypt_value
                for pk in ("phone", "birth_date", "trauma_12m_detail", "pain_now_detail",
                           "chronic_detail", "surgery_detail", "surgery_date", "meds_detail",
                           "allergies_detail", "goal", "wish"):
                    if d.get(pk):
                        d[pk] = decrypt_value(d[pk])
            except Exception as e:
                logger.error(f"decrypt questionnaire fields: {e}")
            return d

    def has_questionnaire(self, athlete_id):
        with self._lock:
            row = self.conn.execute("SELECT 1 FROM questionnaires WHERE athlete_id = ?", (athlete_id,)).fetchone()
            return row is not None

    def has_incomplete_questionnaire(self, athlete_id):
        """Есть ли незавершённая анкета (completed_at IS NULL)."""
        with self._lock:
            row = self.conn.execute("SELECT 1 FROM questionnaires WHERE athlete_id = ? AND completed_at IS NULL", (athlete_id,)).fetchone()
            return row is not None

    def get_questionnaire_progress(self, athlete_id):
        """Получить данные незавершённой анкеты."""
        with self._lock:
            row = self.conn.execute("SELECT * FROM questionnaires WHERE athlete_id = ? AND completed_at IS NULL", (athlete_id,)).fetchone()
            if not row:
                return None
            d = dict(row)
            try:
                from encryptor import decrypt_value
                for pk in ("phone", "birth_date", "trauma_12m_detail", "pain_now_detail",
                           "chronic_detail", "surgery_detail", "surgery_date", "meds_detail",
                           "allergies_detail", "goal", "wish"):
                    if d.get(pk):
                        d[pk] = decrypt_value(d[pk])
            except Exception as e:
                logger.error(f"decrypt progress: {e}")
            return d

    def get_last_wellness(self, athlete_id, limit=7):
        """Последние N опросов спортсмена для тренда."""
        with self._lock:
            rows = self.conn.execute("""
                SELECT survey_date, sleep_score, stress_score, fatigue_score,
                       muscle_soreness, mood_score, resting_hr, sRPE_score,
                       cycle_day, cycle_length, cycle_phase, hrv_ms, complaints
                FROM daily_wellness WHERE athlete_id = ?
                ORDER BY survey_date DESC LIMIT ?
            """, (athlete_id, limit)).fetchall()
            return [dict(r) for r in rows]

    def get_athlete_surveys(self, athlete_id: int, days: int = 7) -> List[Dict]:
        """Опросы спортсмена за окно [today - days .. today] для текстового отчёта.

        Окно совпадает с SQL-выборкой других отчётов (date('now', '-N days')).
        """
        with self._lock:
            rows = self.conn.execute("""
                SELECT survey_date, sleep_score, stress_score, fatigue_score,
                       muscle_soreness, mood_score, resting_hr, hrv_ms,
                       auto_recommendation
                FROM daily_wellness
                WHERE athlete_id = ?
                  AND survey_date >= date('now', '-' || ? || ' days')
                ORDER BY survey_date DESC
            """, (athlete_id, days)).fetchall()
            return [dict(r) for r in rows]

    def save_questionnaire(self, athlete_id, data):
        # Whitelist допустимых колонок — защита от SQL-инъекции
        with self._lock:
            allowed = {"age","gender","position","level","experience","height","weight",
                       "trauma_12m","trauma_12m_detail","zones","pain_now","pain_now_detail",
                       "chronic","chronic_detail","surgery","surgery_detail","surgery_date","meds","meds_detail",
                       "allergies","allergies_detail",
                       "train_count","train_duration","season","form_score","sleep_score",
                       "warmup","recovery","water","diet","pre_meal","supplements",
                       "motivation","stress","match_state","reinjury_fear","goal","wish",
                       "phone","birth_date"}
            clean = {k: v for k, v in data.items() if k in allowed}
            # SQLite не хранит список напрямую — сериализуем list-значения (напр. supplements)
            for k, v in clean.items():
                if isinstance(v, list):
                    clean[k] = ", ".join(str(x) for x in v)
            # Шифруем персональные данные (телефон, др, тексты с мед. деталями) — см. encryptor
            try:
                from encryptor import encrypt_value
                enc_fields = ("phone", "birth_date", "trauma_12m_detail", "pain_now_detail",
                              "chronic_detail", "surgery_detail", "surgery_date", "meds_detail",
                              "allergies_detail", "goal", "wish")
                for pk in enc_fields:
                    if pk in clean and clean[pk] not in (None, ""):
                        clean[pk] = encrypt_value(clean[pk])
            except Exception as e:
                logger.error(f"encrypt questionnaire fields: {e}")
            if not clean:
                return
            existing = self.conn.execute("SELECT id FROM questionnaires WHERE athlete_id = ?", (athlete_id,)).fetchone()
            if existing:
                cols = ", ".join(f"{k} = ?" for k in clean)
                vals = list(clean.values()) + [athlete_id]
                self.conn.execute(f"UPDATE questionnaires SET {cols} WHERE athlete_id = ?", vals)
            else:
                cols = ", ".join(clean.keys())
                ph = ", ".join("?" for _ in clean)
                vals = list(clean.values())
                self.conn.execute(f"INSERT INTO questionnaires (athlete_id, {cols}) VALUES (?, {ph})", [athlete_id] + vals)
            self.conn.commit()

    def complete_questionnaire(self, athlete_id):
        with self._lock:
            self.conn.execute("UPDATE questionnaires SET completed_at = datetime('now') WHERE athlete_id = ?", (athlete_id,))
            self.conn.commit()

    # ============ СОГЛАСИЕ НА ОБРАБОТКУ ПДн (152-ФЗ) ============
    def has_consent(self, user_id) -> bool:
        with self._lock:
            try:
                row = self.conn.execute("SELECT 1 FROM consents WHERE user_id = ?", (user_id,)).fetchone()
                return row is not None
            except Exception:
                return False

    def record_consent(self, user_id, athlete_id=None) -> bool:
        with self._lock:
            try:
                self.conn.execute(
                    "INSERT OR REPLACE INTO consents (user_id, athlete_id, ip_tz) VALUES (?, ?, ?)",
                    (user_id, athlete_id, None)
                )
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"record_consent: {e}")
                return False

    def revoke_consent(self, user_id) -> bool:
        with self._lock:
            try:
                self.conn.execute("DELETE FROM consents WHERE user_id = ?", (user_id,))
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"revoke_consent: {e}")
                return False

    # ============ ПЕРСИСТЕНТНЫЕ СЕССИИ (user_states) ============
    def load_user_state(self, user_id):
        """Загрузить состояние пользователя из БД (JSON-строка). None если нет."""
        with self._lock:
            try:
                row = self.conn.execute("SELECT payload FROM user_states WHERE user_id = ?", (user_id,)).fetchone()
                if not row:
                    return None
                payload = row["payload"]
                # Попытка расшифровки (если зашифровано)
                try:
                    from encryptor import decrypt_value
                    decrypted = decrypt_value(payload)
                    if decrypted and decrypted != payload:
                        import json as _json
                        return _json.loads(decrypted)
                except Exception:
                    pass
                # Если не зашифровано — возвращаем как есть (обратная совместимость)
                import json as _json
                return _json.loads(payload)
            except Exception as e:
                logger.error(f"load_user_state: {e}")
                return None

    def save_user_state(self, user_id, payload) -> bool:
        with self._lock:
            try:
                import json as _json
                from encryptor import encrypt_value
                json_str = _json.dumps(payload, ensure_ascii=False, default=str)
                # Шифруем payload перед сохранением
                encrypted = encrypt_value(json_str)
                self.conn.execute(
                    "INSERT OR REPLACE INTO user_states (user_id, payload, updated_at) VALUES (?, ?, datetime('now'))",
                    (user_id, encrypted)
                )
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"save_user_state: {e}")
                return False

    def delete_user_state(self, user_id) -> bool:
        with self._lock:
            try:
                self.conn.execute("DELETE FROM user_states WHERE user_id = ?", (user_id,))
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"delete_user_state: {e}")
                return False

    # ============ ИНДИВИДУАЛЬНАЯ БАЗОВАЯ ЛИНИЯ (личная норма) ============
    def get_individual_baseline(self, athlete_id, days=30):
        """Личная норма спортсмена по последним `days` опросам.
        Возвращает {'n', 'avg_hr', 'median_hr', 'std_hr',
                     'avg': {sleep,...}, 'median': {sleep,...}, 'std': {sleep,...}} или None (данных < 5)."""
        import statistics
        rows = self.get_last_wellness(athlete_id, days)
        if len(rows) < 5:
            return None
        sums = {"sleep": [], "stress": [], "fatigue": [], "soreness": [], "mood": []}
        hr = []
        for r in rows:
            if r.get("sleep_score") is not None:
                sums["sleep"].append(r["sleep_score"])
            if r.get("stress_score") is not None:
                sums["stress"].append(r["stress_score"])
            if r.get("fatigue_score") is not None:
                sums["fatigue"].append(r["fatigue_score"])
            if r.get("muscle_soreness") is not None:
                sums["soreness"].append(r["muscle_soreness"])
            if r.get("mood_score") is not None:
                sums["mood"].append(r["mood_score"])
            if r.get("resting_hr") is not None:
                hr.append(r["resting_hr"])

        def _stats(vals):
            if not vals:
                return {"avg": None, "median": None, "std": None}
            avg = sum(vals) / len(vals)
            med = statistics.median(vals)
            std = statistics.pstdev(vals) if len(vals) >= 2 else 0
            return {"avg": avg, "median": med, "std": std}

        result = {"n": len(rows)}
        for key, vals in sums.items():
            s = _stats(vals)
            result.setdefault("avg", {})[key] = s["avg"]
            result.setdefault("median", {})[key] = s["median"]
            result.setdefault("std", {})[key] = s["std"]
        hr_s = _stats(hr)
        result["avg_hr"] = hr_s["avg"]
        result["median_hr"] = hr_s["median"]
        result["std_hr"] = hr_s["std"]
        return result

    def get_phase_baseline(self, athlete_id, phase_key, days=90):
        """Личная норма спортсмена для конкретной фазы цикла.
        Возвращает {'n', 'median_hr', 'std_hr', 'median': {sleep,...}, 'std': {sleep,...}} или None."""
        import statistics
        # Маппинг ключей фаз на русские названия в БД
        phase_map = {
            "menstruation": ["менструа"],
            "follicular": ["фоллику"],
            "ovulation": ["овуля"],
            "luteal": ["люте", "желт"],
        }
        like_patterns = phase_map.get(phase_key, [phase_key])
        rows = self.get_last_wellness(athlete_id, days)
        # Фильтруем по фазе
        filtered = []
        for r in rows:
            cp = (r.get("cycle_phase") or "").lower()
            if any(p in cp for p in like_patterns):
                filtered.append(r)
        if len(filtered) < 3:
            return None

        sums = {"sleep": [], "stress": [], "fatigue": [], "soreness": [], "mood": []}
        hr = []
        for r in filtered:
            for key, db_col in [("sleep", "sleep_score"), ("stress", "stress_score"),
                                ("fatigue", "fatigue_score"), ("soreness", "muscle_soreness"),
                                ("mood", "mood_score")]:
                v = r.get(db_col)
                if v is not None:
                    sums[key].append(v)
            v = r.get("resting_hr")
            if v is not None:
                hr.append(v)

        def _stats(vals):
            if not vals:
                return {"median": None, "std": None}
            med = statistics.median(vals)
            std = statistics.pstdev(vals) if len(vals) >= 2 else 0
            return {"median": med, "std": std}

        result = {"n": len(filtered), "phase": phase_key}
        for key, vals in sums.items():
            s = _stats(vals)
            result.setdefault("median", {})[key] = s["median"]
            result.setdefault("std", {})[key] = s["std"]
        hr_s = _stats(hr)
        result["median_hr"] = hr_s["median"]
        result["std_hr"] = hr_s["std"]
        return result

    def get_athletes_with_complaints(self, days=7):
        """Спортсмены с жалобами за последние N дней."""
        with self._lock:
            try:
                rows = self.conn.execute("""
                    SELECT a.id, a.telegram_id, a.full_name, a.team, a.age_group,
                           dw.complaints, dw.survey_date, dw.sleep_score, dw.stress_score,
                           dw.fatigue_score, dw.muscle_soreness, dw.mood_score, dw.resting_hr
                    FROM daily_wellness dw
                    JOIN athletes a ON dw.athlete_id = a.id
                    WHERE dw.complaints IS NOT NULL AND dw.complaints != ''
                    AND dw.survey_date >= date('now', ?)
                    AND NOT EXISTS (SELECT 1 FROM coach_teams ct WHERE ct.telegram_id = a.telegram_id)
                    ORDER BY dw.survey_date DESC
                """, (f"-{days} days",)).fetchall()
                return [dict(r) for r in rows]
            except Exception as e:
                logger.error(f"get_athletes_with_complaints: {e}")
                return []

    def save_watch_data(self, athlete_id: int, data: Dict[str, Any], record_date=None) -> bool:
        """Сохранить данные с умных часов. Возвращает True при успехе."""
        with self._lock:
            try:
                if record_date is None:
                    record_date = date.today()
                # Ключи от watch_parser: 💓 Пульс → resting_hr, 😴 Сон → sleep_hours, 🏃 Шаги → steps,
                # 😰 Стресс → stress, 🫁 SpO2 → spo2, 📊 HRV → hrv, ⚖️ Вес → weight
                def _num(v):
                    try:
                        if v is None:
                            return None
                        if isinstance(v, (int, float)):
                            return float(v)
                        s = str(v).replace(",", ".").replace("ч", "").strip()
                        if not s:
                            return None
                        return float(s)
                    except (TypeError, ValueError):
                        return None

                self.conn.execute("""INSERT INTO watch_data
                    (athlete_id, record_date, resting_hr, heart_rate, sleep_hours, steps,
                     stress, spo2, hrv, weight, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                    athlete_id, record_date.isoformat(),
                    _num(data.get("💓 Пульс")), _num(data.get("heart_rate")),
                    _num(data.get("😴 Сон")), _num(data.get("🏃 Шаги")),
                    _num(data.get("😰 Стресс")), _num(data.get("🫁 SpO2")),
                    _num(data.get("📊 HRV")), _num(data.get("⚖️ Вес")),
                    data.get("_source", "watch")
                ))
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"save_watch_data: {e}")
                return False

    def get_watch_data_period(self, days: int = 7, team: str = None) -> List[Dict]:
        """Получить данные часов за период (для Excel-отчёта)."""
        with self._lock:
            if team:
                rows = self.conn.execute("""
                    SELECT a.full_name, a.team, a.age_group, a.id as athlete_id,
                           wd.record_date, wd.resting_hr, wd.heart_rate, wd.sleep_hours,
                           wd.steps, wd.stress, wd.spo2, wd.hrv, wd.weight
                    FROM watch_data wd
                    JOIN athletes a ON a.id = wd.athlete_id
                    WHERE wd.record_date >= date('now', '-' || ? || ' days') AND a.team = ?
                    ORDER BY a.full_name, wd.record_date DESC
                """, (days, team)).fetchall()
            else:
                rows = self.conn.execute("""
                    SELECT a.full_name, a.team, a.age_group, a.id as athlete_id,
                           wd.record_date, wd.resting_hr, wd.heart_rate, wd.sleep_hours,
                           wd.steps, wd.stress, wd.spo2, wd.hrv, wd.weight
                    FROM watch_data wd
                    JOIN athletes a ON a.id = wd.athlete_id
                    WHERE wd.record_date >= date('now', '-' || ? || ' days')
                    ORDER BY a.full_name, wd.record_date DESC
                """, (days,)).fetchall()
            return [dict(r) for r in rows]

    # ============ БИОИМПЕДАНСНЫЕ ВЕСЫ (GARLYN Bodyscan Master) ============

    BC_FIELDS = (
        "weight_kg", "bmi", "body_fat_pct", "muscle_mass_kg", "body_water_pct",
        "bone_mass_kg", "visceral_fat_index", "subcutaneous_fat_pct",
        "lean_mass_kg", "protein_pct", "bmr_kcal", "amr_kcal",
    )

    def save_body_composition(self, athlete_id: int, record_date,
                              data: Dict[str, Any], source: str = "csv",
                              recorded_by: int = None, device_profile: str = None) -> bool:
        """Сохранение замера биоимпедансных весов. UPSERT по (athlete_id, record_date):
        повторный замер за тот же день обновляет существующую запись, а не дублирует."""
        with self._lock:
            try:
                if record_date is None:
                    record_date = date.today()
                if isinstance(record_date, date):
                    record_date = record_date.isoformat()

                def _num(v):
                    try:
                        if v is None:
                            return None
                        if isinstance(v, (int, float)):
                            return float(v)
                        s = str(v).replace(",", ".").strip()
                        if not s:
                            return None
                        return float(s)
                    except (TypeError, ValueError):
                        return None

                def _int(v):
                    try:
                        if v is None:
                            return None
                        return int(round(float(str(v).replace(",", "."))))
                    except (TypeError, ValueError):
                        return None

                cols = ["athlete_id", "record_date", "source", "recorded_by", "device_profile",
                        "height_cm", "weight_kg", "bmi", "body_fat_pct", "muscle_mass_kg",
                        "body_water_pct", "bone_mass_kg", "visceral_fat_index",
                        "subcutaneous_fat_pct", "lean_mass_kg", "skeletal_muscle_pct",
                        "bone_unit", "bmi_source", "protein_pct",
                        "bmr_kcal", "amr_kcal", "notes"]
                vals = [
                    athlete_id, record_date, source, recorded_by, device_profile,
                    _int(data.get("height_cm")), _num(data.get("weight_kg")),
                    _num(data.get("bmi")), _num(data.get("body_fat_pct")),
                    _num(data.get("muscle_mass_kg")), _num(data.get("body_water_pct")),
                    _num(data.get("bone_mass_kg")), _num(data.get("visceral_fat_index")),
                    _num(data.get("subcutaneous_fat_pct")), _num(data.get("lean_mass_kg")),
                    _num(data.get("skeletal_muscle_pct")), data.get("bone_unit"),
                    data.get("bmi_source"),
                    _num(data.get("protein_pct")), _int(data.get("bmr_kcal")),
                    _int(data.get("amr_kcal")), data.get("notes"),
                ]
                placeholders = ", ".join("?" for _ in cols)
                updates = ", ".join(f"{c}=excluded.{c}" for c in
                                    ("source", "recorded_by", "device_profile", "height_cm", "weight_kg", "bmi",
                                     "body_fat_pct", "muscle_mass_kg", "body_water_pct", "bone_mass_kg",
                                     "visceral_fat_index", "subcutaneous_fat_pct", "lean_mass_kg", "skeletal_muscle_pct",
                                     "bone_unit", "bmi_source", "protein_pct",
                                     "bmr_kcal", "amr_kcal", "notes"))
                self.conn.execute(
                    f"""INSERT INTO body_composition ({', '.join(cols)})
                        VALUES ({placeholders})
                        ON CONFLICT(athlete_id, record_date) DO UPDATE SET {updates}""",
                    vals,
                )
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"save_body_composition: {e}")
                return False

    def get_body_composition(self, athlete_id: int, days: int = None) -> List[Dict]:
        """История замеров состава тела спортсмена (по убыванию даты)."""
        with self._lock:
            try:
                sql = """SELECT * FROM body_composition WHERE athlete_id = ?"""
                params: list = [athlete_id]
                if days:
                    sql += " AND record_date >= date('now', ?)"
                    params.append(f"-{days} days")
                sql += " ORDER BY record_date DESC"
                return [dict(r) for r in self.conn.execute(sql, params).fetchall()]
            except Exception as e:
                logger.error(f"get_body_composition: {e}")
                return []

    def get_latest_body_composition(self, athlete_id: int) -> Optional[Dict]:
        """Последний замер состава тела спортсмена."""
        with self._lock:
            try:
                row = self.conn.execute(
                    """SELECT * FROM body_composition
                       WHERE athlete_id = ? ORDER BY record_date DESC LIMIT 1""",
                    (athlete_id,),
                ).fetchone()
                return dict(row) if row else None
            except Exception as e:
                logger.error(f"get_latest_body_composition: {e}")
                return None

    def get_bc_trend(self, athlete_id: int, days: int = 30) -> Dict[str, list]:
        """Тренд состава тела за период по ключевым метрикам:
        { 'weight_kg': [(date, val), ...], 'body_fat_pct': [...], 'muscle_mass_kg': [...],
          'body_water_pct': [...] } — по возрастанию даты."""
        with self._lock:
            out: Dict[str, list] = {f: [] for f in ("weight_kg", "body_fat_pct", "muscle_mass_kg", "body_water_pct")}
            try:
                rows = self.conn.execute(
                    """SELECT record_date, weight_kg, body_fat_pct, muscle_mass_kg, body_water_pct
                       FROM body_composition WHERE athlete_id = ? AND record_date >= date('now', ?)
                       ORDER BY record_date ASC""",
                    (athlete_id, f"-{days} days"),
                ).fetchall()
                for r in rows:
                    for f in out:
                        v = r[f]
                        if v is not None:
                            out[f].append((r["record_date"], v))
                return out
            except Exception as e:
                logger.error(f"get_bc_trend: {e}")
                return out

    def get_bc_latest_all(self, team: str = None) -> List[Dict]:
        """Последний замер каждого спортсмена — БЕЗ фильтра по давности.

        Нужен для Excel-отчёта «по запросу»: регулярного графика взвешиваний нет,
        и режим «за сутки» отдавал пустой файл именно тогда, когда в него заходят.
        Тренерские профили (telegram_id есть в coach_teams) исключаются, как и в
        get_bc_period.
        """
        with self._lock:
            try:
                sql = """
                    SELECT bc.*, a.full_name, a.team, a.age_group, a.id as athlete_id
                    FROM body_composition bc
                    JOIN athletes a ON a.id = bc.athlete_id
                    JOIN (SELECT athlete_id, MAX(record_date) AS mr FROM body_composition
                          GROUP BY athlete_id) last ON last.athlete_id = bc.athlete_id
                         AND last.mr = bc.record_date
                    WHERE NOT EXISTS (SELECT 1 FROM coach_teams ct
                                      WHERE ct.telegram_id = a.telegram_id)
                """
                params: list = []
                if team:
                    sql += " AND a.team = ?"
                    params.append(team)
                sql += " ORDER BY a.team, a.full_name"
                return [dict(r) for r in self.conn.execute(sql, params).fetchall()]
            except Exception as e:
                logger.error(f"get_bc_latest_all: {e}")
                return []

    def get_bc_period(self, days: int = 90, team: str = None) -> List[Dict]:
        """Получить данные состава тела за период (для Excel-отчёта)."""
        with self._lock:
            try:
                sql = """
                    SELECT bc.*, a.full_name, a.team, a.age_group, a.id as athlete_id
                    FROM body_composition bc
                    JOIN athletes a ON a.id = bc.athlete_id
                    WHERE bc.record_date >= date('now', '-' || ? || ' days')
                      AND NOT EXISTS (SELECT 1 FROM coach_teams ct WHERE ct.telegram_id = a.telegram_id)
                """
                params: list = [days]
                if team:
                    sql += " AND a.team = ?"
                    params.append(team)
                sql += " ORDER BY a.team, a.full_name, bc.record_date DESC"
                return [dict(r) for r in self.conn.execute(sql, params).fetchall()]
            except Exception as e:
                logger.error(f"get_bc_period: {e}")
                return []

    def get_scale_profiles(self) -> Dict[str, int]:
        """Сопоставление «профиль весов → athlete_id»."""
        with self._lock:
            try:
                return {r["profile_name"]: r["athlete_id"] for r in
                        self.conn.execute("SELECT profile_name, athlete_id FROM scale_profiles").fetchall()}
            except Exception as e:
                logger.error(f"get_scale_profiles: {e}")
                return {}

    def get_scale_slot(self, athlete_id: int):
        """Слот весов, который спортсмен назвал своим (None — не выбирал)."""
        with self._lock:
            try:
                row = self.conn.execute(
                    "SELECT profile_name FROM scale_slots WHERE athlete_id = ?",
                    (athlete_id,)).fetchone()
                return row["profile_name"] if row else None
            except Exception as e:
                logger.error(f"get_scale_slot: {e}")
                return None

    def set_scale_slot(self, athlete_id: int, profile_name: str,
                       chosen_by: int = None) -> bool:
        """Запомнить слот за спортсменом (перезапись разрешена только им самим)."""
        with self._lock:
            try:
                self.conn.execute(
                    """INSERT INTO scale_slots (athlete_id, profile_name, chosen_by, chosen_at)
                       VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                       ON CONFLICT(athlete_id) DO UPDATE SET
                           profile_name=excluded.profile_name,
                           chosen_by=excluded.chosen_by,
                           chosen_at=CURRENT_TIMESTAMP""",
                    (athlete_id, profile_name, chosen_by))
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"set_scale_slot: {e}")
                return False

    def get_slot_claims(self):
        """{profile_name: athlete_id} из самодеклараций спортсменов."""
        with self._lock:
            try:
                return {r["profile_name"]: r["athlete_id"] for r in self.conn.execute(
                    "SELECT profile_name, athlete_id FROM scale_slots").fetchall()}
            except Exception as e:
                logger.error(f"get_slot_claims: {e}")
                return {}

    def set_scale_profile(self, profile_name: str, athlete_id: int, mapped_by: int = None) -> bool:
        """Задать/заменить сопоставление профиля весов спортсмену."""
        with self._lock:
            try:
                self.conn.execute(
                    """INSERT INTO scale_profiles (profile_name, athlete_id, mapped_by, mapped_at)
                       VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                       ON CONFLICT(profile_name) DO UPDATE SET athlete_id=excluded.athlete_id,
                           mapped_by=excluded.mapped_by, mapped_at=CURRENT_TIMESTAMP""",
                    (profile_name, athlete_id, mapped_by),
                )
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"set_scale_profile: {e}")
                return False

    def rhr_flag(self, athlete_id: int, current_hr: Optional[float], days: int = 7) -> bool:
        """RHR-флаг (доказательный, по научной критике): база = 7-дневная роллинг-медиана,
        отклонение ≥2× типичной ошибки оптического пульса (3-5 уд/мин) → +8 уд/мин порог.
        Только 2 дня подряд не требуем здесь (решается в боте по prev)."""
        with self._lock:
            try:
                if current_hr is None:
                    return False
                rows = self.conn.execute("""
                    SELECT resting_hr FROM daily_wellness
                    WHERE athlete_id = ? AND resting_hr IS NOT NULL
                    ORDER BY survey_date DESC LIMIT ?
                """, (athlete_id, days)).fetchall()
                vals = [r["resting_hr"] for r in rows]
                if len(vals) < 3:
                    return False
                # роллинг-медиана (последние 7, но не включая сегодняшний)
                base = sorted(vals[1:])  # исключаем сегодняшний (он первый)
                if not base:
                    return False
                median = base[len(base) // 2]
                # типичная ошибка оптического пульса 3-5 уд/мин → порог 2× = 8-10 уд/мин
                threshold = max(8, int(median * 0.10))
                return current_hr >= median + threshold
            except Exception as e:
                logger.error(f"rhr_flag: {e}")
                return False

    def ewma_acwr(self, athlete_id: int, alpha_acute: float = 0.3, alpha_chronic: float = 0.1) -> Optional[Dict]:
        """EWMA ACWR (uncoupled): acute не входит в chronic. Возвращает None при <28 дней sRPE.
        Пороги Габбета не применяем к юным (калибровка на своих данных) — возвращаем значения."""
        with self._lock:
            try:
                rows = self.conn.execute("""
                    SELECT survey_date, sRPE_score FROM daily_wellness
                    WHERE athlete_id = ? AND sRPE_score IS NOT NULL
                    ORDER BY survey_date
                """, (athlete_id,)).fetchall()
                if len(rows) < 28:
                    return None
                acute = chronic = 0.0
                for i, r in enumerate(rows):
                    val = float(r["sRPE_score"])
                    if i == 0:
                        acute = chronic = val
                        continue
                    acute = val * alpha_acute + acute * (1 - alpha_acute)
                    chronic = val * alpha_chronic + chronic * (1 - alpha_chronic)
                if chronic <= 0:
                    return None
                return {"acwr": round(acute / chronic, 2), "acute": round(acute, 1),
                        "chronic": round(chronic, 1), "n": len(rows)}
            except Exception as e:
                logger.error(f"ewma_acwr: {e}")
                return None

    # ============ ТРЕНЕРЫ (coach) ============
    def get_coach_teams(self, telegram_id) -> List[str]:
        """Список команд, закреплённых за тренером. Пусто = не тренер."""
        with self._lock:
            try:
                rows = self.conn.execute(
                    "SELECT team FROM coach_teams WHERE telegram_id = ? ORDER BY team",
                    (telegram_id,)
                ).fetchall()
                return [r["team"] for r in rows]
            except Exception as e:
                logger.error(f"get_coach_teams: {e}")
                return []

    def is_coach(self, telegram_id) -> bool:
        return bool(self.get_coach_teams(telegram_id))

    def set_coach_teams(self, telegram_id, teams: List[str]) -> bool:
        """Полностью заменяет набор команд тренера."""
        with self._lock:
            try:
                self.conn.execute("DELETE FROM coach_teams WHERE telegram_id = ?", (telegram_id,))
                for team in teams:
                    if team:
                        self.conn.execute(
                            "INSERT OR IGNORE INTO coach_teams (telegram_id, team) VALUES (?, ?)",
                            (telegram_id, team)
                        )
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"set_coach_teams: {e}")
                return False

    def remove_coach(self, telegram_id) -> bool:
        """Полностью убирает тренера (все его команды)."""
        with self._lock:
            try:
                self.conn.execute("DELETE FROM coach_teams WHERE telegram_id = ?", (telegram_id,))
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"remove_coach: {e}")
                return False

    def get_team_coaches(self, team: str) -> List[int]:
        """Telegram-идентификаторы тренеров, закреплённых за командой."""
        with self._lock:
            try:
                rows = self.conn.execute(
                    "SELECT telegram_id FROM coach_teams WHERE team = ? ORDER BY telegram_id",
                    (team,)
                ).fetchall()
                return [r["telegram_id"] for r in rows]
            except Exception as e:
                logger.error(f"get_team_coaches: {e}")
                return []

    # ============ ПЕРСОНАЛЬНЫЕ ЦЕЛИ ============
    def add_goal(self, athlete_id: int, goal_type: str, target_value=None, target_text=None, period_days=30) -> int:
        """Добавить цель. Возвращает id."""
        with self._lock:
            try:
                cur = self.conn.execute(
                    "INSERT INTO athlete_goals (athlete_id, goal_type, target_value, target_text, period_days) VALUES (?, ?, ?, ?, ?)",
                    (athlete_id, goal_type, target_value, target_text, period_days)
                )
                self.conn.commit()
                return cur.lastrowid
            except Exception as e:
                logger.error(f"add_goal: {e}")
                return -1

    def get_active_goals(self, athlete_id: int) -> List[Dict]:
        """Активные цели спортсмена."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM athlete_goals WHERE athlete_id = ? AND status = 'active' ORDER BY started_at DESC",
                (athlete_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def update_goal_progress(self, goal_id: int, achieved: bool = False):
        """Отметить цель как достигнутую."""
        with self._lock:
            try:
                if achieved:
                    self.conn.execute(
                        "UPDATE athlete_goals SET status = 'achieved', achieved_at = CURRENT_DATE WHERE id = ?",
                        (goal_id,)
                    )
                else:
                    self.conn.execute(
                        "UPDATE athlete_goals SET status = 'abandoned' WHERE id = ?",
                        (goal_id,)
                    )
                self.conn.commit()
            except Exception as e:
                logger.error(f"update_goal_progress: {e}")

    def check_goals_progress(self, athlete_id: int, survey_data: Dict) -> List[str]:
        """Проверить прогресс целей после опроса. Возвращает список сообщений."""
        goals = self.get_active_goals(athlete_id)
        messages = []
        for g in goals:
            gt = g["goal_type"]
            tv = g.get("target_value")
            if gt == "sleep_min" and survey_data.get("sleep_score") is not None:
                if survey_data["sleep_score"] >= (tv or 6):
                    messages.append(f"🎯 Цель «сон >= {tv}»: ✅ сегодня {survey_data['sleep_score']}")
            elif gt == "readiness_min" and survey_data.get("readiness") is not None:
                if survey_data["readiness"] >= (tv or 7):
                    messages.append(f"🎯 Цель «готовность >= {tv}»: ✅ сегодня {survey_data['readiness']}")
            elif gt == "pain_free" and survey_data.get("pain_nrs") is not None:
                if survey_data["pain_nrs"] == 0:
                    messages.append("🎯 Цель «без боли»: ✅ сегодня NRS = 0")
        return messages

    def get_all_coaches(self) -> List[Dict]:
        """Возвращает тренеров и их команды: [{telegram_id, teams:[...], names:[...]}]."""
        with self._lock:
            out = {}
            try:
                rows = self.conn.execute(
                    "SELECT ct.telegram_id, ct.team, a.full_name, a.username "
                    "FROM coach_teams ct LEFT JOIN athletes a ON a.telegram_id = ct.telegram_id "
                    "ORDER BY ct.telegram_id, ct.team"
                ).fetchall()
                for r in rows:
                    rec = out.setdefault(r["telegram_id"], {"telegram_id": r["telegram_id"],
                                                            "teams": [], "names": []})
                    if r["team"] not in rec["teams"]:
                        rec["teams"].append(r["team"])
                    nm = r["full_name"] or (f"@{r['username']}" if r["username"] else f"id{r['telegram_id']}")
                    if nm not in rec["names"]:
                        rec["names"].append(nm)
            except Exception as e:
                logger.error(f"get_all_coaches: {e}")
            return list(out.values())

    # ============ ВРАЧИ (doctor — полный доступ) ============
    def is_doctor(self, telegram_id) -> bool:
        with self._lock:
            try:
                row = self.conn.execute("SELECT 1 FROM doctors WHERE telegram_id = ?", (telegram_id,)).fetchone()
                return row is not None
            except Exception as e:
                logger.error(f"is_doctor: {e}")
                return False

    def add_doctor(self, telegram_id) -> bool:
        with self._lock:
            try:
                self.conn.execute("INSERT OR IGNORE INTO doctors (telegram_id) VALUES (?)", (telegram_id,))
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"add_doctor: {e}")
                return False

    def remove_doctor(self, telegram_id) -> bool:
        with self._lock:
            try:
                self.conn.execute("DELETE FROM doctors WHERE telegram_id = ?", (telegram_id,))
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"remove_doctor: {e}")
                return False

    def get_all_doctors(self) -> List[Dict]:
        """Врачи: [{telegram_id, names:[...]}]."""
        with self._lock:
            out = []
            try:
                rows = self.conn.execute(
                    "SELECT d.telegram_id, a.full_name, a.username "
                    "FROM doctors d LEFT JOIN athletes a ON a.telegram_id = d.telegram_id "
                    "ORDER BY d.telegram_id"
                ).fetchall()
                for r in rows:
                    nm = r["full_name"] or (f"@{r['username']}" if r["username"] else f"id{r['telegram_id']}")
                    out.append({"telegram_id": r["telegram_id"], "names": [nm]})
            except Exception as e:
                logger.error(f"get_all_doctors: {e}")
            return out

    # ============ МУЛЬТИ-ТЕНАНТНОСТЬ (clubs) ============

    def create_club(self, name: str, slug: str, admin_telegram_id: int,
                    sport: str = "basketball", plan: str = "free") -> Optional[int]:
        """Создать новый клуб. Возвращает club_id или None."""
        with self._lock:
            try:
                # Лимиты тарифов
                limits = {"free": 10, "pro": 50, "enterprise": 200}
                max_athletes = limits.get(plan, 10)
                self.conn.execute(
                    "INSERT INTO clubs (name, slug, admin_telegram_id, sport, subscription_plan, max_athletes) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (name, slug, admin_telegram_id, sport, plan, max_athletes)
                )
                self.conn.commit()
                row = self.conn.execute("SELECT id FROM clubs WHERE slug = ?", (slug,)).fetchone()
                return row["id"] if row else None
            except sqlite3.IntegrityError:
                return None

    def get_club_by_slug(self, slug: str) -> Optional[Dict]:
        with self._lock:
            row = self.conn.execute("SELECT * FROM clubs WHERE slug = ?", (slug,)).fetchone()
            return dict(row) if row else None

    def get_club_by_id(self, club_id: int) -> Optional[Dict]:
        with self._lock:
            row = self.conn.execute("SELECT * FROM clubs WHERE id = ?", (club_id,)).fetchone()
            return dict(row) if row else None

    def get_club_by_admin(self, telegram_id: int) -> Optional[Dict]:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM clubs WHERE admin_telegram_id = ?", (telegram_id,)
            ).fetchone()
            return dict(row) if row else None

    def is_club_admin(self, telegram_id: int) -> bool:
        with self._lock:
            row = self.conn.execute(
                "SELECT 1 FROM clubs WHERE admin_telegram_id = ?", (telegram_id,)
            ).fetchone()
            return row is not None

    def get_club_teams(self, club_id: int) -> List[str]:
        """Получить список команд клуба."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT DISTINCT team FROM athletes WHERE club_id = ? ORDER BY team", (club_id,)
            ).fetchall()
            return [r["team"] for r in rows]

    def get_club_stats(self, club_id: int) -> Dict:
        """Статистика клуба: кол-во спортсменов, тренеров, опросов."""
        with self._lock:
            athletes = self.conn.execute(
                "SELECT COUNT(*) as cnt FROM athletes WHERE club_id = ?", (club_id,)
            ).fetchone()["cnt"]
            teams = len(self.get_club_teams(club_id))
            coaches = self.conn.execute(
                "SELECT COUNT(*) as cnt FROM coach_teams WHERE club_id = ?", (club_id,)
            ).fetchone()["cnt"]
            surveys = self.conn.execute(
                "SELECT COUNT(*) as cnt FROM daily_wellness dw "
                "JOIN athletes a ON dw.athlete_id = a.id WHERE a.club_id = ?", (club_id,)
            ).fetchone()["cnt"]
            return {"athletes": athletes, "teams": teams, "coaches": coaches, "surveys": surveys}

    def register_athlete_mt(self, telegram_id: int, username: str,
                            full_name: str, age_group: str, team: str,
                            club_id: int, first_name: str = None) -> bool:
        """Регистрация спортсмена с привязкой к клубу."""
        with self._lock:
            try:
                # Проверяем лимит клуба
                club = self.get_club_by_id(club_id)
                if club:
                    count = self.conn.execute(
                        "SELECT COUNT(*) as cnt FROM athletes WHERE club_id = ?", (club_id,)
                    ).fetchone()["cnt"]
                    if count >= club["max_athletes"]:
                        return False
                self.conn.execute(
                    """INSERT INTO athletes
                       (telegram_id, username, full_name, age_group, team, last_active, first_name, club_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (telegram_id, username, full_name, age_group, team, date.today(), first_name, club_id)
                )
                self.conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def add_coach_mt(self, telegram_id: int, team: str, club_id: int) -> bool:
        """Назначить тренера команде в рамках клуба."""
        with self._lock:
            try:
                self.conn.execute(
                    "INSERT OR REPLACE INTO coach_teams (telegram_id, team, club_id) VALUES (?, ?, ?)",
                    (telegram_id, team, club_id)
                )
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"add_coach_mt: {e}")
                return False

    def get_athlete_club_id(self, athlete_id: int) -> Optional[int]:
        with self._lock:
            row = self.conn.execute(
                "SELECT club_id FROM athletes WHERE id = ?", (athlete_id,)
            ).fetchone()
            return row["club_id"] if row else None

    def get_user_club(self, telegram_id: int) -> Optional[Dict]:
        """Найти клуб пользователя: сначала как admin, потом как тренер/врач."""
        # Проверяем admin
        with self._lock:
            club = self.get_club_by_admin(telegram_id)
            if club:
                return club
            # Проверяем тренер
            row = self.conn.execute(
                "SELECT c.* FROM coach_teams ct "
                "JOIN clubs c ON ct.club_id = c.id "
                "WHERE ct.telegram_id = ? LIMIT 1", (telegram_id,)
            ).fetchone()
            if row:
                return dict(row)
            # Проверяем врач
            row = self.conn.execute(
                "SELECT c.* FROM doctors d "
                "JOIN clubs c ON d.club_id = c.id "
                "WHERE d.telegram_id = ? LIMIT 1", (telegram_id,)
            ).fetchone()
            if row:
                return dict(row)
            return None
