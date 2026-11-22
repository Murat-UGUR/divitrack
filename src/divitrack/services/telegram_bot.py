"""Telegram botu: mesajla hızlı işlem girişi (onaylı), portföy durumu, alarmlar ve alım planı.

Yalnızca Ayarlar'da girilen sohbet kimliğinden (chat id) gelen mesajlar işlenir.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from divitrack.core.enums import Currency
from divitrack.core.exceptions import DivitrackError
from divitrack.services.quick_entry_service import PreparedEntry

if TYPE_CHECKING:
    from divitrack.bootstrap import Container

log = logging.getLogger(__name__)

HELP = (
    "DiviTrack botu\n"
    "• İşlem yaz: 5 gr altın 5750 · sat 100 EREGL 45,5 · 12 SCHD 31.5$ 18.11.2026\n"
    "  (kaydetmeden önce sorarım; /onay ile kaydedilir, /iptal ile vazgeçilir)\n"
    "• /durum — toplam değer ve K/Z\n"
    "• /alarmlar — alarmların durumu\n"
    "• /plan — bu ayki alım planı"
)


def _tl(v: Decimal) -> str:
    return f"{v:,.0f}".replace(",", ".") + " TL"


@dataclass(slots=True)
class TelegramBot:
    container: Container
    pending: dict[str, PreparedEntry] = field(default_factory=dict)

    def handle(self, chat_id: str, text: str) -> str | None:
        c = self.container
        if str(chat_id) != c.notifier.chat_id:
            log.warning("Yetkisiz sohbetten mesaj: %s", chat_id)
            return None
        text = text.strip()
        cmd = text.split()[0].lower().split("@")[0] if text else ""
        try:
            if cmd in ("/start", "/yardim", "/yardım", "/help"):
                return HELP
            if cmd == "/durum":
                s = c.portfolio.summary(Currency.TRY)
                return (
                    f"Toplam: {_tl(s.total_value)}\nMaliyet: {_tl(s.total_cost)}\n"
                    f"K/Z: {_tl(s.unrealized_pnl)} (%{(s.unrealized_pct or 0):.1f})\nBugün: {_tl(s.day_change)}"
                )
            if cmd == "/alarmlar":
                states = c.alerts.evaluate()
                if not states:
                    return "Tanımlı alarm yok."
                return "\n".join(("🔴 " if s.triggered else "⚪ ") + s.message.removeprefix("🔔 ") for s in states)
            if cmd == "/plan":
                res = c.plans.buy_plan_status()
                if res is None:
                    return "Alım planı tanımlı değil (Aylık Plan sayfasından kurabilirsin)."
                plan, asset, st = res
                msg = f"{asset.symbol}: bu ay kalan {st.this_month_remaining.normalize():f}"
                if st.dip_active:
                    msg += f" + ek {plan.dip_extra.normalize():f} (fiyat eşiğin altında)"
                if st.suggested_amount is not None:
                    msg += f"\nÖnerilen alım: {st.suggested.normalize():f} ≈ {_tl(st.suggested_amount)}"
                return msg
            if cmd == "/onay":
                entry = self.pending.pop(str(chat_id), None)
                if entry is None:
                    return "Onay bekleyen işlem yok."
                c.quick.save(entry)
                return "✅ Kaydedildi: " + entry.describe()
            if cmd == "/iptal":
                return "Vazgeçildi." if self.pending.pop(str(chat_id), None) else "Bekleyen işlem yok."
            if cmd.startswith("/"):
                return "Bilinmeyen komut.\n\n" + HELP
            entry = c.quick.prepare(text)
            self.pending[str(chat_id)] = entry
            warn = ("\n⚠️ " + "\n⚠️ ".join(entry.warnings)) if entry.warnings else ""
            return f"{entry.describe()}{warn}\nKaydetmek için /onay, vazgeçmek için /iptal"
        except DivitrackError as exc:
            return f"❌ {exc}"

    def poll_forever(self, *, sleep: float = 2.0) -> None:  # pragma: no cover - ağ döngüsü
        offset = 0
        notifier = self.container.notifier
        log.info("Telegram botu başladı")
        while True:
            try:
                res: dict[str, Any] = notifier.call("getUpdates", offset=offset, timeout=30)
                for upd in res.get("result", []):
                    offset = upd["update_id"] + 1
                    msg = upd.get("message") or {}
                    if "text" not in msg:
                        continue
                    chat = str(msg["chat"]["id"])
                    reply = self.handle(chat, msg["text"])
                    if reply:
                        notifier.send(reply, chat_id=chat)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                log.warning("Telegram hatası: %s", exc)
                time.sleep(sleep * 5)
            time.sleep(sleep)
