#!/usr/bin/env python3
"""Миграция: мульти-тенантность — таблица clubs + club_id.

Использование: python migration_clubs.py [путь_к_бд]
Если путь не указан, используется data/sport_health.db
"""

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "sport_health.db"


def create_base_tables(conn):
    """Создать базовые таблицы, если их нет (для тестовых БД)."""
    conn.executescript("""
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

        CREATE TABLE IF NOT EXISTS bot_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

        CREATE TABLE IF NOT EXISTS questionnaires (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            athlete_id INTEGER NOT NULL,
            completed_at TIMESTAMP,
            age INTEGER, gender TEXT, phone TEXT, birth_date TEXT,
            position TEXT, level TEXT, experience TEXT,
            height INTEGER, weight INTEGER,
            trauma_12m TEXT, trauma_12m_detail TEXT, zones TEXT,
            pain_now TEXT, pain_now_detail TEXT,
            chronic TEXT, chronic_detail TEXT,
            surgery TEXT, surgery_detail TEXT, surgery_date TEXT,
            meds TEXT, meds_detail TEXT,
            allergies TEXT, allergies_detail TEXT,
            train_count INTEGER, train_duration INTEGER, season TEXT,
            form_score INTEGER, sleep_score INTEGER,
            warmup TEXT, recovery TEXT, water REAL,
            diet TEXT, pre_meal TEXT, supplements TEXT,
            motivation INTEGER, stress TEXT, match_state TEXT,
            reinjury_fear TEXT, goal TEXT, wish TEXT,
            FOREIGN KEY (athlete_id) REFERENCES athletes(id)
        );

        CREATE TABLE IF NOT EXISTS banned_athletes (
            athlete_id INTEGER PRIMARY KEY,
            banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            banned_by INTEGER, reason TEXT
        );

        CREATE TABLE IF NOT EXISTS consents (
            user_id INTEGER PRIMARY KEY, athlete_id INTEGER,
            accepted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, ip_tz TEXT
        );

        CREATE TABLE IF NOT EXISTS coach_teams (
            telegram_id INTEGER NOT NULL, team TEXT NOT NULL,
            assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (telegram_id, team)
        );

        CREATE TABLE IF NOT EXISTS doctors (
            telegram_id INTEGER PRIMARY KEY,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS user_states (
            user_id INTEGER PRIMARY KEY, payload TEXT, updated_at TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            description TEXT
        );

        CREATE TABLE IF NOT EXISTS consultations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            athlete_id INTEGER NOT NULL, complaints TEXT,
            consult_date TEXT NOT NULL, status TEXT DEFAULT 'pending',
            admin_notes TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (athlete_id) REFERENCES athletes(id)
        );

        CREATE INDEX IF NOT EXISTS idx_wellness_athlete_date ON daily_wellness(athlete_id, survey_date);
        CREATE INDEX IF NOT EXISTS idx_q_athlete ON questionnaires(athlete_id);
        CREATE INDEX IF NOT EXISTS idx_athletes_team ON athletes(team);
        CREATE INDEX IF NOT EXISTS idx_athletes_telegram ON athletes(telegram_id);
    """)


def migrate(db_path: str = None):
    path = Path(db_path) if db_path else DB_PATH
    if not path.exists():
        print(f"БД не найдена: {path}")
        sys.exit(1)

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row

    # Создаём базовые таблицы (если их нет — для тестовых БД)
    create_base_tables(conn)

    # Проверяем, есть ли уже clubs
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    if "clubs" in tables:
        print("Миграция уже применена (таблица clubs существует)")
        conn.close()
        return

    print("Применение миграции: мульти-тенантность...")

    # 1. Таблица клубов
    conn.execute("""CREATE TABLE IF NOT EXISTS clubs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        slug TEXT NOT NULL UNIQUE,
        admin_telegram_id INTEGER NOT NULL,
        sport TEXT DEFAULT 'basketball',
        subscription_plan TEXT DEFAULT 'free',
        subscription_expires DATE,
        max_athletes INTEGER DEFAULT 50,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_active INTEGER DEFAULT 1
    )""")

    # 2. Таблица тарифов
    conn.execute("""CREATE TABLE IF NOT EXISTS subscription_plans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        price_rub INTEGER NOT NULL,
        max_athletes INTEGER DEFAULT 50,
        max_coaches INTEGER DEFAULT 5,
        features TEXT,
        is_active INTEGER DEFAULT 1
    )""")

    # Вставляем стандартные тарифы
    plans = [
        ("free", 0, 10, 2, "Базовый опрос, 1 команда, Excel-отчёт"),
        ("pro", 3000, 50, 10, "Все функции, до 5 команд, приоритетная поддержка"),
        ("enterprise", 7000, 200, 50, "Безлимит, кастомизация, выделенный врач"),
    ]
    for name, price, max_a, max_c, features in plans:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO subscription_plans (name, price_rub, max_athletes, max_coaches, features) VALUES (?, ?, ?, ?, ?)",
                (name, price, max_a, max_c, features)
            )
        except Exception:
            pass

    # 3. Добавляем club_id к athletes
    try:
        conn.execute("ALTER TABLE athletes ADD COLUMN club_id INTEGER REFERENCES clubs(id)")
    except Exception:
        pass

    # 4. Добавляем club_id к coach_teams
    try:
        conn.execute("ALTER TABLE coach_teams ADD COLUMN club_id INTEGER REFERENCES clubs(id)")
    except Exception:
        pass

    # 5. Добавляем club_id к doctors
    try:
        conn.execute("ALTER TABLE doctors ADD COLUMN club_id INTEGER REFERENCES clubs(id)")
    except Exception:
        pass

    # 6. Создаём клуб по умолчанию для существующих данных
    default_admin = 351572247  # ваш Telegram ID
    try:
        conn.execute(
            "INSERT INTO clubs (name, slug, admin_telegram_id, sport, subscription_plan, max_athletes) VALUES (?, ?, ?, ?, ?, ?)",
            ("ЧБК Челябинск", "chbk-chelyabinsk", default_admin, "basketball", "enterprise", 200)
        )
        club_id = conn.execute("SELECT id FROM clubs WHERE slug='chbk-chelyabinsk'").fetchone()[0]

        # Привязываем существующих спортсменов к клубу
        conn.execute("UPDATE athletes SET club_id = ? WHERE club_id IS NULL", (club_id,))
        # Привязываем тренеров
        conn.execute("UPDATE coach_teams SET club_id = ? WHERE club_id IS NULL", (club_id,))
        # Привязываем врачей
        conn.execute("UPDATE doctors SET club_id = ? WHERE club_id IS NULL", (club_id,))
        print(f"  Создан клуб 'ЧБК Челябинск' (id={club_id}), привязаны все существующие данные")
    except Exception as e:
        print(f"  Ошибка создания клуба: {e}")

    # 7. Индексы
    conn.execute("CREATE INDEX IF NOT EXISTS idx_athletes_club ON athletes(club_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_coach_teams_club ON coach_teams(club_id)")

    # 8. Лог миграции
    conn.execute("""CREATE TABLE IF NOT EXISTS migration_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        migration_name TEXT NOT NULL,
        applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        description TEXT
    )""")
    conn.execute(
        "INSERT INTO migration_log (migration_name, description) VALUES (?, ?)",
        ("v4_multitenant", "Мульти-тенантность: clubs, club_id, subscription_plans")
    )

    conn.commit()
    conn.close()
    print("Миграция завершена успешно!")


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else None
    migrate(db)
