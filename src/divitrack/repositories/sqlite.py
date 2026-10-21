"""SQLite repository'leri: satır ↔ entity dönüşümü ve CRUD."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import Decimal

from divitrack.core.entities import (
    Asset,
    CashFlow,
    Category,
    Dividend,
    Goal,
    ProviderQuote,
    Snapshot,
    Transaction,
)
from divitrack.core.enums import AssetClass, Currency, DividendStatus, FlowKind, Purpose, TxType
from divitrack.core.exceptions import DuplicateError, NotFoundError, ValidationError
from divitrack.db.database import Database


def _dec(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def _date(value: str | None) -> date | None:
    return None if value is None else date.fromisoformat(value)


def _s(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")


class _Repo:
    def __init__(self, db: Database) -> None:
        self.db = db


# --------------------------------------------------------------------------- assets
class AssetRepository(_Repo):
    @staticmethod
    def _row(r: sqlite3.Row) -> Asset:
        return Asset(
            id=r["id"],
            symbol=r["symbol"],
            name=r["name"],
            asset_class=AssetClass(r["asset_class"]),
            currency=Currency(r["currency"]),
            manual_price=_dec(r["manual_price"]),
            purpose=Purpose(r["purpose"]),
        )

    def add(self, asset: Asset) -> Asset:
        try:
            with self.db.transaction() as c:
                cur = c.execute(
                    "INSERT INTO assets(symbol,name,asset_class,currency,manual_price,purpose) VALUES (?,?,?,?,?,?)",
                    (
                        asset.symbol,
                        asset.name,
                        asset.asset_class.value,
                        asset.currency.value,
                        _s(asset.manual_price),
                        asset.purpose.value,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateError(f"{asset.symbol} ({asset.asset_class.label}) zaten kayıtlı") from exc
        asset.id = cur.lastrowid
        return asset

    def update(self, asset: Asset) -> Asset:
        with self.db.transaction() as c:
            cur = c.execute(
                "UPDATE assets SET symbol=?, name=?, asset_class=?, currency=?, manual_price=?, purpose=? WHERE id=?",
                (
                    asset.symbol,
                    asset.name,
                    asset.asset_class.value,
                    asset.currency.value,
                    _s(asset.manual_price),
                    asset.purpose.value,
                    asset.id,
                ),
            )
        if cur.rowcount == 0:
            raise NotFoundError(f"Varlık bulunamadı: {asset.id}")
        return asset

    def delete(self, asset_id: int) -> None:
        with self.db.transaction() as c:
            c.execute("DELETE FROM assets WHERE id=?", (asset_id,))

    def get(self, asset_id: int) -> Asset:
        with self.db.transaction() as c:
            r = c.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        if r is None:
            raise NotFoundError(f"Varlık bulunamadı: {asset_id}")
        return self._row(r)

    def find(self, symbol: str, asset_class: AssetClass) -> Asset | None:
        with self.db.transaction() as c:
            r = c.execute(
                "SELECT * FROM assets WHERE symbol=? AND asset_class=?",
                (symbol.strip().upper(), asset_class.value),
            ).fetchone()
        return None if r is None else self._row(r)

    def list(self) -> list[Asset]:
        with self.db.transaction() as c:
            rows = c.execute("SELECT * FROM assets ORDER BY asset_class, symbol").fetchall()
        return [self._row(r) for r in rows]


# --------------------------------------------------------------------------- transactions
class TransactionRepository(_Repo):
    @staticmethod
    def _row(r: sqlite3.Row) -> Transaction:
        return Transaction(
            id=r["id"],
            asset_id=r["asset_id"],
            tx_type=TxType(r["tx_type"]),
            quantity=Decimal(r["quantity"]),
            price=Decimal(r["price"]),
            fee=Decimal(r["fee"]),
            trade_date=date.fromisoformat(r["trade_date"]),
            note=r["note"],
        )

    def add(self, tx: Transaction) -> Transaction:
        with self.db.transaction() as c:
            cur = c.execute(
                "INSERT INTO transactions(asset_id,tx_type,quantity,price,fee,trade_date,note) VALUES (?,?,?,?,?,?,?)",
                (
                    tx.asset_id,
                    tx.tx_type.value,
                    _s(tx.quantity),
                    _s(tx.price),
                    _s(tx.fee),
                    tx.trade_date.isoformat(),
                    tx.note,
                ),
            )
        tx.id = cur.lastrowid
        return tx

    def add_many(self, txs: Iterable[Transaction]) -> int:
        rows = [
            (t.asset_id, t.tx_type.value, _s(t.quantity), _s(t.price), _s(t.fee), t.trade_date.isoformat(), t.note)
            for t in txs
        ]
        with self.db.transaction() as c:
            c.executemany(
                "INSERT INTO transactions(asset_id,tx_type,quantity,price,fee,trade_date,note) VALUES (?,?,?,?,?,?,?)",
                rows,
            )
        return len(rows)

    def delete(self, tx_id: int) -> None:
        with self.db.transaction() as c:
            c.execute("DELETE FROM transactions WHERE id=?", (tx_id,))

    def list(self, asset_id: int | None = None) -> list[Transaction]:
        sql = "SELECT * FROM transactions"
        params: tuple[object, ...] = ()
        if asset_id is not None:
            sql += " WHERE asset_id=?"
            params = (asset_id,)
        sql += " ORDER BY trade_date, id"
        with self.db.transaction() as c:
            rows = c.execute(sql, params).fetchall()
        return [self._row(r) for r in rows]

    def by_asset(self) -> dict[int, list[Transaction]]:
        grouped: dict[int, list[Transaction]] = {}
        for tx in self.list():
            grouped.setdefault(tx.asset_id, []).append(tx)
        return grouped


# --------------------------------------------------------------------------- dividends
class DividendRepository(_Repo):
    @staticmethod
    def _row(r: sqlite3.Row) -> Dividend:
        return Dividend(
            id=r["id"],
            asset_id=r["asset_id"],
            ex_date=_date(r["ex_date"]),
            pay_date=date.fromisoformat(r["pay_date"]),
            amount_per_share=Decimal(r["amount_per_share"]),
            quantity=Decimal(r["quantity"]),
            currency=Currency(r["currency"]),
            withholding_rate=Decimal(r["withholding_rate"]),
            status=DividendStatus(r["status"]),
            note=r["note"],
        )

    def add(self, d: Dividend) -> Dividend:
        with self.db.transaction() as c:
            cur = c.execute(
                "INSERT INTO dividends(asset_id,ex_date,pay_date,amount_per_share,quantity,currency,"
                "withholding_rate,status,note) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    d.asset_id,
                    d.ex_date.isoformat() if d.ex_date else None,
                    d.pay_date.isoformat(),
                    _s(d.amount_per_share),
                    _s(d.quantity),
                    d.currency.value,
                    _s(d.withholding_rate),
                    d.status.value,
                    d.note,
                ),
            )
        d.id = cur.lastrowid
        return d

    def set_status(self, dividend_id: int, status: DividendStatus) -> None:
        with self.db.transaction() as c:
            c.execute("UPDATE dividends SET status=? WHERE id=?", (status.value, dividend_id))

    def delete(self, dividend_id: int) -> None:
        with self.db.transaction() as c:
            c.execute("DELETE FROM dividends WHERE id=?", (dividend_id,))

    def list(self) -> list[Dividend]:
        with self.db.transaction() as c:
            rows = c.execute("SELECT * FROM dividends ORDER BY pay_date DESC, id DESC").fetchall()
        return [self._row(r) for r in rows]


# --------------------------------------------------------------------------- price cache
class PriceCacheRepository(_Repo):
    def get_many(self, symbols: Sequence[str]) -> dict[str, ProviderQuote]:
        if not symbols:
            return {}
        marks = ",".join("?" * len(symbols))
        with self.db.transaction() as c:
            rows = c.execute(f"SELECT * FROM price_cache WHERE symbol IN ({marks})", tuple(symbols)).fetchall()
        return {
            r["symbol"]: ProviderQuote(
                symbol=r["symbol"],
                price=Decimal(r["price"]),
                previous_close=_dec(r["previous_close"]),
                fetched_at=datetime.fromisoformat(r["fetched_at"]),
            )
            for r in rows
        }

    def upsert_many(self, quotes: Iterable[ProviderQuote]) -> None:
        rows = [(q.symbol, _s(q.price), _s(q.previous_close), q.fetched_at.isoformat()) for q in quotes]
        with self.db.transaction() as c:
            c.executemany(
                "INSERT INTO price_cache(symbol,price,previous_close,fetched_at) VALUES (?,?,?,?) "
                "ON CONFLICT(symbol) DO UPDATE SET price=excluded.price, "
                "previous_close=excluded.previous_close, fetched_at=excluded.fetched_at",
                rows,
            )

    def clear(self) -> None:
        with self.db.transaction() as c:
            c.execute("DELETE FROM price_cache")


# --------------------------------------------------------------------------- snapshots
class SnapshotRepository(_Repo):
    def upsert(self, snap: Snapshot) -> None:
        self.upsert_many([snap])

    def upsert_many(self, snaps: Iterable[Snapshot]) -> None:
        rows = [(s.snapshot_date.isoformat(), s.currency.value, _s(s.total_value), _s(s.total_cost)) for s in snaps]
        with self.db.transaction() as c:
            c.executemany(
                "INSERT INTO portfolio_snapshots(snapshot_date,currency,total_value,total_cost) "
                "VALUES (?,?,?,?) ON CONFLICT(snapshot_date,currency) DO UPDATE SET "
                "total_value=excluded.total_value, total_cost=excluded.total_cost",
                rows,
            )

    def list(self, currency: Currency) -> list[Snapshot]:
        with self.db.transaction() as c:
            rows = c.execute(
                "SELECT * FROM portfolio_snapshots WHERE currency=? ORDER BY snapshot_date",
                (currency.value,),
            ).fetchall()
        return [
            Snapshot(
                date.fromisoformat(r["snapshot_date"]),
                Decimal(r["total_value"]),
                Decimal(r["total_cost"]),
                Currency(r["currency"]),
            )
            for r in rows
        ]

    def list_all(self) -> list[Snapshot]:
        return [s for cur in Currency for s in self.list(cur)]

    def clear(self, currency: Currency | None = None) -> None:
        with self.db.transaction() as c:
            if currency is None:
                c.execute("DELETE FROM portfolio_snapshots")
            else:
                c.execute("DELETE FROM portfolio_snapshots WHERE currency=?", (currency.value,))


# --------------------------------------------------------------------------- settings
class SettingsRepository(_Repo):
    def get(self, key: str, default: str | None = None) -> str | None:
        with self.db.transaction() as c:
            r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return default if r is None else str(r["value"])

    def set(self, key: str, value: str) -> None:
        with self.db.transaction() as c:
            c.execute(
                "INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def all(self) -> dict[str, str]:
        with self.db.transaction() as c:
            return {r["key"]: r["value"] for r in c.execute("SELECT key, value FROM settings")}


# --------------------------------------------------------------------------- budget
class CategoryRepository(_Repo):
    @staticmethod
    def _row(r: sqlite3.Row) -> Category:
        return Category(id=r["id"], name=r["name"], kind=FlowKind(r["kind"]), monthly_budget=_dec(r["monthly_budget"]))

    def add(self, cat: Category) -> Category:
        try:
            with self.db.transaction() as c:
                cur = c.execute(
                    "INSERT INTO categories(name,kind,monthly_budget) VALUES (?,?,?)",
                    (cat.name, cat.kind.value, _s(cat.monthly_budget)),
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateError(f"'{cat.name}' kategorisi zaten var") from exc
        cat.id = cur.lastrowid
        return cat

    def update(self, cat: Category) -> Category:
        try:
            with self.db.transaction() as c:
                cur = c.execute(
                    "UPDATE categories SET name=?, kind=?, monthly_budget=? WHERE id=?",
                    (cat.name, cat.kind.value, _s(cat.monthly_budget), cat.id),
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateError(f"'{cat.name}' kategorisi zaten var") from exc
        if cur.rowcount == 0:
            raise NotFoundError(f"Kategori bulunamadı: {cat.id}")
        return cat

    def delete(self, category_id: int) -> None:
        try:
            with self.db.transaction() as c:
                c.execute("DELETE FROM categories WHERE id=?", (category_id,))
        except sqlite3.IntegrityError as exc:
            raise ValidationError("Bu kategoride kayıt var; önce kayıtları silin veya taşıyın") from exc

    def get(self, category_id: int) -> Category:
        with self.db.transaction() as c:
            r = c.execute("SELECT * FROM categories WHERE id=?", (category_id,)).fetchone()
        if r is None:
            raise NotFoundError(f"Kategori bulunamadı: {category_id}")
        return self._row(r)

    def find(self, name: str) -> Category | None:
        with self.db.transaction() as c:
            r = c.execute("SELECT * FROM categories WHERE lower(name)=lower(?)", (" ".join(name.split()),)).fetchone()
        return None if r is None else self._row(r)

    def list(self) -> list[Category]:
        with self.db.transaction() as c:
            rows = c.execute("SELECT * FROM categories ORDER BY kind DESC, name").fetchall()
        return [self._row(r) for r in rows]


class CashFlowRepository(_Repo):
    @staticmethod
    def _row(r: sqlite3.Row) -> CashFlow:
        return CashFlow(
            id=r["id"],
            category_id=r["category_id"],
            flow_date=date.fromisoformat(r["flow_date"]),
            amount=Decimal(r["amount"]),
            note=r["note"],
        )

    def add(self, flow: CashFlow) -> CashFlow:
        return self.add_many([flow])[0]

    def add_many(self, flows: Sequence[CashFlow]) -> list[CashFlow]:
        with self.db.transaction() as c:
            for f in flows:
                cur = c.execute(
                    "INSERT INTO cash_flows(category_id,flow_date,amount,note) VALUES (?,?,?,?)",
                    (f.category_id, f.flow_date.isoformat(), _s(f.amount), f.note),
                )
                f.id = cur.lastrowid
        return list(flows)

    def delete(self, flow_id: int) -> None:
        with self.db.transaction() as c:
            c.execute("DELETE FROM cash_flows WHERE id=?", (flow_id,))

    def list(self, start: date | None = None, end: date | None = None) -> list[CashFlow]:
        """`start` dahil, `end` hariç tarih aralığındaki kayıtlar (yeniden eskiye)."""
        sql = "SELECT * FROM cash_flows WHERE 1=1"
        params: list[object] = []
        if start is not None:
            sql += " AND flow_date >= ?"
            params.append(start.isoformat())
        if end is not None:
            sql += " AND flow_date < ?"
            params.append(end.isoformat())
        sql += " ORDER BY flow_date DESC, id DESC"
        with self.db.transaction() as c:
            rows = c.execute(sql, params).fetchall()
        return [self._row(r) for r in rows]


class CpiRepository(_Repo):
    def all(self) -> dict[str, Decimal]:
        with self.db.transaction() as c:
            return {r["month"]: Decimal(r["value"]) for r in c.execute("SELECT month, value FROM cpi_monthly")}

    def replace_all(self, index: dict[str, Decimal]) -> None:
        with self.db.transaction() as c:
            c.execute("DELETE FROM cpi_monthly")
            c.executemany(
                "INSERT INTO cpi_monthly(month, value) VALUES (?, ?)",
                [(m, _s(v)) for m, v in sorted(index.items())],
            )

    def upsert(self, month: str, value: Decimal) -> None:
        with self.db.transaction() as c:
            c.execute(
                "INSERT INTO cpi_monthly(month, value) VALUES (?, ?) "
                "ON CONFLICT(month) DO UPDATE SET value=excluded.value",
                (month, _s(value)),
            )


class CategoryRuleRepository(_Repo):
    def list(self) -> list[tuple[int, str, int]]:
        """(id, kalıp, kategori id) listesi."""
        with self.db.transaction() as c:
            rows = c.execute("SELECT id, pattern, category_id FROM category_rules ORDER BY pattern").fetchall()
        return [(r["id"], r["pattern"], r["category_id"]) for r in rows]

    def upsert(self, pattern: str, category_id: int) -> None:
        with self.db.transaction() as c:
            c.execute(
                "INSERT INTO category_rules(pattern, category_id) VALUES (?, ?) "
                "ON CONFLICT(pattern) DO UPDATE SET category_id=excluded.category_id",
                (pattern, category_id),
            )

    def delete(self, rule_id: int) -> None:
        with self.db.transaction() as c:
            c.execute("DELETE FROM category_rules WHERE id=?", (rule_id,))


class ScoreHistoryRepository(_Repo):
    def upsert(self, month: str, score: Decimal, components: dict[str, str]) -> None:
        with self.db.transaction() as c:
            c.execute(
                "INSERT INTO score_history(month, score, components) VALUES (?, ?, ?) "
                "ON CONFLICT(month) DO UPDATE SET score=excluded.score, components=excluded.components",
                (month, _s(score), json.dumps(components, ensure_ascii=False)),
            )

    def list(self) -> list[tuple[str, Decimal, dict[str, str]]]:
        with self.db.transaction() as c:
            rows = c.execute("SELECT month, score, components FROM score_history ORDER BY month").fetchall()
        return [(r["month"], Decimal(r["score"]), json.loads(r["components"])) for r in rows]


class GoalRepository(_Repo):
    def _assets(self, c: sqlite3.Connection, goal_id: int) -> tuple[int, ...]:
        rows = c.execute("SELECT asset_id FROM goal_assets WHERE goal_id=? ORDER BY asset_id", (goal_id,))
        return tuple(r["asset_id"] for r in rows)

    def _write_assets(self, c: sqlite3.Connection, goal: Goal) -> None:
        c.execute("DELETE FROM goal_assets WHERE goal_id=?", (goal.id,))
        for aid in goal.asset_ids:
            # Varlık başka bir hedefe bağlıysa bu hedefe taşınır
            c.execute(
                "INSERT INTO goal_assets(asset_id, goal_id) VALUES (?, ?) "
                "ON CONFLICT(asset_id) DO UPDATE SET goal_id=excluded.goal_id",
                (aid, goal.id),
            )

    def add(self, goal: Goal) -> Goal:
        try:
            with self.db.transaction() as c:
                cur = c.execute(
                    "INSERT INTO goals(name, target_amount, target_date, extra_saved) VALUES (?, ?, ?, ?)",
                    (goal.name, _s(goal.target_amount), goal.target_date.isoformat(), _s(goal.extra_saved)),
                )
                goal.id = cur.lastrowid
                self._write_assets(c, goal)
        except sqlite3.IntegrityError as exc:
            raise DuplicateError(f"'{goal.name}' adlı hedef zaten var") from exc
        return goal

    def update(self, goal: Goal) -> Goal:
        try:
            with self.db.transaction() as c:
                cur = c.execute(
                    "UPDATE goals SET name=?, target_amount=?, target_date=?, extra_saved=? WHERE id=?",
                    (goal.name, _s(goal.target_amount), goal.target_date.isoformat(), _s(goal.extra_saved), goal.id),
                )
                if cur.rowcount == 0:
                    raise NotFoundError(f"Hedef bulunamadı: {goal.id}")
                self._write_assets(c, goal)
        except sqlite3.IntegrityError as exc:
            raise DuplicateError(f"'{goal.name}' adlı hedef zaten var") from exc
        return goal

    def delete(self, goal_id: int) -> None:
        with self.db.transaction() as c:
            c.execute("DELETE FROM goals WHERE id=?", (goal_id,))

    def list(self) -> list[Goal]:
        with self.db.transaction() as c:
            rows = c.execute("SELECT * FROM goals ORDER BY target_date, name").fetchall()
            return [
                Goal(
                    id=r["id"],
                    name=r["name"],
                    target_amount=Decimal(r["target_amount"]),
                    target_date=date.fromisoformat(r["target_date"]),
                    extra_saved=Decimal(r["extra_saved"]),
                    asset_ids=self._assets(c, r["id"]),
                )
                for r in rows
            ]
