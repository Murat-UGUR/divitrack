"""Domain varlıkları (entities). Saf Python — veritabanı veya UI bilgisi içermez."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from divitrack.core.enums import AssetClass, Currency, DividendStatus, FlowKind, Purpose, TxType
from divitrack.core.exceptions import ValidationError
from divitrack.core.money import ONE, ZERO, pct


@dataclass(slots=True)
class Asset:
    """Takip edilen enstrüman. Adet/maliyet burada değil, işlem defterinden türetilir.

    `manual_price`: fiyatı API'den gelmeyen varlıklar (BES fonu, mevduat) için birim değer.
    Nakit için adet = tutar, fiyat = 1 kabul edilir.
    """

    symbol: str
    asset_class: AssetClass
    currency: Currency
    name: str = ""
    manual_price: Decimal | None = None
    purpose: Purpose = Purpose.UNASSIGNED
    id: int | None = None

    def __post_init__(self) -> None:
        self.symbol = self.symbol.strip().upper()
        if not self.symbol:
            raise ValidationError("Sembol boş olamaz")
        self.asset_class = AssetClass(self.asset_class)
        self.currency = Currency(self.currency)
        self.purpose = Purpose(self.purpose)
        if self.manual_price is not None and self.manual_price < 0:
            raise ValidationError("Manuel fiyat negatif olamaz")

    @property
    def display_name(self) -> str:
        return f"{self.symbol} — {self.name}" if self.name else self.symbol


@dataclass(slots=True)
class Transaction:
    asset_id: int
    tx_type: TxType
    quantity: Decimal
    price: Decimal
    trade_date: date
    fee: Decimal = ZERO
    note: str = ""
    id: int | None = None

    def __post_init__(self) -> None:
        self.tx_type = TxType(self.tx_type)
        if self.tx_type is TxType.BONUS:
            self.price = ZERO  # bedelsiz/bölünme: maliyetsiz adet
        if self.quantity <= 0:
            raise ValidationError("Adet sıfırdan büyük olmalı")
        if self.price < 0:
            raise ValidationError("Fiyat negatif olamaz")
        if self.fee < 0:
            raise ValidationError("Komisyon negatif olamaz")

    @property
    def gross_amount(self) -> Decimal:
        return self.quantity * self.price


@dataclass(slots=True)
class Dividend:
    """Kullanıcının kaydettiği temettü (beklenen veya ödenmiş). Tutarlar brüttür."""

    asset_id: int
    pay_date: date
    amount_per_share: Decimal
    quantity: Decimal
    currency: Currency
    status: DividendStatus = DividendStatus.EXPECTED
    ex_date: date | None = None
    withholding_rate: Decimal = ZERO
    note: str = ""
    id: int | None = None

    def __post_init__(self) -> None:
        self.currency = Currency(self.currency)
        self.status = DividendStatus(self.status)
        if self.amount_per_share < 0:
            raise ValidationError("Hisse başı temettü negatif olamaz")
        if self.quantity <= 0:
            raise ValidationError("Adet sıfırdan büyük olmalı")
        if not ZERO <= self.withholding_rate < ONE:
            raise ValidationError("Stopaj oranı 0 ile 1 arasında olmalı (ör. 0.15)")

    @property
    def gross(self) -> Decimal:
        return self.amount_per_share * self.quantity

    @property
    def tax(self) -> Decimal:
        return self.gross * self.withholding_rate

    @property
    def net(self) -> Decimal:
        return self.gross - self.tax


@dataclass(frozen=True, slots=True)
class ProviderQuote:
    """Sağlayıcıdan gelen ham fiyat (sağlayıcı sembolü cinsinden)."""

    symbol: str
    price: Decimal
    previous_close: Decimal | None
    fetched_at: datetime


@dataclass(frozen=True, slots=True)
class PriceQuote:
    """Bir varlığın kendi para birimindeki fiyatı."""

    price: Decimal
    previous_close: Decimal | None
    currency: Currency
    as_of: datetime | None
    source: str = "market"  # market | manual | derived | cash
    stale: bool = False

    @property
    def change(self) -> Decimal:
        if self.previous_close is None:
            return ZERO
        return self.price - self.previous_close

    @property
    def change_pct(self) -> Decimal | None:
        if self.previous_close is None:
            return None
        return pct(self.change, self.previous_close)


@dataclass(slots=True)
class PositionState:
    """İşlem defterinden hesaplanan pozisyon (varlığın kendi para biriminde)."""

    quantity: Decimal = ZERO
    avg_cost: Decimal = ZERO
    cost_basis: Decimal = ZERO
    realized_pnl: Decimal = ZERO
    fees_paid: Decimal = ZERO
    first_trade: date | None = None

    @property
    def is_open(self) -> bool:
        return self.quantity > 0


@dataclass(frozen=True, slots=True)
class Snapshot:
    snapshot_date: date
    total_value: Decimal
    total_cost: Decimal
    currency: Currency


@dataclass(slots=True)
class DividendHistory:
    """Sağlayıcıdan gelen geçmiş temettü ödemeleri (hisse başı, brüt)."""

    symbol: str
    payments: list[tuple[date, Decimal]] = field(default_factory=list)


@dataclass(slots=True)
class Category:
    """Gelir veya gider kategorisi. `monthly_budget` yalnızca giderlerde anlamlıdır (aylık üst sınır)."""

    name: str
    kind: FlowKind
    monthly_budget: Decimal | None = None
    id: int | None = None

    def __post_init__(self) -> None:
        self.name = " ".join(self.name.split())
        if not self.name:
            raise ValidationError("Kategori adı boş olamaz")
        self.kind = FlowKind(self.kind)
        if self.monthly_budget is not None and self.monthly_budget < 0:
            raise ValidationError("Bütçe negatif olamaz")


@dataclass(slots=True)
class Goal:
    """Birikim hedefi (ev peşinatı, acil durum fonu…). İlerleme = bağlı varlıkların TL değeri + `extra_saved`."""

    name: str
    target_amount: Decimal
    target_date: date
    extra_saved: Decimal = ZERO
    asset_ids: tuple[int, ...] = ()
    id: int | None = None

    def __post_init__(self) -> None:
        self.name = " ".join(self.name.split())
        if not self.name:
            raise ValidationError("Hedef adı boş olamaz")
        if self.target_amount <= 0:
            raise ValidationError("Hedef tutarı sıfırdan büyük olmalı")
        if self.extra_saved < 0:
            raise ValidationError("Ek birikim negatif olamaz")
        self.asset_ids = tuple(sorted(set(self.asset_ids)))


@dataclass(slots=True)
class CashFlow:
    """Bir gelir veya gider kaydı. Tutar her zaman pozitiftir; yönü kategoriden gelir."""

    category_id: int
    flow_date: date
    amount: Decimal
    note: str = ""
    id: int | None = None

    def __post_init__(self) -> None:
        if self.amount <= 0:
            raise ValidationError("Tutar sıfırdan büyük olmalı")
