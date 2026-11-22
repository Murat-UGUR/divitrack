"""Komut satırı: zamanlanmış görevler ve Telegram botu için.

    python -m divitrack.cli check            # fiyatları yenile, günlük kaydı al, alarmları denetle (+Telegram)
    python -m divitrack.cli add "5 gr altın 5750"   # metinle işlem ekle (onay sorar; --yes ile sormaz)
    python -m divitrack.cli bot              # Telegram botunu çalıştır

Windows Görev Zamanlayıcı veya cron ile `check` komutunu her akşam çalıştırabilirsin.
"""

from __future__ import annotations

import argparse
import sys

from divitrack.bootstrap import build_container
from divitrack.core.enums import Currency
from divitrack.core.exceptions import DivitrackError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="divitrack", description="DiviTrack komut satırı")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="fiyatları yenile, günlük kaydı al ve alarmları denetle")
    add = sub.add_parser("add", help='metinle işlem ekle: "5 gr altın 5750"')
    add.add_argument("text")
    add.add_argument("--yes", action="store_true", help="onay sormadan kaydet")
    sub.add_parser("bot", help="Telegram botunu çalıştır")
    args = parser.parse_args(argv)

    c = build_container()
    if args.command == "check":
        summary = c.portfolio.summary(Currency.TRY, force_refresh=True)
        c.snapshots.record(summary)
        fired = c.alerts.check(summary)
        print(f"Toplam değer: {summary.total_value:,.2f} TL")
        for s in fired:
            print(s.message)
        if not fired:
            print("Tetiklenen alarm yok.")
        return 0
    if args.command == "add":
        try:
            entry = c.quick.prepare(args.text)
        except DivitrackError as exc:
            print(f"Hata: {exc}", file=sys.stderr)
            return 2
        print(entry.describe())
        for w in entry.warnings:
            print(f"Uyarı: {w}")
        if not args.yes and input("Kaydedilsin mi? [e/H] ").strip().lower() not in ("e", "evet", "y", "yes"):
            print("Vazgeçildi.")
            return 1
        c.quick.save(entry)
        print("Kaydedildi.")
        return 0
    if args.command == "bot":  # pragma: no cover - ağ döngüsü
        if not c.notifier.configured:
            print("Önce Ayarlar > Bildirimler bölümünden bot anahtarını ve sohbet kimliğini gir.", file=sys.stderr)
            return 2
        from divitrack.services.telegram_bot import TelegramBot

        TelegramBot(c).poll_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
