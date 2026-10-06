"""Veri taşınabilirliği: JSON tam yedek, CSV işlem/temettü dışa-içe aktarma, DB yedeği."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from divitrack.core.calculations import compute_position
from divitrack.core.entities import Asset, Dividend, Snapshot, Transaction
from divitrack.core.enums import AssetClass, Currency, DividendStatus, TxType
from divitrack.core.exceptions import DivitrackError, ImportFormatError
from divitrack.core.money import to_decimal
from divitrack.db.database import Database
from divitrack.repositories.sqlite import (
    AssetRepository,
    DividendRepository,
    SettingsRepository,
    SnapshotRepository,
    TransactionRepository,
)

FORMAT_NAME = "divitrack-export"
FORMAT_VERSION = 1

TX_COLUMNS = ["date", "symbol", "asset_class", "currency", "type", "quantity", "price", "fee", "note"]
DIV_COLUMNS = [
    "pay_date",
    "ex_date",
    "symbol",
    "asset_class",
    "currency",
    "amount_per_share",
    "quantity",
    "withholding_rate",
    "status",
    "note",
]


def _s(v: Decimal | None) -> str | None:
    return None if v is None else format(v.normalize(), "f")


@dataclass(slots=True)
class ImportReport:
    assets_created: int = 0
    transactions: int = 0
    dividends: int = 0
    snapshots: int = 0
    skipped: int = 0

    def __str__(self) -> str:
        return (
            f"{self.assets_created} yeni varlık, {self.transactions} işlem, "
            f"{self.dividends} temettü, {self.snapshots} kayıt içe aktarıldı"
            + (f"; {self.skipped} satır atlandı" if self.skipped else "")
        )


class DataTransferService:
    def __init__(
        self,
        db: Database,
        assets: AssetRepository,
        transactions: TransactionRepository,
        dividends: DividendRepository,
        snapshots: SnapshotRepository,
        settings: SettingsRepository,
    ) -> None:
        self.db = db
        self.assets = assets
        self.transactions = transactions
        self.dividends = dividends
        self.snapshots = snapshots
        self.settings = settings

    # ================================================================== JSON
    def export_json(self) -> str:
        assets = self.assets.list()
        payload: dict[str, Any] = {
            "format": FORMAT_NAME,
            "version": FORMAT_VERSION,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "assets": [
                {
                    "id": a.id,
                    "symbol": a.symbol,
                    "name": a.name,
                    "asset_class": a.asset_class.value,
                    "currency": a.currency.value,
                    "manual_price": _s(a.manual_price),
                }
                for a in assets
            ],
            "transactions": [
                {
                    "asset_id": t.asset_id,
                    "type": t.tx_type.value,
                    "quantity": _s(t.quantity),
                    "price": _s(t.price),
                    "fee": _s(t.fee),
                    "date": t.trade_date.isoformat(),
                    "note": t.note,
                }
                for t in self.transactions.list()
            ],
            "dividends": [
                {
                    "asset_id": d.asset_id,
                    "pay_date": d.pay_date.isoformat(),
                    "ex_date": d.ex_date.isoformat() if d.ex_date else None,
                    "amount_per_share": _s(d.amount_per_share),
                    "quantity": _s(d.quantity),
                    "currency": d.currency.value,
                    "withholding_rate": _s(d.withholding_rate),
                    "status": d.status.value,
                    "note": d.note,
                }
                for d in self.dividends.list()
            ],
            "snapshots": [
                {
                    "date": s.snapshot_date.isoformat(),
                    "currency": s.currency.value,
                    "total_value": _s(s.total_value),
                    "total_cost": _s(s.total_cost),
                }
                for s in self.snapshots.list_all()
            ],
            "settings": self.settings.all(),
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def import_json(self, text: str | bytes, *, replace: bool = False) -> ImportReport:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ImportFormatError(f"Geçersiz JSON: {exc}") from exc
        if not isinstance(data, dict) or data.get("format") != FORMAT_NAME:
            raise ImportFormatError("Bu dosya bir DiviTrack dışa aktarımı değil")
        if int(data.get("version", 0)) > FORMAT_VERSION:
            raise ImportFormatError("Dosya daha yeni bir DiviTrack sürümüyle oluşturulmuş")

        try:
            parsed = self._parse_payload(data)
        except (KeyError, ValueError, TypeError, DivitrackError) as exc:
            raise ImportFormatError(f"Dosya okunamadı: {exc}") from exc
        if replace:
            self._wipe()
        return self._apply(*parsed)

    def _parse_payload(self, data: dict[str, Any]) -> tuple[Any, ...]:
        assets = [
            (
                int(a["id"]),
                Asset(
                    symbol=a["symbol"],
                    name=a.get("name", ""),
                    asset_class=AssetClass(a["asset_class"]),
                    currency=Currency(a["currency"]),
                    manual_price=to_decimal(a["manual_price"]) if a.get("manual_price") else None,
                ),
            )
            for a in data.get("assets", [])
        ]
        txs = [
            (
                int(t["asset_id"]),
                TxType(t["type"]),
                to_decimal(t["quantity"]),
                to_decimal(t["price"]),
                to_decimal(t.get("fee") or "0"),
                date.fromisoformat(t["date"]),
                t.get("note", ""),
            )
            for t in data.get("transactions", [])
        ]
        divs = [(int(d["asset_id"]), d) for d in data.get("dividends", [])]
        snaps = [
            Snapshot(
                date.fromisoformat(s["date"]),
                to_decimal(s["total_value"]),
                to_decimal(s["total_cost"]),
                Currency(s["currency"]),
            )
            for s in data.get("snapshots", [])
        ]
        settings = {str(k): str(v) for k, v in (data.get("settings") or {}).items()}
        return assets, txs, divs, snaps, settings

    def _apply(
        self,
        assets: list[tuple[int, Asset]],
        txs: list[tuple[Any, ...]],
        divs: list[tuple[int, dict[str, Any]]],
        snaps: list[Snapshot],
        settings: dict[str, str],
    ) -> ImportReport:
        report = ImportReport()
        id_map: dict[int, int] = {}
        for old_id, asset in assets:
            existing = self.assets.find(asset.symbol, asset.asset_class)
            if existing is None:
                existing = self.assets.add(asset)
                report.assets_created += 1
            assert existing.id is not None
            id_map[old_id] = existing.id

        existing_keys = {(t.asset_id, t.tx_type, t.quantity, t.price, t.trade_date) for t in self.transactions.list()}
        new_txs: list[Transaction] = []
        for old_asset, tx_type, qty, price, fee, d, note in txs:
            aid = id_map.get(old_asset)
            if aid is None or (aid, tx_type, qty, price, d) in existing_keys:
                report.skipped += 1
                continue
            new_txs.append(
                Transaction(asset_id=aid, tx_type=tx_type, quantity=qty, price=price, fee=fee, trade_date=d, note=note)
            )
        self._validate_ledger(new_txs)
        report.transactions = self.transactions.add_many(new_txs)

        existing_divs = {(d.asset_id, d.pay_date, d.amount_per_share) for d in self.dividends.list()}
        for old_asset, d in divs:
            aid = id_map.get(old_asset)
            amount = to_decimal(d["amount_per_share"])
            pay = date.fromisoformat(d["pay_date"])
            if aid is None or (aid, pay, amount) in existing_divs:
                report.skipped += 1
                continue
            self.dividends.add(
                Dividend(
                    asset_id=aid,
                    pay_date=pay,
                    ex_date=date.fromisoformat(d["ex_date"]) if d.get("ex_date") else None,
                    amount_per_share=amount,
                    quantity=to_decimal(d["quantity"]),
                    currency=Currency(d["currency"]),
                    withholding_rate=to_decimal(d.get("withholding_rate") or "0"),
                    status=DividendStatus(d["status"]),
                    note=d.get("note", ""),
                )
            )
            report.dividends += 1

        self.snapshots.upsert_many(snaps)
        report.snapshots = len(snaps)
        for k, v in settings.items():
            self.settings.set(k, v)
        return report

    def _validate_ledger(self, new_txs: list[Transaction]) -> None:
        combined = self.transactions.by_asset()
        for t in new_txs:
            combined.setdefault(t.asset_id, []).append(t)
        for txs in combined.values():
            compute_position(txs)

    def _wipe(self) -> None:
        with self.db.transaction() as c:
            for table in ("transactions", "dividends", "portfolio_snapshots", "assets", "price_cache"):
                c.execute(f"DELETE FROM {table}")

    # ================================================================== CSV
    def export_transactions_csv(self) -> str:
        assets = {a.id: a for a in self.assets.list()}
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=TX_COLUMNS)
        w.writeheader()
        for t in self.transactions.list():
            a = assets[t.asset_id]
            w.writerow(
                {
                    "date": t.trade_date.isoformat(),
                    "symbol": a.symbol,
                    "asset_class": a.asset_class.value,
                    "currency": a.currency.value,
                    "type": t.tx_type.value,
                    "quantity": _s(t.quantity),
                    "price": _s(t.price),
                    "fee": _s(t.fee),
                    "note": t.note,
                }
            )
        return buf.getvalue()

    def export_dividends_csv(self) -> str:
        assets = {a.id: a for a in self.assets.list()}
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=DIV_COLUMNS)
        w.writeheader()
        for d in self.dividends.list():
            a = assets[d.asset_id]
            w.writerow(
                {
                    "pay_date": d.pay_date.isoformat(),
                    "ex_date": d.ex_date.isoformat() if d.ex_date else "",
                    "symbol": a.symbol,
                    "asset_class": a.asset_class.value,
                    "currency": d.currency.value,
                    "amount_per_share": _s(d.amount_per_share),
                    "quantity": _s(d.quantity),
                    "withholding_rate": _s(d.withholding_rate),
                    "status": d.status.value,
                    "note": d.note,
                }
            )
        return buf.getvalue()

    def import_transactions_csv(self, text: str | bytes) -> ImportReport:
        """CSV kolonları: date,symbol,asset_class,currency,type,quantity,price,fee,note.

        Ayraç olarak virgül veya noktalı virgül (Excel TR) kabul edilir. Varlıklar yoksa oluşturulur.
        Tüm dosya doğrulanır; tek bir hatalı satır varsa hiçbir şey yazılmaz.
        """
        if isinstance(text, bytes):
            text = text.decode("utf-8-sig")
        sample = text[:2048]
        delimiter = ";" if sample.count(";") > sample.count(",") else ","
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        fields = {f.strip().lower() for f in reader.fieldnames or []}
        missing = {"date", "symbol", "asset_class", "currency", "type", "quantity", "price"} - fields
        if missing:
            raise ImportFormatError(f"Eksik kolon(lar): {', '.join(sorted(missing))}")

        rows: list[tuple[Asset, Transaction]] = []
        for lineno, raw in enumerate(reader, start=2):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
            if not any(row.values()):
                continue
            try:
                asset = Asset(
                    symbol=row["symbol"],
                    asset_class=AssetClass(row["asset_class"].upper()),
                    currency=Currency(row["currency"].upper()),
                )
                tx = Transaction(
                    asset_id=0,
                    tx_type=TxType(row["type"].upper()),
                    quantity=to_decimal(row["quantity"], field="quantity"),
                    price=to_decimal(row["price"], field="price"),
                    fee=to_decimal(row.get("fee") or "0", field="fee"),
                    trade_date=date.fromisoformat(row["date"]),
                    note=row.get("note", ""),
                )
            except (ValueError, DivitrackError) as exc:
                raise ImportFormatError(f"Satır {lineno}: {exc}") from exc
            rows.append((asset, tx))

        report = ImportReport()
        # Önce defteri bellekte doğrula (mevcut + yeni)
        ledger = self.transactions.by_asset()
        pending: dict[tuple[str, AssetClass], list[Transaction]] = {}
        for asset, tx in rows:
            existing = self.assets.find(asset.symbol.removesuffix(".IS"), asset.asset_class)
            key = (asset.symbol.removesuffix(".IS"), asset.asset_class)
            base = ledger.get(existing.id, []) if existing and existing.id else []
            pending.setdefault(key, list(base)).append(tx)
        for txs in pending.values():
            compute_position(txs)

        for asset, tx in rows:
            symbol = asset.symbol.removesuffix(".IS") if asset.asset_class is AssetClass.BIST_STOCK else asset.symbol
            existing = self.assets.find(symbol, asset.asset_class)
            if existing is None:
                asset.symbol = symbol
                existing = self.assets.add(asset)
                report.assets_created += 1
            assert existing.id is not None
            tx.asset_id = existing.id
            self.transactions.add(tx)
            report.transactions += 1
        return report

    # ================================================================== DB yedeği
    def backup_db(self) -> bytes:
        return self.db.backup_bytes()
