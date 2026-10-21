"""Dağılım, yeni para paylaştırma, senaryo ve puan hesapları (saf fonksiyonlar)."""

from __future__ import annotations

from collections.abc import Hashable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import TypeVar

from divitrack.core.money import HUNDRED, ONE, ZERO

K = TypeVar("K", bound=Hashable)


def weights(values: Mapping[K, Decimal]) -> dict[K, Decimal]:
    """Değerlerin toplam içindeki payı (0–1)."""
    total = sum(values.values(), ZERO)
    if total <= 0:
        return {k: ZERO for k in values}
    return {k: v / total for k, v in values.items()}


def drift(values: Mapping[K, Decimal], targets: Mapping[K, Decimal]) -> dict[K, Decimal]:
    """Gerçekleşen pay − hedef pay, yüzde puan olarak (ör. +7.5 = hedefin 7,5 puan üstünde)."""
    w = weights({k: values.get(k, ZERO) for k in {*values, *targets}})
    return {k: (w.get(k, ZERO) - targets.get(k, ZERO)) * HUNDRED for k in {*w, *targets}}


def allocate(amount: Decimal, values: Mapping[K, Decimal], targets: Mapping[K, Decimal]) -> dict[K, Decimal]:
    """Yeni parayı, satış yapmadan dağılımı hedefe en çok yaklaştıracak şekilde böler.

    1. Para eklendikten sonraki toplam için her amacın hedef tutarı bulunur.
    2. Hedefin altında kalan amaçların açığı kapatılır; para yetmezse açıklar oranında bölünür.
    3. Açıklar kapandıktan sonra kalan para hedef oranlarına göre dağıtılır.

    `targets` 0–1 arası paylardır; toplamı 1 değilse normalize edilir.
    """
    if amount <= 0:
        return {k: ZERO for k in targets}
    target_sum = sum(targets.values(), ZERO)
    if target_sum <= 0:
        return {k: ZERO for k in targets}
    norm = {k: t / target_sum for k, t in targets.items()}
    current_total = sum(values.values(), ZERO)
    new_total = current_total + amount
    gaps = {k: max(ZERO, norm[k] * new_total - values.get(k, ZERO)) for k in norm}
    gap_sum = sum(gaps.values(), ZERO)
    if gap_sum >= amount:
        return {k: amount * g / gap_sum for k, g in gaps.items()}
    rest = amount - gap_sum
    return {k: gaps[k] + rest * norm[k] for k in norm}


def apply_shocks(
    rows: list[tuple[Decimal, str, bool]], class_shocks: Mapping[str, Decimal], fx_shock: Decimal
) -> Decimal:
    """Senaryo: her satır (değer, varlık sınıfı, yabancı para mı) için şok uygulanmış toplam değer.

    Şoklar oran olarak verilir (−0.20 = %20 düşüş). Yabancı para cinsinden varlıklara kur şoku da uygulanır.
    """
    total = ZERO
    for value, asset_class, foreign in rows:
        factor = ONE + class_shocks.get(asset_class, ZERO)
        if foreign:
            factor *= ONE + fx_shock
        total += value * factor
    return total


# --------------------------------------------------------------------------- puanlama
@dataclass(frozen=True, slots=True)
class ScoreComponent:
    key: str
    title: str
    points: Decimal
    max_points: Decimal
    detail: str
    tip: str = ""
    available: bool = True  # veri yoksa toplam puana katılmaz

    @property
    def missing(self) -> Decimal:
        return self.max_points - self.points if self.available else ZERO


def linear_score(value: Decimal, good: Decimal, bad: Decimal, max_points: Decimal) -> Decimal:
    """`good` ve daha iyisinde tam puan, `bad` ve daha kötüsünde 0; arası doğrusal.

    `good < bad` ise küçük değer iyidir (ör. yoğunlaşma), `good > bad` ise büyük değer iyidir.
    """
    if good == bad:
        return max_points if value == good else ZERO
    ratio = (value - bad) / (good - bad)
    ratio = min(ONE, max(ZERO, ratio))
    return (ratio * max_points).quantize(Decimal("0.1"))


def total_score(components: list[ScoreComponent]) -> Decimal | None:
    """Verisi olan bileşenlerin puanını 100 üzerinden ölçekler."""
    usable = [c for c in components if c.available]
    max_sum = sum((c.max_points for c in usable), ZERO)
    if max_sum <= 0:
        return None
    return (sum((c.points for c in usable), ZERO) / max_sum * HUNDRED).quantize(Decimal("1"))


def score_grade(score: Decimal | None) -> str:
    if score is None:
        return "Veri yok"
    if score >= 85:
        return "Çok iyi"
    if score >= 70:
        return "İyi"
    if score >= 50:
        return "Gözden geçir"
    return "Dikkat"
