"""TÜFE servisi: elle girilen endeksin yanında TCMB EVDS'den otomatik güncelleme."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from divitrack.core.inflation import latest_month
from divitrack.repositories.sqlite import CpiRepository, SettingsRepository
from divitrack.services.market.evds_client import EvdsClient

API_KEY_SETTING = "secret.evds_api_key"
API_KEY_ENV = "DIVITRACK_EVDS_API_KEY"
DEFAULT_START = date(2015, 1, 1)


@dataclass(frozen=True, slots=True)
class CpiUpdate:
    added: int
    changed: int
    latest: str | None

    def __str__(self) -> str:
        if not self.added and not self.changed:
            return f"TÜFE zaten güncel (son ay {self.latest or '—'})"
        return f"TÜFE güncellendi: {self.added} yeni ay, {self.changed} düzeltme (son ay {self.latest})"


class CpiService:
    def __init__(
        self,
        repo: CpiRepository,
        settings: SettingsRepository,
        client_factory: Callable[[str], EvdsClient] = EvdsClient,
        *,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.repo = repo
        self.settings = settings
        self.client_factory = client_factory
        self.today = today

    @property
    def api_key(self) -> str:
        return os.environ.get(API_KEY_ENV) or self.settings.get(API_KEY_SETTING) or ""

    @property
    def key_from_env(self) -> bool:
        return bool(os.environ.get(API_KEY_ENV))

    def set_api_key(self, key: str) -> None:
        self.settings.set(API_KEY_SETTING, key.strip())

    def index(self) -> dict[str, Decimal]:
        return self.repo.all()

    def update_from_evds(self, start: date | None = None) -> CpiUpdate:
        """EVDS'deki aylık TÜFE (2003=100) değerlerini tabloya yazar.

        Elle girilmiş değerler EVDS'nin değeriyle değiştirilir; EVDS'de olmayan aylar korunur.
        Not: EVDS endeksi 2003=100 tabanlıdır. Tablonuzda farklı tabanla girilmiş aylar varsa
        oranlar bozulmasın diye önce o ayları silin veya tamamen EVDS'ye geçin.
        """
        client = self.client_factory(self.api_key)
        fetched = client.cpi(start or DEFAULT_START, self.today())
        current = self.repo.all()
        added = sum(1 for m in fetched if m not in current)
        changed = sum(1 for m, v in fetched.items() if m in current and current[m] != v)
        if fetched:
            self.repo.replace_all({**current, **fetched})
        return CpiUpdate(added, changed, latest_month({**current, **fetched}))
