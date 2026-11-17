# -*- coding: utf-8 -*-
"""Архив входящих файлов весов (provenance) — итерация 1 аудита весов.

Зачем: бот читал CSV только в память и нигде не сохранял, поэтому после любой
правки парсера нельзя ни переимпортировать историю, ни доказать, что именно
прилетело (в логе остаётся 200 байт заголовка). Правила архива:
 - файл кладётся в <inbox>/<ГГГГ-ММ-ДД>/<sha12>_<имя>, имя чистится;
 - повтор того же содержимого не создаёт копию;
 - ошибка диска не должна ронять импорт: возврат None и продолжение;
 - права 0600 (медицинские данные несовершеннолетних).
"""
import asyncio
import io
import os
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import bot as bot_module
from database import Database
from scale_archive import archive_upload

TODAY = date(2026, 11, 18)
CSV = ('date,"Вес"\n"11/17/2026 20:14:45","73.4кг"\n').encode("utf-8")
TEAM = "Славянка ЧКПЗ-2"
DOCTOR_ID = 990001
ATHLETE_TG = 700002


def _bot_obj(db):
    obj = object.__new__(bot_module.SportHealthBot)
    obj.db = db
    obj.rate_limiter = {}
    obj.user_states = {}
    return obj


def _msg_update(uid):
    sent = []

    async def reply(t, **kw):
        sent.append(t)

    upd = SimpleNamespace(callback_query=None, effective_user=SimpleNamespace(id=uid),
                          message=SimpleNamespace(text="", reply_text=reply))
    return upd, sent


def test_archive_writes_dated_file_named_by_hash(tmp_path):
    p = archive_upload(CSV, "data20261117201445.csv", TODAY, base_dir=str(tmp_path))
    assert p, "файл не заархивирован"
    dest = Path(p)
    assert dest.parent.name == "2026-11-18", dest
    assert dest.name.endswith("_data20261117201445.csv"), dest.name
    assert len(dest.name.split("_")[0]) == 12
    assert io.open(str(dest), "rb").read() == CSV


def test_archive_is_idempotent_by_content(tmp_path):
    a = archive_upload(CSV, "first.csv", TODAY, base_dir=str(tmp_path))
    b = archive_upload(CSV, "second-name.csv", TODAY, base_dir=str(tmp_path))
    files = list(tmp_path.rglob("*.csv"))
    assert len(files) == 1, f"повтор залил копию: {files}"
    assert os.path.abspath(a) == os.path.abspath(b)


def test_archive_sanitizes_names_and_blocks_traversal(tmp_path):
    p = archive_upload(CSV, "../../etc/passwd", TODAY, base_dir=str(tmp_path))
    dest = Path(p).resolve()
    assert str(dest).startswith(str(tmp_path.resolve())), dest
    assert (tmp_path / "2026-11-18").is_dir()
    q = archive_upload(CSV, "Фитнес - 26117839 - №1 - 17.11.2026.csv", TODAY,
                       base_dir=str(tmp_path))
    assert q and len(Path(q).name) < 120

def test_archive_failure_does_not_raise(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    assert archive_upload(CSV, "a.csv", TODAY, base_dir=str(blocker)) is None


def test_archive_sets_private_mode_on_posix(tmp_path):
    p = archive_upload(CSV, "a.csv", TODAY, base_dir=str(tmp_path))
    if os.name != "posix":
        import pytest
        pytest.skip("права 0600 проверяем только на Linux-проде")
    assert (os.stat(p).st_mode & 0o077) == 0, oct(os.stat(p).st_mode)


def test_import_scale_csv_keeps_original_file_and_saves_nothing(tmp_path, monkeypatch):
    """Групповой файл от спортсмена: в базу 0 строк, в архиве — исходник."""
    monkeypatch.setattr(bot_module, "ADMIN_TELEGRAM_IDS", {DOCTOR_ID})
    db = Database(db_path=str(tmp_path / "arch.db"))
    db.register_athlete(ATHLETE_TG, "u2", "Третья Тестовая", "U17", TEAM)
    aid = db.get_athlete_by_telegram_id(ATHLETE_TG)["id"]
    obj = _bot_obj(db)
    raw = ('"date","x"\n"11/17/2026 20:17:08","1"\n').encode("utf-8")
    records = [
        {"record_date": "2026-11-17", "device_profile": "Данные пользователя 1",
         "weight_kg": 68.3, "body_fat_pct": 27.6},
        {"record_date": "2026-11-17", "device_profile": "Данные пользователя 2",
         "weight_kg": 73.4, "body_fat_pct": 14.7},
    ]
    upd, sent = _msg_update(ATHLETE_TG)
    inbox = tmp_path / "inbox"
    asyncio.run(obj._import_scale_csv(upd, None, records, "data20261117201708.csv",
                                     content=raw, inbox_dir=str(inbox)))
    assert db.get_body_composition(aid, days=999) == [], \
        "групповой файл залили на спортсмена"
    archived = list(inbox.rglob("*.csv"))
    assert len(archived) == 1, f"исходник не в архиве: {archived}"
    assert archived[0].read_bytes() == raw
    db.close()
