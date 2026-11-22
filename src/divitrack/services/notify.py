"""Bildirim kanalları: Telegram (isteğe bağlı). Ağ çağrısı `transport` ile değiştirilebilir (testler)."""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from divitrack.repositories.sqlite import SettingsRepository

log = logging.getLogger(__name__)

TOKEN_KEY = "secret.telegram_token"  # "secret." önekli ayarlar yedeğe yazılmaz
CHAT_KEY = "notify.telegram_chat_id"
API = "https://api.telegram.org/bot{token}/{method}"

Transport = Callable[[str, dict[str, Any]], dict[str, Any]]


def _http(url: str, params: dict[str, Any]) -> dict[str, Any]:
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=35) as resp:  # noqa: S310
        return json.loads(resp.read().decode())


class TelegramNotifier:
    def __init__(self, settings: SettingsRepository, transport: Transport | None = None, env_token: str = "") -> None:
        self.settings = settings
        self.transport = transport or _http
        self.env_token = env_token

    @property
    def token(self) -> str:
        return self.env_token or self.settings.get(TOKEN_KEY) or ""

    @property
    def chat_id(self) -> str:
        return self.settings.get(CHAT_KEY) or ""

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def configure(self, token: str | None, chat_id: str) -> None:
        if token:
            self.settings.set(TOKEN_KEY, token.strip())
        self.settings.set(CHAT_KEY, chat_id.strip())

    def call(self, method: str, **params: Any) -> dict[str, Any]:
        if not self.token:
            raise RuntimeError("Telegram bot anahtarı girilmemiş")
        return self.transport(API.format(token=self.token, method=method), params)

    def send(self, text: str, chat_id: str | None = None) -> bool:
        target = chat_id or self.chat_id
        if not (self.token and target):
            return False
        try:
            return bool(self.call("sendMessage", chat_id=target, text=text).get("ok"))
        except Exception as exc:  # ağ hataları uygulamayı durdurmamalı
            log.warning("Telegram mesajı gönderilemedi: %s", exc)
            return False
