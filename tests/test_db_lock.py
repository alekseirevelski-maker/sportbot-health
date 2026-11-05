# -*- coding: utf-8 -*-
"""Task 5: Database._lock (threading.RLock) — все публичные методы под замком.

Дым-тест: два потока по 200 записей save_survey одновременно на tmp БД →
без исключений, итог 400 строк. Без замка sqlite3-курсоры одного соединения
конфликтуют (вложенные транзакции/commit вперемешку).
"""
import threading

from database import Database


def test_lock_exists():
    db = Database(db_path=":memory:")
    assert isinstance(getattr(db, "_lock", None), type(threading.RLock()))


def test_concurrent_save_survey(tmp_path):
    db = Database(db_path=str(tmp_path / "lock.db"))
    ids = []
    for i in range(400):
        db.register_athlete(10000 + i, f"u{i}", f"Тест Тестович {i}", "U17", "ЧБК")
        ids.append(db.get_athlete_by_telegram_id(10000 + i)["id"])

    errors = []

    def worker(chunk):
        try:
            for aid in chunk:
                db.save_survey(aid, {})
        except BaseException as e:  # noqa: BLE001 — любой сбой потока = провал теста
            errors.append(e)

    t1 = threading.Thread(target=worker, args=(ids[:200],))
    t2 = threading.Thread(target=worker, args=(ids[200:],))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors, f"concurrent save_survey raised: {errors[:3]}"
    rows = db.conn.execute("SELECT COUNT(*) FROM daily_wellness").fetchone()[0]
    assert rows == 400
