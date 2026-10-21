"""TEFAS, EVDS ve Excel okuma — ağ yok; HTTP katmanı sahte `transport` ile değiştirilir."""

import io
import json
from datetime import date, datetime
from decimal import Decimal as D

import pytest

from divitrack.core.entities import Asset, ProviderQuote
from divitrack.core.enums import AssetClass, Currency
from divitrack.core.exceptions import ImportFormatError
from divitrack.core.statement import ColumnMap, parse_statement, read_table
from divitrack.services.cpi_service import API_KEY_ENV, API_KEY_SETTING, CpiService
from divitrack.services.market.evds_client import EvdsClient, EvdsError
from divitrack.services.market.symbols import provider_symbol
from divitrack.services.market.tefas_provider import URL, CompositeProvider, TefasProvider, months_back


class Recorder:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, url, method, headers, body):
        self.calls.append((url, method, headers, json.loads(body) if body else None))
        response = self.responses(url, json.loads(body) if body else None)
        if isinstance(response, Exception):
            raise response
        return json.dumps(response).encode()


def tefas_rows(code):
    return {
        "resultList": [
            {"tarih": "2026-10-17", "fonKodu": code, "fiyat": 1.5},
            {"tarih": "2026-10-20T00:00:00", "fonKodu": code, "fiyat": "1,6"},
            {"tarih": "2026-10-19", "fonKodu": code, "fiyat": None},
            {"tarih": "bozuk", "fiyat": 1},
        ]
    }


# --------------------------------------------------------------------------- TEFAS
def test_fund_symbols_route_to_tefas() -> None:
    assert provider_symbol(Asset("tte", AssetClass.FUND, Currency.TRY)) == "TEFAS:TTE"
    assert provider_symbol(Asset("TTE", AssetClass.FUND, Currency.TRY, manual_price=D(1))) is None


def test_months_back_snaps_to_valid_periods() -> None:
    today = date(2026, 10, 21)
    assert months_back(date(2026, 10, 15), today) == 1
    assert months_back(date(2026, 6, 1), today) == 6
    assert months_back(date(2025, 1, 1), today) == 36
    assert months_back(date(2010, 1, 1), today) == 60


def test_tefas_quotes_and_history() -> None:
    transport = Recorder(lambda url, body: tefas_rows(body["fonKodu"]))
    p = TefasProvider(transport, today=lambda: date(2026, 10, 21))
    quotes = p.fetch_quotes(["TEFAS:TTE", "AAPL"])
    assert list(quotes) == ["TEFAS:TTE"]
    assert quotes["TEFAS:TTE"].price == D("1.6") and quotes["TEFAS:TTE"].previous_close == D("1.5")
    url, method, headers, body = transport.calls[0]
    assert (url, method, body) == (URL, "POST", {"fonKodu": "TTE", "dil": "TR", "periyod": 1})
    assert headers["Content-Type"] == "application/json"

    hist = p.fetch_history(["TEFAS:TTE"], date(2026, 10, 18))
    assert hist == {"TEFAS:TTE": [(date(2026, 10, 20), D("1.6"))]}
    assert p.fetch_dividends("TEFAS:TTE") == []


def test_tefas_errors_are_per_fund() -> None:
    def respond(url, body):
        return ConnectionError("WAF") if body["fonKodu"] == "BAD" else tefas_rows(body["fonKodu"])

    p = TefasProvider(Recorder(respond))
    assert list(p.fetch_quotes(["TEFAS:BAD", "TEFAS:AFT"])) == ["TEFAS:AFT"]
    assert p.fetch_history(["TEFAS:BAD"], date(2026, 10, 1)) == {}
    empty = TefasProvider(Recorder(lambda url, body: {"resultList": None}))
    assert empty.fetch_quotes(["TEFAS:X"]) == {}


class StubProvider:
    name = "stub"

    def __init__(self, fail=False):
        self.fail = fail
        self.seen = []

    def fetch_quotes(self, symbols):
        self.seen.append(list(symbols))
        if self.fail:
            raise ConnectionError("down")
        return {s: ProviderQuote(s, D(1), None, datetime.now()) for s in symbols}

    def fetch_dividends(self, symbol):
        return [(date(2026, 1, 1), D(1))]

    def fetch_history(self, symbols, start):
        if self.fail:
            raise ConnectionError("down")
        return {s: [(start, D(1))] for s in symbols}


def test_composite_provider_routes_and_isolates_failures() -> None:
    tefas, yahoo = StubProvider(), StubProvider(fail=True)
    comp = CompositeProvider({"TEFAS:": tefas}, yahoo)
    assert list(comp.fetch_quotes(["TEFAS:TTE", "AAPL"])) == ["TEFAS:TTE"]
    assert tefas.seen == [["TEFAS:TTE"]] and yahoo.seen == [["AAPL"]]
    with pytest.raises(ConnectionError):
        comp.fetch_quotes(["AAPL"])  # hiçbir şey alınamadıysa hata yukarı çıkar (önbelleğe düşülür)
    assert list(comp.fetch_history(["TEFAS:TTE", "AAPL"], date(2026, 1, 1))) == ["TEFAS:TTE"]
    assert comp.fetch_dividends("AAPL") == [(date(2026, 1, 1), D(1))]
    assert CompositeProvider({}, None).fetch_quotes(["AAPL"]) == {}
    assert CompositeProvider({}, None).fetch_dividends("AAPL") == []


