# 📈 DiviTrack

**Gizlilik odaklı, tamamen lokal çalışan kişisel portföy ve temettü takip uygulaması.**

BIST hisseleri, ABD hisse/ETF'leri, kıymetli madenler ve nakit/BES varlıklarınızı tek panelde izleyin; temettü gelirlerinizi kaydedin, gelecek 12 ayın temettü takvimini görün. Tüm veriler **sizin bilgisayarınızda**, tek bir SQLite dosyasında durur. Hesap yok, bulut yok, telemetri yok.

[![CI](https://github.com/Murat-UGUR/divitrack/actions/workflows/ci.yml/badge.svg)](https://github.com/Murat-UGUR/divitrack/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-blue) ![Streamlit](https://img.shields.io/badge/UI-Streamlit-red) ![SQLite](https://img.shields.io/badge/DB-SQLite-lightgrey) ![License](https://img.shields.io/badge/license-MIT-green)

---

## ✨ Özellikler

| Modül | Açıklama |
|---|---|
| **Portföy Yönetimi** | Alım/satım işlem defteri; ağırlıklı ortalama maliyet (komisyon dahil), gerçekleşen ve gerçekleşmemiş K/Z, açığa satış koruması. TRY, USD, EUR. |
| **Varlık Sınıfları** | BIST Hisse · ABD Hisse/ETF · Kıymetli Maden (gram/ons altın ve gümüş) · Nakit/BES (manuel fiyat) |
| **Temettü Takibi** | Ödenen/beklenen kayıtlar, stopaj (varsayılan BIST %15, ABD %20 — ayarlanabilir), TTM verim ve maliyete göre verim, 24 aylık takvim grafiği, Yahoo geçmişinden otomatik kayıt önerisi |
| **Canlı Veri** | `yfinance` ile fiyat, temettü geçmişi ve döviz kurları; SQLite önbelleği, ağ hatasında son bilinen fiyatla çalışma, tam çevrimdışı mod |
| **Dashboard** | Toplam değer + günlük değişim, K/Z, 12 aylık net temettü beklentisi, varlık dağılımı (donut), zaman içindeki değer grafiği (geçmişe dönük yeniden oluşturma dahil) |
| **Veri Taşınabilirliği** | JSON tam yedek (birleştir/geri yükle), işlem ve temettü CSV'leri (Excel'in `;` ayracı desteklenir), tek tıkla `.db` yedeği |

## 🚀 Hızlı Başlangıç

```bash
git clone https://github.com/Murat-UGUR/divitrack.git
cd divitrack
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py             # → http://127.0.0.1:8501
```

İsteğe bağlı ayarlar için `cp .env.example .env`.

**Docker ile:**

```bash
docker build -t divitrack .
docker run -p 127.0.0.1:8501:8501 -v "$(pwd)/data:/app/data" divitrack
```

## 🧭 Kullanım

1. **Varlıklar & İşlemler → Varlıklar**: varlığı tanımlayın (ör. BIST `THYAO`, ABD `SCHD`, maden `Gram Altın`, nakit `NAKIT-USD`).
2. **Yeni İşlem**: alış/satış girin. Ondalık için `12,5` veya `12.5` yazabilirsiniz.
3. **Portföy Özeti**: değer, K/Z ve dağılım otomatik hesaplanır. Değer grafiği için *Geçmişi işlemlerden yeniden oluştur*.
4. **Temettüler**: *Geçmişten Aktar* sekmesi, elde tuttuğunuz döneme denk gelen ödemeleri tek tıkla kaydeder; gelecek ödemeler geçen yılın takvimine göre tahmin edilir.
5. **Ayarlar & Veri**: stopaj oranları, yedekleme, CSV/JSON içe aktarma.

### Sembol kuralları

| Varlık | Örnek | Not |
|---|---|---|
| BIST hisse | `THYAO`, `TUPRS` | `.IS` soneki otomatik eklenir; para birimi TRY |
| ABD hisse/ETF | `AAPL`, `SCHD`, `VOO` | |
| Gram altın / gümüş | `XAU-GRAM`, `XAG-GRAM` | `ons (GC=F / SI=F) × USDTRY ÷ 31,1035` ile türetilir (TRY) |
| Ons altın / gümüş | `XAU-OZ`, `XAG-OZ` | Vadeli kontrat fiyatı (USD), spot'a yakın |
| Nakit | `NAKIT-TRY` | Adet = tutar, fiyat = 1 |
| BES / fon | `BES-FON1` | Manuel birim fiyat girilir, istenince güncellenir |

### CSV formatı

```csv
date,symbol,asset_class,currency,type,quantity,price,fee,note
2024-01-15,THYAO,BIST_STOCK,TRY,BUY,100,285.50,12.5,
2024-02-01,SCHD,US_STOCK,USD,BUY,10,76.20,0,
```

`asset_class`: `BIST_STOCK | US_STOCK | PRECIOUS_METAL | CASH` · `type`: `BUY | SELL`

## 🏛️ Mimari

Clean Architecture: bağımlılıklar **yalnızca içe doğru** akar. `core` saf Python'dur; Streamlit, SQLite veya yfinance bilmez.

```
┌───────────────────────────────────────────────────────────┐
│ UI (Streamlit)        ui/pages, ui/components, ui/tables  │
├───────────────────────────────────────────────────────────┤
│ Services              portfolio · dividend_engine ·       │
│                       snapshot · market · io/transfer     │
├───────────────────────────────────────────────────────────┤
│ Core (Domain)         entities · calculations · ports     │
├───────────────────────────────────────────────────────────┤
│ Infrastructure        db/ (sqlite3) · repositories/ ·     │
│                       market/yfinance_provider            │
└───────────────────────────────────────────────────────────┘
          bootstrap.py → tüm bağımlılıkları bağlar (DI)
```

Ayrıntılar: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

```
divitrack/
├── app.py                       # streamlit run app.py
├── src/divitrack/
│   ├── config.py                # .env / ortam değişkenleri
│   ├── bootstrap.py             # composition root
│   ├── core/                    # enums, entities, calculations, ports, money, exceptions
│   ├── db/                      # sqlite3 bağlantısı, şema + migration (PRAGMA user_version)
│   ├── repositories/            # satır ↔ entity, CRUD
│   ├── services/
│   │   ├── market/              # PriceProvider adaptörü, sembol eşleme, önbellek, FX
│   │   ├── io/                  # JSON/CSV içe-dışa aktarma, DB yedeği
│   │   ├── portfolio_service.py
│   │   ├── dividend_engine.py
│   │   └── snapshot_service.py
│   └── ui/                      # Streamlit sayfaları, grafikler, tablolar, biçimlendirme
├── tests/                       # unit + integration + UI smoke testleri
├── docs/                        # mimari ve Git rehberi
└── data/                        # lokal veritabanı (git'e girmez)
```

## 🔒 Gizlilik

- Tüm veriler `data/divitrack.db` içinde kalır; `.gitignore` tüm `*.db`, `*.csv`, `data/` ve `.env` dosyalarını dışlar.
- Sunucu yalnızca `127.0.0.1`'e bağlanır; Streamlit telemetrisi kapalıdır.
- Dışarıya giden tek trafik Yahoo Finance'e **yalnızca sembol adlarıyla** yapılan fiyat sorgularıdır. Adet, maliyet, işlem bilgisi asla gönderilmez. `DIVITRACK_OFFLINE=1` ile bu da kapatılabilir.

## 🧪 Geliştirme

```bash
pip install -r requirements-dev.txt
make test     # pytest + coverage (ağ gerektirmez, sahte sağlayıcı kullanır)
make lint     # ruff + mypy
make format
```

Yeni bir veri sağlayıcısı eklemek için `core/ports.py` içindeki `PriceProvider` protokolünü uygulayan bir sınıf yazıp `bootstrap.default_provider` içinde bağlamanız yeterli.

## 🗺️ Yol Haritası

- [x] Mimari ve proje iskeleti
- [x] SQLite şeması, domain modelleri, repository katmanı
- [x] Fiyat servisi, kur çevrimi, temettü motoru
- [x] Streamlit arayüzü ve dashboard
- [x] CI (GitHub Actions)
- [ ] TEFAS entegrasyonu (BES/yatırım fonu fiyatları)
- [ ] İşlem tarihindeki kurla TRY bazlı maliyet (enflasyon/kur etkisi ayrıştırma)
- [ ] Hedef dağılım ve rebalans önerisi

## ⚠️ Sorumluluk Reddi

Kişisel takip amaçlıdır; yatırım veya vergi tavsiyesi değildir. Yahoo Finance verileri gayriresmî bir istemciyle (`yfinance`) çekilir, gecikmeli veya hatalı olabilir. Stopaj oranları mevzuatla değişebilir; kendi durumunuza göre doğrulayın.

## 📄 Lisans

MIT © Murat UĞUR
