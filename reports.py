"""Генерация TXT-отчётов для врачей (простой текстовый формат, не PDF)."""

import os
from datetime import datetime, date, timedelta
from pathlib import Path

from database import Database


class ReportGenerator:
    def __init__(self):
        self.db = Database()

    def generate_athlete_report(self, athlete_id: int, days: int = 7) -> str:
        """Генерация TXT-отчёта для спортсмена."""
        athlete = self.db.get_athlete_by_id(athlete_id)
        if not athlete:
            return None

        surveys = self.db.get_athlete_surveys(athlete_id, days)
        if not surveys:
            return None

        # Простой текстовый отчёт (для MVP)
        report_path = Path(__file__).parent / "data" / f"report_{athlete_id}_{date.today()}.txt"

        with open(report_path, "w", encoding="utf-8") as f:
            f.write(f"ОТЧЁТ О СОСТОЯНИИ ЗДОРОВЬЯ\n")
            f.write(f"{'='*40}\n\n")
            f.write(f"Спортсмен: {athlete['full_name']}\n")
            f.write(f"Группа: {athlete['age_group']}\n")
            f.write(f"Период: {days} дней\n")
            f.write(f"Дата отчёта: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n")

            f.write(f"{'='*40}\n")
            f.write(f"СРЕДНИЕ ПОКАЗАТЕЛИ\n")
            f.write(f"{'='*40}\n\n")

            stats = self.db.get_athlete_stats(athlete_id, days)
            if stats.get("avg_sleep"):
                f.write(f"Качество сна: {stats['avg_sleep']:.1f}/7\n")
            if stats.get("avg_stress"):
                f.write(f"Стресс: {stats['avg_stress']:.1f}/7\n")
            if stats.get("avg_fatigue"):
                f.write(f"Утомление: {stats['avg_fatigue']:.1f}/7\n")
            if stats.get("avg_hr"):
                f.write(f"Пульс покоя: {stats['avg_hr']:.0f} уд/мин\n")
            if stats.get("avg_hrv"):
                f.write(f"HRV: {stats['avg_hrv']:.1f} мс\n")

            f.write(f"\n{'='*40}\n")
            f.write(f"ЕЖЕДНЕВНЫЕ ДАННЫЕ\n")
            f.write(f"{'='*40}\n\n")

            for s in surveys:
                f.write(f"📅 {s['survey_date']}\n")
                f.write(f"  Сон: {s.get('sleep_score', '-')}/7 | "
                       f"Стресс: {s.get('stress_score', '-')}/7 | "
                       f"Утомление: {s.get('fatigue_score', '-')}/7\n")
                if s.get('resting_hr'):
                    f.write(f"  Пульс: {s['resting_hr']} уд/мин")
                if s.get('hrv_ms'):
                    f.write(f" | HRV: {s['hrv_ms']} мс")
                f.write("\n\n")

            # Рекомендации
            f.write(f"{'='*40}\n")
            f.write(f"АВТОМАТИЧЕСКИЕ РЕКОМЕНДАЦИИ\n")
            f.write(f"{'='*40}\n\n")

            for s in surveys:
                if s.get('auto_recommendation'):
                    f.write(f"📅 {s['survey_date']}:\n")
                    f.write(f"  {s['auto_recommendation']}\n\n")

        return str(report_path)

    def generate_team_report(self, age_group: str, days: int = 7) -> str:
        """Генерация отчёта по команде."""
        stats = self.db.get_team_stats(age_group, days)
        athletes = self.db.get_athletes_by_age_group(age_group)

        report_path = Path(__file__).parent / "data" / f"team_report_{age_group}_{date.today()}.txt"

        with open(report_path, "w", encoding="utf-8") as f:
            f.write(f"ОТЧЁТ ПО КОМАНДЕ\n")
            f.write(f"{'='*40}\n\n")
            f.write(f"Возрастная группа: {age_group}\n")
            f.write(f"Период: {days} дней\n")
            f.write(f"Дата отчёта: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n")

            f.write(f"{'='*40}\n")
            f.write(f"СРЕДНИЕ ПО КОМАНДЕ\n")
            f.write(f"{'='*40}\n\n")

            if stats.get("avg_sleep"):
                f.write(f"Качество сна: {stats['avg_sleep']:.1f}/7\n")
            if stats.get("avg_stress"):
                f.write(f"Стресс: {stats['avg_stress']:.1f}/7\n")
            if stats.get("avg_fatigue"):
                f.write(f"Утомление: {stats['avg_fatigue']:.1f}/7\n")
            if stats.get("avg_hr"):
                f.write(f"Пульс покоя: {stats['avg_hr']:.0f} уд/мин\n")
            if stats.get("avg_hrv"):
                f.write(f"HRV: {stats['avg_hrv']:.1f} мс\n")
            f.write(f"Всего опросов: {stats.get('total_surveys', 0)}\n")

            f.write(f"\n{'='*40}\n")
            f.write(f"СПОРТСМЕНЫ\n")
            f.write(f"{'='*40}\n\n")

            for a in athletes:
                f.write(f"• {a['full_name']} ({a['age_group']})\n")

        return str(report_path)
