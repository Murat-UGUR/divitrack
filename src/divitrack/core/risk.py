"""Risk ölçüleri: para girişlerinden arındırılmış zirveden düşüş, oynaklık ve korelasyon (saf fonksiyonlar)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

TRADING_DAYS = 252


@dataclass(frozen=True, slots=True)
class Drawdown:
    max_pct: Decimal  # en büyük düşüş (eksi yüzde, ör. -12.5)
    peak: date | None
    trough: date | None
    current_pct: Decimal  # bugün zirveye göre (eksi ya da 0)
    recovered: bool


def performance_index(points: Sequence[tuple[date, Decimal, Decimal]]) -> list[tuple[date, float]]:
    """(tarih, değer, maliyet) dizisinden para giriş/çıkışından arındırılmış endeks (ilk gün = 100).

    Maliyetteki değişim o günkü yeni para (eksi ise çekilen para) sayılır:
    getiri = (değer − yeni para) / önceki değer − 1. Böylece alım yapmak "yükseliş", satış "düşüş" görünmez.
    """
    if not points:
        return []
    pts = sorted(points)
    out = [(pts[0][0], 100.0)]
    level = 100.0
    for (_, v0, c0), (d1, v1, c1) in zip(pts, pts[1:], strict=False):
        if v0 <= 0:
            out.append((d1, level))
            continue
        flow = float(c1 - c0)
        level *= (float(v1) - flow) / float(v0)
        out.append((d1, level))
    return out


def drawdown(index: Sequence[tuple[date, float]]) -> Drawdown | None:
    if len(index) < 2:
        return None
    peak_v, peak_d = index[0][1], index[0][0]
    best = 0.0
    best_peak: date | None = None
    best_trough: date | None = None
    for d, v in index:
        if v > peak_v:
            peak_v, peak_d = v, d
        dd = v / peak_v - 1 if peak_v > 0 else 0.0
        if dd < best:
            best, best_peak, best_trough = dd, peak_d, d
    last = index[-1][1]
    top = max(v for _, v in index)
    current = last / top - 1 if top > 0 else 0.0
    return Drawdown(
        max_pct=Decimal(str(round(best * 100, 4))),
        peak=best_peak,
        trough=best_trough,
        current_pct=Decimal(str(round(current * 100, 4))),
        recovered=current > -1e-9,
    )


def daily_returns(series: dict[date, Decimal]) -> dict[date, float]:
    days = sorted(series)
    out: dict[date, float] = {}
    for d0, d1 in zip(days, days[1:], strict=False):
        p0, p1 = float(series[d0]), float(series[d1])
        if p0 > 0 and p1 > 0:
            out[d1] = math.log(p1 / p0)
    return out


def volatility(returns: dict[date, float]) -> Decimal | None:
    """Yıllıklandırılmış oynaklık (%) — günlük log getirilerin standart sapması × √252."""
    values = list(returns.values())
    if len(values) < 20:
        return None
    mean = sum(values) / len(values)
    var = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
    return Decimal(str(round(math.sqrt(var) * math.sqrt(TRADING_DAYS) * 100, 4)))


def correlation(a: dict[date, float], b: dict[date, float]) -> Decimal | None:
    common = sorted(set(a) & set(b))
    if len(common) < 20:
        return None
    xs, ys = [a[d] for d in common], [b[d] for d in common]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    r = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / (sx * sy)
    return Decimal(str(round(r, 4)))
