"""Aracı kurum / banka işlem dökümü içe aktarma: önizleme, kayıtlı kolon profilleri, mükerrer koruması."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from divitrack.core.broker import FIELDS, BrokerParse, BrokerRow, guess_columns, parse_broker
from divitrack.core.entities import Asset, Transaction
from divitrack.core.enums import AssetClass, Currency
from divitrack.core.exceptions import DivitrackError, ValidationError
from divitrack.core.statement import read_table
from divitrack.repositories.sqlite import SettingsRepository
from divitrack.services.portfolio_service import PortfolioService

PROFILES_KEY = "import.broker_profiles"
PRESET_NAMES = ("Aracı kurum", "Portföy uygulaması", "Banka (altın/fon)")


@dataclass(slots=True)
class BrokerPreview:
    header: list[str]
    rows: list[list[str]]
    columns: dict[str, int | None]
    parsed: BrokerParse
    unknown_symbols: list[str]


@dataclass(slots=True)
class BrokerImportResult:
    added: int = 0
    duplicates: int = 0
    errors: list[str] = field(default_factory=list)
    created_assets: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        parts = [f"{self.added} işlem eklendi"]
        if self.duplicates:
            parts.append(f"{self.duplicates} mükerrer atlandı")
        if self.created_assets:
            parts.append("yeni varlık: " + ", ".join(self.created_assets))
        if self.errors:
            parts.append(f"{len(self.errors)} hata")
        return " · ".join(parts)


class BrokerImportService:
    def __init__(self, portfolio: PortfolioService, settings: SettingsRepository) -> None:
        self.portfolio = portfolio
        self.settings = settings

    # ------------------------------------------------------------------ profiller
    def profiles(self) -> dict[str, dict[str, str | None]]:
        """Profil adı → {alan: başlık adı}. Başlık adıyla saklanır; kolon sırası değişse de çalışır."""
        raw = self.settings.get(PROFILES_KEY)
        return json.loads(raw) if raw else {}

    def save_profile(self, name: str, header: list[str], columns: dict[str, int | None]) -> None:
        name = name.strip()
        if not name:
            raise ValidationError("Profil adı boş olamaz")
        profiles = self.profiles()
        profiles[name] = {f: (header[i] if i is not None else None) for f, i in columns.items()}
        self.settings.set(PROFILES_KEY, json.dumps(profiles, ensure_ascii=False))

    def delete_profile(self, name: str) -> None:
        profiles = self.profiles()
        profiles.pop(name, None)
        self.settings.set(PROFILES_KEY, json.dumps(profiles, ensure_ascii=False))

    @staticmethod
    def apply_profile(profile: dict[str, str | None], header: list[str]) -> dict[str, int | None]:
        lookup = {h.strip().casefold(): i for i, h in enumerate(header)}
        return {f: lookup.get((profile.get(f) or "").strip().casefold()) for f in FIELDS}

    # ------------------------------------------------------------------ önizleme ve kayıt
    def preview(self, data: str | bytes, columns: dict[str, int | None] | None = None) -> BrokerPreview:
        header, rows = read_table(data)
        cols = columns or guess_columns(header)
        try:
            parsed = parse_broker(rows, cols)
        except ValidationError as exc:
            parsed = BrokerParse(errors=[str(exc)])
        known = {a.symbol for a in self.portfolio.assets.list()}
        unknown = sorted({r.symbol for r in parsed.rows} - known)
        return BrokerPreview(header, rows, cols, parsed, unknown)

    def import_rows(
        self,
        rows: list[BrokerRow],
        new_assets: dict[str, tuple[AssetClass, Currency]] | None = None,
    ) -> BrokerImportResult:
        """Satırları işlem defterine yazar. Bilinmeyen semboller `new_assets` ile tanımlanmadıysa atlanır."""
        result = BrokerImportResult()
        new_assets = new_assets or {}
        by_symbol: dict[str, Asset] = {}
        for a in self.portfolio.assets.list():
            by_symbol.setdefault(a.symbol, a)
        existing = {
            (t.asset_id, t.trade_date, t.tx_type, t.quantity, t.price) for t in self.portfolio.transactions.list()
        }
        for r in sorted(rows, key=lambda r: (r.trade_date, r.tx_type != "BUY")):
            asset = by_symbol.get(r.symbol)
            if asset is None and r.symbol in new_assets:
                cls, ccy = new_assets[r.symbol]
                try:
                    asset = self.portfolio.add_asset(Asset(r.symbol, cls, r.currency or ccy))
                except DivitrackError as exc:
                    result.errors.append(f"{r.symbol}: {exc}")
                    continue
                by_symbol[r.symbol] = asset
                result.created_assets.append(r.symbol)
            if asset is None or asset.id is None:
                result.errors.append(f"Satır {r.line}: {r.symbol} tanımlı değil")
                continue
            key = (asset.id, r.trade_date, r.tx_type, r.quantity, r.price)
            if key in existing:
                result.duplicates += 1
                continue
            try:
                self.portfolio.add_transaction(
                    Transaction(asset.id, r.tx_type, r.quantity, r.price, r.trade_date, r.fee, "döküm aktarımı")
                )
            except DivitrackError as exc:
                result.errors.append(f"Satır {r.line}: {exc}")
                continue
            existing.add(key)
            result.added += 1
        return result
