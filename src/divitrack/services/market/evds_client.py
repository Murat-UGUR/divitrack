"""TCMB EVDS (Elektronik Veri Dağıtım Sistemi) istemcisi — TÜFE endeksini otomatik almak için.

EVDS ücretsizdir ama kişisel API anahtarı ister (evds3.tcmb.gov.tr → üye ol → profil → API anahtarı).
Anahtar URL'de değil `key` başlığında gönderilir:

    GET https://evds3.tcmb.gov.tr/igmevdsms-dis/series=TP.FG.J0&startDate=01-01-2020&endDate=31-12-2026&type=json
    key: <API anahtarı>
    → {"items": [{"Tarih": "2026-9", "TP_FG_J0": "3412.58", ...}, ...]}

TP.FG.J0: Tüketici fiyat endeksi (Genel, 2003=100), aylık.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from divitrack.core.exceptions import DivitrackError
from divitrack.core.inflation import parse_month
from divitrack.services.market.http import Transport, get_json, urllib_transport

BASE_URL = "https://evds3.tcmb.gov.tr/igmevdsms-dis/"
CPI_SERIES = "TP.FG.J0"


class EvdsError(DivitrackError):
    pass


class EvdsClient:
    def __init__(self, api_key: str, transport: Transport | None = None) -> None:
        if not api_key.strip():
            raise EvdsError("EVDS API anahtarı girilmemiş")
        self.api_key = api_key.strip()
        self.transport = transport or urllib_transport()

    def series(self, code: str, start: date, end: date) -> dict[str, Decimal]:
        """Aylık seri: ay ("YYYY-AA") → değer. Boş (henüz açıklanmamış) aylar atlanır."""
        url = (
            f"{BASE_URL}series={code}&startDate={start:%d-%m-%Y}&endDate={end:%d-%m-%Y}"
            "&type=json&frequency=5&aggregationTypes=last&formulas=0"
        )
        try:
            body = get_json(self.transport, url, {"key": self.api_key})
        except EvdsError:
            raise
        except Exception as exc:
            raise EvdsError(f"EVDS'ye ulaşılamadı veya yanıt okunamadı: {exc}") from exc
        items = body.get("items") if isinstance(body, dict) else None
        if not isinstance(items, list):
            raise EvdsError("EVDS yanıtında veri yok (anahtarı ve seri kodunu kontrol edin)")
        field = code.replace(".", "_")
        out: dict[str, Decimal] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            value = _decimal(item.get(field))
            if value is None or not item.get("Tarih"):
                continue
            try:
                out[parse_month(str(item["Tarih"]))] = value
            except DivitrackError:
                continue
        return out

    def cpi(self, start: date, end: date | None = None) -> dict[str, Decimal]:
        return self.series(CPI_SERIES, start, end or date.today())


def _decimal(value: Any) -> Decimal | None:
    if value in (None, "", "null"):
        return None
    try:
        result = Decimal(str(value).replace(",", "."))
    except InvalidOperation:
        return None
    return result if result > 0 else None
