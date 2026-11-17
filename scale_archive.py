# -*- coding: utf-8 -*-
"""Архив входящих файлов весов (provenance).

Раньше бот читал CSV только в память и тут же забывал: нельзя было ни
переимпортировать историю после правки парсера, ни доказать, что именно
прилетело (в логе остаётся 200 байт заголовка). Отсюда минимальный склад
исходников: ``<inbox>/ГГГГ-ММ-ДД/<sha12>_<имя файла>``.

Правила:
 * имя чистится (``../`` и управляющие символы не пройдут), результат всегда
   внутри каталога архива;
 * одно и то же содержимое = один файл (дедуп по sha256) — повторная отправка
   того же экспорта не плодит копии;
 * права 0600: это медицинские данные несовершеннолетних;
 * любая ошибка диска НЕ прерывает импорт — возвращаем ``None``, импорт решает
   сам по себе.

Каталог по умолчанию лежит рядом с БД (``data/scale_inbox``), чтобы попадать в
тот же бэкап-контур.
"""
import hashlib
import logging
import os
import re
from datetime import date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

INBOX_SUBDIR = "scale_inbox"
MAX_NAME_LEN = 80
_UNSAFE = re.compile(r"[\x00-\x1f<>:\"/\\|?*]+")
_SPACES = re.compile(r"\s+")


def _safe_name(fname: str) -> str:
    """Имя файла из Telegram → безопасное имя внутри каталога архива."""
    base = os.path.basename(str(fname or "").replace("\\", "/"))
    base = _UNSAFE.sub("_", base)
    base = _SPACES.sub("_", base).strip(" ._")
    if len(base) > MAX_NAME_LEN:
        stem, ext = os.path.splitext(base)
        base = stem[:MAX_NAME_LEN - len(ext)[:8] - 1] + ext[:8]
    return base or "upload.csv"


def default_inbox_dir() -> str:
    """Каталог архива по умолчанию: рядом с боевой БД."""
    from config import DB_PATH
    return str(Path(DB_PATH).parent / INBOX_SUBDIR)


def archive_upload(content, fname: str, today: date,
                   base_dir: Optional[str] = None) -> Optional[str]:
    """Сохранить исходный файл весов. Путь к файлу или None (ошибка/пустой ввод).

    Никак не влияет на импорт: вызывающий обязан работать и при None.
    """
    try:
        payload = bytes(content or b"")
    except (TypeError, ValueError):
        payload = b""
    if not payload:
        return None
    try:
        root = Path(base_dir or default_inbox_dir())
        day = root / today.isoformat()
        digest = hashlib.sha256(payload).hexdigest()[:12]
        day.mkdir(parents=True, exist_ok=True)
        # Дедуп по содержимому, а не по «хеш+имя»: тот же экспорт приложение
        # весов каждый раз отдаёт под новым именем (data<ts>.csv, «Фитнес - …»),
        # и копия нам не нужна.
        for existing in sorted(day.glob(digest + "_*")):
            return str(existing)
        target = day / (digest + "_" + _safe_name(fname))
        target.write_bytes(payload)
        try:
            os.chmod(target, 0o600)
        except Exception:
            pass  # Windows/особые ФС: права best-effort, содержимое важнее
        logger.info("scale inbox: сохранён %s (%d байт)", target.name, len(payload))
        return str(target)
    except OSError as e:
        logger.error("scale inbox: не удалось сохранить %r: %s", fname, e)
        return None
