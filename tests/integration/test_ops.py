"""0.9: alarmlar ve Telegram, hızlı giriş, Telegram botu, döküm aktarma, parola ve şifreli yedek, CLI."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from divitrack.core.entities import Alert, Asset, Snapshot, Transaction
from divitrack.core.enums import AlertKind, AssetClass, Currency, TxType
from divitrack.core.exceptions import ValidationError
from divitrack.services.notify import TelegramNotifier
from divitrack.services.telegram_bot import TelegramBot

TODAY = date.today()


@pytest.fixture
def c(container):
    container.portfolio.add_asset(Asset("XAU-GRAM", AssetClass.PRECIOUS_METAL, Currency.TRY))
    thy = container.portfolio.add_asset(Asset("THYAO", AssetClass.BIST_STOCK, Currency.TRY))
    container.portfolio.add_transaction(Transaction(thy.id, TxType.BUY, D("100"), D("250"), date(2026, 1, 5)))
    return container


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, params: dict) -> dict:
        self.calls.append((url.rsplit("/", 1)[-1], params))
        return {"ok": True, "result": []}


def _telegram(c) -> FakeTransport:
    t = FakeTransport()
    c.notifier.transport = t
    c.notifier.configure("123:abc", "42")
    return t


# --------------------------------------------------------------------------- alarmlar
def test_alerts_trigger_and_notify_once_per_day(c) -> None:
    t = _telegram(c)
    thy = next(a for a in c.portfolio.assets.list() if a.symbol == "THYAO")
    c.alerts.add(Alert(AlertKind.PRICE_ABOVE, D("290"), thy.id, "kâr al"))  # fiyat 300 → tetiklenir
    c.alerts.add(Alert(AlertKind.PRICE_BELOW, D("200"), thy.id))  # tetiklenmez
    c.alerts.add(Alert(AlertKind.WEIGHT_ABOVE, D("90"), thy.id))  # tek kalem %100 → tetiklenir
    states = c.alerts.evaluate()
    assert [s.triggered for s in states] == [True, False, True]
    assert "THYAO fiyatı 290 üstüne çıkarsa (kâr al)" in states[0].message
    fired = c.alerts.check()
    assert len(fired) == 2 and len([x for x in t.calls if x[0] == "sendMessage"]) == 2
    assert t.calls[0][1]["chat_id"] == "42"
    c.alerts.check()  # aynı gün tekrar bildirim yok
    assert len([x for x in t.calls if x[0] == "sendMessage"]) == 2


def test_inactive_alert_and_drawdown(c) -> None:
    snaps = c.snapshots.snapshots
    snaps.upsert(Snapshot(TODAY - timedelta(days=2), D("100000"), D("100000"), Currency.TRY))
    snaps.upsert(Snapshot(TODAY - timedelta(days=1), D("85000"), D("100000"), Currency.TRY))
    al = c.alerts.add(Alert(AlertKind.DRAWDOWN, D("10")))
    (s,) = c.alerts.evaluate()
    assert s.triggered and s.current == D("15.0")
    c.alerts.set_active(al, False)
    assert not c.alerts.evaluate()[0].triggered


def test_alert_validation(c) -> None:
    with pytest.raises(ValidationError):
        Alert(AlertKind.PRICE_BELOW, D("10"))  # varlık yok
    with pytest.raises(ValidationError):
        Alert(AlertKind.DRAWDOWN, D("150"))
    assert Alert(AlertKind.DRAWDOWN, D("5"), asset_id=3).asset_id is None


def test_notifier_not_configured_and_errors(container) -> None:
    n = TelegramNotifier(container.settings_repo)
    assert not n.configured and n.send("x") is False

    def boom(url, params):
        raise OSError("ağ yok")

    n = TelegramNotifier(container.settings_repo, transport=boom, env_token="1:a")
    n.configure(None, "5")
    assert n.configured and n.send("x") is False


def test_telegram_token_not_in_backup(c) -> None:
    _telegram(c)
    assert "123:abc" not in c.transfer.export_json()


# --------------------------------------------------------------------------- hızlı giriş ve bot
def test_quick_entry_prepare_and_save(c) -> None:
    e = c.quick.prepare("5 gr altın 5750 01.11.2026")
    assert e.asset.symbol == "XAU-GRAM" and e.transaction.trade_date == date(2026, 11, 1)
    assert "Alış: 5 XAU-GRAM × 5750" in e.describe()
    c.quick.save(e)
    assert any(t.note == "hızlı giriş" for t in c.portfolio.transactions.list())
    warned = c.quick.prepare("1 altın 100$")
    assert warned.warnings
    with pytest.raises(ValidationError):
        c.quick.prepare("5 ASELS 300")  # kayıtlı değil


def test_telegram_bot_flow(c) -> None:
    _telegram(c)
    bot = TelegramBot(c)
    assert bot.handle("99", "/durum") is None  # yetkisiz sohbet
    assert "Toplam: 30.000 TL" in bot.handle("42", "/durum")
    reply = bot.handle("42", "sat 10 THYAO 300")
    assert "Satış: 10 THYAO" in reply and "/onay" in reply
    assert bot.handle("42", "/onay").startswith("✅")
    assert bot.handle("42", "/onay") == "Onay bekleyen işlem yok."
    assert bot.handle("42", "5 ASELS 1").startswith("❌")
    assert bot.handle("42", "sat 1000 THYAO 300").startswith("Satış")
    assert bot.handle("42", "/onay").startswith("❌")  # açığa satış engellenir
    assert "Tanımlı alarm yok" in bot.handle("42", "/alarmlar")
    assert "planı tanımlı değil" in bot.handle("42", "/plan")
    assert "Bilinmeyen komut" in bot.handle("42", "/xyz")


# --------------------------------------------------------------------------- döküm aktarma
BROKER_LIKE = (
    "İşlem Tarihi;Sembol;İşlem Tipi;Adet;Fiyat;İşlem Tutarı;Komisyon;Para Birimi\n"
    "15.04.2026;SCHD;Alış;10;30,50;305,00;0;USD\n"
    "12.08.2026;SCHD;Alış;6;31,25;187,50;0;USD\n"
    "30.06.2026;SCHD;Temettü;0;0;2,10;0;USD\n"
    "10.10.2026;THYAO;Satış;10;310;3.100;1,5;TL\n"
)
BANK_LIKE = "Tarih,Ürün,Miktar (gr),Tutar\n10.02.2026,Gram Altın Alış,4,24000\n12.02.2026,Altın,3,18150\n"


def test_broker_import_with_auto_columns(c) -> None:
    pv = c.broker_import.preview(BROKER_LIKE.encode("utf-8"))
    assert pv.columns["side"] == 2 and pv.columns["fee"] == 6
    assert len(pv.parsed.rows) == 3 and pv.parsed.skipped == 1 and not pv.parsed.errors
    assert pv.unknown_symbols == ["SCHD"]
    res = c.broker_import.import_rows(pv.parsed.rows, {"SCHD": (AssetClass.US_STOCK, Currency.USD)})
    assert res.added == 3 and res.created_assets == ["SCHD"]
    again = c.broker_import.import_rows(c.broker_import.preview(BROKER_LIKE).parsed.rows)
    assert again.added == 0 and again.duplicates == 3
    sell = next(t for t in c.portfolio.transactions.list() if t.tx_type is TxType.SELL)
    assert sell.fee == D("1.5") and sell.price == D("310")


def test_broker_import_bank_gold_from_amount(c) -> None:
    pv = c.broker_import.preview(BANK_LIKE)
    rows = pv.parsed.rows
    assert [r.symbol for r in rows] == ["XAU-GRAM", "XAU-GRAM"] and rows[0].price == D("6000")
    assert c.broker_import.import_rows(rows).added == 2


def test_broker_profiles(c) -> None:
    pv = c.broker_import.preview(BROKER_LIKE)
    c.broker_import.save_profile("Kurum A", pv.header, pv.columns)
    prof = c.broker_import.profiles()["Kurum A"]
    assert prof["quantity"] == "Adet"
    reordered = ["Adet", "İşlem Tarihi", "Sembol", "Fiyat"]
    cols = c.broker_import.apply_profile(prof, reordered)
    assert cols["quantity"] == 0 and cols["date"] == 1 and cols["fee"] is None
    c.broker_import.delete_profile("Kurum A")
    assert c.broker_import.profiles() == {}
    bad = c.broker_import.preview("Tarih;Sembol\n01.01.2026;X\n")
    assert bad.parsed.errors


# --------------------------------------------------------------------------- güvenlik
def test_password_lifecycle(c) -> None:
    s = c.security
    assert not s.has_password
    with pytest.raises(ValidationError):
        s.set_password("kısa")
    s.set_password("cok-gizli-parola")
    assert s.has_password and s.verify("cok-gizli-parola") and not s.verify("yanlis")
    with pytest.raises(ValidationError):
        s.set_password("yeni-parola-123", current="yanlis")
    s.set_password("yeni-parola-123", current="cok-gizli-parola")
    assert s.verify("yeni-parola-123")
    assert "pbkdf2" not in c.transfer.export_json()  # özet yedeğe yazılmaz
    s.clear_password("yeni-parola-123")
    assert not s.has_password


def test_encrypted_backup_roundtrip(c) -> None:
    raw = c.transfer.backup_db()
    blob = c.security.encrypt(raw, "yedek-parolasi")
    assert blob.startswith(b"DTENC1") and raw not in blob
    assert c.security.decrypt(blob, "yedek-parolasi") == raw
    with pytest.raises(ValidationError):
        c.security.decrypt(blob, "yanlis-parola")
    with pytest.raises(ValidationError):
        c.security.decrypt(b"baska dosya", "yedek-parolasi")


# --------------------------------------------------------------------------- CLI
def test_cli_add_and_check(c, monkeypatch, capsys) -> None:
    import divitrack.cli as cli

    monkeypatch.setattr(cli, "build_container", lambda: c)
    assert cli.main(["add", "2 gr altın 5000", "--yes"]) == 0
    assert "Kaydedildi" in capsys.readouterr().out
    assert cli.main(["add", "2 ASELS 5000", "--yes"]) == 2
    monkeypatch.setattr("builtins.input", lambda _: "h")
    assert cli.main(["add", "1 gr altın 5000"]) == 1
    assert cli.main(["check"]) == 0
    assert "Tetiklenen alarm yok" in capsys.readouterr().out
    assert c.snapshots.history(Currency.TRY)
