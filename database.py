"""Модуль работы с базой данных спортсменов v2.2."""

import sqlite3
import logging
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
    "Позитив",
    "Славянка ЧКПЗ",
    "Славянка ЧКПЗ-2",
    "Студ. сборная ЧО",
    "Шаблоны",
]


class Database:
    def __init__(self, db_path: str = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # WAL-журнал повышает конкурентность чтения/записи (важно при асинхронном доступе)
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        self._create_tables()

    def _create_tables(self):
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
        # Миграция: добавляем колонки в questionnaires для существующих БД (CREATE IF NOT EXISTS их не добавит)
        for col, coltype in (("phone", "TEXT"), ("birth_date", "TEXT"), ("surgery_date", "TEXT"),
                             ("allergies", "TEXT"), ("allergies_detail", "TEXT")):
            try:
                self.conn.execute(f"ALTER TABLE questionnaires ADD COLUMN {col} {coltype}")
            except Exception:
                pass  # колонка уже существует
        self.conn.commit()

    def get_setting(self, key: str, default: str = None) -> str:
        row = self.conn.execute("SELECT value FROM bot_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> bool:
        try:
            self.conn.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)", (key, value))
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Set setting error: {e}")
            return False

    def update_athlete_gender(self, athlete_id: int, gender: str, cycle_length: int = 28) -> bool:
        try:
            self.conn.execute(
                "UPDATE athletes SET gender = ?, cycle_length_default = ? WHERE id = ?",
                (gender, cycle_length, athlete_id)
            )
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Update gender: {e}")
            return False

    def get_cycle_history(self, athlete_id: int, days: int = 30) -> List[Dict]:
        """Получить историю дней цикла для спортсменки."""
        rows = self.conn.execute("""
            SELECT survey_date, cycle_day, cycle_length, cycle_phase
            FROM daily_wellness
            WHERE athlete_id = ? AND cycle_day IS NOT NULL
                AND survey_date >= date('now', '-' || ? || ' days')
            ORDER BY survey_date DESC
        """, (athlete_id, days)).fetchall()
        return [dict(r) for r in rows]

    def register_athlete(self, telegram_id: int, username: str,
                         full_name: str, age_group: str, team: str) -> bool:
        try:
            self.conn.execute(
                """INSERT INTO athletes
                   (telegram_id, username, full_name, age_group, team, last_active)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (telegram_id, username, full_name, age_group, team, date.today())
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def get_athlete_by_telegram_id(self, telegram_id: int) -> Optional[Dict]:
        row = self.conn.execute(
            "SELECT * FROM athletes WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_all_athletes(self) -> List[Dict]:
        rows = self.conn.execute(
            "SELECT * FROM athletes ORDER BY team, full_name"
        ).fetchall()
        return [dict(r) for r in rows]

    def has_survey_today(self, athlete_id: int) -> bool:
        row = self.conn.execute(
            "SELECT id FROM daily_wellness WHERE athlete_id = ? AND survey_date = ?",
            (athlete_id, date.today())
        ).fetchone()
        return row is not None

    def get_survey_today(self, athlete_id: int) -> Optional[Dict]:
        row = self.conn.execute(
            "SELECT * FROM daily_wellness WHERE athlete_id = ? AND survey_date = ?",
            (athlete_id, date.today())
        ).fetchone()
        return dict(row) if row else None

    def get_wellness_by_period(self, days: int = 7, team: str = None) -> List[Dict]:
        """Получить ВСЕ опросы по дням за период (не средние), опционально по команде."""
        if team:
            rows = self.conn.execute("""
                SELECT a.full_name, a.team, a.age_group, a.id as athlete_id,
                       dw.survey_date, dw.protocol_type,
                       dw.sleep_score, dw.stress_score, dw.fatigue_score,
                       dw.muscle_soreness, dw.mood_score, dw.resting_hr,
                       dw.hrv_ms, dw.had_training, dw.sRPE_score,
                       dw.cycle_day, dw.cycle_phase, dw.complaints
                FROM daily_wellness dw
                JOIN athletes a ON a.id = dw.athlete_id
                WHERE dw.survey_date >= date('now', '-' || ? || ' days') AND a.team = ?
                ORDER BY a.full_name, dw.survey_date DESC
            """, (days, team)).fetchall()
        else:
            rows = self.conn.execute("""
                SELECT a.full_name, a.team, a.age_group, a.id as athlete_id,
                       dw.survey_date, dw.protocol_type,
                       dw.sleep_score, dw.stress_score, dw.fatigue_score,
                       dw.muscle_soreness, dw.mood_score, dw.resting_hr,
                       dw.hrv_ms, dw.had_training, dw.sRPE_score,
                       dw.cycle_day, dw.cycle_phase, dw.complaints
                FROM daily_wellness dw
                JOIN athletes a ON a.id = dw.athlete_id
                WHERE dw.survey_date >= date('now', '-' || ? || ' days')
                ORDER BY a.full_name, dw.survey_date DESC
            """, (days,)).fetchall()
        return [dict(r) for r in rows]

    def save_survey(self, athlete_id: int, data: Dict[str, Any]) -> bool:
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
                cycle_day=?, cycle_length=?, cycle_phase=?
                WHERE athlete_id=? AND survey_date=?""", (
                data.get("sleep"), data.get("stress"), data.get("fatigue"),
                data.get("soreness"), data.get("mood"), data.get("hr"),
                data.get("hrv"), data.get("training", 0), data.get("srpe"),
                data.get("protocol", "simple"),
                data.get("cycle_day"), data.get("cycle_length"), data.get("cycle_phase"),
                athlete_id, today
            ))
        else:
            self.conn.execute("""INSERT INTO daily_wellness
                (athlete_id, survey_date, protocol_type, sleep_score,
                 stress_score, fatigue_score, muscle_soreness, mood_score,
                 resting_hr, hrv_ms, had_training, sRPE_score,
                 cycle_day, cycle_length, cycle_phase)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                athlete_id, today, data.get("protocol", "simple"),
                data.get("sleep"), data.get("stress"), data.get("fatigue"),
                data.get("soreness"), data.get("mood"), data.get("hr"),
                data.get("hrv"), data.get("training", 0), data.get("srpe"),
                data.get("cycle_day"), data.get("cycle_length"), data.get("cycle_phase")
            ))

        # Обновляем streak
        last_surveys = self.conn.execute(
            """SELECT DISTINCT survey_date FROM daily_wellness
               WHERE athlete_id = ?
               ORDER BY survey_date DESC LIMIT 60""",
            (athlete_id,)
        ).fetchall()
        dates = []
        for row in last_surveys:
            d = row["survey_date"]
            if isinstance(d, str):
                d = date.fromisoformat(d)
            dates.append(d)

        streak = 0
        check_date = today
        for d in dates:
            if d == check_date:
                streak += 1
                check_date -= timedelta(days=1)
            elif d < check_date:
                break

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

    def get_athlete_stats(self, athlete_id: int, days: int = 7) -> Dict:
        row = self.conn.execute("""
            SELECT
                COUNT(*) as total_days,
                AVG(sleep_score) as avg_sleep,
                AVG(stress_score) as avg_stress,
                AVG(fatigue_score) as avg_fatigue,
                AVG(muscle_soreness) as avg_soreness,
                AVG(mood_score) as avg_mood,
                AVG(resting_hr) as avg_hr,
                AVG(hrv_ms) as avg_hrv
            FROM daily_wellness
            WHERE athlete_id = ? AND survey_date >= date('now', '-' || ? || ' days')
        """, (athlete_id, days)).fetchone()
        return dict(row) if row else {}

    def get_trend_data(self, athlete_id: int, metric: str, days: int = 7) -> List[Tuple]:
        column = {
            "sleep": "sleep_score",
            "stress": "stress_score",
            "fatigue": "fatigue_score",
            "soreness": "muscle_soreness",
            "mood": "mood_score",
            "hr": "resting_hr",
            "hrv": "hrv_ms",
        }.get(metric, "sleep_score")

        # Защита от SQL-инъекции: допускаем ТОЛЬКО whitelist-колонки
        allowed = ["sleep_score","stress_score","fatigue_score","muscle_soreness","mood_score","resting_hr","hrv_ms"]
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
        rows = self.conn.execute("""
            SELECT a.id, a.full_name, a.team, a.age_group, a.survey_streak, a.total_surveys,
                   dw.sleep_score, dw.stress_score, dw.fatigue_score,
                   dw.muscle_soreness, dw.mood_score, dw.resting_hr, dw.hrv_ms
            FROM athletes a
            LEFT JOIN daily_wellness dw ON a.id = dw.athlete_id AND dw.survey_date = date('now')
            ORDER BY a.team, a.full_name
        """).fetchall()
        return [dict(r) for r in rows]

    def get_athletes_week_stats(self) -> List[Dict]:
        """Получить всех спортсменов со средними за 7 дней (для отчета врача)."""
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
            GROUP BY a.id
            ORDER BY a.team, a.full_name
        """).fetchall()
        return [dict(r) for r in rows]

    def get_team_stats_period(self, team: str = None, days: int = 7) -> List[Dict]:
        """Получить статистику спортсменов за период, опционально по команде."""
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
                GROUP BY a.id
                ORDER BY a.team, a.full_name
            """, (days,)).fetchall()
        return [dict(r) for r in rows]

    def get_athletes_by_team(self, team: str) -> List[Dict]:
        rows = self.conn.execute(
            "SELECT * FROM athletes WHERE team = ? ORDER BY full_name", (team,)
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self):
        self.conn.close()

    def get_athlete_by_id(self, athlete_id):
        row = self.conn.execute("SELECT * FROM athletes WHERE id = ?", (athlete_id,)).fetchone()
        return dict(row) if row else None

    def search_athletes(self, query):
        like = f"%{query}%"
        rows = self.conn.execute("SELECT * FROM athletes WHERE full_name LIKE ? OR username LIKE ? OR CAST(telegram_id AS TEXT) LIKE ? ORDER BY team, full_name", (like, like, f"%{query}%")).fetchall()
        return [dict(r) for r in rows]

    def is_athlete_banned(self, athlete_id):
        row = self.conn.execute("SELECT 1 FROM banned_athletes WHERE athlete_id = ?", (athlete_id,)).fetchone()
        return row is not None

    def ban_athlete(self, athlete_id, admin_id, reason="Заблокирован администратором"):
        try:
            self.conn.execute("INSERT OR REPLACE INTO banned_athletes (athlete_id, banned_by, reason) VALUES (?, ?, ?)", (athlete_id, admin_id, reason))
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Ban athlete: {e}")
            return False

    def unban_athlete(self, athlete_id):
        try:
            self.conn.execute("DELETE FROM banned_athletes WHERE athlete_id = ?", (athlete_id,))
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Unban athlete: {e}")
            return False

    def get_banned_athletes(self):
        rows = self.conn.execute("SELECT a.id, a.full_name, a.team, b.banned_at, b.reason FROM athletes a JOIN banned_athletes b ON a.id = b.athlete_id ORDER BY b.banned_at DESC").fetchall()
        return [dict(r) for r in rows]

    def get_questionnaire(self, athlete_id):
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
        row = self.conn.execute("SELECT 1 FROM questionnaires WHERE athlete_id = ?", (athlete_id,)).fetchone()
        return row is not None

    def has_incomplete_questionnaire(self, athlete_id):
        """Есть ли незавершённая анкета (completed_at IS NULL)."""
        row = self.conn.execute("SELECT 1 FROM questionnaires WHERE athlete_id = ? AND completed_at IS NULL", (athlete_id,)).fetchone()
        return row is not None

    def get_questionnaire_progress(self, athlete_id):
        """Получить данные незавершённой анкеты."""
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
        rows = self.conn.execute("""
            SELECT survey_date, sleep_score, stress_score, fatigue_score,
                   muscle_soreness, mood_score, resting_hr, sRPE_score
            FROM daily_wellness WHERE athlete_id = ?
            ORDER BY survey_date DESC LIMIT ?
        """, (athlete_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def save_questionnaire(self, athlete_id, data):
        # Whitelist допустимых колонок — защита от SQL-инъекции
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
        self.conn.execute("UPDATE questionnaires SET completed_at = datetime('now') WHERE athlete_id = ?", (athlete_id,))
        self.conn.commit()

    # ============ СОГЛАСИЕ НА ОБРАБОТКУ ПДн (152-ФЗ) ============
    def has_consent(self, user_id) -> bool:
        try:
            row = self.conn.execute("SELECT 1 FROM consents WHERE user_id = ?", (user_id,)).fetchone()
            return row is not None
        except Exception:
            return False

    def record_consent(self, user_id, athlete_id=None) -> bool:
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
        try:
            row = self.conn.execute("SELECT payload FROM user_states WHERE user_id = ?", (user_id,)).fetchone()
            return row["payload"] if row else None
        except Exception as e:
            logger.error(f"load_user_state: {e}")
            return None

    def save_user_state(self, user_id, payload) -> bool:
        try:
            import json as _json
            self.conn.execute(
                "INSERT OR REPLACE INTO user_states (user_id, payload, updated_at) VALUES (?, ?, datetime('now'))",
                (user_id, _json.dumps(payload, ensure_ascii=False, default=str))
            )
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"save_user_state: {e}")
            return False

    def delete_user_state(self, user_id) -> bool:
        try:
            self.conn.execute("DELETE FROM user_states WHERE user_id = ?", (user_id,))
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"delete_user_state: {e}")
            return False
