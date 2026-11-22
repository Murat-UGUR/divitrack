"""Uygulama parolası ve şifreli yedek.

Parola PBKDF2-HMAC-SHA256 (600.000 tur, rastgele tuz) ile özetlenip saklanır; parolanın kendisi saklanmaz.
Şifreli yedek: veritabanı yedeği, paroladan türetilen anahtarla Fernet (AES-128-CBC + HMAC-SHA256) ile
şifrelenir. Not: parola arayüzü kilitler; veritabanı dosyası diskte şifresiz durur (tam disk şifreleme önerilir).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

from divitrack.core.exceptions import ValidationError
from divitrack.repositories.sqlite import SettingsRepository

HASH_KEY = "secret.app_password"
ITERATIONS = 600_000
MAGIC = b"DTENC1"
MIN_LENGTH = 8


def _derive(password: str, salt: bytes, iterations: int = ITERATIONS) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)


class SecurityService:
    def __init__(self, settings: SettingsRepository) -> None:
        self.settings = settings

    # ------------------------------------------------------------------ parola
    @property
    def has_password(self) -> bool:
        return bool(self.settings.get(HASH_KEY))

    def set_password(self, new: str, current: str | None = None) -> None:
        if self.has_password and not self.verify(current or ""):
            raise ValidationError("Mevcut parola yanlış")
        if len(new) < MIN_LENGTH:
            raise ValidationError(f"Parola en az {MIN_LENGTH} karakter olmalı")
        salt = os.urandom(16)
        stored = f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${_derive(new, salt).hex()}"
        self.settings.set(HASH_KEY, stored)

    def clear_password(self, current: str) -> None:
        if not self.verify(current):
            raise ValidationError("Parola yanlış")
        self.settings.set(HASH_KEY, "")

    def verify(self, password: str) -> bool:
        stored = self.settings.get(HASH_KEY) or ""
        try:
            _, iters, salt, digest = stored.split("$")
        except ValueError:
            return False
        return hmac.compare_digest(_derive(password, bytes.fromhex(salt), int(iters)).hex(), digest)

    # ------------------------------------------------------------------ şifreli yedek
    @staticmethod
    def encrypt(data: bytes, password: str) -> bytes:
        from cryptography.fernet import Fernet

        if len(password) < MIN_LENGTH:
            raise ValidationError(f"Yedek parolası en az {MIN_LENGTH} karakter olmalı")
        salt = os.urandom(16)
        key = base64.urlsafe_b64encode(_derive(password, salt))
        return MAGIC + salt + Fernet(key).encrypt(data)

    @staticmethod
    def decrypt(blob: bytes, password: str) -> bytes:
        from cryptography.fernet import Fernet, InvalidToken

        if not blob.startswith(MAGIC):
            raise ValidationError("Bu dosya şifreli bir DiviTrack yedeği değil")
        salt, token = blob[len(MAGIC) : len(MAGIC) + 16], blob[len(MAGIC) + 16 :]
        try:
            return Fernet(base64.urlsafe_b64encode(_derive(password, salt))).decrypt(token)
        except InvalidToken as exc:
            raise ValidationError("Parola yanlış ya da dosya bozuk") from exc
