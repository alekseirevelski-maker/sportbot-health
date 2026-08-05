# -*- coding: utf-8 -*-
"""Шифрование персональных данных (телефон, дата рождения) через Fernet.
Ключ — ENCRYPTION_KEY из .env (минимум 32 символа)."""
import os
import base64
import logging
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)

_SALT = b"sportbot_health_salt_2026"
_cipher = None
_key_valid = False


def _get_cipher():
    global _cipher, _key_valid
    if _cipher is not None:
        return _cipher
    key = os.environ.get("ENCRYPTION_KEY", "") or os.environ.get("SPORTBOT_KEY", "")
    if len(key) < 32:
        logger.warning("ENCRYPTION_KEY не задан или <32 символов — шифрование ПД ОТКЛЮЧЕНО")
        _key_valid = False
        return None
    try:
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=_SALT, iterations=100000)
        der = base64.urlsafe_b64encode(kdf.derive(key.encode()))
        _cipher = Fernet(der)
        _key_valid = True
        return _cipher
    except Exception as e:
        logger.error(f"encryptor init error: {e}")
        _cipher = None
        return None


def encrypt_value(value) -> Optional[str]:
    """Зашифровать строковое значение. None-защита."""
    if value is None or value == "":
        return value
    c = _get_cipher()
    if c is None:
        return value  # нет ключа — сохраняем открытым (не ломаем работу)
    try:
        return c.encrypt(str(value).encode()).decode()
    except Exception as e:
        logger.error(f"encrypt error: {e}")
        return value


def decrypt_value(value) -> Optional[str]:
    """Расшифровать. Если не получается или значение не зашифровано — вернуть как есть."""
    if value is None or value == "":
        return value
    c = _get_cipher()
    if c is None:
        return value
    try:
        return c.decrypt(value.encode()).decode()
    except (InvalidToken, Exception):
        # либо не зашифровано (старые данные), либо повреждено — возвращаем как есть
        return value
