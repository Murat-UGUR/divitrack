"""Yahoo Finance adaptörü (`yfinance`). `PriceProvider` port'unu uygular.

Not: Yahoo'nun resmi bir API'si yoktur; yfinance gayriresmî bir istemcidir ve
zaman zaman kırılabilir. Bu yüzden servis katmanı her hatada önbelleğe düşer.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

log = logging.getLogger(__name__)


def _to_decimal(value: Any) -> Decimal | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return Decimal(repr(round(f, 6)))


def _close_series(df: Any, symbol: str, n_symbols: int) -> Any:
    """yf.download çıktısından tek sembolün Close serisini, kolon yapısından bağımsız alır."""
    import pandas as pd

    if df is None or getattr(df, "empty", True):
        return None
    cols = df.columns
    if isinstance(cols, pd.MultiIndex):
        lvl0 = set(cols.get_level_values(0))
        lvl1 = set(cols.get_level_values(1))
        if symbol in lvl0 and "Close" in lvl1:
            return df[symbol]["Close"]
        if "Close" in lvl0 and symbol in lvl1:
            return df["Close"][symbol]
        return None
    if "Close" in cols and n_symbols == 1:
        return df["Close"]
    return None


class YFinanceProvider:
    name = "yfinance"

    def __init__(self, timeout: int = 20) -> None:
        import yfinance  # noqa: F401  — kurulu değilse burada hata verir

        self.timeout = timeout

    def _download(self, symbols: Sequence[str], **kwargs: Any) -> Any:
        import yfinance as yf

        return yf.download(
            list(symbols),
            interval="1d",
            auto_adjust=False,
            progress=False,
            group_by="ticker",
            threads=True,
            timeout=self.timeout,
            **kwargs,
        )

    def fetch_quotes(self, symbols: Sequence[str]) -> dict[str, Any]:
        from divitrack.core.entities import ProviderQuote

        if not symbols:
            return {}
        df = self._download(symbols, period="10d")
        now = datetime.now()
        out: dict[str, ProviderQuote] = {}
        for sym in symbols:
            series = _close_series(df, sym, len(symbols))
            if series is None:
                continue
            closes = [v for v in (_to_decimal(x) for x in series.dropna().tolist()) if v is not None]
            if not closes:
                log.info("Fiyat bulunamadı: %s", sym)
                continue
            prev = closes[-2] if len(closes) > 1 else None
            out[sym] = ProviderQuote(symbol=sym, price=closes[-1], previous_close=prev, fetched_at=now)
        return out

    def fetch_dividends(self, symbol: str) -> list[tuple[date, Decimal]]:
        import yfinance as yf

        series = yf.Ticker(symbol).dividends
        out: list[tuple[date, Decimal]] = []
        if series is None or series.empty:
            return out
        for ts, value in series.items():
            amount = _to_decimal(value)
            if amount is not None and amount > 0:
                out.append((ts.date(), amount))
        return sorted(out)

    def fetch_history(self, symbols: Sequence[str], start: date) -> dict[str, list[tuple[date, Decimal]]]:
        if not symbols:
            return {}
        df = self._download(symbols, start=start.isoformat())
        out: dict[str, list[tuple[date, Decimal]]] = {}
        for sym in symbols:
            series = _close_series(df, sym, len(symbols))
            if series is None:
                continue
            points: list[tuple[date, Decimal]] = []
            for ts, value in series.dropna().items():
                v = _to_decimal(value)
                if v is not None:
                    points.append((ts.date(), v))
            out[sym] = points
        return out
