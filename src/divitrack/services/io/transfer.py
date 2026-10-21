"""Veri taşınabilirliği: JSON tam yedek, CSV işlem/temettü/gelir-gider dışa-içe aktarma, DB yedeği."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from divitrack.core.calculations import compute_position
from divitrack.core.entities import Asset, CashFlow, Category, Dividend, Snapshot, Transaction
from divitrack.core.enums import AssetClass, Currency, DividendStatus, FlowKind, Purpose, TxType
from divitrack.core.exceptions import DivitrackError, ImportFormatError
from divitrack.core.inflation import parse_month
from divitrack.core.money import to_decimal
from divitrack.db.database import Database
from divitrack.repositories.sqlite import (
    AssetRepository,
    CashFlowRepository,
    CategoryRepository,
    CategoryRuleRepository,
    CpiRepository,
    DividendRepository,
    SettingsRepository,
    SnapshotRepository,
    TransactionRepository,
)

FORMAT_NAME = "divitrack-export"
FORMAT_VERSION = 3  # v2: amaç, kurumsal işlemler, bütçe, TÜFE · v3: döküm kuralları

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
FLOW_COLUMNS = ["date", "category", "kind", "amount", "note"]
SECRET_PREFIX = "secret."


def _s(v: Decimal | None) -> str | None:
    return None if v is None else format(v.normalize(), "f")


@dataclass(slots=True)
class ImportReport:
    assets_created: int = 0
    transactions: int = 0
    dividends: int = 0
    snapshots: int = 0
    cash_flows: int = 0
    categories_created: int = 0
    skipped: int = 0

    def __str__(self) -> str:
        parts = [
            f"{self.assets_created} yeni varlık",
            f"{self.transactions} işlem",
            f"{self.dividends} temettü",
            f"{self.snapshots} kayıt",
        ]
        if self.cash_flows or self.categories_created:
            parts.append(f"{self.cash_flows} gelir/gider")
        return ", ".join(parts) + " içe aktarıldı" + (f"; {self.skipped} satır atlandı" if self.skipped else "")


class DataTransferService:
    def __init__(
        self,
        db: Database,
        assets: AssetRepository,
        transactions: TransactionRepository,
        dividends: DividendRepository,
        snapshots: SnapshotRepository,
        settings: SettingsRepository,
        categories: CategoryRepository,
        flows: CashFlowRepository,
        cpi: CpiRepository,
        rules: CategoryRuleRepository,
    ) -> None:
        self.db = db
        self.categories = categories
        self.flows = flows
        self.cpi = cpi
        self.rules = rules
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
                    "purpose": a.purpose.value,
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
            # "secret." ile başlayan ayarlar (API anahtarları) yedeğe yazılmaz
            "settings": {k: v for k, v in self.settings.all().items() if not k.startswith(SECRET_PREFIX)},
            "categories": [
                {"id": c.id, "name": c.name, "kind": c.kind.value, "monthly_budget": _s(c.monthly_budget)}
                for c in self.categories.list()
            ],
            "cash_flows": [
                {"category_id": f.category_id, "date": f.flow_date.isoformat(), "amount": _s(f.amount), "note": f.note}
                for f in reversed(self.flows.list())
            ],
            "cpi": {m: _s(v) for m, v in sorted(self.cpi.all().items())},
            "category_rules": [{"pattern": pattern, "category_id": cid} for _, pattern, cid in self.rules.list()],
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
                    purpose=Purpose(a.get("purpose") or Purpose.UNASSIGNED),
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
        categories = [
            (
                int(c["id"]),
                Category(
                    c["name"],
                    FlowKind(c["kind"]),
                    to_decimal(c["monthly_budget"]) if c.get("monthly_budget") else None,
                ),
            )
            for c in data.get("categories", [])
        ]
        flows = [
            (int(f["category_id"]), date.fromisoformat(f["date"]), to_decimal(f["amount"]), f.get("note", ""))
            for f in data.get("cash_flows", [])
        ]
        cpi = {parse_month(m): to_decimal(v) for m, v in (data.get("cpi") or {}).items()}
        rules = [(str(r["pattern"]), int(r["category_id"])) for r in data.get("category_rules", [])]
        return assets, txs, divs, snaps, settings, categories, flows, cpi, rules

    def _apply(
        self,
        assets: list[tuple[int, Asset]],
        txs: list[tuple[Any, ...]],
        divs: list[tuple[int, dict[str, Any]]],
        snaps: list[Snapshot],
        settings: dict[str, str],
        categories: list[tuple[int, Category]],
        flows: list[tuple[int, date, Decimal, str]],
        cpi: dict[str, Decimal],
        rules: list[tuple[str, int]],
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

        cat_map: dict[int, int] = {}
        for old_id, cat in categories:
            existing_cat = self.categories.find(cat.name)
            if existing_cat is None:
                existing_cat = self.categories.add(cat)
                report.categories_created += 1
            elif cat.monthly_budget is not None:
                existing_cat.monthly_budget = cat.monthly_budget
                self.categories.update(existing_cat)
            assert existing_cat.id is not None
            cat_map[old_id] = existing_cat.id
        existing_flows = {(f.category_id, f.flow_date, f.amount, f.note) for f in self.flows.list()}
        new_flows: list[CashFlow] = []
        for old_cat, d, amount, note in flows:
            cid = cat_map.get(old_cat)
            if cid is None or (cid, d, amount, note) in existing_flows:
                report.skipped += 1
                continue
            new_flows.append(CashFlow(cid, d, amount, note))
        self.flows.add_many(new_flows)
        report.cash_flows = len(new_flows)
        for pattern, old_cat in rules:
            if old_cat in cat_map:
                self.rules.upsert(pattern, cat_map[old_cat])
        if cpi:
            self.cpi.replace_all({**self.cpi.all(), **cpi})
        return report

    def _validate_ledger(self, new_txs: list[Transaction]) -> None:
        combined = self.transactions.by_asset()
        for t in new_txs:
            combined.setdefault(t.asset_id, []).append(t)
        for txs in combined.values():
            compute_position(txs)

    def _wipe(self) -> None:
        with self.db.transaction() as c:
            for table in (
                "transactions",
                "dividends",
                "portfolio_snapshots",
                "assets",
                "price_cache",
                "cash_flows",
                "cpi_monthly",
                "score_history",
            ):
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

    def export_cash_flows_csv(self) -> str:
        cats = {c.id: c for c in self.categories.list()}
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=FLOW_COLUMNS)
        w.writeheader()
        for f in reversed(self.flows.list()):
            c = cats[f.category_id]
            w.writerow(
                {
                    "date": f.flow_date.isoformat(),
                    "category": c.name,
                    "kind": c.kind.value,
                    "amount": _s(f.amount),
                    "note": f.note,
                }
            )
        return buf.getvalue()

    def import_cash_flows_csv(self, text: str | bytes) -> ImportReport:
        """CSV kolonları: date,category,amount[,kind,note].

        Tutarın işareti yok sayılır (banka dökümlerinde gider eksi gelir). Kategori yoksa oluşturulur;
        türü `kind` kolonundan (INCOME/EXPENSE veya Gelir/Gider), o da yoksa tutarın işaretinden
        (eksi → gider) belirlenir. Hatalı satır varsa hiçbir şey yazılmaz.
        """
        if isinstance(text, bytes):
            text = text.decode("utf-8-sig")
        sample = text[:2048]
        delimiter = ";" if sample.count(";") > sample.count(",") else ","
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        fields = {f.strip().lower() for f in reader.fieldnames or []}
        missing = {"date", "category", "amount"} - fields
        if missing:
            raise ImportFormatError(f"Eksik kolon(lar): {', '.join(sorted(missing))}")
        kinds = {"INCOME": FlowKind.INCOME, "GELIR": FlowKind.INCOME, "GELİR": FlowKind.INCOME}
        kinds |= {"EXPENSE": FlowKind.EXPENSE, "GIDER": FlowKind.EXPENSE, "GİDER": FlowKind.EXPENSE}

        parsed: list[tuple[str, FlowKind | None, CashFlow]] = []
        for lineno, raw in enumerate(reader, start=2):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
            if not any(row.values()):
                continue
            try:
                amount = to_decimal(row["amount"], field="amount")
                kind_text = row.get("kind", "").upper()
                if kind_text and kind_text not in kinds:
                    raise ValueError(f"Bilinmeyen tür: {row['kind']}")
                kind = kinds.get(kind_text) or (FlowKind.EXPENSE if amount < 0 else None)
                name = " ".join(row["category"].split())
                if not name:
                    raise ValueError("Kategori boş")
                flow = CashFlow(0, date.fromisoformat(row["date"]), abs(amount), row.get("note", ""))
            except (ValueError, DivitrackError) as exc:
                raise ImportFormatError(f"Satır {lineno}: {exc}") from exc
            parsed.append((name, kind, flow))

        report = ImportReport()
        for name, kind, flow in parsed:
            cat = self.categories.find(name)
            if cat is None:
                cat = self.categories.add(Category(name, kind or FlowKind.INCOME))
                report.categories_created += 1
            elif kind is not None and cat.kind is not kind:
                raise ImportFormatError(f"'{cat.name}' kategorisi {cat.kind.label.lower()} türünde")
            assert cat.id is not None
            flow.category_id = cat.id
        self.flows.add_many([f for _, _, f in parsed])
        report.cash_flows = len(parsed)
        return report

    # ================================================================== DB yedeği
    def backup_db(self) -> bytes:
        return self.db.backup_bytes()
