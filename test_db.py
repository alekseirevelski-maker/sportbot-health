"""Тестирование базы данных."""
from database import Database
from datetime import date

db = Database()

# Очищаем тестовые данные
db.conn.execute('DELETE FROM daily_wellness')
db.conn.execute('DELETE FROM athletes')
db.conn.execute('DELETE FROM alerts')
db.conn.commit()

# Тест регистрации
athlete_id = db.add_athlete(telegram_id=111, username='test1', full_name='Иванов Иван', age_group='U17')
print(f'1. Регистрация: ID={athlete_id}')

# Тест дубликата
dup_id = db.add_athlete(telegram_id=111, username='test1', full_name='Иванов Иван', age_group='U17')
print(f'2. Дубликат: ID={dup_id} (ожидается None)')

# Тест опроса
survey_id = db.save_survey(
    athlete_id=athlete_id,
    survey_date=date.today(),
    protocol_type='full',
    sleep_score=6, stress_score=3, fatigue_score=4,
    muscle_soreness=3, mood_score=6,
    resting_hr=58, hrv_ms=42.5,
    had_training=1, sRPE_score=5,
    auto_recommendation='Все показатели в норме.'
)
print(f'3. Опрос: ID={survey_id}')

# Тест статистики
stats = db.get_athlete_stats(athlete_id)
print(f'4. Статистика: {stats}')

# Тест алертов
alert_id = db.save_alert(athlete_id, 'hooper_index', 18, 16, 'warning')
print(f'5. Алерт: ID={alert_id}')

# Тест алертов с именами (один запрос)
alertsWithNames = db.get_unread_alerts_with_athletes()
print(f'6. Алерты с именами: {len(alertsWithNames)}')
if alertsWithNames:
    first = alertsWithNames[0]
    print(f'   Первый: {first["full_name"]} - {first["metric_type"]}')

db.close()
print('\n✅ Все тесты пройдены!')
