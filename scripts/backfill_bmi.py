#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Досчитать ИМТ там, где файл весов его не отдал (решение владельца 18.11).

Почему нужен скрипт, а не миграция: это правка ДАННЫХ, а не схемы. Правила
docs/DEPLOY.md: запуск только явным ключом --apply, до этого — dry-run, и только
после свежей копии БД. Повторный запуск ничего не меняет (идемпотентен).

Что считается: bmi = вес_кг / (рост_м)^2, округление до 0.1 — ровно как это
показывает приложение весов. Рост берётся из самой строки замера; если его нет —
из последней анкеты спортсмена (и тогда это помечено в bmi_source).

bmi_source: 'file' — ИМТ пришёл из файла весов, 'computed' — посчитан нами,
'computed_anamnesis' — посчитан по росту из анкеты (рост мог измениться с даты
замера — для подростка это до полупункта ИМТ).

Запуск на сервере:
    python3 scripts/backfill_bmi.py data/sport_health.db            # dry-run
    python3 scripts/backfill_bmi.py data/sport_health.db --apply    # применить
"""
import argparse
import sqlite3
import sys

MIN_H, MAX_H = 100, 230      # здоровый диапазон роста в см


def _bmi(weight_kg, height_cm):
    try:
        w, h = float(weight_kg), float(height_cm)
    except (TypeError, ValueError):
        return None
    if not (20 <= w <= 250) or not (MIN_H <= h <= MAX_H):
        return None
    return round(w / ((h / 100.0) ** 2), 1)


def plan(db_path, apply_changes=False, verbose=True):
    """Собрать (и опционально применить) расчёт ИМТ для строк без него."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(body_composition)")]
        if "bmi_source" not in cols:
            raise SystemExit("нет колонки bmi_source — сначала нужен деплой кода")
        rows = con.execute(
            """SELECT id, athlete_id, height_cm, weight_kg FROM body_composition
               WHERE bmi IS NULL AND weight_kg IS NOT NULL
               ORDER BY athlete_id, record_date""").fetchall()
        questionnaire = {r["athlete_id"]: r["height"] for r in con.execute(
            """SELECT q.athlete_id, q.height FROM questionnaires q
               JOIN (SELECT athlete_id, MAX(id) mid FROM questionnaires GROUP BY athlete_id) t
                 ON t.mid = q.id""").fetchall()}

        changes = []
        for r in rows:
            h, source = r["height_cm"], "computed"
            if h is None:
                h = questionnaire.get(r["athlete_id"])
                source = "computed_anamnesis"
            value = _bmi(r["weight_kg"], h)
            if value is None:
                continue
            changes.append((value, source, r["id"]))

        if verbose:
            print("строк без ИМТ с весом: %d, посчитаем: %d" % (len(rows), len(changes)))
            for value, source, rid in changes[:15]:
                print("  id=%-5s bmi=%-5s источник=%s" % (rid, value, source))
            if len(changes) > 15:
                print("  … и ещё %d" % (len(changes) - 15))
            if not apply_changes:
                print("РЕЖИМ ПРОСМОТРА: база не изменена (нужен --apply)")

        if apply_changes and changes:
            con.executemany(
                "UPDATE body_composition SET bmi = ?, bmi_source = ? WHERE id = ?",
                [(v, s, rid) for v, s, rid in changes])
            con.commit()
            if verbose:
                print("применено:", len(changes))
        return {"rows": len(changes), "values": [c[0] for c in changes]}
    finally:
        con.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Досчёт ИМТ в body_composition")
    ap.add_argument("db", nargs="?", default="data/sport_health.db")
    ap.add_argument("--apply", action="store_true", help="записать (по умолчанию только показать)")
    args = ap.parse_args(argv)
    plan(args.db, apply_changes=args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