def test_fund_priced_through_market_service(container, provider) -> None:
    provider.prices["TEFAS:TTE"] = ("2.5", "2.4")
    fund = container.portfolio.add_asset(Asset("tte", AssetClass.FUND, Currency.USD))
    assert fund.currency is Currency.TRY  # fonlar TL
    quote = container.market.quotes_for([fund])[fund.id]
    assert quote.price == D("2.5") and quote.currency is Currency.TRY


# --------------------------------------------------------------------------- EVDS
EVDS_BODY = {
    "totalCount": 3,
    "items": [
        {"Tarih": "2026-7", "TP_FG_J0": "3300.10", "UNIXTIME": {"$numberLong": "1"}},
        {"Tarih": "2026-8", "TP_FG_J0": "3350,25"},
        {"Tarih": "2026-9", "TP_FG_J0": None},
    ],
}


def test_evds_cpi_parsing_and_key_header() -> None:
    transport = Recorder(lambda url, body: EVDS_BODY)
    client = EvdsClient(" anahtar ", transport)
    cpi = client.cpi(date(2026, 1, 1), date(2026, 10, 21))
    assert cpi == {"2026-07": D("3300.10"), "2026-08": D("3350.25")}
    url, method, headers, _ = transport.calls[0]
    assert url.startswith("https://evds3.tcmb.gov.tr/igmevdsms-dis/series=TP.FG.J0&startDate=01-01-2026")
    assert "endDate=21-10-2026" in url and "key=" not in url
    assert headers["key"] == "anahtar" and method == "GET"


def test_evds_errors() -> None:
    with pytest.raises(EvdsError):
        EvdsClient("  ")
    with pytest.raises(EvdsError):
        EvdsClient("k", Recorder(lambda url, body: {"error": "yetkisiz"})).cpi(date(2026, 1, 1))
    with pytest.raises(EvdsError):
        EvdsClient("k", Recorder(lambda url, body: OSError("timeout"))).cpi(date(2026, 1, 1))


def test_cpi_service_merges_and_hides_key(container, monkeypatch) -> None:
    c = container
    c.cpi.replace_all({"2026-06": D("3250"), "2026-07": D("1")})
    svc = CpiService(c.cpi, c.settings_repo, lambda key: EvdsClient(key, Recorder(lambda url, body: EVDS_BODY)))
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    with pytest.raises(EvdsError):
        svc.update_from_evds()
    svc.set_api_key("gizli-anahtar")
    result = svc.update_from_evds(date(2026, 1, 1))
    assert (result.added, result.changed, result.latest) == (1, 1, "2026-08")
    assert c.cpi.all()["2026-06"] == D("3250")  # EVDS'de olmayan ay korunur
    assert "1 yeni ay" in str(result)
    assert "zaten güncel" in str(svc.update_from_evds(date(2026, 1, 1)))

    assert c.settings_repo.get(API_KEY_SETTING) == "gizli-anahtar"
    assert "gizli-anahtar" not in c.transfer.export_json()
    monkeypatch.setenv(API_KEY_ENV, "ortamdan")
    assert svc.api_key == "ortamdan" and svc.key_from_env


# --------------------------------------------------------------------------- Excel
def test_xlsx_statement() -> None:
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Hesap: TR00"])
    ws.append([])
    ws.append(["İşlem Tarihi", "Açıklama", "Tutar"])
    ws.append([datetime(2026, 10, 3), "MİGROS", -1250.4])
    ws.append([date(2026, 10, 4), "Faiz", 15.125])
    ws.append(["03.10.2026", "Maaş", 65000])
    buf = io.BytesIO()
    wb.save(buf)

    header, rows = read_table(buf.getvalue())
    assert header == ["İşlem Tarihi", "Açıklama", "Tutar"]
    parsed = parse_statement(rows, ColumnMap(date=0, description=1, amount=2))
    assert [(r.day, r.amount) for r in parsed] == [
        (date(2026, 10, 3), D("-1250.4")),
        (date(2026, 10, 4), D("15.125")),  # binlik ayırıcı sanılmaz
        (date(2026, 10, 3), D("65000")),
    ]


def test_old_xls_and_broken_xlsx_rejected() -> None:
    with pytest.raises(ImportFormatError):
        read_table(b"\xd0\xcf\x11\xe0" + b"\x00" * 10)
    with pytest.raises(ImportFormatError):
        read_table(b"PK\x03\x04bozuk")
