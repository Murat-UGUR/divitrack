"""Composition root: tüm bağımlılıkları tek yerde oluşturur (manuel dependency injection)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from divitrack.config import Settings
from divitrack.core.enums import Currency
from divitrack.core.ports import PriceProvider
from divitrack.db.database import Database
from divitrack.repositories.sqlite import (
    AlertRepository,
    AssetRepository,
    AssetSnapshotRepository,
    CashFlowRepository,
    CategoryRepository,
    CategoryRuleRepository,
    CpiRepository,
    DividendRepository,
    GoalRepository,
    PpiRepository,
    PriceCacheRepository,
    ScoreHistoryRepository,
    SettingsRepository,
    SnapshotRepository,
    TransactionRepository,
)
from divitrack.services.alert_service import AlertService
from divitrack.services.analytics_service import AnalyticsService
from divitrack.services.budget_service import BudgetService
from divitrack.services.cpi_service import CpiService
from divitrack.services.dividend_engine import DividendEngine
from divitrack.services.goal_service import GoalService
from divitrack.services.insights_service import InsightsService
from divitrack.services.io.broker_import import BrokerImportService
from divitrack.services.io.transfer import DataTransferService
from divitrack.services.market.market_data_service import MarketDataService
from divitrack.services.notify import TelegramNotifier
from divitrack.services.plan_service import PlanService
from divitrack.services.portfolio_service import PortfolioService
from divitrack.services.quick_entry_service import QuickEntryService
from divitrack.services.risk_service import RiskService
from divitrack.services.security_service import SecurityService
from divitrack.services.snapshot_service import SnapshotService
from divitrack.services.tax_service import TaxService

log = logging.getLogger(__name__)

BASE_CURRENCY_KEY = "base_currency"


@dataclass(slots=True)
class Container:
    settings: Settings
    db: Database
    settings_repo: SettingsRepository
    market: MarketDataService
    portfolio: PortfolioService
    dividends: DividendEngine
    snapshots: SnapshotService
    transfer: DataTransferService
    budget: BudgetService
    insights: InsightsService
    cpi: CpiRepository
    inflation: CpiService
    goals: GoalService
    analytics: AnalyticsService
    risk: RiskService
    tax: TaxService
    plans: PlanService
    ppi: PpiRepository
    notifier: TelegramNotifier
    alerts: AlertService
    quick: QuickEntryService
    broker_import: BrokerImportService
    security: SecurityService

    @property
    def base_currency(self) -> Currency:
        stored = self.settings_repo.get(BASE_CURRENCY_KEY)
        try:
            return Currency(stored or self.settings.base_currency)
        except ValueError:
            return Currency.TRY

    def set_base_currency(self, currency: Currency) -> None:
        self.settings_repo.set(BASE_CURRENCY_KEY, currency.value)


def default_provider(settings: Settings) -> PriceProvider | None:
    """Yahoo (hisse, ETF, maden, kur) + TEFAS (yatırım fonları). Çevrimdışı modda hiçbiri."""
    if settings.offline:
        return None
    from divitrack.services.market.tefas_provider import PREFIX, CompositeProvider, TefasProvider

    yahoo: PriceProvider | None = None
    try:
        from divitrack.services.market.yfinance_provider import YFinanceProvider

        yahoo = YFinanceProvider()
    except ImportError:
        log.warning("yfinance kurulu değil — yalnızca TEFAS fiyatları alınacak")
    return CompositeProvider({PREFIX: TefasProvider()}, yahoo)


def build_container(
    settings: Settings | None = None, provider: PriceProvider | None = None, *, use_default_provider: bool = True
) -> Container:
    settings = settings or Settings.load()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if provider is None and use_default_provider:
        provider = default_provider(settings)

    db = Database(settings.db_path)
    assets = AssetRepository(db)
    txs = TransactionRepository(db)
    divs = DividendRepository(db)
    snaps = SnapshotRepository(db)
    settings_repo = SettingsRepository(db)

    categories = CategoryRepository(db)
    flows = CashFlowRepository(db)
    cpi = CpiRepository(db)

    market = MarketDataService(provider, PriceCacheRepository(db), settings.price_cache_ttl_min)
    portfolio = PortfolioService(assets, txs, market)
    budget = BudgetService(categories, flows, settings_repo, CategoryRuleRepository(db))
    insights = InsightsService(portfolio, txs, divs, budget, cpi, market, settings_repo, ScoreHistoryRepository(db))
    dividends = DividendEngine(divs, settings_repo, provider)
    notifier = TelegramNotifier(settings_repo, env_token=settings.telegram_token)
    container = Container(
        settings=settings,
        db=db,
        settings_repo=settings_repo,
        market=market,
        portfolio=portfolio,
        dividends=dividends,
        snapshots=SnapshotService(snaps, portfolio, asset_snapshots=AssetSnapshotRepository(db)),
        transfer=DataTransferService(
            db,
            assets,
            txs,
            divs,
            snaps,
            settings_repo,
            categories,
            flows,
            cpi,
            CategoryRuleRepository(db),
            GoalRepository(db),
            PpiRepository(db),
            AlertRepository(db),
        ),
        budget=budget,
        insights=insights,
        cpi=cpi,
        inflation=CpiService(cpi, settings_repo),
        goals=GoalService(GoalRepository(db), portfolio, budget),
        analytics=AnalyticsService(portfolio, txs, cpi, market, settings_repo, insights),
        risk=RiskService(snaps, portfolio),
        tax=TaxService(portfolio, PpiRepository(db), settings_repo),
        plans=PlanService(portfolio, dividends, settings_repo),
        ppi=PpiRepository(db),
        notifier=notifier,
        alerts=AlertService(AlertRepository(db), portfolio, snaps, notifier),
        quick=QuickEntryService(portfolio),
        broker_import=BrokerImportService(portfolio, settings_repo),
        security=SecurityService(settings_repo),
    )
    container.insights.goals = container.goals
    return container
